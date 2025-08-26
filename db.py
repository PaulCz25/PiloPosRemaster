# db.py — Postgres multi-tenant (psycopg3) con COMMIT al salir
import os
import psycopg
from psycopg.rows import dict_row
from flask import g

# OJO: sin espacios/saltos de línea
DATABASE_URL = os.environ["DATABASE_URL"].strip()

class _WrappedConn:
    """Permite usar placeholders estilo SQLite (?) en tu código actual."""
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
    def execute(self, sql: str, params=()):
        if params:
            sql = sql.replace("?", "%s")
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)
    def __getattr__(self, name):
        return getattr(self._conn, name)

class _DBCtx:
    """Uso: with get_db() as conn: conn.execute(...)."""
    def __enter__(self):
        schema = getattr(g, "tenant_schema", None)
        if not schema:
            raise RuntimeError("Tenant no resuelto (g.tenant_schema vacío)")
        self._raw = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        self._raw.autocommit = False  # manejamos commit/rollback manualmente
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
            else:
                try:
                    self._raw.commit()   # <<--- COMMIT EN ÉXITO
                except Exception:
                    self._raw.rollback()
        finally:
            self._raw.close()

def get_db():
    return _DBCtx()

def init_db():
    # Solo verifica conexión
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
