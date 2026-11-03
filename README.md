# Mensajería Urbana · Persistencia políglota

Proyecto final I Parcial · Proyecto B · **Grupo 5** — Mirley Ñacato, Michael Tonsto, David Sangoquiza.

Sistema web para una empresa ficticia de entregas urbanas: registro de envíos con código único,
despacho con control de capacidad, seguimiento con línea de tiempo y evidencias de entrega.
Todos los datos (personas, teléfonos, coordenadas, fotos) son **ficticios**.

## Arquitectura

```
Navegador → web (nginx :8080) → /api → API FastAPI (monolito modular) → PostgreSQL · MongoDB · Redis
                                                     sincronizador ↗ (outbox de PostgreSQL → MongoDB)
```

| Gestor | Papel | Datos |
|---|---|---|
| **PostgreSQL 16** | Fuente oficial (consistencia fuerte) | Envíos, asignaciones y capacidad, historial de estados inmutable, outbox |
| **MongoDB 7** | Datos heterogéneos y vista derivada | `evidencias_entrega` (oficial) · `proyeccion_envios` (búsqueda, reconstruible) |
| **Redis 7** | Estado efímero | Posición simulada del repartidor con TTL; sin persistencia a propósito |

| Contenedor | Función | Puerto en el equipo |
|---|---|---|
| `mu-web` | nginx: interfaz web y proxy de `/api` | **8080** |
| `mu-api` | API REST FastAPI | 127.0.0.1:8000 (solo para Swagger/Postman) |
| `mu-sincronizador` | Proyecta el outbox en MongoDB cada 2 s | — |
| `mu-postgres`, `mu-mongo`, `mu-redis` | Bases de datos (solo red interna `red_interna`) | — |

## Requisitos previos

- **Docker Desktop** con WSL 2 (Windows) o Docker Engine + Compose v2. Recomendado: 8 GB de RAM.
- Nada más: Python y las bases de datos corren dentro de los contenedores.

## Ejecución limpia

```powershell
git clone <url-del-repositorio> mensajeria-urbana
cd mensajeria-urbana
Copy-Item .env.example .env          # macOS/Linux: cp .env.example .env
docker compose up --build -d
docker compose ps                    # 6 contenedores; las bases en (healthy)
```

La primera vez descarga las imágenes (unos 2 min). Los scripts de `db/postgres/init` y `db/mongo/init`
crean el esquema y los datos semilla **solo cuando los volúmenes están vacíos**.

## URLs

| Qué | URL |
|---|---|
| Aplicación web | http://localhost:8080 |
| Documentación interactiva de la API (OpenAPI) | http://localhost:8000/api/docs |
| Salud (sin secretos) | http://localhost:8080/health |

## Usuarios ficticios

| Rol | Usuario | Clave |
|---|---|---|
| Administrador | `admin` | `Admin123!` |
| Operador | `operador1` | `Operador123!` |
| Cliente | `cliente1`, `cliente2`, `cliente3` | `Cliente123!` |
| Repartidor | `repartidor1` (Carlos, cap. 3) · `repartidor2` (Lucía, cap. 2) · `repartidor3` (Mateo, cap. 5) · `repartidor4` (Sofía, cap. 1) | `Repartidor123!` |

Envíos semilla: `MU-DEMO-0001` a `MU-DEMO-0005`. Las credenciales de las bases están en `.env`
(no se sube al repositorio; se entrega `.env.example`).

## Pruebas

**Automatizadas** (9 pruebas; crean sus propios datos y se pueden repetir):
```powershell
docker compose exec api pytest -v tests
```
**De fallos y reinicios** (Redis, persistencia, sincronizador, MongoDB caído): ver [`docs/pruebas.md`](docs/pruebas.md).

## Detener, reiniciar y limpiar

```powershell
docker compose stop                  # detiene sin borrar nada
docker compose start                 # vuelve a iniciar
docker compose down                  # quita contenedores; CONSERVA los datos (volúmenes)
docker compose up -d                 # vuelve a crearlos con los mismos datos
docker compose restart redis         # reinicia solo Redis: se pierden las posiciones (efímeras)
docker compose down -v               # ⚠ BORRA los volúmenes: vuelve a los datos semilla
```

## Revisar los datos directamente

```powershell
docker compose exec postgres psql -U mensajeria -d mensajeria -c "SELECT codigo, estado FROM envios ORDER BY id;"
docker compose exec mongo mongosh -u mensajeria -p cambiar_clave_mongo --authenticationDatabase admin mensajeria --eval "db.evidencias_entrega.find().pretty()"
docker compose exec redis redis-cli KEYS "repartidor:*"
```

## Estructura

```
├── docker-compose.yml · .env.example · README.md
├── backend/            API y sincronizador (Python 3.12, FastAPI)
│   ├── app/            main, config, conexiones, seguridad, errores, estados, servicios,
│   │   │               temporal (Redis), sincronizacion, sincronizador
│   │   └── rutas/      auth, envios, despacho, repartidor, consultas, admin, sistema
│   └── tests/          test_flujos.py (pytest)
├── frontend/           index.html · app.js · styles.css · nginx.conf
├── db/postgres/init/   01_esquema.sql · 02_semilla.sql
├── db/mongo/init/      01_colecciones.js
└── docs/               API.md · openapi.json · pruebas.md
```

## Problemas comunes

- **`Docker Engine stopped` / error de WSL:** cerrar Docker Desktop, `wsl --shutdown`, volver a abrirlo.
- **Un contenedor queda en `Restarting`:** `docker compose logs <servicio> --tail 30`.
- **Cambié un script de `db/*/init` y no se aplica:** solo corren con volúmenes vacíos → `docker compose down -v` y `up -d`.
- **El puerto 8080 está ocupado:** cambiar `"8080:80"` en el servicio `web` de `docker-compose.yml`.
