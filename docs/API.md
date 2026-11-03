# Especificación de la API

- **Base:** `http://localhost:8080/api` (a través de nginx) o `http://localhost:8000/api` (directo).
- **OpenAPI completo:** [`openapi.json`](openapi.json) · interactivo en `/api/docs`.
- **Autenticación:** `POST /api/auth/login` devuelve un token; enviarlo como `Authorization: Bearer <token>`.
- **Formato de error (uniforme):** `{"error": {"codigo": "...", "mensaje": "...", "detalle": {...}}}`

| Código HTTP | Significado |
|---|---|
| 200 / 201 | Correcto / creado (un reintento idempotente responde 200 con `"repetido": true`) |
| 401 | Sin sesión o token inválido |
| 403 | El rol no puede hacer la operación, o el envío no le pertenece |
| 404 | No existe |
| 409 | Regla de negocio: transición inválida, capacidad excedida, ya entregado… |
| 422 | Datos inválidos (Pydantic) o catálogo inexistente |
| 503 | Un gestor no responde (`POSTGRES_NO_DISPONIBLE`, `EVIDENCIA_NO_GUARDADA`, `REDIS_NO_DISPONIBLE`…) |

## Endpoints

| Método y ruta | Rol | Cuerpo / parámetros | Respuestas |
|---|---|---|---|
| `GET /health` · `GET /api/health` | Público | — | 200 ok/degradado · 503 caído |
| `POST /api/auth/login` | Público | `{usuario, clave}` | 200 `{token, usuario}` · 401 · 422 |
| `GET /api/auth/yo` | Cualquiera | — | 200 · 401 |
| `GET /api/catalogos` | Público | — | 200 `{zonas, tipos_servicio}` |
| `POST /api/envios` | Cliente, operador, admin | Cabecera `Idempotency-Key`; `{tipo_servicio, zona_origen, zona_destino, origen_direccion, origen_lat, origen_lng, destino_direccion, destino_lat, destino_lng, remitente_nombre, destinatario_nombre, destinatario_telefono?, peso_kg, cliente_id?}` | 201 · 200 repetido · 403 · 422 |
| `GET /api/envios` | Operador, admin | `?codigo&estado&zona&destinatario&desde&hasta&fuente=auto\|mongodb\|postgresql&limite` | 200 `{fuente, resultados}` · 503 si `fuente=mongodb` y cae |
| `GET /api/envios/mios` | Cliente | — | 200 |
| `GET /api/envios/{codigo}` | Dueño, operador, admin, repartidor que lo tuvo | — | 200 `{envio, historial, evidencia, posicion_repartidor, avisos, fuentes}` · 403 · 404 |
| `GET /api/seguimiento/{codigo}` | Público | — | 200 (sin teléfonos; solo primer nombre) · 404 |
| `PATCH /api/envios/{codigo}/estado` | Según máquina de estados | `{estado_nuevo, evento_id, observacion?, lat?, lng?}` | 200 · 403 · 404 · 409 `TRANSICION_INVALIDA` · 422 |
| `PATCH /api/envios/{codigo}/destinatario` | Operador, admin | `{evento_id, destinatario_nombre, destinatario_telefono?, destino_direccion?, zona_destino?}` | 200 · 409 `ENVIO_FINALIZADO` · 422 |
| `POST /api/asignaciones` | Operador, admin | `{codigo, repartidor_id, evento_id}` | 201 · 200 repetido · 404 · 409 `CAPACIDAD_EXCEDIDA` / `ENVIO_NO_PENDIENTE` |
| `POST /api/asignaciones/{codigo}/reasignar` | Operador, admin | `{repartidor_id, evento_id, observacion?}` | 200 · 409 |
| `POST /api/asignaciones/{codigo}/liberar` | Operador, admin | `{evento_id, observacion?}` | 200 · 409 |
| `GET /api/despacho/repartidores` | Operador, admin | — | 200 (carga calculada en PostgreSQL) |
| `GET /api/repartidor/tareas` | Repartidor | — | 200 `{tareas[], posicion}` con `acciones` permitidas |
| `POST /api/repartidor/tareas/{codigo}/aceptar` | Repartidor asignado | `{evento_id}` | 200 · 409 |
| `POST /api/envios/{codigo}/entrega` | Repartidor asignado | `{evento_id, receptor{nombre, relacion?}, foto{tipo: url\|referencia_archivo, valor}, observacion?, extras?, lat?, lng?}` | 201 · 200 repetido · 403 · 409 · 503 `EVIDENCIA_NO_GUARDADA` |
| `POST /api/repartidor/ubicacion` | Repartidor | `{lat, lng}` | 200 · 503 |
| `POST /api/repartidor/desconectar` | Repartidor | — | 200 |
| `GET /api/repartidores/estado-temporal` | Operador, admin | — | 200 (vigente / no vigente) |
| `GET /api/admin/sync/estado` | Operador, admin | — | 200 `{sincronizador, outbox{pendientes, con_error, retraso_seg…}, …}` |
| `POST /api/admin/sync/reconstruir` | Admin | — | 200 |
| `GET /api/admin/reconciliacion` | Operador, admin | — | 200 `{descuadres_estado_historial, evidencias_pendientes, resultado}` |
| `GET/POST /api/admin/zonas` · `PATCH /api/admin/zonas/{id}` | Admin (GET también operador) | `{codigo, nombre}` · `{nombre?, activa?}` | 200 · 201 · 404 · 409 |
| `GET/POST /api/admin/tipos-servicio` · `PATCH …/{id}` | Admin | `{codigo, nombre, horas_objetivo}` · `{nombre?, horas_objetivo?, activo?}` | 200 · 201 · 404 · 409 |
| `POST /api/admin/repartidores` · `PATCH …/{id}` | Admin | `{nombre, zona, vehiculo, capacidad_max, usuario, clave}` · `{nombre?, zona?, vehiculo?, capacidad_max?, activo?}` | 201 · 200 · 409 `CAPACIDAD_MENOR_A_CARGA` · 422 |
| `GET /api/admin/clientes` | Admin, operador | — | 200 |

## Máquina de estados

| Desde | Hacia | Rol | Endpoint |
|---|---|---|---|
| CREADO | ASIGNADO | Operador, admin | `POST /asignaciones` (verifica capacidad) |
| CREADO | CANCELADO | Cliente (propio), operador, admin | `PATCH …/estado` |
| ASIGNADO | ASIGNADO | Operador, admin | `…/reasignar` |
| ASIGNADO / INCIDENCIA | CREADO | Operador, admin | `…/liberar` |
| ASIGNADO | RETIRADO | Repartidor asignado | `PATCH …/estado` |
| RETIRADO | EN_TRANSITO | Repartidor asignado | `PATCH …/estado` |
| ASIGNADO / RETIRADO / EN_TRANSITO | INCIDENCIA | Repartidor, operador, admin (observación obligatoria) | `PATCH …/estado` |
| INCIDENCIA | EN_TRANSITO | Repartidor, operador, admin | `PATCH …/estado` |
| INCIDENCIA | ASIGNADO | Operador, admin | `…/reasignar` |
| ASIGNADO / INCIDENCIA | CANCELADO | Operador, admin (libera capacidad) | `PATCH …/estado` |
| EN_TRANSITO | ENTREGADO | Repartidor asignado (evidencia obligatoria; libera capacidad) | `POST …/entrega` |

ENTREGADO y CANCELADO son finales. Cualquier otro par responde **409 TRANSICION_INVALIDA**,
con las transiciones permitidas en `detalle`.

## Ejemplos

**Registrar envío**
```http
POST /api/envios
Authorization: Bearer <token de cliente1>
Idempotency-Key: 6c0b8a1e-1f9e-4c3a-9a51-2f6d8e7b0c11

{"tipo_servicio":"EXPRESS","zona_origen":"NOR","zona_destino":"SUR",
 "origen_direccion":"Av. Ficticia N10-100","origen_lat":-0.15,"origen_lng":-78.48,
 "destino_direccion":"Calle Ejemplo S5-12","destino_lat":-0.29,"destino_lng":-78.54,
 "remitente_nombre":"Librería Papel Azul","destinatario_nombre":"Pedro Prueba","peso_kg":2}

201 Created
{"repetido": false, "mensaje": "Envío registrado con código MU-261103-000001",
 "envio": {"codigo": "MU-261103-000001", "estado": "CREADO", "version": 1, ...}}
```

**Transición inválida**
```http
PATCH /api/envios/MU-261103-000001/estado
{"estado_nuevo":"ENTREGADO","evento_id":"3fa85f64-5717-4562-b3fc-2c963f66afa6"}

409 Conflict
{"error":{"codigo":"TRANSICION_INVALIDA","mensaje":"Transición inválida: CREADO → ENTREGADO",
 "detalle":{"permitidas_desde_estado_actual":["ASIGNADO","CANCELADO"]}}}
```

**Capacidad excedida**
```http
POST /api/asignaciones
{"codigo":"MU-DEMO-0002","repartidor_id":4,"evento_id":"22222222-2222-4222-8222-222222222222"}

409 Conflict
{"error":{"codigo":"CAPACIDAD_EXCEDIDA","mensaje":"Sofía Entrega Demo ya tiene 1 de 1 envíos activos",
 "detalle":{"repartidor_id":4,"carga_actual":1,"capacidad_max":1}}}
```
