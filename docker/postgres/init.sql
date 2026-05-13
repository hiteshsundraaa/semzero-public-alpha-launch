-- SemZero demo database
-- Realistic e-commerce schema with messy FK chains to trigger fragility detection

CREATE TABLE IF NOT EXISTS users (
    id         SERIAL PRIMARY KEY,
    email      TEXT   NOT NULL UNIQUE,
    name       TEXT,
    status     TEXT   NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id         SERIAL PRIMARY KEY,
    name       TEXT   NOT NULL,
    sku        TEXT   UNIQUE,
    price      NUMERIC(10,2) NOT NULL,
    stock      INTEGER DEFAULT 0,
    category   TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    status     TEXT    NOT NULL DEFAULT 'pending',
    total      NUMERIC(12,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty        INTEGER NOT NULL DEFAULT 1,
    unit_price NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    event_type TEXT,
    payload    JSONB,
    ts         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS revenue_daily (
    date       DATE    NOT NULL,
    total      NUMERIC(14,2),
    order_count INTEGER
);

-- Seed
INSERT INTO users   VALUES (1,'alice@demo.com','Alice','active',NOW())
    ON CONFLICT DO NOTHING;
INSERT INTO users   VALUES (2,'bob@demo.com',  'Bob',  'active',NOW())
    ON CONFLICT DO NOTHING;
INSERT INTO products VALUES (1,'Widget','WID-001',9.99, 100,'tools',      NOW())
    ON CONFLICT DO NOTHING;
INSERT INTO products VALUES (2,'Gadget','GAD-002',29.99,50, 'electronics',NOW())
    ON CONFLICT DO NOTHING;
INSERT INTO orders  VALUES (1,1,'completed',19.98,NOW()) ON CONFLICT DO NOTHING;
INSERT INTO orders  VALUES (2,2,'pending',  29.99,NOW()) ON CONFLICT DO NOTHING;
INSERT INTO order_items VALUES (1,1,1,2,9.99)  ON CONFLICT DO NOTHING;
INSERT INTO order_items VALUES (2,2,2,1,29.99) ON CONFLICT DO NOTHING;

-- Enable pg_stat_statements for Wind Tunnel query extraction
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
