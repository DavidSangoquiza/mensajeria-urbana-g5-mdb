"""Tokens de sesión firmados con HMAC y control de roles.

Token = base64(datos) + "." + firma. Si alguien cambia el rol dentro del
token, la firma deja de coincidir y la API lo rechaza.
"""
import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config
from app.errores import ErrorNegocio

_bearer = HTTPBearer(auto_error=False)


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip("=")


def _firmar(cuerpo: str) -> str:
    return _b64(hmac.new(config.TOKEN_SECRET.encode(), cuerpo.encode(), hashlib.sha256).digest())


def crear_token(u: dict) -> str:
    datos = {
        "uid": u["id"],
        "usuario": u["usuario"],
        "nombre": u["nombre"],
        "rol": u["rol"],
        "rid": u["repartidor_id"],          # id de repartidor (solo rol REPARTIDOR)
        "exp": int(time.time()) + config.TOKEN_HORAS * 3600,
    }
    cuerpo = _b64(json.dumps(datos).encode())
    return f"{cuerpo}.{_firmar(cuerpo)}"


def leer_token(token: str) -> dict | None:
    try:
        cuerpo, firma = token.split(".")
        if not hmac.compare_digest(firma, _firmar(cuerpo)):
            return None
        datos = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
        return datos if datos["exp"] > time.time() else None
    except Exception:
        return None


def usuario_actual(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if cred is None:
        raise ErrorNegocio(401, "NO_AUTENTICADO", "Inicie sesión para continuar")
    usuario = leer_token(cred.credentials)
    if usuario is None:
        raise ErrorNegocio(401, "TOKEN_INVALIDO", "Sesión inválida o vencida; inicie sesión de nuevo")
    return usuario


def usuario_opcional(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict | None:
    return leer_token(cred.credentials) if cred else None


def requiere_rol(*roles: str):
    """Uso: usuario = Depends(requiere_rol("OPERADOR", "ADMIN"))"""

    def verificar(usuario: dict = Depends(usuario_actual)) -> dict:
        if usuario["rol"] not in roles:
            raise ErrorNegocio(403, "SIN_PERMISO", f"El rol {usuario['rol']} no puede realizar esta operación",
                               {"roles_permitidos": list(roles)})
        return usuario

    return verificar