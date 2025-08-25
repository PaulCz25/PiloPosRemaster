# db.py
import os
import sqlite3

# Carpeta escribible en Render (en Free/Starter NO usar /var/data)
DATA_DIR = os.environ.get("DATA_DIR", "/tmp/pilotopos")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "pilotopos.db")

def get_db():
    """
    Retorna una conexión SQLite lista para usar.
    Se puede usar como context manager:
        with get_db() as conn:
            conn.execute("...")
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Crea las tablas mínimas si no existen (idempotente).
    Nota: 'extra' es TEXT y guardamos JSON serializado como cadena.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS productos(
            id        TEXT PRIMARY KEY,
            nombre    TEXT NOT NULL,
            precio    REAL NOT NULL DEFAULT 0,
            stock     INTEGER NOT NULL DEFAULT 0,
            categoria TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre);

        CREATE TABLE IF NOT EXISTS proveedores(
            id        TEXT PRIMARY KEY,
            nombre    TEXT NOT NULL,
            telefono  TEXT,
            email     TEXT,
            direccion TEXT
        );

        CREATE TABLE IF NOT EXISTS ventas(
            id      TEXT PRIMARY KEY,
            fecha   TEXT NOT NULL,    -- 'YYYY-MM-DD HH:MM'
            cliente TEXT,
            total   REAL NOT NULL DEFAULT 0,
            extra   TEXT              -- JSON serializado
        );

        CREATE TABLE IF NOT EXISTS venta_items(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            venta_id        TEXT NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
            producto_id     TEXT NOT NULL REFERENCES productos(id),
            cantidad        INTEGER NOT NULL,
            precio_unitario REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_venta_items_venta ON venta_items(venta_id);
        """)
