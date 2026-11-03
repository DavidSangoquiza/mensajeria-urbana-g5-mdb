"""Consulta operativa (proyección en MongoDB), corrección de destino y estado técnico del sincronizador."""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.conexiones import mongo_db, pg_pool
from app.errores import ErrorNegocio
from app.seguridad import requiere_rol
from app.servicios import SQL_ENVIO, bloquear_envio, ejecutar_idempotente, registrar_cambio
from app.sincronizacion import normalizar

router = APIRouter(tags=["Consulta operativa y sincronización"])
OPERACION = requiere_rol("OPERADOR", "ADMIN")
ESTADOS = Literal["CREADO", "ASIGNADO", "RETIRADO", "EN_TRANSITO", "INCIDENCIA", "ENTREGADO", "CANCELADO"]


# ---------------------------------------------------------------- búsqueda
def buscar_mongo(codigo, estado, zona, destinatario, desde, hasta, limite):
    filtro = {}
    if codigo:
        filtro["_id"] = {"$regex": "^" + re.escape(codigo.upper())}
    if estado:
        filtro["estado"] = estado
    if zona:
        filtro["zona_destino.codigo"] = zona.upper()
    if destinatario:                                   # cada palabra debe empezar igual (índice multiclave)
        filtro["$and"] = [{"palabras": {"$regex": "^" + re.escape(p)}} for p in normalizar(destinatario)]
    if desde or hasta:
        rango = {}
        if desde:
            rango["$gte"] = datetime.combine(desde, datetime.min.time(), timezone.utc)
        if hasta:
            rango["$lt"] = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), timezone.utc)
        filtro["creado_en"] = rango
    return list(mongo_db["proyeccion_envios"].find(filtro).sort("creado_en", -1).limit(limite))


def buscar_postgres(codigo, estado, zona, destinatario, desde, hasta, limite):
    sql = SQL_ENVIO + """
     WHERE (%(codigo)s::text IS NULL OR e.codigo ILIKE %(codigo)s || '%%')
       AND (%(estado)s::text IS NULL OR e.estado = %(estado)s)
       AND (%(zona)s::text IS NULL OR zd.codigo = upper(%(zona)s))
       AND (%(dest)s::text IS NULL OR e.destinatario_nombre ILIKE '%%' || %(dest)s || '%%')
       AND (%(desde)s::date IS NULL OR e.creado_en >= %(desde)s::date)
       AND (%(hasta)s::date IS NULL OR e.creado_en < %(hasta)s::date + 1)
     ORDER BY e.creado_en DESC LIMIT %(limite)s"""
    with pg_pool.connection() as cx:
        return cx.execute(sql, {"codigo": codigo, "estado": estado, "zona": zona, "dest": destinatario,
                                "desde": desde, "hasta": hasta, "limite": limite}).fetchall()


@router.get("/envios", summary="Buscar envíos por código, estado, zona, destinatario y fechas")
def buscar(codigo: str | None = Query(None, max_length=20),
           estado: ESTADOS | None = None,
           zona: str | None = Query(None, max_length=10, description="Código de zona de destino"),
           destinatario: str | None = Query(None, max_length=80),
           desde: date | None = None, hasta: date | None = None,
           fuente: Literal["auto", "mongodb", "postgresql"] = "auto",
           limite: int = Query(50, ge=1, le=200),
           usuario: dict = Depends(OPERACION)):
    args = (codigo, estado, zona, destinatario, desde, hasta, limite)
    if fuente == "postgresql":
        return {"fuente": "postgresql (fuente oficial)", "resultados": buscar_postgres(*args)}
    try:
        return {"fuente": "mongodb (proyeccion_envios, consistencia eventual)", "resultados": buscar_mongo(*args)}
    except Exception:
        if fuente == "mongodb":
            raise ErrorNegocio(503, "BUSQUEDA_NO_DISPONIBLE", "La proyección de búsqueda (MongoDB) no responde")
        # La búsqueda es opcional: si cae, se consulta la fuente oficial
        return {"fuente": "postgresql (respaldo: MongoDB no disponible)", "resultados": buscar_postgres(*args)}


# ---------------------------------------------------------------- corregir destino (flujo obligatorio 5)
class CorreccionDestino(BaseModel):
    evento_id: uuid.UUID
    destinatario_nombre: str = Field(min_length=3, max_length=80)
    destinatario_telefono: str | None = Field(default=None, pattern=r"^0\d{9}$")
    destino_direccion: str | None = Field(default=None, min_length=5, max_length=160)
    zona_destino: str | None = Field(default=None, max_length=10)


@router.patch("/envios/{codigo}/destinatario", summary="Corregir datos de destino (se propaga a la búsqueda)")
def corregir_destino(codigo: str, datos: CorreccionDestino, usuario: dict = Depends(OPERACION)):
    codigo = codigo.upper()

    def operacion(cx):
        envio = bloquear_envio(cx, codigo)
        if envio["estado"] in ("ENTREGADO", "CANCELADO"):
            raise ErrorNegocio(409, "ENVIO_FINALIZADO", f"No se corrige un envío {envio['estado']}")
        zona_id = envio["zona_destino_id"]
        if datos.zona_destino:
            z = cx.execute("SELECT id FROM zonas WHERE codigo = %s AND activa", (datos.zona_destino.upper(),)).fetchone()
            if not z:
                raise ErrorNegocio(422, "CATALOGO_INVALIDO", "Zona de destino inexistente o inactiva")
            zona_id = z["id"]
        antes = {"destinatario_nombre": envio["destinatario_nombre"], "destino_direccion": envio["destino_direccion"]}
        cx.execute(
            """UPDATE envios SET destinatario_nombre = %s,
                                 destinatario_telefono = COALESCE(%s, destinatario_telefono),
                                 destino_direccion = COALESCE(%s, destino_direccion),
                                 zona_destino_id = %s
                WHERE id = %s""",
            (datos.destinatario_nombre, datos.destinatario_telefono, datos.destino_direccion, zona_id, envio["id"]))
        # El estado no cambia; queda el evento en el historial y el aviso en el outbox
        registrar_cambio(cx, envio, envio["estado"], usuario, "DATOS_ACTUALIZADOS", datos.evento_id,
                         detalle={"antes": antes, "despues": datos.model_dump(exclude={"evento_id"}, exclude_none=True)})

    return ejecutar_idempotente(datos.evento_id, codigo, operacion)


# ---------------------------------------------------------------- estado técnico
@router.get("/admin/sync/estado", summary="Pendientes, retraso, errores y latido del sincronizador")
def estado_sync(usuario: dict = Depends(OPERACION)):
    with pg_pool.connection() as cx:
        o = cx.execute(
            """SELECT count(*) FILTER (WHERE procesado_en IS NULL)                       AS pendientes,
                      count(*) FILTER (WHERE procesado_en IS NULL AND intentos > 0)      AS con_error,
                      extract(epoch FROM now() - min(creado_en) FILTER (WHERE procesado_en IS NULL))::int
                                                                                         AS retraso_seg,
                      round(avg(extract(epoch FROM procesado_en - creado_en))
                            FILTER (WHERE procesado_en > now() - interval '10 minutes')::numeric, 2)::float
                                                                                         AS retraso_promedio_seg
                 FROM outbox""").fetchone()
        s = cx.execute("""SELECT ultimo_latido, ultimo_procesado_id, ultimo_error,
                                 extract(epoch FROM now() - ultimo_latido)::int AS seg_desde_latido
                            FROM sincronizador_estado WHERE id = 1""").fetchone()
        total = cx.execute("SELECT count(*) AS n FROM envios").fetchone()["n"]
    try:
        proyectados = mongo_db["proyeccion_envios"].count_documents({})
    except Exception:
        proyectados = None
    activo = s["seg_desde_latido"] is not None and s["seg_desde_latido"] < 15
    return {"sincronizador": "activo" if activo else "detenido",
            "ultimo_latido": s["ultimo_latido"], "ultimo_error": s["ultimo_error"],
            "outbox": o, "envios_postgresql": total, "documentos_mongodb": proyectados}


@router.post("/admin/sync/reconstruir", summary="Regenerar toda la proyección desde PostgreSQL")
def reconstruir(usuario: dict = Depends(requiere_rol("ADMIN"))):
    with pg_pool.connection() as cx:
        n = cx.execute("INSERT INTO outbox (envio_codigo) SELECT codigo FROM envios").rowcount
    return {"mensaje": f"{n} envíos encolados; el sincronizador los proyectará en segundos"}


@router.get("/admin/reconciliacion", summary="Verificar que estado vigente e historial no se contradicen")
def reconciliacion(usuario: dict = Depends(OPERACION)):
    with pg_pool.connection() as cx:
        descuadres = cx.execute(
            """SELECT e.codigo, e.estado, ult.estado_nuevo AS ultimo_evento
                 FROM envios e
                 JOIN LATERAL (SELECT estado_nuevo FROM historial_estados h
                                WHERE h.envio_id = e.id ORDER BY h.id DESC LIMIT 1) ult ON TRUE
                WHERE e.estado <> ult.estado_nuevo""").fetchall()
    try:
        pendientes = [d["_id"] for d in mongo_db["evidencias_entrega"].find({"estado": "PENDIENTE"}, {"_id": 1})]
    except Exception:
        pendientes = None
    return {"descuadres_estado_historial": descuadres, "evidencias_pendientes": pendientes,
            "resultado": "OK" if not descuadres else "HAY DESCUADRES"}
