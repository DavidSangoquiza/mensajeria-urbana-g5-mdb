-- =====================================================================
-- 01_esquema.sql  ·  PostgreSQL = FUENTE OFICIAL (Source of Truth)
-- Envíos, despacho/asignaciones, historial de estados y outbox.
-- Se ejecuta solo la primera vez que se crea el volumen pg_datos.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- crypt() para claves y gen_random_uuid()

-- ---------- Catálogos (los mantiene el administrador) ----------------
CREATE TABLE zonas (
    id      SERIAL PRIMARY KEY,
    codigo  VARCHAR(10)  NOT NULL UNIQUE,          -- NOR, CEN, SUR...
    nombre  VARCHAR(60)  NOT NULL UNIQUE,
    activa  BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE tipos_servicio (
    id              SERIAL PRIMARY KEY,
    codigo          VARCHAR(15)  NOT NULL UNIQUE,  -- ESTANDAR, EXPRESS...
    nombre          VARCHAR(60)  NOT NULL,
    horas_objetivo  INT          NOT NULL CHECK (horas_objetivo > 0),
    activo          BOOLEAN      NOT NULL DEFAULT TRUE
);

-- ---------- Despacho: repartidores y su capacidad --------------------
CREATE TABLE repartidores (
    id             SERIAL PRIMARY KEY,
    nombre         VARCHAR(80)  NOT NULL,
    zona_id        INT          NOT NULL REFERENCES zonas(id),
    vehiculo       VARCHAR(20)  NOT NULL DEFAULT 'MOTO'
                   CHECK (vehiculo IN ('MOTO','BICICLETA','AUTO','A_PIE')),
    capacidad_max  INT          NOT NULL CHECK (capacidad_max BETWEEN 1 AND 20),
    activo         BOOLEAN      NOT NULL DEFAULT TRUE
);

-- ---------- Usuarios y roles ----------------------------------------
CREATE TABLE usuarios (
    id             SERIAL PRIMARY KEY,
    usuario        VARCHAR(40)  NOT NULL UNIQUE,
    nombre         VARCHAR(80)  NOT NULL,
    rol            VARCHAR(12)  NOT NULL
                   CHECK (rol IN ('CLIENTE','OPERADOR','REPARTIDOR','ADMIN')),
    clave_hash     TEXT         NOT NULL,          -- bcrypt vía pgcrypto
    repartidor_id  INT UNIQUE REFERENCES repartidores(id),
    activo         BOOLEAN      NOT NULL DEFAULT TRUE,
    -- un usuario REPARTIDOR siempre está vinculado a un repartidor, y solo él
    CONSTRAINT ck_usuario_repartidor CHECK ((rol = 'REPARTIDOR') = (repartidor_id IS NOT NULL))
);

-- ---------- Solicitudes y envíos ------------------------------------
CREATE SEQUENCE seq_codigo_envio;

CREATE TABLE envios (
    id                     SERIAL PRIMARY KEY,
    codigo                 VARCHAR(20)  NOT NULL UNIQUE,   -- MU-AAMMDD-000001
    cliente_id             INT          REFERENCES usuarios(id),
    creado_por             INT          NOT NULL REFERENCES usuarios(id),
    tipo_servicio_id       INT          NOT NULL REFERENCES tipos_servicio(id),
    zona_origen_id         INT          NOT NULL REFERENCES zonas(id),
    zona_destino_id        INT          NOT NULL REFERENCES zonas(id),
    origen_direccion       VARCHAR(160) NOT NULL,
    origen_lat             NUMERIC(9,6) NOT NULL CHECK (origen_lat  BETWEEN -90  AND 90),
    origen_lng             NUMERIC(9,6) NOT NULL CHECK (origen_lng  BETWEEN -180 AND 180),
    destino_direccion      VARCHAR(160) NOT NULL,
    destino_lat            NUMERIC(9,6) NOT NULL CHECK (destino_lat BETWEEN -90  AND 90),
    destino_lng            NUMERIC(9,6) NOT NULL CHECK (destino_lng BETWEEN -180 AND 180),
    remitente_nombre       VARCHAR(80)  NOT NULL,
    destinatario_nombre    VARCHAR(80)  NOT NULL,
    destinatario_telefono  VARCHAR(20),
    peso_kg                NUMERIC(6,2) NOT NULL CHECK (peso_kg > 0 AND peso_kg <= 50),
    estado                 VARCHAR(12)  NOT NULL DEFAULT 'CREADO'
                           CHECK (estado IN ('CREADO','ASIGNADO','RETIRADO','EN_TRANSITO',
                                             'INCIDENCIA','ENTREGADO','CANCELADO')),
    version                INT          NOT NULL DEFAULT 1,    -- sube en cada cambio
    evidencia_id           VARCHAR(40),                         -- _id del documento en MongoDB
    idempotency_key        VARCHAR(80)  NOT NULL,
    creado_en              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actualizado_en         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_envio_idempotencia UNIQUE (idempotency_key)  -- evita duplicados por reintento
);
CREATE INDEX ix_envios_estado        ON envios (estado, creado_en DESC);
CREATE INDEX ix_envios_zona_destino  ON envios (zona_destino_id);
CREATE INDEX ix_envios_cliente       ON envios (cliente_id, creado_en DESC);

-- ---------- Asignaciones (vínculo envío ↔ repartidor) ---------------
CREATE TABLE asignaciones (
    id                 SERIAL PRIMARY KEY,
    envio_id           INT          NOT NULL REFERENCES envios(id),
    repartidor_id      INT          NOT NULL REFERENCES repartidores(id),
    operador_id        INT          NOT NULL REFERENCES usuarios(id),
    activa             BOOLEAN      NOT NULL DEFAULT TRUE,
    creada_en          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    liberada_en        TIMESTAMPTZ,
    motivo_liberacion  VARCHAR(30)
);
-- Un envío solo puede tener UNA asignación activa (índice único parcial)
CREATE UNIQUE INDEX uq_asignacion_activa ON asignaciones (envio_id) WHERE activa;
-- Para contar rápido la carga actual de un repartidor
CREATE INDEX ix_asignaciones_repartidor ON asignaciones (repartidor_id) WHERE activa;

-- ---------- Seguimiento: historial de eventos INMUTABLE --------------
CREATE TABLE historial_estados (
    id               BIGSERIAL    PRIMARY KEY,           -- orden garantizado
    evento_id        UUID         NOT NULL UNIQUE,        -- idempotencia de cambios
    envio_id         INT          NOT NULL REFERENCES envios(id),
    tipo             VARCHAR(25)  NOT NULL,               -- CREACION, ASIGNACION, CAMBIO_ESTADO, ENTREGA...
    estado_anterior  VARCHAR(12),
    estado_nuevo     VARCHAR(12)  NOT NULL,               -- estado del envío DESPUÉS del evento
    actor_id         INT          REFERENCES usuarios(id),
    actor_rol        VARCHAR(12)  NOT NULL,
    lat              NUMERIC(9,6),
    lng              NUMERIC(9,6),
    detalle          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ocurrido_en      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX ix_historial_envio ON historial_estados (envio_id, id);

-- Append-only: el historial no se modifica ni se borra (auditoría)
CREATE FUNCTION fn_historial_inmutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'historial_estados es de solo inserción: no se permite %', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_historial_inmutable
    BEFORE UPDATE OR DELETE ON historial_estados
    FOR EACH ROW EXECUTE FUNCTION fn_historial_inmutable();

-- ---------- Outbox: cambios pendientes de proyectar en MongoDB --------
-- Se inserta en la MISMA transacción que el cambio del envío.
CREATE TABLE outbox (
    id            BIGSERIAL    PRIMARY KEY,
    envio_codigo  VARCHAR(20)  NOT NULL,
    evento_id     UUID,                                   -- NULL = reproyectar solo el envío
    creado_en     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    procesado_en  TIMESTAMPTZ,
    intentos      INT          NOT NULL DEFAULT 0,
    ultimo_error  TEXT
);
CREATE INDEX ix_outbox_pendientes ON outbox (id) WHERE procesado_en IS NULL;

-- ---------- Latido del sincronizador (pantalla técnica) --------------
CREATE TABLE sincronizador_estado (
    id                   INT PRIMARY KEY CHECK (id = 1),
    ultimo_latido        TIMESTAMPTZ,
    ultimo_procesado_id  BIGINT,
    ultimo_error         TEXT
);
INSERT INTO sincronizador_estado (id) VALUES (1);