"""Operaciones comunes sobre envíos (siempre dentro de una transacción local de PostgreSQL)."""
import uuid

import psycopg
from psycopg.types.json import Jsonb

from app.conexiones import pg_pool
from app.errores import ErrorNegocio

# Vista completa de un envío con sus catálogos y la asignación activa
SQL_ENVIO = """
SELECT e.id, e.codigo, e.estado, e.version, e.cliente_id,
       e.remitente_nombre, e.destinatario_nombre, e.destinatario_telefono,
       e.origen_direccion,  e.origen_lat::float  AS origen_lat,  e.origen_lng::float  AS origen_lng,
       e.destino_direccion, e.destino_lat::float AS destino_lat, e.destino_lng::float AS destino_lng,
       e.peso_kg::float AS peso_kg, e.evidencia_id, e.creado_en, e.actualizado_en,
       ts.codigo AS tipo_servicio_codigo, ts.nombre AS tipo_servicio,
       zo.codigo AS zona_origen_codigo,   zo.nombre AS zona_origen,
       zd.codigo AS zona_destino_codigo,  zd.nombre AS zona_destino,
       a.id AS asignacion_id, a.repartidor_id, r.nombre AS repartidor
  FROM envios e
  JOIN tipos_servicio ts ON ts.id = e.tipo_servicio_id
  JOIN zonas zo ON zo.id = e.zona_origen_id
  JOIN zonas zd ON zd.id = e.zona_destino_id
  LEFT JOIN asignaciones a ON a.envio_id = e.id AND a.activa
  LEFT JOIN repartidores r ON r.id = a.repartidor_id
"""


def obtener_envio(cx, codigo: str) -> dict:
    envio = cx.execute(SQL_ENVIO + " WHERE e.codigo = %s", (codigo,)).fetchone()
    if envio is None:
        raise ErrorNegocio(404, "ENVIO_NO_ENCONTRADO", f"No existe el envío {codigo}")
    return envio


def bloquear_envio(cx, codigo: str) -> dict:
    """SELECT ... FOR UPDATE: otra transacción sobre el mismo envío espera hasta que esta termine."""
    envio = cx.execute("SELECT * FROM envios WHERE codigo = %s FOR UPDATE", (codigo,)).fetchone()
    if envio is None:
        raise ErrorNegocio(404, "ENVIO_NO_ENCONTRADO", f"No existe el envío {codigo}")
    return envio


def asignacion_activa(cx, envio_id: int) -> dict | None:
    return cx.execute("SELECT * FROM asignaciones WHERE envio_id = %s AND activa", (envio_id,)).fetchone()


def verificar_acceso(cx, envio: dict, usuario: dict, solo_lectura: bool = False):
    """Operador/admin ven todo; el cliente, lo suyo; el repartidor, lo que tiene asignado.
    Para solo lectura, el repartidor también ve los envíos que tuvo a cargo (ya entregados)."""
    rol = usuario["rol"]
    if rol in ("OPERADOR", "ADMIN"):
        return
    if rol == "CLIENTE" and envio["cliente_id"] == usuario["uid"]:
        return
    if rol == "REPARTIDOR":
        a = asignacion_activa(cx, envio["id"])
        if a and a["repartidor_id"] == usuario["rid"]:
            return
        if solo_lectura and cx.execute("SELECT 1 FROM asignaciones WHERE envio_id = %s AND repartidor_id = %s",
                                       (envio["id"], usuario["rid"])).fetchone():
            return
    raise ErrorNegocio(403, "SIN_ACCESO", "No tiene acceso a este envío")


def registrar_cambio(cx, envio: dict, nuevo: str, usuario: dict, tipo: str,
                     evento_id: uuid.UUID | None = None, lat=None, lng=None, detalle: dict | None = None):
    """En UNA transacción: estado vigente + evento en el historial + aviso en el outbox.
    Así el estado nunca contradice al historial."""
    cx.execute("UPDATE envios SET estado = %s, version = version + 1, actualizado_en = now() WHERE id = %s",
               (nuevo, envio["id"]))
    return registrar_evento(cx, envio, envio["estado"], nuevo, usuario, tipo, evento_id, lat, lng, detalle)


def registrar_evento(cx, envio: dict, anterior: str | None, nuevo: str, usuario: dict, tipo: str,
                     evento_id: uuid.UUID | None = None, lat=None, lng=None, detalle: dict | None = None):
    """Inserta el evento inmutable en el historial y su aviso en el outbox."""
    evento_id = evento_id or uuid.uuid4()
    cx.execute(
        """INSERT INTO historial_estados (evento_id, envio_id, tipo, estado_anterior, estado_nuevo,
                                          actor_id, actor_rol, lat, lng, detalle)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (evento_id, envio["id"], tipo, anterior, nuevo, usuario["uid"], usuario["rol"], lat, lng, Jsonb(detalle or {})),
    )
    cx.execute("INSERT INTO outbox (envio_codigo, evento_id) VALUES (%s, %s)", (envio["codigo"], evento_id))
    return evento_id


def liberar_asignacion(cx, envio_id: int, motivo: str) -> int | None:
    """Cierra la asignación activa; la capacidad del repartidor queda libre."""
    fila = cx.execute(
        """UPDATE asignaciones SET activa = FALSE, liberada_en = now(), motivo_liberacion = %s
            WHERE envio_id = %s AND activa RETURNING repartidor_id""",
        (motivo, envio_id),
    ).fetchone()
    return fila["repartidor_id"] if fila else None


def historial(cx, envio_id: int) -> list[dict]:
    return cx.execute(
        """SELECT h.id AS secuencia, h.evento_id, h.tipo, h.estado_anterior, h.estado_nuevo,
                  h.actor_rol, u.nombre AS actor, h.lat::float AS lat, h.lng::float AS lng,
                  h.detalle, h.ocurrido_en
             FROM historial_estados h
             LEFT JOIN usuarios u ON u.id = h.actor_id
            WHERE h.envio_id = %s
            ORDER BY h.id""",
        (envio_id,),
    ).fetchall()


def ejecutar_idempotente(evento_id: uuid.UUID, codigo: str, operacion) -> dict:
    """Ejecuta operacion(cx) en una transacción.
    Si el evento_id ya fue registrado (doble clic o reintento de red), no repite nada
    y devuelve el estado actual marcado como 'repetido'."""
    with pg_pool.connection() as cx:
        previo = cx.execute(
            """SELECT e.codigo FROM historial_estados h JOIN envios e ON e.id = h.envio_id
                WHERE h.evento_id = %s""", (evento_id,)).fetchone()
        if previo is None:
            try:
                with cx.transaction():
                    extra = operacion(cx) or {}
                return {"repetido": False, **extra, "envio": obtener_envio(cx, codigo)}
            except psycopg.errors.UniqueViolation as e:
                if e.diag.constraint_name != "historial_estados_evento_id_key":
                    raise
                previo = {"codigo": codigo}          # otra petición con el mismo evento_id ganó
        if previo["codigo"] != codigo:
            raise ErrorNegocio(409, "EVENTO_ID_REUTILIZADO", "Ese evento_id ya se usó en otro envío")
        return {"repetido": True, "mensaje": "Operación ya registrada; no se aplicó de nuevo",
                "envio": obtener_envio(cx, codigo)}
