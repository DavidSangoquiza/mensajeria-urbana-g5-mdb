"""API REST · Mensajería urbana (monolito modular).

Único punto de entrada a los datos: el navegador nunca se conecta
directamente a PostgreSQL, MongoDB ni Redis.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import conexiones
from app.errores import registrar_manejadores
from app.rutas import auth, envios, sistema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    conexiones.abrir()
    yield
    conexiones.cerrar()


app = FastAPI(
    title="API Mensajería Urbana · Grupo 5",
    description="Persistencia políglota: PostgreSQL (oficial), MongoDB (evidencias y búsqueda), Redis (estado temporal).",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=ciclo_de_vida,
)
registrar_manejadores(app)

# /health también fuera de /api, como pide el enunciado
app.add_api_route("/health", sistema.health, methods=["GET"], include_in_schema=False)

app.include_router(sistema.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(envios.router, prefix="/api")
