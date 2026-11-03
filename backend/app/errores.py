"""Errores con formato uniforme: {"error": {"codigo", "mensaje", "detalle"}}."""
import logging

import psycopg
import psycopg_pool
import pymongo.errors
import redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("api")


class ErrorNegocio(Exception):
    """Error esperado de una regla de negocio (409, 403, 404...)."""

    def __init__(self, status: int, codigo: str, mensaje: str, detalle: dict | None = None):
        super().__init__(mensaje)
        self.status = status
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalle = detalle or {}


def respuesta(status: int, codigo: str, mensaje: str, detalle: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"codigo": codigo, "mensaje": mensaje, "detalle": detalle or {}}})


def registrar_manejadores(app: FastAPI):
    @app.exception_handler(ErrorNegocio)
    async def _negocio(_: Request, e: ErrorNegocio):
        return respuesta(e.status, e.codigo, e.mensaje, e.detalle)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, e: RequestValidationError):
        campos = [{"campo": ".".join(str(p) for p in err["loc"][1:]), "problema": err["msg"]} for err in e.errors()]
        return respuesta(422, "DATOS_INVALIDOS", "Revise los datos enviados", {"campos": campos})

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(psycopg_pool.PoolTimeout)
    async def _postgres(_: Request, e: Exception):
        log.error("PostgreSQL no disponible: %s", e)
        return respuesta(503, "POSTGRES_NO_DISPONIBLE", "La base de datos principal no responde. Reintente en unos segundos.")

    @app.exception_handler(pymongo.errors.PyMongoError)
    async def _mongo(_: Request, e: Exception):
        log.error("MongoDB no disponible: %s", e)
        return respuesta(503, "MONGO_NO_DISPONIBLE", "MongoDB no responde. Reintente en unos segundos.")

    @app.exception_handler(redis.RedisError)
    async def _redis(_: Request, e: Exception):
        log.error("Redis no disponible: %s", e)
        return respuesta(503, "REDIS_NO_DISPONIBLE", "El estado temporal (Redis) no responde.")

    @app.exception_handler(Exception)
    async def _general(_: Request, e: Exception):
        log.exception("Error no controlado")
        return respuesta(500, "ERROR_INTERNO", "Error interno del servidor.")