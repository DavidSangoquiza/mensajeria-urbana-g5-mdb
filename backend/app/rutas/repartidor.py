"""Vista del repartidor: tareas, aceptación, entrega con evidencia y posición temporal.

Entrega con evidencia (atraviesa DOS gestores, sin transacción conjunta):
  1. MongoDB : se guarda la evidencia con estado PENDIENTE   (_id = EV-{codigo})
  2. PostgreSQL (transacción local): envío -> ENTREGADO + evidencia_id + evento + outbox,
     y se libera la capacidad del repartidor
  3. MongoDB : la evidencia pasa a CONFIRMADA (si falla, el sincronizador la confirma luego)
Si falla el paso 1, el envío sigue EN_TRANSITO y se puede reintentar.
Si falla el paso 2, queda una evidencia PENDIENTE que el reintento (mismo evento_id) reutiliza.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal

import pymongo.errors
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.conexiones import mongo_db, pg_pool
from app.errores import ErrorNegocio
from app.estados import TRANSICIONES, validar_transicion
from app.seguridad import requiere_rol
from app.servicios import (SQL_ENVIO, asignacion_activa, bloquear_envio, ejecutar_idempotente,
                           liberar_asignacion, obtener_envio, registrar_cambio, verificar_acceso)
from app import temporal

router = APIRouter(tags=["Repartidor y entregas"])
REPARTIDOR = requiere_rol("REPARTIDOR")
OPERACION = requiere_rol("OPERADOR", "ADMIN")


def acciones_repartidor(estado: str) -> list[str]:
    """Botones que la web debe mostrar según la máquina de estados."""
    acciones = [destino for destino, roles in TRANSICIONES.get(estado, {}).items() if "REPARTIDOR" in roles]
    if estado == "ASIGNADO":
        acciones.insert(0, "ACEPTAR")
    return acciones


# ---------------------------------------------------------------- tareas
@router.get("/repartidor/tareas", summary="Envíos activos asignados al repartidor y acciones permitidas")
def tareas(usuario: dict = Depends(REPARTIDOR)):
    with pg_pool.connection() as cx:
        envios = cx.execute(SQL_ENVIO + " WHERE a.repartidor_id = %s ORDER BY e.actualizado_en DESC",
                            (usuario["rid"],)).fetchall()
        for e in envios:
            e["acciones"] = acciones_repartidor(e["estado"])
            e["aceptada"] = cx.execute(
                """SELECT 1 FROM historial_estados
                    WHERE envio_id = %s AND tipo = 'ACEPTACION'
                      AND id > (SELECT max(id) FROM historial_estados
                                 WHERE envio_id = %s AND tipo IN ('ASIGNACION', 'REASIGNACION'))""",
                (e["id"], e["id"])).fetchone() is not None
    return {"repartidor_id": usuario["rid"], "tareas": envios, "posicion": temporal.leer_posicion_segura(usuario["rid"])}


class EventoSimple(BaseModel):
    evento_id: uuid.UUID


@router.post("/repartidor/tareas/{codigo}/aceptar", summary="El repartidor acepta la asignación")
def aceptar(codigo: str, datos: EventoSimple, usuario: dict = Depends(REPARTIDOR)):
    codigo = codigo.upper()

    def operacion(cx):
        envio = bloquear_envio(cx, codigo)
        if envio["estado"] != "ASIGNADO":
            raise ErrorNegocio(409, "NO_ACEPTABLE", f"Solo se acepta un envío ASIGNADO (está {envio['estado']})")
        verificar_acceso(cx, envio, usuario)
        # Evento de aceptación: no cambia el estado (ASIGNADO -> ASIGNADO), queda en el historial
        registrar_cambio(cx, envio, "ASIGNADO", usuario, "ACEPTACION", datos.evento_id)

    return ejecutar_idempotente(datos.evento_id, codigo, operacion)


# ---------------------------------------------------------------- entrega
class Receptor(BaseModel):
    nombre: str = Field(min_length=2, max_length=80, examples=["Elena Destino Cuatro"])
    relacion: str | None = Field(default=None, max_length=40, examples=["Titular"])


class Foto(BaseModel):
    tipo: Literal["url", "referencia_archivo"] = "referencia_archivo"
    valor: str = Field(min_length=3, max_length=300, examples=["foto_entrega_001.jpg"])


class EntregaEntrada(BaseModel):
    evento_id: uuid.UUID
    receptor: Receptor
    foto: Foto
    observacion: str | None = Field(default=None, max_length=300)
    extras: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=10,
                                                        description="Atributos variables (firma simulada, bultos, etc.)")
    lat: float | None = Field(default=None, ge=-0.5, le=0.2)
    lng: float | None = Field(default=None, ge=-78.8, le=-78.2)


@router.post("/envios/{codigo}/entrega", status_code=201, summary="Registrar entrega con evidencia (MongoDB + PostgreSQL)")
def entregar(codigo: str, datos: EntregaEntrada, response: Response, usuario: dict = Depends(REPARTIDOR)):
    codigo = codigo.upper()
    evidencia_id = f"EV-{codigo}"
    coleccion = mongo_db["evidencias_entrega"]

    # 0) Validación previa (solo lectura) para no guardar evidencias sin sentido
    with pg_pool.connection() as cx:
        repetido = cx.execute("SELECT 1 FROM historial_estados WHERE evento_id = %s", (datos.evento_id,)).fetchone()
        if not repetido:
            envio = obtener_envio(cx, codigo)
            validar_transicion(envio["estado"], "ENTREGADO", usuario["rol"])
            verificar_acceso(cx, envio, usuario)

    if not repetido:
        # 1) Evidencia en MongoDB (fuente oficial del contenido de la prueba de entrega)
        documento = {
            "_id": evidencia_id,
            "envio_codigo": codigo,
            "evento_id": str(datos.evento_id),
            "receptor": datos.receptor.model_dump(exclude_none=True),
            "foto": datos.foto.model_dump(),
            "observacion": datos.observacion,
            "extras": datos.extras,
            "ubicacion_entrega": ({"type": "Point", "coordinates": [datos.lng, datos.lat]}
                                  if datos.lat is not None and datos.lng is not None else None),
            "registrada_por": {"usuario": usuario["usuario"], "repartidor_id": usuario["rid"]},
            "estado": "PENDIENTE",
            "registrada_en": datetime.now(timezone.utc),
        }
        try:
            # Reemplaza una evidencia PENDIENTE de un intento fallido; nunca una CONFIRMADA
            coleccion.replace_one({"_id": evidencia_id, "estado": "PENDIENTE"}, documento, upsert=True)
        except pymongo.errors.DuplicateKeyError:
            raise ErrorNegocio(409, "YA_ENTREGADO", "Este envío ya tiene una evidencia confirmada")
        except pymongo.errors.PyMongoError:
            raise ErrorNegocio(503, "EVIDENCIA_NO_GUARDADA",
                               "MongoDB no disponible: la entrega NO se registró y el envío sigue EN_TRANSITO. "
                               "Puede reintentar con el mismo evento_id.")

    # 2) Transacción local en PostgreSQL
    def operacion(cx):
        envio = bloquear_envio(cx, codigo)
        validar_transicion(envio["estado"], "ENTREGADO", usuario["rol"])
        verificar_acceso(cx, envio, usuario)
        liberar_asignacion(cx, envio["id"], "ENTREGA")                     # libera capacidad
        cx.execute("UPDATE envios SET evidencia_id = %s WHERE id = %s", (evidencia_id, envio["id"]))
        registrar_cambio(cx, envio, "ENTREGADO", usuario, "ENTREGA", datos.evento_id, datos.lat, datos.lng,
                         {"evidencia_id": evidencia_id, "receptor": datos.receptor.nombre})

    resultado = ejecutar_idempotente(datos.evento_id, codigo, operacion)

    # 3) Confirmar la evidencia (si MongoDB falla aquí, el sincronizador la confirma después)
    try:
        coleccion.update_one({"_id": evidencia_id}, {"$set": {"estado": "CONFIRMADA",
                                                              "confirmada_en": datetime.now(timezone.utc)}})
        resultado["evidencia"] = coleccion.find_one({"_id": evidencia_id})
    except pymongo.errors.PyMongoError:
        resultado["aviso"] = "Entrega registrada; la evidencia se confirmará cuando MongoDB responda"

    if resultado["repetido"]:
        response.status_code = 200
    return resultado


# ---------------------------------------------------------------- estado temporal (Redis)
class Posicion(BaseModel):
    lat: float = Field(ge=-0.5, le=0.2, examples=[-0.2])
    lng: float = Field(ge=-78.8, le=-78.2, examples=[-78.5])


@router.post("/repartidor/ubicacion", summary="Reportar posición simulada (Redis con TTL)")
def reportar_ubicacion(datos: Posicion, usuario: dict = Depends(REPARTIDOR)):
    ttl = temporal.guardar_posicion(usuario["rid"], datos.lat, datos.lng)   # si Redis cae -> 503
    return {"mensaje": f"Posición guardada; caduca en {ttl} s si no se vuelve a reportar",
            "clave_redis": temporal.clave(usuario["rid"]), "fuente": "redis"}


@router.post("/repartidor/desconectar", summary="Marcar la posición como no vigente")
def desconectar(usuario: dict = Depends(REPARTIDOR)):
    temporal.marcar_no_vigente(usuario["rid"])
    return {"mensaje": "Posición marcada como no vigente", "fuente": "redis"}


@router.get("/repartidores/estado-temporal", summary="Posición vigente o no vigente de cada repartidor")
def estado_temporal(usuario: dict = Depends(OPERACION)):
    with pg_pool.connection() as cx:
        reps = cx.execute("SELECT id, nombre FROM repartidores WHERE activo ORDER BY id").fetchall()
    return [{**r, "posicion": temporal.leer_posicion_segura(r["id"])} for r in reps]
