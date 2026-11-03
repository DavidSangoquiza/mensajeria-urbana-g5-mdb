"""Salud del sistema: estado de la API y de cada gestor, sin exponer secretos."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.conexiones import mongo_cliente, pg_pool, redis_cli

router = APIRouter(tags=["Sistema"])


@router.get("/health", summary="Salud de la API y sus dependencias")
def health():
    deps = {}
    try:
        with pg_pool.connection(timeout=2) as cx:
            cx.execute("SELECT 1")
        deps["postgres"] = "ok"
    except Exception:
        deps["postgres"] = "caido"
    try:
        mongo_cliente.admin.command("ping")
        deps["mongo"] = "ok"
    except Exception:
        deps["mongo"] = "caido"
    try:
        redis_cli.ping()
        deps["redis"] = "ok"
    except Exception:
        deps["redis"] = "caido"

    if deps["postgres"] == "caido":
        estado = "caido"            # sin la fuente oficial no se puede operar
    elif "caido" in deps.values():
        estado = "degradado"        # opera, con funciones limitadas
    else:
        estado = "ok"
    return JSONResponse(status_code=503 if estado == "caido" else 200,
                        content={"api": "ok", "estado": estado, "dependencias": deps})