"""Estado temporal en Redis: última posición simulada y conexión del repartidor.

Clave  repartidor:{id}:estado  (HASH)  con EXPIRE = UBICACION_TTL_SEG.
Si el repartidor deja de reportar, la clave caduca sola: el dato deja de ser vigente.
Redis corre sin persistencia: al reiniciarlo se pierde (es efímero por diseño).
"""
from datetime import datetime, timezone

from app import config
from app.conexiones import redis_cli


def clave(repartidor_id: int) -> str:
    return f"repartidor:{repartidor_id}:estado"


def guardar_posicion(repartidor_id: int, lat: float, lng: float) -> int:
    k = clave(repartidor_id)
    with redis_cli.pipeline() as p:                       # HSET + EXPIRE juntos
        p.hset(k, mapping={"lat": lat, "lng": lng, "conectado": 1,
                           "actualizado_en": datetime.now(timezone.utc).isoformat()})
        p.expire(k, config.UBICACION_TTL_SEG)
        p.execute()
    return config.UBICACION_TTL_SEG


def leer_posicion(repartidor_id: int) -> dict:
    k = clave(repartidor_id)
    datos = redis_cli.hgetall(k)
    if not datos:
        return {"vigente": False, "mensaje": "Sin posición vigente (caducó o nunca se reportó)"}
    return {"vigente": True, "lat": float(datos["lat"]), "lng": float(datos["lng"]),
            "actualizado_en": datos["actualizado_en"], "expira_en_seg": redis_cli.ttl(k)}


def marcar_no_vigente(repartidor_id: int) -> bool:
    return redis_cli.delete(clave(repartidor_id)) == 1


def leer_posicion_segura(repartidor_id: int) -> dict:
    """Para pantallas: si Redis cae, se informa y la operación principal continúa."""
    try:
        return leer_posicion(repartidor_id)
    except Exception:
        return {"vigente": False, "mensaje": "Redis no disponible: posición desconocida"}
