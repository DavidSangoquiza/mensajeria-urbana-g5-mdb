"""Proyección PostgreSQL -> MongoDB (patrón transactional outbox).

Flujo: escritura en la fuente oficial (PostgreSQL, misma transacción que el outbox)
       -> el sincronizador lee el outbox -> actualiza la proyección en MongoDB
       -> la consulta operativa lee la proyección.
La proyección siempre se reconstruye leyendo el estado ACTUAL de PostgreSQL,
así que reprocesar un aviso es seguro (idempotente).
"""
import unicodedata
from datetime import datetime, timezone

from app.servicios import SQL_ENVIO


def normalizar(texto: str) -> list[str]:
    """'Pedro Pérez' -> ['pedro', 'perez'] (minúsculas, sin tildes) para buscar por prefijo."""
    sin_tildes = unicodedata.normalize("NFD", texto or "").encode("ascii", "ignore").decode()
    return [p for p in sin_tildes.lower().split() if p]


def documento_proyeccion(e: dict) -> dict:
    return {
        "_id": e["codigo"],
        "codigo": e["codigo"],
        "estado": e["estado"],
        "version": int(e["version"]),
        "tipo_servicio": e["tipo_servicio"],
        "zona_origen": {"codigo": e["zona_origen_codigo"], "nombre": e["zona_origen"]},
        "zona_destino": {"codigo": e["zona_destino_codigo"], "nombre": e["zona_destino"]},
        "remitente_nombre": e["remitente_nombre"],
        "destinatario_nombre": e["destinatario_nombre"],
        "destino_direccion": e["destino_direccion"],
        "palabras": normalizar(e["destinatario_nombre"]),
        "repartidor": {"id": e["repartidor_id"], "nombre": e["repartidor"]} if e["repartidor_id"] else None,
        "evidencia_id": e["evidencia_id"],
        "creado_en": e["creado_en"],
        "actualizado_en": e["actualizado_en"],
        "proyectado_en": datetime.now(timezone.utc),
    }


def proyectar_envio(cx, mongo_db, codigo: str):
    """Lee el envío en PostgreSQL y reemplaza su documento en MongoDB."""
    e = cx.execute(SQL_ENVIO + " WHERE e.codigo = %s", (codigo,)).fetchone()
    if e is None:
        mongo_db["proyeccion_envios"].delete_one({"_id": codigo})
        return
    mongo_db["proyeccion_envios"].replace_one({"_id": codigo}, documento_proyeccion(e), upsert=True)
    # Reconciliación: si PostgreSQL ya dice ENTREGADO, la evidencia queda CONFIRMADA
    if e["estado"] == "ENTREGADO" and e["evidencia_id"]:
        mongo_db["evidencias_entrega"].update_one(
            {"_id": e["evidencia_id"], "estado": "PENDIENTE"},
            {"$set": {"estado": "CONFIRMADA", "confirmada_en": datetime.now(timezone.utc)}})


def procesar_lote(pg_pool, mongo_db, limite: int = 100) -> dict:
    """Procesa los avisos pendientes EN ORDEN. Si MongoDB falla, se detiene y lo reintenta luego."""
    procesados, error, ultimo_id = 0, None, None
    with pg_pool.connection() as cx:
        with cx.transaction():
            pendientes = cx.execute(
                """SELECT id, envio_codigo FROM outbox
                    WHERE procesado_en IS NULL
                    ORDER BY id LIMIT %s
                    FOR UPDATE SKIP LOCKED""", (limite,)).fetchall()
            ya = set()
            for aviso in pendientes:
                try:
                    if aviso["envio_codigo"] not in ya:          # varios avisos del mismo envío: una proyección
                        proyectar_envio(cx, mongo_db, aviso["envio_codigo"])
                        ya.add(aviso["envio_codigo"])
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"[:500]
                    cx.execute("UPDATE outbox SET intentos = intentos + 1, ultimo_error = %s WHERE id = %s",
                               (error, aviso["id"]))
                    break                                         # conserva el orden
                cx.execute("UPDATE outbox SET procesado_en = now() WHERE id = %s", (aviso["id"],))
                procesados += 1
                ultimo_id = aviso["id"]
            cx.execute(
                """UPDATE sincronizador_estado
                      SET ultimo_latido = now(),
                          ultimo_procesado_id = COALESCE(%s, ultimo_procesado_id),
                          ultimo_error = %s
                    WHERE id = 1""", (ultimo_id, error))
    return {"procesados": procesados, "error": error}
