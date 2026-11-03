"""Configuración leída de variables de entorno (nunca claves en el código)."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mensajeria:cambiar_clave_pg@localhost:5432/mensajeria")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "mensajeria")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

TOKEN_SECRET = os.getenv("TOKEN_SECRET", "cambiar_este_secreto")
TOKEN_HORAS = int(os.getenv("TOKEN_HORAS", "8"))

UBICACION_TTL_SEG = int(os.getenv("UBICACION_TTL_SEG", "120"))
SYNC_INTERVALO_SEG = float(os.getenv("SYNC_INTERVALO_SEG", "2"))