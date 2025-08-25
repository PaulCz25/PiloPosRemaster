# store.py
from typing import Dict, List
from db import get_db

# -------- Productos --------

def productos_listar() -> List[Dict]:
    with get_db() as conn:
        cur = conn.execute(
            "SELECT id, nombre, precio, stock, categoria FROM productos ORDER BY nombre"
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def productos_guardar(p: Dict) -> str:
    """
    p = { id, nombre, precio, stock, categoria }
    Inserta o actualiza por id (UPSERT en SQLite).
    """
    pid = str(p.get("id") or "").strip()
    nombre = (p.get("nombre") or "").strip()
    precio = float(p.get("precio") or 0)
    stock = int(p.get("stock") or 0)
    categoria = (p.get("categoria") or "").strip()

    if not pid or not nombre:
        raise ValueError("Faltan campos obligatorios (id, nombre)")

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
            (pid, nombre, precio, stock, categoria),
        )
    return pid

def productos_eliminar(pid: str) -> None:
    pid = str(pid or "").strip()
    if not pid:
        return
    with get_db() as conn:
        # Limpia items que referencian al producto (por si tu FK no está en cascada)
        conn.execute("DELETE FROM venta_items WHERE producto_id = ?", (pid,))
        conn.execute("DELETE FROM productos WHERE id = ?", (pid,))


# -------- Proveedores --------

def proveedores_listar() -> List[Dict]:
    with get_db() as conn:
        cur = conn.execute(
            "SELECT id, nombre, telefono, email, direccion FROM proveedores ORDER BY nombre"
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def proveedores_guardar(p: Dict) -> str:
    """
    p = { id?, nombre, telefono?, email?, direccion? }
    Si no viene id, usamos el nombre como id.
    """
    pid = (p.get("id") or p.get("nombre") or "").strip()
    nombre = (p.get("nombre") or "").strip()
    telefono = (p.get("telefono") or "").strip()
    email = (p.get("email") or "").strip()
    direccion = (p.get("direccion") or "").strip()

    if not pid or not nombre:
        raise ValueError("Faltan campos obligatorios (id/nombre)")

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO proveedores (id, nombre, telefono, email, direccion)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre=excluded.nombre,
                telefono=excluded.telefono,
                email=excluded.email,
                direccion=excluded.direccion
            """,
            (pid, nombre, telefono, email, direccion),
        )
    return pid

def proveedores_eliminar(pid: str) -> None:
    pid = str(pid or "").strip()
    if not pid:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM proveedores WHERE id = ?", (pid,))
