"""Proceso sincronizador (contenedor mu-sincronizador).

Corre aparte de la API: si se detiene, la API sigue operando y los avisos
se acumulan en el outbox; al volver, los procesa en orden.
Ejecutar con:  python -m app.sincronizador
"""
import logging
import signal
import time

from app import config
from app.conexiones import mongo_db, pg_pool
from app.sincronizacion import procesar_lote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s sincronizador: %(message)s")
log = logging.getLogger("sincronizador")
activo = True


def detener(*_):
    global activo
    activo = False


def main():
    signal.signal(signal.SIGTERM, detener)
    signal.signal(signal.SIGINT, detener)
    pg_pool.open(wait=False)
    espera = config.SYNC_INTERVALO_SEG
    log.info("Iniciado; intervalo %.1f s", espera)
    while activo:
        try:
            r = procesar_lote(pg_pool, mongo_db)
            if r["procesados"]:
                log.info("Proyectados %d avisos", r["procesados"])
            if r["error"]:
                espera = min(espera * 2, 30)                 # espera exponencial hasta 30 s
                log.warning("MongoDB falló (%s); reintento en %.0f s", r["error"], espera)
            else:
                espera = config.SYNC_INTERVALO_SEG
        except Exception as e:                               # PostgreSQL caído u otro fallo
            espera = min(espera * 2, 30)
            log.warning("Ciclo fallido (%s); reintento en %.0f s", e, espera)
        time.sleep(espera)
    pg_pool.close()
    log.info("Detenido")


if __name__ == "__main__":
    main()
