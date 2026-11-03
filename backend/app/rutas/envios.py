"""Dominio: solicitudes, envíos y seguimiento (fuente oficial: PostgreSQL)."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import temporal
from app.conexiones import mongo_db, pg_pool
from app.errores import ErrorNegocio
from app.estados import ENDPOINT_DEDICADO, validar_transicion
from app.seguridad import requiere_rol, usuario_actual
from app.servicios import (SQL_ENVIO, bloquear_envio, ejecutar_idempotente, historial, liberar_asignacion,
                           obtener_envio, registrar_cambio, registrar_evento, verificar_acceso)

router = APIRouter(tags=["Envíos y seguimiento"])

Estado = Literal["CREADO", "ASIGNADO", "RETIRADO", "EN_TRANSITO", "INCIDENCIA", "ENTREGADO", "CANCELADO"]
# Coordenadas ficticias dentro del área metropolitana de Quito
Lat = Field(ge=-0.5, le=0.2)
Lng = Field(ge=-78.8, le=-78.2)


# ---------------------------------------------------------------- catálogos
@router.get("/catalogos", summary="Zonas y tipos de servicio activos (público)")
def catalogos():
    with pg_pool.connection() as cx:
        zonas = cx.execute("SELECT codigo, nombre FROM zonas WHERE activa ORDER BY nombre").fetchall()
        tipos = cx.execute("SELECT codigo, nombre, horas_objetivo FROM tipos_servicio WHERE activo ORDER BY id").fetchall()
    return {"zonas": zonas, "tipos_servicio": tipos}


# ---------------------------------------------------------------- crear envío
class EnvioEntrada(BaseModel):
    tipo_servicio: str = Field(max_length=15, examples=["ESTANDAR"])
    zona_origen: str = Field(max_length=10, examples=["NOR"])
    zona_destino: str = Field(max_length=10, examples=["SUR"])
    origen_direccion: str = Field(min_length=5, max_length=160, examples=["Av. Ficticia N10-100"])
    origen_lat: float = Lat
    origen_lng: float = Lng
    destino_direccion: str = Field(min_length=5, max_length=160, examples=["Calle Ejemplo S5-12"])
    destino_lat: float = Lat
    destino_lng: float = Lng
    remitente_nombre: str = Field(min_length=3, max_length=80)
    destinatario_nombre: str = Field(min_length=3, max_length=80)
    destinatario_telefono: str | None = Field(default=None, pattern=r"^0\d{9}$", examples=["0991234567"])
    peso_kg: float = Field(gt=0, le=50)
    cliente_id: int | None = Field(default=None, description="Solo operador/admin: cliente para quien se registra")


@router.post("/envios", status_code=201, summary="Registrar envío (Idempotency-Key obligatoria)")
def crear_envio(datos: EnvioEntrada,
                idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80),
                usuario: dict = Depends(requiere_rol("CLIENTE", "OPERADOR", "ADMIN"))):
    with pg_pool.connection() as cx:
        with cx.transaction():
            # Dos peticiones con la misma clave se atienden una detrás de otra
            cx.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (idempotency_key,))
            previo = cx.execute("SELECT codigo FROM envios WHERE idempotency_key = %s", (idempotency_key,)).fetchone()
            if previo:
                envio = obtener_envio(cx, previo["codigo"])
                return JSONResponse(status_code=200, content=jsonable_encoder(
                    {"repetido": True, "mensaje": "Este envío ya fue registrado con esa Idempotency-Key", "envio": envio}))

            # La API no confía en el navegador: valida catálogos y cliente en la base
            tipo = cx.execute("SELECT id FROM tipos_servicio WHERE codigo = %s AND activo", (datos.tipo_servicio,)).fetchone()
            zo = cx.execute("SELECT id FROM zonas WHERE codigo = %s AND activa", (datos.zona_origen,)).fetchone()
            zd = cx.execute("SELECT id FROM zonas WHERE codigo = %s AND activa", (datos.zona_destino,)).fetchone()
            if not tipo or not zo or not zd:
                raise ErrorNegocio(422, "CATALOGO_INVALIDO", "Tipo de servicio o zona inexistente o inactiva")

            if usuario["rol"] == "CLIENTE":
                cliente_id = usuario["uid"]
            else:
                cliente_id = datos.cliente_id
                if cliente_id is not None and not cx.execute(
                        "SELECT 1 FROM usuarios WHERE id = %s AND rol = 'CLIENTE' AND activo", (cliente_id,)).fetchone():
                    raise ErrorNegocio(422, "CLIENTE_INVALIDO", "El cliente indicado no existe")

            # Código único: fecha + secuencia (UNIQUE en la tabla)
            codigo = cx.execute(
                """SELECT 'MU-' || to_char(now() AT TIME ZONE 'America/Guayaquil', 'YYMMDD')
                          || '-' || lpad(nextval('seq_codigo_envio')::text, 6, '0') AS c""").fetchone()["c"]

            envio = cx.execute(
                """INSERT INTO envios (codigo, cliente_id, creado_por, tipo_servicio_id, zona_origen_id, zona_destino_id,
                                       origen_direccion, origen_lat, origen_lng, destino_direccion, destino_lat, destino_lng,
                                       remitente_nombre, destinatario_nombre, destinatario_telefono, peso_kg, idempotency_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (codigo, cliente_id, usuario["uid"], tipo["id"], zo["id"], zd["id"],
                 datos.origen_direccion, datos.origen_lat, datos.origen_lng,
                 datos.destino_direccion, datos.destino_lat, datos.destino_lng,
                 datos.remitente_nombre, datos.destinatario_nombre, datos.destinatario_telefono,
                 datos.peso_kg, idempotency_key),
            ).fetchone()
            # Primer evento del historial (estado_anterior = NULL) + outbox, en la misma transacción
            registrar_evento(cx, envio, None, "CREADO", usuario, "CREACION")

        return {"repetido": False, "mensaje": f"Envío registrado con código {codigo}", "envio": obtener_envio(cx, codigo)}


# ---------------------------------------------------------------- consultas
@router.get("/envios/mios", summary="Envíos del cliente que inició sesión")
def mis_envios(usuario: dict = Depends(requiere_rol("CLIENTE"))):
    with pg_pool.connection() as cx:
        return cx.execute(SQL_ENVIO + " WHERE e.cliente_id = %s ORDER BY e.creado_en DESC", (usuario["uid"],)).fetchall()


def leer_evidencia(evidencia_id: str | None) -> tuple[dict | None, str | None]:
    """Evidencia desde MongoDB; si MongoDB cae, el detalle se muestra igual con un aviso."""
    if not evidencia_id:
        return None, None
    try:
        return mongo_db["evidencias_entrega"].find_one({"_id": evidencia_id}), None
    except Exception:
        return None, "MongoDB no disponible: la entrega consta en PostgreSQL, la evidencia no se puede mostrar ahora"


@router.get("/envios/{codigo}", summary="Detalle del envío: PostgreSQL + evidencia (MongoDB) + posición (Redis)")
def detalle_envio(codigo: str, usuario: dict = Depends(usuario_actual)):
    with pg_pool.connection() as cx:
        envio = obtener_envio(cx, codigo.upper())
        verificar_acceso(cx, envio, usuario, solo_lectura=True)
        hist = historial(cx, envio["id"])
    evidencia, aviso = leer_evidencia(envio["evidencia_id"])
    posicion = (temporal.leer_posicion_segura(envio["repartidor_id"])
                if envio["repartidor_id"] and envio["estado"] in ("ASIGNADO", "RETIRADO", "EN_TRANSITO", "INCIDENCIA")
                else None)
    return {"envio": envio, "historial": hist, "evidencia": evidencia, "posicion_repartidor": posicion,
            "avisos": [aviso] if aviso else [],
            "fuentes": {"envio": "postgresql", "historial": "postgresql", "evidencia": "mongodb", "posicion": "redis"}}


@router.get("/seguimiento/{codigo}", summary="Seguimiento público por código (sin datos sensibles)")
def seguimiento(codigo: str):
    with pg_pool.connection() as cx:
        e = obtener_envio(cx, codigo.upper())
        linea = [{"secuencia": h["secuencia"], "tipo": h["tipo"], "estado": h["estado_nuevo"],
                  "rol": h["actor_rol"], "observacion": h["detalle"].get("observacion"), "fecha": h["ocurrido_en"]}
                 for h in historial(cx, e["id"])]
    evidencia, aviso = leer_evidencia(e["evidencia_id"])
    resumen = ({"recibido_por": evidencia["receptor"]["nombre"], "registrada_en": evidencia["registrada_en"],
                "estado": evidencia["estado"]} if evidencia else None)
    return {"codigo": e["codigo"], "estado": e["estado"], "tipo_servicio": e["tipo_servicio"],
            "zona_origen": e["zona_origen"], "zona_destino": e["zona_destino"],
            "destinatario": e["destinatario_nombre"].split()[0],      # solo el primer nombre
            "linea_de_tiempo": linea, "prueba_de_entrega": resumen, "avisos": [aviso] if aviso else [],
            "fuente": "postgresql (estado e historial) + mongodb (evidencia)"}


# ---------------------------------------------------------------- cambio de estado
class CambioEstado(BaseModel):
    estado_nuevo: Estado
    evento_id: uuid.UUID = Field(description="UUID generado por el cliente; reintentar con el mismo no duplica")
    observacion: str | None = Field(default=None, max_length=300)
    lat: float | None = Field(default=None, ge=-0.5, le=0.2)
    lng: float | None = Field(default=None, ge=-78.8, le=-78.2)


@router.patch("/envios/{codigo}/estado", summary="Cambiar estado según la máquina de estados (idempotente)")
def cambiar_estado(codigo: str, datos: CambioEstado,
                   usuario: dict = Depends(requiere_rol("CLIENTE", "OPERADOR", "ADMIN", "REPARTIDOR"))):
    codigo = codigo.upper()

    def operacion(cx):
        envio = bloquear_envio(cx, codigo)
        validar_transicion(envio["estado"], datos.estado_nuevo, usuario["rol"])
        if datos.estado_nuevo in ENDPOINT_DEDICADO:
            raise ErrorNegocio(422, "USAR_ENDPOINT_DEDICADO",
                               f"Para pasar a {datos.estado_nuevo} use {ENDPOINT_DEDICADO[datos.estado_nuevo]}")
        verificar_acceso(cx, envio, usuario)        # cliente: el suyo; repartidor: el asignado
        if datos.estado_nuevo == "INCIDENCIA" and not datos.observacion:
            raise ErrorNegocio(422, "OBSERVACION_REQUERIDA", "Describa la incidencia en 'observacion'")

        detalle = {"observacion": datos.observacion} if datos.observacion else {}
        if datos.estado_nuevo == "CANCELADO":
            liberado = liberar_asignacion(cx, envio["id"], "CANCELACION")   # devuelve la capacidad
            if liberado:
                detalle["repartidor_liberado"] = liberado
        registrar_cambio(cx, envio, datos.estado_nuevo, usuario, "CAMBIO_ESTADO",
                         datos.evento_id, datos.lat, datos.lng, detalle)

    return ejecutar_idempotente(datos.evento_id, codigo, operacion)
