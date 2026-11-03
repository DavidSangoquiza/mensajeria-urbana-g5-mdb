-- =====================================================================
-- 02_semilla.sql  ·  Datos FICTICIOS de demostración
-- Claves: admin/Admin123!  operador1/Operador123!
--         cliente1..3/Cliente123!  repartidor1..4/Repartidor123!
-- =====================================================================

INSERT INTO zonas (codigo, nombre) VALUES
    ('NOR', 'Zona Norte'),
    ('CEN', 'Zona Centro'),
    ('SUR', 'Zona Sur'),
    ('VAL', 'Zona Valle'),
    ('CAL', 'Zona Calderón');

INSERT INTO tipos_servicio (codigo, nombre, horas_objetivo) VALUES
    ('ESTANDAR',   'Estándar',   24),
    ('EXPRESS',    'Express',     4),
    ('PROGRAMADO', 'Programado', 48);

-- Capacidades distintas para poder probar el control de capacidad
INSERT INTO repartidores (nombre, zona_id, vehiculo, capacidad_max) VALUES
    ('Carlos Ruta Demo',   1, 'MOTO',      3),   -- id 1
    ('Lucía Paquete Demo', 2, 'BICICLETA', 2),   -- id 2
    ('Mateo Envío Demo',   3, 'AUTO',      5),   -- id 3
    ('Sofía Entrega Demo', 4, 'MOTO',      1);   -- id 4  (capacidad 1 → prueba 3)

INSERT INTO usuarios (usuario, nombre, rol, clave_hash, repartidor_id) VALUES
    ('admin',       'Administrador Demo',  'ADMIN',      crypt('Admin123!',      gen_salt('bf', 8)), NULL),
    ('operador1',   'Operador Demo',       'OPERADOR',   crypt('Operador123!',   gen_salt('bf', 8)), NULL),
    ('cliente1',    'Librería Papel Azul', 'CLIENTE',    crypt('Cliente123!',    gen_salt('bf', 8)), NULL),
    ('cliente2',    'Farmacia Hoja Verde', 'CLIENTE',    crypt('Cliente123!',    gen_salt('bf', 8)), NULL),
    ('cliente3',    'Ana Demo Pruebas',    'CLIENTE',    crypt('Cliente123!',    gen_salt('bf', 8)), NULL),
    ('repartidor1', 'Carlos Ruta Demo',    'REPARTIDOR', crypt('Repartidor123!', gen_salt('bf', 8)), 1),
    ('repartidor2', 'Lucía Paquete Demo',  'REPARTIDOR', crypt('Repartidor123!', gen_salt('bf', 8)), 2),
    ('repartidor3', 'Mateo Envío Demo',    'REPARTIDOR', crypt('Repartidor123!', gen_salt('bf', 8)), 3),
    ('repartidor4', 'Sofía Entrega Demo',  'REPARTIDOR', crypt('Repartidor123!', gen_salt('bf', 8)), 4);
-- ids: admin=1 operador1=2 cliente1=3 cliente2=4 cliente3=5 repartidor1..4=6..9

-- ---------- Envíos de demostración (coordenadas inventadas) ----------
INSERT INTO envios (codigo, cliente_id, creado_por, tipo_servicio_id, zona_origen_id, zona_destino_id,
                    origen_direccion, origen_lat, origen_lng, destino_direccion, destino_lat, destino_lng,
                    remitente_nombre, destinatario_nombre, destinatario_telefono, peso_kg,
                    estado, version, idempotency_key, creado_en, actualizado_en) VALUES
 ('MU-DEMO-0001', 3, 3, 1, 1, 3, 'Av. Ficticia N10-100', -0.150000, -78.480000, 'Calle Ejemplo S5-12',   -0.290000, -78.540000,
  'Librería Papel Azul', 'Pedro Destino Uno',   '0990000001', 1.50, 'CREADO',      1, 'semilla-0001', now() - interval '5 hours', now() - interval '5 hours'),
 ('MU-DEMO-0002', 3, 3, 2, 1, 2, 'Av. Ficticia N10-100', -0.150000, -78.480000, 'Pasaje Inventado E3-45', -0.220000, -78.510000,
  'Librería Papel Azul', 'María Destino Dos',   '0990000002', 0.80, 'CREADO',      1, 'semilla-0002', now() - interval '4 hours', now() - interval '4 hours'),
 ('MU-DEMO-0003', 4, 4, 1, 2, 5, 'Calle Demo 123',       -0.220000, -78.512000, 'Conjunto Ficticio casa 7', -0.100000, -78.420000,
  'Farmacia Hoja Verde', 'Jorge Destino Tres',  '0990000003', 3.20, 'ASIGNADO',    2, 'semilla-0003', now() - interval '3 hours', now() - interval '2 hours'),
 ('MU-DEMO-0004', 4, 4, 2, 2, 4, 'Calle Demo 123',       -0.220000, -78.512000, 'Av. Supuesta 456',      -0.305000, -78.445000,
  'Farmacia Hoja Verde', 'Elena Destino Cuatro','0990000004', 0.50, 'EN_TRANSITO', 4, 'semilla-0004', now() - interval '6 hours', now() - interval '30 minutes'),
 ('MU-DEMO-0005', 5, 5, 3, 3, 1, 'Barrio Imaginario 9',  -0.280000, -78.550000, 'Av. Ficticia N20-50',   -0.130000, -78.490000,
  'Ana Demo Pruebas',    'Luis Destino Cinco',  '0990000005', 5.00, 'CREADO',      1, 'semilla-0005', now() - interval '1 hour',  now() - interval '1 hour');

-- Los códigos nuevos usarán la secuencia desde 1 (formato MU-AAMMDD-000001)

-- ---------- Asignaciones activas coherentes con los estados ----------
INSERT INTO asignaciones (envio_id, repartidor_id, operador_id, creada_en) VALUES
    (3, 2, 2, now() - interval '2 hours'),    -- MU-DEMO-0003 → Lucía
    (4, 1, 2, now() - interval '5 hours');    -- MU-DEMO-0004 → Carlos

-- ---------- Historial: cada estado vigente coincide con su último evento
INSERT INTO historial_estados (evento_id, envio_id, tipo, estado_anterior, estado_nuevo, actor_id, actor_rol, lat, lng, detalle, ocurrido_en) VALUES
 (gen_random_uuid(), 1, 'CREACION',      NULL,       'CREADO',      3, 'CLIENTE',    NULL, NULL, '{}', now() - interval '5 hours'),
 (gen_random_uuid(), 2, 'CREACION',      NULL,       'CREADO',      3, 'CLIENTE',    NULL, NULL, '{}', now() - interval '4 hours'),
 (gen_random_uuid(), 3, 'CREACION',      NULL,       'CREADO',      4, 'CLIENTE',    NULL, NULL, '{}', now() - interval '3 hours'),
 (gen_random_uuid(), 3, 'ASIGNACION',    'CREADO',   'ASIGNADO',    2, 'OPERADOR',   NULL, NULL, '{"repartidor_id": 2, "repartidor": "Lucía Paquete Demo"}', now() - interval '2 hours'),
 (gen_random_uuid(), 4, 'CREACION',      NULL,       'CREADO',      4, 'CLIENTE',    NULL, NULL, '{}', now() - interval '6 hours'),
 (gen_random_uuid(), 4, 'ASIGNACION',    'CREADO',   'ASIGNADO',    2, 'OPERADOR',   NULL, NULL, '{"repartidor_id": 1, "repartidor": "Carlos Ruta Demo"}', now() - interval '5 hours'),
 (gen_random_uuid(), 4, 'CAMBIO_ESTADO', 'ASIGNADO', 'RETIRADO',    6, 'REPARTIDOR', -0.220000, -78.512000, '{}', now() - interval '1 hour'),
 (gen_random_uuid(), 4, 'CAMBIO_ESTADO', 'RETIRADO', 'EN_TRANSITO', 6, 'REPARTIDOR', -0.260000, -78.480000, '{}', now() - interval '30 minutes'),
 (gen_random_uuid(), 5, 'CREACION',      NULL,       'CREADO',      5, 'CLIENTE',    NULL, NULL, '{}', now() - interval '1 hour');

-- ---------- Outbox: el sincronizador proyectará todo al arrancar -----
INSERT INTO outbox (envio_codigo, evento_id)
SELECT e.codigo, h.evento_id
FROM historial_estados h JOIN envios e ON e.id = h.envio_id
ORDER BY h.id;