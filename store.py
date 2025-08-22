
# store.py - Adaptador de almacenamiento para PilotoPOS usando SQLite
import json
from db import get_db

# ---------------- PROVEEDORES ----------------
def proveedores_listar():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, nombre, telefono, email, direccion "
            "FROM proveedores "
            "ORDER BY nombre"
        ).fetchall()
    return [dict(r) for r in rows]

def proveedores_guardar(data: dict):
    """
    Crea/actualiza proveedor en una sola operación (UPSERT).
    Compatible con SQLite y Postgres. Para Postgres se usa jsonb vía json(?).
    """
    pid = str(data.get('id') or '').strip()
    nombre = (data.get('nombre') or '').strip()
    telefono = (data.get('telefono') or '').strip()
    email = (data.get('email') or '').strip()
    direccion = (data.get('direccion') or '').strip()
    extra = data.get('extra') or {}

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO proveedores (id, nombre, telefono, email, direccion, extra)
            VALUES (?, ?, ?, ?, ?, json(?))
            ON CONFLICT(id) DO UPDATE SET
                nombre=excluded.nombre,
                telefono=excluded.telefono,
                email=excluded.email,
                direccion=excluded.direccion,
                extra=excluded.extra
            """,
            (pid, nombre, telefono, email, direccion, json.dumps(extra, ensure_ascii=False))
        )
    return pid

def proveedores_eliminar(pid: str):
    with get_db() as conn:
        conn.execute("DELETE FROM proveedores WHERE id=?", (str(pid),))

# ---------------- PRODUCTOS ----------------
def productos_listar():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, nombre, precio, stock, categoria "
            "FROM productos "
            "ORDER BY nombre"
        ).fetchall()
    return [dict(r) for r in rows]

def productos_guardar(data: dict):
    """
    Crea/actualiza producto en una sola operación (UPSERT).
    Sin cambiar cómo se muestran los datos en la UI.
    """
    pid = str(data.get('id') or '').strip()
    nombre = (data.get('nombre') or '').strip()
    # Mantén tipos seguros por si vienen strings
    try:
        precio = float(data.get('precio') or 0)
    except Exception:
        precio = 0.0
    try:
        stock = int(data.get('stock') or 0)
    except Exception:
        stock = 0
    categoria = (data.get('categoria') or '').strip()

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO productos (id, nombre, precio, stock, categoria)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre=excluded.nombre,
                precio=excluded.precio,
                stock=excluded.stock,
                categoria=excluded.categoria
            """,
            (pid, nombre, precio, stock, categoria)
        )
    return pid


def productos_eliminar(pid: str):
    with get_db() as conn:
        conn.execute("DELETE FROM productos WHERE id=?", (str(pid),))

# ---------------- USUARIOS (opcional) ----------------
def usuarios_listar():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username as nombre, rol "
            "FROM usuarios "
            "ORDER BY username"
        ).fetchall()
    return [dict(r) for r in rows]
