"""Administración: zonas, tipos de servicio, repartidores y capacidad operativa (PostgreSQL)."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.conexiones import pg_pool
from app.errores import ErrorNegocio
from app.seguridad import requiere_rol

router = APIRouter(prefix="/admin", tags=["Administración"])
ADMIN = requiere_rol("ADMIN")
Vehiculo = Literal["MOTO", "BICICLETA", "AUTO", "A_PIE"]


def reproyectar(cx, condicion: str, valor):
    """Un cambio de catálogo altera la vista de búsqueda: se encolan los envíos afectados."""
    cx.execute(f"INSERT INTO outbox (envio_codigo) SELECT codigo FROM envios WHERE {condicion}", (valor,))


# ---------------------------------------------------------------- zonas
class ZonaNueva(BaseModel):
    codigo: str = Field(min_length=2, max_length=10, pattern=r"^[A-Za-z]+$")
    nombre: str = Field(min_length=3, max_length=60)


class ZonaCambio(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=60)
    activa: bool | None = None


@router.get("/zonas")
def listar_zonas(usuario: dict = Depends(requiere_rol("ADMIN", "OPERADOR"))):
    with pg_pool.connection() as cx:
        return cx.execute("SELECT * FROM zonas ORDER BY id").fetchall()


@router.post("/zonas", status_code=201)
def crear_zona(datos: ZonaNueva, usuario: dict = Depends(ADMIN)):
    with pg_pool.connection() as cx:
        if cx.execute("SELECT 1 FROM zonas WHERE codigo = %s OR nombre = %s",
                      (datos.codigo.upper(), datos.nombre)).fetchone():
            raise ErrorNegocio(409, "ZONA_DUPLICADA", "Ya existe una zona con ese código o nombre")
        return cx.execute("INSERT INTO zonas (codigo, nombre) VALUES (%s, %s) RETURNING *",
                          (datos.codigo.upper(), datos.nombre)).fetchone()


@router.patch("/zonas/{zona_id}")
def cambiar_zona(zona_id: int, datos: ZonaCambio, usuario: dict = Depends(ADMIN)):
    with pg_pool.connection() as cx:
        with cx.transaction():
            z = cx.execute(
                """UPDATE zonas SET nombre = COALESCE(%s, nombre), activa = COALESCE(%s, activa)
                    WHERE id = %s RETURNING *""", (datos.nombre, datos.activa, zona_id)).fetchone()
            if z is None:
                raise ErrorNegocio(404, "ZONA_NO_ENCONTRADA", "No existe esa zona")
            if datos.nombre:                       # el nombre de zona aparece en la proyección de búsqueda
                cx.execute("""INSERT INTO outbox (envio_codigo) SELECT codigo FROM envios
                               WHERE zona_origen_id = %s OR zona_destino_id = %s""", (zona_id, zona_id))
        return z


# ---------------------------------------------------------------- tipos de servicio
class TipoNuevo(BaseModel):
    codigo: str = Field(min_length=3, max_length=15, pattern=r"^[A-Za-z_]+$")
    nombre: str = Field(min_length=3, max_length=60)
    horas_objetivo: int = Field(gt=0, le=720)


class TipoCambio(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=60)
    horas_objetivo: int | None = Field(default=None, gt=0, le=720)
    activo: bool | None = None


@router.get("/tipos-servicio")
def listar_tipos(usuario: dict = Depends(requiere_rol("ADMIN", "OPERADOR"))):
    with pg_pool.connection() as cx:
        return cx.execute("SELECT * FROM tipos_servicio ORDER BY id").fetchall()


@router.post("/tipos-servicio", status_code=201)
def crear_tipo(datos: TipoNuevo, usuario: dict = Depends(ADMIN)):
    with pg_pool.connection() as cx:
        if cx.execute("SELECT 1 FROM tipos_servicio WHERE codigo = %s", (datos.codigo.upper(),)).fetchone():
            raise ErrorNegocio(409, "TIPO_DUPLICADO", "Ya existe un tipo de servicio con ese código")
        return cx.execute("INSERT INTO tipos_servicio (codigo, nombre, horas_objetivo) VALUES (%s, %s, %s) RETURNING *",
                          (datos.codigo.upper(), datos.nombre, datos.horas_objetivo)).fetchone()


@router.patch("/tipos-servicio/{tipo_id}")
def cambiar_tipo(tipo_id: int, datos: TipoCambio, usuario: dict = Depends(ADMIN)):
    with pg_pool.connection() as cx:
        with cx.transaction():
            t = cx.execute(
                """UPDATE tipos_servicio SET nombre = COALESCE(%s, nombre),
                          horas_objetivo = COALESCE(%s, horas_objetivo), activo = COALESCE(%s, activo)
                    WHERE id = %s RETURNING *""",
                (datos.nombre, datos.horas_objetivo, datos.activo, tipo_id)).fetchone()
            if t is None:
                raise ErrorNegocio(404, "TIPO_NO_ENCONTRADO", "No existe ese tipo de servicio")
            if datos.nombre:
                reproyectar(cx, "tipo_servicio_id = %s", tipo_id)
        return t


# ---------------------------------------------------------------- repartidores y capacidad
class RepartidorNuevo(BaseModel):
    nombre: str = Field(min_length=3, max_length=80)
    zona: str = Field(max_length=10, examples=["NOR"])
    vehiculo: Vehiculo = "MOTO"
    capacidad_max: int = Field(ge=1, le=20)
    usuario: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9_]+$")
    clave: str = Field(min_length=8, max_length=100)


class RepartidorCambio(BaseModel):
    nombre: str | None = Field(default=None, min_length=3, max_length=80)
    zona: str | None = Field(default=None, max_length=10)
    vehiculo: Vehiculo | None = None
    capacidad_max: int | None = Field(default=None, ge=1, le=20)
    activo: bool | None = None


@router.post("/repartidores", status_code=201)
def crear_repartidor(datos: RepartidorNuevo, usuario: dict = Depends(ADMIN)):
    with pg_pool.connection() as cx:
        with cx.transaction():
            z = cx.execute("SELECT id FROM zonas WHERE codigo = %s AND activa", (datos.zona.upper(),)).fetchone()
            if not z:
                raise ErrorNegocio(422, "CATALOGO_INVALIDO", "Zona inexistente o inactiva")
            if cx.execute("SELECT 1 FROM usuarios WHERE usuario = %s", (datos.usuario,)).fetchone():
                raise ErrorNegocio(409, "USUARIO_DUPLICADO", "Ese nombre de usuario ya existe")
            rep = cx.execute(
                "INSERT INTO repartidores (nombre, zona_id, vehiculo, capacidad_max) VALUES (%s, %s, %s, %s) RETURNING *",
                (datos.nombre, z["id"], datos.vehiculo, datos.capacidad_max)).fetchone()
            cx.execute(
                """INSERT INTO usuarios (usuario, nombre, rol, clave_hash, repartidor_id)
                   VALUES (%s, %s, 'REPARTIDOR', crypt(%s, gen_salt('bf', 8)), %s)""",
                (datos.usuario, datos.nombre, datos.clave, rep["id"]))
        return {**rep, "usuario": datos.usuario}


@router.patch("/repartidores/{repartidor_id}")
def cambiar_repartidor(repartidor_id: int, datos: RepartidorCambio, usuario: dict = Depends(ADMIN)):
    with pg_pool.connection() as cx:
        with cx.transaction():
            rep = cx.execute("SELECT * FROM repartidores WHERE id = %s FOR UPDATE", (repartidor_id,)).fetchone()
            if rep is None:
                raise ErrorNegocio(404, "REPARTIDOR_NO_ENCONTRADO", "No existe ese repartidor")
            carga = cx.execute("SELECT count(*) AS n FROM asignaciones WHERE repartidor_id = %s AND activa",
                               (repartidor_id,)).fetchone()["n"]
            if datos.capacidad_max is not None and datos.capacidad_max < carga:
                raise ErrorNegocio(409, "CAPACIDAD_MENOR_A_CARGA",
                                   f"Tiene {carga} envíos activos; la capacidad no puede ser menor",
                                   {"carga_actual": carga})
            zona_id = rep["zona_id"]
            if datos.zona:
                z = cx.execute("SELECT id FROM zonas WHERE codigo = %s AND activa", (datos.zona.upper(),)).fetchone()
                if not z:
                    raise ErrorNegocio(422, "CATALOGO_INVALIDO", "Zona inexistente o inactiva")
                zona_id = z["id"]
            nuevo = cx.execute(
                """UPDATE repartidores SET nombre = COALESCE(%s, nombre), zona_id = %s,
                          vehiculo = COALESCE(%s, vehiculo), capacidad_max = COALESCE(%s, capacidad_max),
                          activo = COALESCE(%s, activo)
                    WHERE id = %s RETURNING *""",
                (datos.nombre, zona_id, datos.vehiculo, datos.capacidad_max, datos.activo, repartidor_id)).fetchone()
            if datos.nombre:
                cx.execute("UPDATE usuarios SET nombre = %s WHERE repartidor_id = %s", (datos.nombre, repartidor_id))
                cx.execute("""INSERT INTO outbox (envio_codigo)
                              SELECT e.codigo FROM envios e JOIN asignaciones a ON a.envio_id = e.id AND a.activa
                               WHERE a.repartidor_id = %s""", (repartidor_id,))
        return {**nuevo, "carga_actual": carga}


@router.get("/clientes")
def listar_clientes(usuario: dict = Depends(requiere_rol("ADMIN", "OPERADOR"))):
    with pg_pool.connection() as cx:
        return cx.execute("SELECT id, usuario, nombre, activo FROM usuarios WHERE rol = 'CLIENTE' ORDER BY id").fetchall()
