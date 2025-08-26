# db.py — versión Postgres multi-tenant (usa psycopg3)
import os
import psycopg
from psycopg.rows import dict_row
from flask import g

DATABASE_URL = os.environ["DATABASE_URL"]  # debe venir de Render

class _WrappedConn:
    """Adaptador pequeño para permitir conn.execute('SQL ?', [params]) como en SQLite."""
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def execute(self, sql: str, params=()):
        # Traduce placeholders estilo SQLite (?) -> psycopg (%s)
        if params:
            sql = sql.replace("?", "%s")
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def __getattr__(self, name):
        # delega commit(), rollback(), cursor(), etc.
        return getattr(self._conn, name)

class _DBCtx:
    """Context manager compatible con 'with get_db() as conn:'."""
    def __enter__(self):
        schema = getattr(g, "tenant_schema", None)
        if not schema:
            raise RuntimeError("Tenant no resuelto (g.tenant_schema vacío)")

        # Abre conexión a Postgres con filas tipo dict
        self._raw = psycopg.connect(DATABASE_URL, row_factory=dict_row)

        # Fija el search_path al schema del sitio y luego public
        with self._raw.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')

        return _WrappedConn(self._raw)

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                try:
                    self._raw.rollback()
                except Exception:
                    pass
        finally:
            self._raw.close()

def get_db():
    """Uso: with get_db() as conn: conn.execute(...).fetchall()"""
    return _DBCtx()

def init_db():
    """Solo verifica conectividad; no crea tablas aquí."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
