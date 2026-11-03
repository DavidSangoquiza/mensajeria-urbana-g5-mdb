"""Dominio: despacho y asignaciones (fuente oficial: PostgreSQL).

Control de capacidad concurrente:
  1. Se bloquea la fila del envío      (SELECT ... FOR UPDATE)
  2. Se bloquea la fila del repartidor (SELECT ... FOR UPDATE)
  3. Se cuentan sus asignaciones activas y se compara con capacidad_max
Si dos operadores asignan a la vez al mismo repartidor, el segundo ESPERA
al primero y, al continuar, ya ve la carga actualizada -> 409 CAPACIDAD_EXCEDIDA.
"""
import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.conexiones import pg_pool
from app.errores import ErrorNegocio
from app.estados import validar_transicion
from app.seguridad import requiere_rol
from app.servicios import (asignacion_activa, bloquear_envio, ejecutar_idempotente,
                           liberar_asignacion, registrar_cambio)

router = APIRouter(tags=["Despacho y asignaciones"])
OPERACION = requiere_rol("OPERADOR", "ADMIN")


def reservar_capacidad(cx, repartidor_id: int) -> dict:
    """Bloquea al repartidor y verifica que tenga un cupo libre."""
    rep = cx.execute("SELECT * FROM repartidores WHERE id = %s FOR UPDATE", (repartidor_id,)).fetchone()
    if rep is None:
        raise ErrorNegocio(404, "REPARTIDOR_NO_ENCONTRADO", f"No existe el repartidor {repartidor_id}")
    if not rep["activo"]:
        raise ErrorNegocio(409, "REPARTIDOR_INACTIVO", f"{rep['nombre']} no está activo")
    carga = cx.execute("SELECT count(*) AS n FROM asignaciones WHERE repartidor_id = %s AND activa",
                       (repartidor_id,)).fetchone()["n"]
    if carga >= rep["capacidad_max"]:
        raise ErrorNegocio(409, "CAPACIDAD_EXCEDIDA",
                           f"{rep['nombre']} ya tiene {carga} de {rep['capacidad_max']} envíos activos",
                           {"repartidor_id": repartidor_id, "carga_actual": carga, "capacidad_max": rep["capacidad_max"]})
    return {**rep, "carga_despues": carga + 1}


def crear_asignacion(cx, envio: dict, rep: dict, usuario: dict) -> dict:
    return cx.execute(
        "INSERT INTO asignaciones (envio_id, repartidor_id, operador_id) VALUES (%s, %s, %s) RETURNING *",
        (envio["id"], rep["id"], usuario["uid"])).fetchone()


# ---------------------------------------------------------------- asignar
class AsignarEntrada(BaseModel):
    codigo: str = Field(max_length=20, examples=["MU-DEMO-0001"])
    repartidor_id: int = Field(gt=0, examples=[4])
    evento_id: uuid.UUID


@router.post("/asignaciones", status_code=201, summary="Asignar un envío CREADO a un repartidor (controla capacidad)")
def asignar(datos: AsignarEntrada, response: Response, usuario: dict = Depends(OPERACION)):
    codigo = datos.codigo.upper()

    def operacion(cx):
        envio = bloquear_envio(cx, codigo)                       # 1) bloquea el envío
        if envio["estado"] != "CREADO":
            raise ErrorNegocio(409, "ENVIO_NO_PENDIENTE",
                               f"El envío está {envio['estado']}; para cambiar de repartidor use /reasignar")
        validar_transicion(envio["estado"], "ASIGNADO", usuario["rol"])
        rep = reservar_capacidad(cx, datos.repartidor_id)        # 2) y 3) bloquea y cuenta
        asignacion = crear_asignacion(cx, envio, rep, usuario)
        registrar_cambio(cx, envio, "ASIGNADO", usuario, "ASIGNACION", datos.evento_id,
                         detalle={"repartidor_id": rep["id"], "repartidor": rep["nombre"]})
        return {"asignacion": asignacion,
                "capacidad": {"repartidor": rep["nombre"], "carga_actual": rep["carga_despues"],
                              "capacidad_max": rep["capacidad_max"]}}

    resultado = ejecutar_idempotente(datos.evento_id, codigo, operacion)
    if resultado["repetido"]:
        response.status_code = 200
    return resultado


# ---------------------------------------------------------------- reasignar
class ReasignarEntrada(BaseModel):
    repartidor_id: int = Field(gt=0)
    evento_id: uuid.UUID
    observacion: str | None = Field(default=None, max_length=300)


@router.post("/asignaciones/{codigo}/reasignar", summary="Cambiar de repartidor (ASIGNADO o INCIDENCIA)")
def reasignar(codigo: str, datos: ReasignarEntrada, usuario: dict = Depends(OPERACION)):
    codigo = codigo.upper()

    def operacion(cx):
        envio = bloquear_envio(cx, codigo)
        validar_transicion(envio["estado"], "ASIGNADO", usuario["rol"])
        if envio["estado"] == "CREADO":
            raise ErrorNegocio(409, "SIN_ASIGNACION", "El envío no tiene repartidor; use POST /api/asignaciones")
        actual = asignacion_activa(cx, envio["id"])
        if actual and actual["repartidor_id"] == datos.repartidor_id:
            raise ErrorNegocio(409, "MISMO_REPARTIDOR", "El envío ya está asignado a ese repartidor")
        rep = reservar_capacidad(cx, datos.repartidor_id)
        anterior = liberar_asignacion(cx, envio["id"], "REASIGNACION")   # libera el cupo del anterior
        asignacion = crear_asignacion(cx, envio, rep, usuario)
        detalle = {"repartidor_id": rep["id"], "repartidor": rep["nombre"], "repartidor_anterior": anterior}
        if datos.observacion:
            detalle["observacion"] = datos.observacion
        registrar_cambio(cx, envio, "ASIGNADO", usuario, "REASIGNACION", datos.evento_id, detalle=detalle)
        return {"asignacion": asignacion}

    return ejecutar_idempotente(datos.evento_id, codigo, operacion)


# ---------------------------------------------------------------- liberar
class LiberarEntrada(BaseModel):
    evento_id: uuid.UUID
    observacion: str | None = Field(default=None, max_length=300)


@router.post("/asignaciones/{codigo}/liberar", summary="Quitar el repartidor: el envío vuelve a CREADO")
def liberar(codigo: str, datos: LiberarEntrada, usuario: dict = Depends(OPERACION)):
    codigo = codigo.upper()

    def operacion(cx):
        envio = bloquear_envio(cx, codigo)
        validar_transicion(envio["estado"], "CREADO", usuario["rol"])
        anterior = liberar_asignacion(cx, envio["id"], "LIBERADA")
        detalle = {"repartidor_liberado": anterior}
        if datos.observacion:
            detalle["observacion"] = datos.observacion
        registrar_cambio(cx, envio, "CREADO", usuario, "LIBERACION", datos.evento_id, detalle=detalle)

    return ejecutar_idempotente(datos.evento_id, codigo, operacion)


# ---------------------------------------------------------------- panel
@router.get("/despacho/repartidores", summary="Repartidores con su carga y capacidad (calculadas en PostgreSQL)")
def repartidores(usuario: dict = Depends(OPERACION)):
    with pg_pool.connection() as cx:
        filas = cx.execute(
            """SELECT r.id, r.nombre, r.vehiculo, r.capacidad_max, r.activo,
                      z.codigo AS zona_codigo, z.nombre AS zona,
                      count(a.id)::int AS carga_actual
                 FROM repartidores r
                 JOIN zonas z ON z.id = r.zona_id
                 LEFT JOIN asignaciones a ON a.repartidor_id = r.id AND a.activa
                GROUP BY r.id, z.codigo, z.nombre
                ORDER BY r.id""").fetchall()
    for f in filas:
        f["cupos_libres"] = f["capacidad_max"] - f["carga_actual"]
        f["disponible"] = f["activo"] and f["cupos_libres"] > 0
    return filas
