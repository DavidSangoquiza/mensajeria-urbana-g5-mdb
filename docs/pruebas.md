# Pruebas reproducibles

## A. Automatizadas (pytest)

```powershell
docker compose exec api pytest -v tests
```

| Prueba | Qué verifica | Prueba del enunciado |
|---|---|---|
| test_01_salud | `/health` responde y PostgreSQL está ok | — |
| test_02_codigo_unico_e_idempotencia | Código único; mismo `Idempotency-Key` no duplica; consulta pública | 1 |
| test_03_validaciones | 422 con datos inválidos, 401 sin sesión, 403 por rol | Seguridad |
| test_04_transicion_invalida | `CREADO → ENTREGADO` rechazado con 409 | 2 |
| test_05_secuencia_valida… | Secuencia completa con incidencia, evidencia CONFIRMADA, historial ordenado y capacidad liberada | 2 y flujos 2–4 |
| test_06_capacidad_concurrente | Dos asignaciones **simultáneas** a un repartidor con 1 cupo: una 201 y otra 409 | 3 |
| test_07_cambio_de_estado_idempotente | Mismo `evento_id` dos veces: un solo evento en el historial | Idempotencia |
| test_08_sincronizacion_de_la_busqueda | Corrección en PostgreSQL llega a MongoDB; imprime el retraso medido | Flujo 5 |
| test_09_reconciliacion | Ningún envío con estado distinto a su último evento | Consistencia |

## B. Fallos y reinicios (manuales)

Ejecutar en la carpeta del proyecto. Las capturas de cada prueba están en el informe.

### B1. Reinicio de Redis: efímero vs. durable (prueba 4)
1. Como `repartidor1`, en **Mis tareas** pulsar **Posición al azar**.
2. Como `operador1`, abrir **Técnico**: el repartidor aparece *vigente* con su TTL.
   También: `docker compose exec redis redis-cli TTL repartidor:1:estado`.
3. `docker compose restart redis`
4. Recargar **Técnico**: *Sin posición vigente*. Abrir el detalle de un envío entregado: historial y evidencia intactos.

**Esperado:** la posición (Redis, sin persistencia) se pierde; envíos, historial (PostgreSQL) y evidencias (MongoDB) se conservan.

### B2. Persistencia tras reinicio completo (prueba 5, primera parte)
1. Anotar un envío ENTREGADO (por ejemplo, el de la prueba B1).
2. `docker compose down` y luego `docker compose up -d` (**sin** `-v`).
3. Consultar el envío en **Seguimiento** y en **Detalle**.

**Esperado:** estado, historial y evidencia siguen iguales (volúmenes `pg_datos` y `mongo_datos`).

### B3. Caída del sincronizador (prueba 5, segunda parte)
1. `docker compose stop sincronizador`
2. Como `operador1`, en **Detalle** de un envío CREADO corregir el destinatario.
3. En **Despacho** buscar ese código con fuente *MongoDB* (dato viejo) y con fuente *PostgreSQL* (dato nuevo).
4. En **Técnico**: sincronizador *detenido* y avisos pendientes > 0.
5. `docker compose start sincronizador` → en segundos, pendientes = 0 y MongoDB muestra el dato nuevo.

**Esperado:** la API sigue operando; los cambios esperan en el outbox y se proyectan en orden al volver.

### B4. Caída de MongoDB (búsqueda y evidencias)
1. `docker compose stop mongo`
2. **Despacho → Buscar:** la fuente cambia a *postgresql (respaldo: MongoDB no disponible)*.
3. Cambiar el estado de un envío (retirado, en tránsito): funciona, porque es PostgreSQL.
4. Intentar **Confirmar entrega**: responde 503 *EVIDENCIA_NO_GUARDADA* y el envío **sigue EN_TRANSITO**.
5. `docker compose start mongo` → reintentar la entrega: funciona sin duplicar (mismo `evento_id`).

### B5. Reconstrucción de la proyección
Como `admin`, en **Técnico** pulsar **Reconstruir proyección**: los pendientes suben y vuelven a 0;
la búsqueda queda igual porque se regenera desde PostgreSQL.

## Cuándo es seguro reintentar

| Operación | Mecanismo | Reintento |
|---|---|---|
| Crear envío | Cabecera `Idempotency-Key` (UNIQUE en `envios`) | Seguro: devuelve el mismo envío con `repetido: true` |
| Cambiar estado, asignar, reasignar, liberar, corregir destino | `evento_id` (UNIQUE en `historial_estados`) | Seguro con el mismo `evento_id` |
| Entregar | `evento_id` + `_id = EV-{codigo}` en MongoDB | Seguro; nunca reemplaza una evidencia CONFIRMADA |
| Reportar posición | Sobrescribe la clave en Redis | Siempre seguro |
