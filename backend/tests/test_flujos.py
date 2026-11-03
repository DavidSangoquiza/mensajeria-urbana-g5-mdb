"""Pruebas automatizadas de los flujos y reglas del enunciado (Proyecto B).

Se ejecutan contra la API en marcha y crean sus propios datos, así que se pueden repetir:
    docker compose exec api pytest -v tests
Variable opcional: API_URL (por defecto http://localhost:8000/api, la propia API dentro del contenedor).
"""
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

API = os.getenv("API_URL", "http://localhost:8000/api")
c = httpx.Client(base_url=API, timeout=20)


def token(usuario, clave):
    r = c.post("/auth/login", json={"usuario": usuario, "clave": clave})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


CREADOS = []          # repartidores de prueba: se desactivan al terminar para no ensuciar el panel


@pytest.fixture(scope="session")
def h():
    cab = {"cliente": token("cliente1", "Cliente123!"), "operador": token("operador1", "Operador123!"),
           "admin": token("admin", "Admin123!")}
    yield cab
    for rid in CREADOS:
        c.patch(f"/admin/repartidores/{rid}", headers=cab["admin"], json={"activo": False})


def envio_nuevo(cab, destinatario="Destino Prueba Pytest", clave=None):
    cuerpo = {"tipo_servicio": "ESTANDAR", "zona_origen": "NOR", "zona_destino": "SUR",
              "origen_direccion": "Av. Prueba 100", "origen_lat": -0.15, "origen_lng": -78.48,
              "destino_direccion": "Calle Prueba 200", "destino_lat": -0.29, "destino_lng": -78.54,
              "remitente_nombre": "Remitente Pytest", "destinatario_nombre": destinatario, "peso_kg": 1.0}
    return c.post("/envios", json=cuerpo, headers={**cab, "Idempotency-Key": clave or str(uuid.uuid4())})


def repartidor_nuevo(h, capacidad):
    """Crea un repartidor exclusivo para la prueba (no depende de los datos semilla)."""
    sufijo = uuid.uuid4().hex[:8]
    r = c.post("/admin/repartidores", headers=h["admin"],
               json={"nombre": f"Repartidor Pytest {sufijo}", "zona": "NOR", "vehiculo": "MOTO",
                     "capacidad_max": capacidad, "usuario": f"rep_{sufijo}", "clave": "Pytest1234!"})
    assert r.status_code == 201, r.text
    CREADOS.append(r.json()["id"])
    return r.json()["id"], token(f"rep_{sufijo}", "Pytest1234!")


def estado(codigo, nuevo, cab, **extra):
    return c.patch(f"/envios/{codigo}/estado", headers=cab,
                   json={"estado_nuevo": nuevo, "evento_id": str(uuid.uuid4()), **extra})


# ---------------------------------------------------------------- pruebas
def test_01_salud():
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["dependencias"]["postgres"] == "ok"


def test_02_codigo_unico_e_idempotencia(h):
    """Prueba 1 del enunciado: código único y consulta posterior; un reintento no duplica."""
    clave = str(uuid.uuid4())
    r1 = envio_nuevo(h["cliente"], clave=clave)
    assert r1.status_code == 201, r1.text
    codigo = r1.json()["envio"]["codigo"]
    assert codigo.startswith("MU-") and r1.json()["envio"]["estado"] == "CREADO"

    r2 = envio_nuevo(h["cliente"], clave=clave)                      # mismo Idempotency-Key
    assert r2.status_code == 200 and r2.json()["repetido"] is True
    assert r2.json()["envio"]["codigo"] == codigo

    r3 = envio_nuevo(h["cliente"])                                    # otra clave: otro código
    assert r3.json()["envio"]["codigo"] != codigo

    s = c.get(f"/seguimiento/{codigo}")                               # consulta pública posterior
    assert s.status_code == 200 and s.json()["estado"] == "CREADO"


def test_03_validaciones(h):
    r = c.post("/envios", headers={**h["cliente"], "Idempotency-Key": str(uuid.uuid4())}, json={"peso_kg": 99})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "DATOS_INVALIDOS"
    assert c.get("/envios/mios").status_code == 401                  # sin sesión
    assert c.get("/despacho/repartidores", headers=h["cliente"]).status_code == 403   # rol sin permiso


def test_04_transicion_invalida(h):
    """Prueba 2 del enunciado: rechazar creado -> entregado sin pasos autorizados."""
    codigo = envio_nuevo(h["cliente"]).json()["envio"]["codigo"]
    r = estado(codigo, "ENTREGADO", h["operador"])
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "TRANSICION_INVALIDA"
    r = estado(codigo, "EN_TRANSITO", h["operador"])
    assert r.status_code == 409


def test_05_secuencia_valida_entrega_y_capacidad_liberada(h):
    """Flujos 2, 3 y 4: asignar, retirar, tránsito, incidencia, entregar con evidencia."""
    rep_id, rep = repartidor_nuevo(h, capacidad=1)
    codigo = envio_nuevo(h["cliente"], destinatario="Elena Pytest Entrega").json()["envio"]["codigo"]

    r = c.post("/asignaciones", headers=h["operador"],
               json={"codigo": codigo, "repartidor_id": rep_id, "evento_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    assert c.post(f"/repartidor/tareas/{codigo}/aceptar", headers=rep,
                  json={"evento_id": str(uuid.uuid4())}).status_code == 200
    assert estado(codigo, "RETIRADO", rep).status_code == 200
    assert estado(codigo, "EN_TRANSITO", rep).status_code == 200
    assert estado(codigo, "INCIDENCIA", rep).status_code == 422       # falta la observación
    assert estado(codigo, "INCIDENCIA", rep, observacion="Portón cerrado").status_code == 200
    assert estado(codigo, "EN_TRANSITO", rep).status_code == 200

    entrega = {"evento_id": str(uuid.uuid4()), "receptor": {"nombre": "Elena Pytest", "relacion": "Titular"},
               "foto": {"tipo": "referencia_archivo", "valor": "foto_pytest.jpg"}, "extras": {"firma_simulada": True}}
    r = c.post(f"/envios/{codigo}/entrega", headers=rep, json=entrega)
    assert r.status_code == 201, r.text
    assert r.json()["envio"]["estado"] == "ENTREGADO"
    assert r.json()["evidencia"]["estado"] == "CONFIRMADA"

    r = c.post(f"/envios/{codigo}/entrega", headers=rep, json=entrega)   # reintento: no duplica
    assert r.status_code == 200 and r.json()["repetido"] is True

    d = c.get(f"/envios/{codigo}", headers=h["operador"]).json()
    estados = [x["estado_nuevo"] for x in d["historial"]]
    assert estados == ["CREADO", "ASIGNADO", "ASIGNADO", "RETIRADO", "EN_TRANSITO", "INCIDENCIA", "EN_TRANSITO", "ENTREGADO"]
    assert d["envio"]["estado"] == estados[-1]                         # estado vigente = último evento

    reps = {x["id"]: x for x in c.get("/despacho/repartidores", headers=h["operador"]).json()}
    assert reps[rep_id]["carga_actual"] == 0                           # la entrega liberó la capacidad


def test_06_capacidad_concurrente(h):
    """Prueba 3 del enunciado: dos asignaciones SIMULTÁNEAS al mismo repartidor con 1 cupo."""
    rep_id, _ = repartidor_nuevo(h, capacidad=1)
    codigos = [envio_nuevo(h["cliente"]).json()["envio"]["codigo"] for _ in range(2)]

    def asignar(codigo):
        return c.post("/asignaciones", headers=h["operador"],
                      json={"codigo": codigo, "repartidor_id": rep_id, "evento_id": str(uuid.uuid4())})

    with ThreadPoolExecutor(max_workers=2) as ex:
        respuestas = list(ex.map(asignar, codigos))
    estados_http = sorted(r.status_code for r in respuestas)
    assert estados_http == [201, 409], [r.text for r in respuestas]
    rechazada = next(r for r in respuestas if r.status_code == 409)
    assert rechazada.json()["error"]["codigo"] == "CAPACIDAD_EXCEDIDA"


def test_07_cambio_de_estado_idempotente(h):
    rep_id, rep = repartidor_nuevo(h, capacidad=2)
    codigo = envio_nuevo(h["cliente"]).json()["envio"]["codigo"]
    c.post("/asignaciones", headers=h["operador"], json={"codigo": codigo, "repartidor_id": rep_id, "evento_id": str(uuid.uuid4())})
    evento = str(uuid.uuid4())
    r1 = c.patch(f"/envios/{codigo}/estado", headers=rep, json={"estado_nuevo": "RETIRADO", "evento_id": evento})
    r2 = c.patch(f"/envios/{codigo}/estado", headers=rep, json={"estado_nuevo": "RETIRADO", "evento_id": evento})
    assert r1.json()["repetido"] is False and r2.json()["repetido"] is True
    d = c.get(f"/envios/{codigo}", headers=h["operador"]).json()
    assert [x["estado_nuevo"] for x in d["historial"]].count("RETIRADO") == 1


def test_08_sincronizacion_de_la_busqueda(h):
    """Flujo 5: el cambio en PostgreSQL llega a la proyección de MongoDB (consistencia eventual)."""
    codigo = envio_nuevo(h["cliente"], destinatario="Nombre Original Pytest").json()["envio"]["codigo"]
    nuevo = f"Corregido Pytest {uuid.uuid4().hex[:5]}"
    r = c.patch(f"/envios/{codigo}/destinatario", headers=h["operador"],
                json={"evento_id": str(uuid.uuid4()), "destinatario_nombre": nuevo})
    assert r.status_code == 200
    inicio = time.time()
    while time.time() - inicio < 20:
        res = c.get(f"/envios?codigo={codigo}&fuente=mongodb", headers=h["operador"]).json()["resultados"]
        if res and res[0]["destinatario_nombre"] == nuevo:
            break
        time.sleep(0.5)
    else:
        pytest.fail("La proyección no se actualizó en 20 s (¿está corriendo el sincronizador?)")
    print(f"\nRetraso de sincronización medido: {time.time() - inicio:.2f} s")


def test_09_reconciliacion(h):
    r = c.get("/admin/reconciliacion", headers=h["operador"]).json()
    assert r["descuadres_estado_historial"] == [] and r["resultado"] == "OK"
