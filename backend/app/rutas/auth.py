"""Inicio de sesión: la clave se verifica en PostgreSQL con bcrypt (pgcrypto)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.conexiones import pg_pool
from app.errores import ErrorNegocio
from app.seguridad import crear_token, usuario_actual

router = APIRouter(prefix="/auth", tags=["Autenticación"])


class LoginEntrada(BaseModel):
    usuario: str = Field(min_length=3, max_length=40, examples=["operador1"])
    clave: str = Field(min_length=1, max_length=100, examples=["Operador123!"])


@router.post("/login", summary="Iniciar sesión y obtener token")
def login(datos: LoginEntrada):
    with pg_pool.connection() as cx:
        u = cx.execute(
            """SELECT id, usuario, nombre, rol, repartidor_id
                 FROM usuarios
                WHERE usuario = %s AND activo
                  AND clave_hash = crypt(%s, clave_hash)""",
            (datos.usuario, datos.clave),
        ).fetchone()
    if u is None:
        raise ErrorNegocio(401, "CREDENCIALES_INVALIDAS", "Usuario o clave incorrectos")
    return {"token": crear_token(u), "usuario": u}


@router.get("/yo", summary="Datos del usuario de la sesión")
def yo(usuario: dict = Depends(usuario_actual)):
    return usuario