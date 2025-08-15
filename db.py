# db.py: conexión y utilidades de SQLite para PilotoPOS
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("pilotopos.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    schema_path = Path(__file__).with_name("schema.sql")
    schema = schema_path.read_text(encoding="utf-8")
    with get_db() as conn:
        conn.executescript(schema)
        # Rendimiento/concurrencia
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

if __name__ == "__main__":
    init_db()
    print(f"Base inicializada en: {DB_PATH}")
