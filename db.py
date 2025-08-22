# db.py - Adaptador de base de datos para PilotoPOS
# - Usa SQLite por defecto en /var/data/pilotopos.db
# - Si existe DATABASE_URL (PostgreSQL), se conecta a Postgres y adapta las consultas con placeholders "?"
import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = Path(os.getenv("DB_PATH", "/var/data/pilotopos.db"))

def using_postgres() -> bool:
    return bool(DATABASE_URL)

# ------------------------- PostgreSQL -------------------------
if using_postgres():
    import re
    import psycopg2
    import psycopg2.extras
    from psycopg2.pool import SimpleConnectionPool

    # ----- POOL DE CONEXIONES (¡nuevo!)
    _PG_MIN = int(os.getenv("PG_POOL_MIN", "1"))
    _PG_MAX = int(os.getenv("PG_POOL_MAX", "10"))
    _PG_SSLMODE = os.getenv("PG_SSLMODE", "require")
    _PG_POOL = SimpleConnectionPool(_PG_MIN, _PG_MAX, DATABASE_URL, sslmode=_PG_SSLMODE)

    class _PgCursorWrapper:
        def __init__(self, cur):
            self._cur = cur
        def fetchall(self):
            return self._cur.fetchall() if self._cur else []
        def fetchone(self):
            return self._cur.fetchone() if self._cur else None
        @property
        def rowcount(self):
            return getattr(self._cur, "rowcount", -1) if self._cur else -1
        def __getattr__(self, name):
            # delega cualquier otra cosa al cursor real
            if self._cur:
                return getattr(self._cur, name)
            raise AttributeError(name)

    class _PgConn:
        """Envuelve psycopg2 para imitar el API mínimo de sqlite3.Connection usado en el proyecto."""
        def __init__(self):
            # Toma una conexión del pool (autocommit off para que funcione con with ...)
            self._conn = _PG_POOL.getconn()
            self._conn.autocommit = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type:
                    self._conn.rollback()
                else:
                    self._conn.commit()
            finally:
                # Devuelve la conexión al pool (no se cierra)
                _PG_POOL.putconn(self._conn)

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

            sql2, params2 = _normalize_sql(sql, params)

            upper = sql2.strip().upper()
            if upper in ("BEGIN", "COMMIT", "ROLLBACK"):
                # Mantén compatibilidad con llamadas existentes
                with self._conn.cursor() as c:
                    if upper == "BEGIN":
                        c.execute("BEGIN")
                    elif upper == "COMMIT":
                        self._conn.commit()
                    else:
                        self._conn.rollback()
                # devolvemos un wrapper "vacío" pero compatible
                return _PgCursorWrapper(None)

            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql2, params2)
            return _PgCursorWrapper(cur)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

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
        # Devuelve un objeto con .execute() y soporte de context manager
        return _PgConn()

    def init_db():
        # En Postgres no ejecutamos schema.sql (es de SQLite).
        # Usa el archivo schema_postgres.sql para crear las tablas (si aplica).
        pass

# ------------------------- SQLite -------------------------
else:
    import sqlite3

    def get_db():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # detect_types para manejar tipos/fechas si en el futuro los usas
        conn = sqlite3.connect(
            DB_PATH,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row

        # ----- PRAGMAs SIEMPRE (¡mejora! antes sólo en init_db)
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        try:
            # Ignorado por SQLite si el SO no lo soporta
            conn.execute("PRAGMA mmap_size=30000000000;")
        except Exception:
            pass

        return conn

    def init_db():
        schema_path = Path(__file__).with_name("schema.sql")
        if not schema_path.exists():
            return
        schema = schema_path.read_text(encoding="utf-8")
        with get_db() as conn:
            # get_db ya aplica PRAGMAs a esta conexión
            conn.executescript(schema)

if __name__ == "__main__":
    init_db()
    print("Usando", "PostgreSQL (pool)" if using_postgres() else f"SQLite en {DB_PATH}")
