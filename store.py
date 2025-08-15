
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
    pid = str(data.get('id') or '').strip()
    nombre = data.get('nombre') or ''
    telefono = data.get('telefono')
    email = data.get('email')
    direccion = data.get('direccion')
    extra = {k: v for k, v in data.items() if k not in {'id','nombre','telefono','email','direccion'}}

    with get_db() as conn:
        if not pid:
            pid = str(conn.execute("SELECT COALESCE(MAX(CAST(id AS INTEGER)),0)+1 FROM proveedores").fetchone()[0])
            conn.execute(
                "INSERT INTO proveedores (id, nombre, telefono, email, direccion, extra) "
                "VALUES (?, ?, ?, ?, ?, json(?))",
                (pid, nombre, telefono, email, direccion, json.dumps(extra, ensure_ascii=False))
            )
        else:
            existe = conn.execute("SELECT 1 FROM proveedores WHERE id=?", (pid,)).fetchone()
            if existe:
                conn.execute(
                    "UPDATE proveedores SET nombre=?, telefono=?, email=?, direccion=?, extra=json(?) WHERE id=?",
                    (nombre, telefono, email, direccion, json.dumps(extra, ensure_ascii=False), pid)
                )
            else:
                conn.execute(
                    "INSERT INTO proveedores (id, nombre, telefono, email, direccion, extra) "
                    "VALUES (?, ?, ?, ?, ?, json(?))",
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
    pid = str(data.get('id') or '').strip()
    nombre = data.get('nombre') or ''
    precio = float(data.get('precio') or 0)
    stock = int(data.get('stock') or 0)
    categoria = data.get('categoria')
    extra = {k: v for k, v in data.items() if k not in {'id','nombre','precio','stock','categoria'}}

    with get_db() as conn:
        if not pid:
            pid = str(conn.execute("SELECT COALESCE(MAX(CAST(id AS INTEGER)),0)+1 FROM productos").fetchone()[0])
            conn.execute(
                "INSERT INTO productos (id, nombre, precio, stock, categoria, extra) "
                "VALUES (?, ?, ?, ?, ?, json(?))",
                (pid, nombre, precio, stock, categoria, json.dumps(extra, ensure_ascii=False))
            )
        else:
            existe = conn.execute("SELECT 1 FROM productos WHERE id=?", (pid,)).fetchone()
            if existe:
                conn.execute(
                    "UPDATE productos SET nombre=?, precio=?, stock=?, categoria=?, extra=json(?) WHERE id=?",
                    (nombre, precio, stock, categoria, json.dumps(extra, ensure_ascii=False), pid)
                )
            else:
                conn.execute(
                    "INSERT INTO productos (id, nombre, precio, stock, categoria, extra) "
                    "VALUES (?, ?, ?, ?, ?, json(?))",
                    (pid, nombre, precio, stock, categoria, json.dumps(extra, ensure_ascii=False))
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
