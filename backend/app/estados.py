"""Máquina de estados del envío.

TRANSICIONES[desde][hacia] = roles autorizados.
Cualquier par que no esté aquí se rechaza con 409 TRANSICION_INVALIDA.
"""
from app.errores import ErrorNegocio

OPERACION = {"OPERADOR", "ADMIN"}

TRANSICIONES = {
    "CREADO": {
        "ASIGNADO": OPERACION,
        "CANCELADO": {"CLIENTE"} | OPERACION,
    },
    "ASIGNADO": {
        "ASIGNADO": OPERACION,                  # reasignar a otro repartidor
        "CREADO": OPERACION,                    # liberar: vuelve a pendientes
        "RETIRADO": {"REPARTIDOR"},
        "INCIDENCIA": {"REPARTIDOR"} | OPERACION,
        "CANCELADO": OPERACION,
    },
    "RETIRADO": {
        "EN_TRANSITO": {"REPARTIDOR"},
        "INCIDENCIA": {"REPARTIDOR"} | OPERACION,
    },
    "EN_TRANSITO": {
        "ENTREGADO": {"REPARTIDOR"},
        "INCIDENCIA": {"REPARTIDOR"} | OPERACION,
    },
    "INCIDENCIA": {
        "EN_TRANSITO": {"REPARTIDOR"} | OPERACION,   # incidencia resuelta
        "ASIGNADO": OPERACION,                       # reasignar
        "CREADO": OPERACION,                         # liberar
        "CANCELADO": OPERACION,
    },
    "ENTREGADO": {},    # final
    "CANCELADO": {},    # final
}

# Transiciones válidas que exigen datos extra y tienen su propio endpoint
ENDPOINT_DEDICADO = {
    "ASIGNADO": "POST /api/asignaciones",
    "CREADO": "POST /api/asignaciones/{codigo}/liberar",
    "ENTREGADO": "POST /api/envios/{codigo}/entrega",
}


def validar_transicion(actual: str, nuevo: str, rol: str):
    permitidas = TRANSICIONES.get(actual, {})
    if nuevo not in permitidas:
        raise ErrorNegocio(409, "TRANSICION_INVALIDA", f"Transición inválida: {actual} → {nuevo}",
                           {"permitidas_desde_estado_actual": sorted(permitidas)})
    if rol not in permitidas[nuevo]:
        raise ErrorNegocio(403, "SIN_PERMISO", f"El rol {rol} no puede pasar un envío de {actual} a {nuevo}",
                           {"roles_permitidos": sorted(permitidas[nuevo])})
