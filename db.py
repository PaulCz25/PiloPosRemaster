# db.py - Adaptador de base de datos para PilotoPOS
# - Usa SQLite por defecto en /var/data/pilotopos.db
# - Si existe DATABASE_URL (PostgreSQL), se conecta a Postgres y adapta las consultas con placeholders "?"
import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = Path(os.getenv("DB_PATH", "/var/data/pilotopos.db"))

def using_postgres() -> bool:
    return bool(DATABASE_URL)

# ------------------------- PostgreSQL -------------------------
if using_postgres():
    import psycopg2
    import psycopg2.extras
    import re

    class _PgCursorWrapper:
        def __init__(self, cur):
            self._cur = cur
        def fetchall(self):
            return self._cur.fetchall()
        def fetchone(self):
            return self._cur.fetchone()
        @property
        def rowcount(self):
            # necesario para checks como: if cur and cur.rowcount > 0:
            return getattr(self._cur, "rowcount", -1)
        def __getattr__(self, name):
            return getattr(self._cur, name)

    class _PgConn:
        """Envuelve psycopg2 para imitar el API mínimo de sqlite3.Connection usado en el proyecto."""
        def __init__(self):
            # Forzamos SSL; si ya viene en la URL no estorba.
            self._conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type:
                    self._conn.rollback()
                else:
                    self._conn.commit()
            finally:
                self._conn.close()

        def execute(self, sql: str, params=None):
            """
            Soporta:
              - placeholders de SQLite (?) → %s
              - json(?) → %s::jsonb
              - BEGIN/COMMIT/ROLLBACK enviados como SQL
            Devuelve un cursor wrapper compatible con fetchone/fetchall/rowcount.
            """
            if params is None:
                params = []

            # Normaliza SQL y parámetros para Postgres
            sql2, params2 = _normalize_sql(sql, params)

            # Manejo especial de transacciones si vienen como SQL
            upper = sql2.strip().upper()
            if upper == "BEGIN":
                with self._conn.cursor() as c:
                    c.execute("BEGIN")
                return _PgCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))
            if upper == "COMMIT":
                self._conn.commit()
                return _PgCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))
            if upper == "ROLLBACK":
                self._conn.rollback()
                return _PgCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql2, params2)
            return _PgCursorWrapper(cur)

    def _normalize_sql(sql: str, params):
        """
        Convierte placeholders SQLite (?) -> psycopg2 (%s)
        y transforma json(?) -> %s::jsonb para compatibilidad.
        """
        # json(?) -> %s::jsonb
        sql = re.sub(r'json\s*\(\s*\?\s*\)', '%s::jsonb', sql, flags=re.IGNORECASE)

        # Reemplazo simple de ? por %s (en este proyecto no se usan literales con '?')
        if '?' in sql:
            sql = sql.replace('?', '%s')

        # Asegurar que params sea lista
        if not isinstance(params, (list, tuple)):
            params = [params]
        return sql, list(params)

    def get_db():
        return _PgConn()

    def init_db():
        # En Postgres no ejecutamos schema.sql (es de SQLite).
        # Usa el archivo schema_postgres.sql para crear las tablas (ya lo cargaste desde DBeaver).
        pass

# ------------------------- SQLite -------------------------
else:
    import sqlite3
    def get_db():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_db():
        schema_path = Path(__file__).with_name("schema.sql")
        schema = schema_path.read_text(encoding="utf-8")
        with get_db() as conn:
            conn.executescript(schema)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")

if __name__ == "__main__":
    init_db()
    print("Usando", "PostgreSQL" if using_postgres() else f"SQLite en {DB_PATH}")
