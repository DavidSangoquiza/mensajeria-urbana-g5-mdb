"""Conexiones a los tres gestores.

Tiempos de espera cortos: si un gestor cae, la API lo detecta en 1-2 s
y responde 503 en vez de quedarse colgada.
"""
import redis
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pymongo import MongoClient

from app import config

# PostgreSQL: pool de conexiones; cada fila llega como diccionario
pg_pool = ConnectionPool(
    conninfo=config.DATABASE_URL,
    min_size=1,
    max_size=10,
    timeout=5,
    kwargs={"row_factory": dict_row},
    open=False,
)

# MongoDB: el cliente se conecta de forma perezosa (al primer uso)
mongo_cliente = MongoClient(
    config.MONGO_URL,
    serverSelectionTimeoutMS=2000,
    connectTimeoutMS=2000,
    tz_aware=True,
)
mongo_db = mongo_cliente[config.MONGO_DB]

# Redis: estado temporal
redis_cli = redis.Redis.from_url(
    config.REDIS_URL,
    socket_timeout=1,
    socket_connect_timeout=1,
    decode_responses=True,
)


def abrir():
    pg_pool.open(wait=False)


def cerrar():
    pg_pool.close()
    mongo_cliente.close()
    redis_cli.close()