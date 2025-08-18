from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo  # ← zona horaria real

# === Config persistencia (Opción 1: SQLite + Disco Persistente) ===
# DATA_DIR apunta al disco persistente en Render (p. ej., /var/data)
DATA_DIR = os.getenv("DATA_DIR", "/var/data")
os.makedirs(DATA_DIR, exist_ok=True)  # asegura el directorio base
os.makedirs(os.path.join(DATA_DIR, "static"), exist_ok=True)  # para exportaciones JSON

# WebSockets en Flask
from flask_socketio import SocketIO, join_room, emit

# Zona horaria por defecto: Baja California (Tijuana).
# Puedes sobreescribirla en Render con la env var APP_TZ
LOCAL_TZ = ZoneInfo(os.getenv("APP_TZ", "America/Tijuana"))

# Adaptadores SQLite
from store import (
    proveedores_listar, proveedores_guardar, proveedores_eliminar,
    productos_listar, productos_guardar, productos_eliminar,
)
from db import get_db, init_db

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-key')

# ---- Socket.IO ----
# En Render funciona con WebSockets; con gunicorn + gevent-websocket (ver startCommand).
socketio = SocketIO(app, cors_allowed_origins="*")

# Estado "reciente" para sincronizar a una tablet que se conecta después
_last_state = None

@socketio.on("join")
def on_join(data):
    """
    data puede ser {'role': 'display'|'admin'} o un string directo.
    """
    role = data.get('role') if isinstance(data, dict) else str(data)
    if role == "display":
        join_room("display")
        # Si hay estado previo, sincroniza a la tablet inmediatamente
        if _last_state:
            emit("state", _last_state)
    elif role == "admin":
        join_room("admin")

@socketio.on("update-display")
def on_update_display(payload):
    """
    Se llama desde la PC (admin). Reenvía estado a la(s) tablet(s).
    """
    global _last_state
    _last_state = payload
    emit("state", payload, to="display")


# --------------------- Inicializar DB (Flask 3 compatible) ---------------------
try:
    with app.app_context():
        init_db()
except Exception as e:
    print('init_db warning:', e)


# ===================== EXPORTADORES (opcionales) =====================
def export_productos_json():
    # ⬇️ Ocultamos los productos creados “al vuelo”
    data = [p for p in productos_listar() if (p.get('categoria') or '').upper() != 'MANUAL']
    out = {}
    for p in data:
        codigo = str(p.get('id') or '')
        out[codigo] = {
            'nombre': p.get('nombre') or '',
            'precio': float(p.get('precio') or 0),
            'cantidad': int(p.get('stock') or 0),
            'seccion': p.get('categoria') or ''
        }
    # Persistir en disco persistente
    out_dir = os.path.join(DATA_DIR, 'static')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'productos.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=4)


def export_historial_json():
    items_salida = []
    with get_db() as conn:
        ventas = conn.execute(
            'SELECT id, fecha, total, extra FROM ventas ORDER BY fecha ASC, id ASC'
        ).fetchall()
        for v in ventas:
            fecha_txt = v['fecha'] or ''
            if ' ' in fecha_txt:
                fecha_str, hora_str = fecha_txt.split(' ', 1)
            else:
                fecha_str, hora_str = fecha_txt, ''

            redondeo = 0.0
            if v['extra']:
                try:
                    extra = json.loads(v['extra'])
                    redondeo = float(extra.get('redondeo', 0))
                    if not hora_str and extra.get('hora'):
                        hora_str = str(extra['hora'])
                except Exception:
                    pass

            det = conn.execute(
                'SELECT vi.cantidad, COALESCE(p.nombre, vi.producto_id) AS nombre '
                'FROM venta_items vi LEFT JOIN productos p ON p.id = vi.producto_id '
                'WHERE vi.venta_id=?',
                (v['id'],)
            ).fetchall()

            resumen = {}
            for r in det:
                nom = r['nombre']
                cant = int(r['cantidad'] or 0)
                if nom in resumen:
                    resumen[nom]['cantidad'] += cant
                else:
                    resumen[nom] = {'nombre': nom, 'cantidad': cant}

            items_salida.append({
                'fecha': fecha_str,
                'hora': hora_str,
                'total': float(v['total'] or 0),
                'redondeo': round(redondeo, 2),
                'productos': list(resumen.values())
            })

    # Persistir en disco persistente
    with open(os.path.join(DATA_DIR, 'historial.json'), 'w', encoding='utf-8') as f:
        json.dump(items_salida, f, ensure_ascii=False, indent=4)


# ===================== Rutas =====================

@app.route('/')
def index():
    return redirect(url_for('venta'))


@app.route('/venta')
def venta():
    if 'carrito' not in session:
        session['carrito'] = []

    carrito = session.get('carrito', [])
    total = sum(item.get('precio', 0) for item in carrito)
    total_redondeado = round(total + 0.5)

    # Para forzar que la tablet renderice el bloque (aunque su sesión esté vacía)
    display_mode = (request.args.get('display') == '1')

    return render_template(
        'venta.html',
        carrito=carrito,
        total=total,
        total_redondeado=total_redondeado,
        mensaje=session.pop('mensaje', None),
        display_mode=display_mode
    )


@app.route('/agregar-producto', methods=['POST'])
def agregar_producto():
    codigo = (request.form.get('codigo') or '').strip()

    with get_db() as conn:
        row = conn.execute(
            'SELECT id, nombre, precio FROM productos WHERE id=?',
            (codigo,)
        ).fetchone()

    if row:
        producto = {'nombre': row['nombre'], 'precio': float(row['precio'])}
        carrito = session.get('carrito', [])
        carrito.append(producto)
        session['carrito'] = carrito
        return redirect(url_for('venta'))
    else:
        return redirect(url_for('almacen', codigo=codigo))


# ====== NUEVO: Agregar productos manuales al carrito ======
@app.post('/carrito/agregar_manual')
def carrito_agregar_manual():
    data = request.get_json(silent=True) or {}
    nombre = (data.get('nombre') or '').strip()
    try:
        precio = float(data.get('precio'))
    except (TypeError, ValueError):
        precio = None

    if not nombre or precio is None or precio < 0:
        return jsonify({'ok': False, 'error': 'Datos inválidos'}), 400

    carrito = session.get('carrito', [])
    carrito.append({'nombre': nombre, 'precio': float(precio)})
    session['carrito'] = carrito
    nuevo_total = sum(item.get('precio', 0) for item in carrito)

    return jsonify({'ok': True, 'total': round(nuevo_total, 2), 'count': len(carrito)})


@app.route('/redondear', methods=['POST'])
def redondear():
    aceptado = request.form.get('aceptado')
    carrito = session.get('carrito', [])
    total = sum(item.get('precio', 0) for item in carrito)
    redondeo = round(total + 0.5) - total if aceptado == 'si' else 0
    total_final = round(total + 0.5) if aceptado == 'si' else total

    # Agrupa por nombre y guarda también el precio capturado (para manuales)
    resumen = {}
    for item in carrito:
        nombre = item.get('nombre')
        precio_item = float(item.get('precio') or 0)
        if nombre in resumen:
            resumen[nombre]['cantidad'] += 1
        else:
            resumen[nombre] = {'nombre': nombre, 'cantidad': 1, 'precio': precio_item}

    # Fecha/hora con TZ local (Tijuana)
    ahora = datetime.now(LOCAL_TZ)
    fecha_str = ahora.strftime('%Y-%m-%d')
    hora_str = ahora.strftime('%H:%M')
    venta_id = ahora.strftime('V%Y%m%d%H%M%S%f')

    try:
        with get_db() as conn:
            conn.execute('BEGIN')

            extra = {'redondeo': float(redondeo), 'hora': hora_str}
            conn.execute(
                'INSERT INTO ventas (id, fecha, cliente, total, extra) VALUES (?, ?, ?, ?, json(?))',
                (venta_id, f'{fecha_str} {hora_str}', None, float(total_final), json.dumps(extra, ensure_ascii=False))
            )

            for nombre, info in resumen.items():
                cantidad = int(info['cantidad'])

                # ¿Existe en productos? → usar su id/precio.
                prow = conn.execute(
                    'SELECT id, precio FROM productos WHERE nombre=? LIMIT 1',
                    (nombre,)
                ).fetchone()

                if prow:
                    pid = prow['id']
                    pu = float(prow['precio'] or 0.0)
                    tiene_db = True
                else:
                    # ---- Crear un producto "MANUAL" para respetar la FK ----
                    pu = float(info.get('precio', 0.0))
                    pid = ahora.strftime('M%Y%m%d%H%M%S%f')  # id único
                    conn.execute(
                        'INSERT INTO productos (id, nombre, precio, stock, categoria) VALUES (?,?,?,?,?)',
                        (pid, nombre, pu, 0, 'MANUAL')
                    )
                    tiene_db = False  # no tocar stock aunque exista la fila

                conn.execute(
                    'INSERT INTO venta_items (venta_id, producto_id, cantidad, precio_unitario) VALUES (?, ?, ?, ?)',
                    (venta_id, pid, cantidad, pu)
                )

                # Si es un producto real del almacén, baja stock.
                if tiene_db:
                    conn.execute(
                        'UPDATE productos SET stock = MAX(0, stock - ?) WHERE id = ?',
                        (cantidad, pid)
                    )

            conn.execute('COMMIT')
    except Exception as e:
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        session['mensaje'] = f'❌ Error al completar la venta: {e}'
        return redirect(url_for('venta'))

    # (Compat opcional: espejo JSON persistente)
    try:
        export_productos_json()
        export_historial_json()
    except Exception as _e:
        print('export warning:', _e)

    session['mensaje'] = (
        f'✅ Venta completada con redondeo de ${redondeo:.2f}.' if aceptado == 'si'
        else '✅ Venta completada sin redondeo.'
    )
    session['carrito'] = []
    session['ultimo_ticket'] = venta_id  # para botón de ticket en ventas
    return redirect(url_for('venta'))


@app.route('/almacen')
def almacen():
    codigo = request.args.get('codigo', '')
    return render_template('almacen.html', codigo=codigo)


@app.route('/guardar_producto', methods=['POST'])
def guardar_producto():
    data = request.get_json() or {}
    codigo = data.get('codigo')
    nombre = data.get('nombre')
    precio = float(data.get('precio'))
    cantidad = int(data.get('cantidad'))
    seccion = data.get('seccion', '')

    data_sql = {
        'id': codigo,
        'nombre': nombre,
        'precio': precio,
        'stock': cantidad,
        'categoria': seccion,
    }
    _id = productos_guardar(data_sql)

    try:
        export_productos_json()
    except Exception as e:
        print('export productos warning:', e)

    return {'success': True, 'id': _id}


# ===================== BORRADO FORZADO =====================
@app.route('/eliminar_producto', methods=['POST'])
def eliminar_producto():
    data = request.get_json() or {}
    codigo = str(data.get('codigo') or '').strip()
    if not codigo:
        return jsonify({'success': False, 'message': 'Falta el código.'}), 400

    cur = None
    try:
        with get_db() as conn:
            conn.execute('BEGIN')
            # 1) Eliminar detalle de ventas que use este producto
            conn.execute('DELETE FROM venta_items WHERE producto_id = ?', (codigo,))
            # 2) Eliminar el producto
            cur = conn.execute('DELETE FROM productos WHERE id = ?', (codigo,))
            conn.execute('COMMIT')

        if cur and cur.rowcount > 0:
            try:
                export_productos_json()
            except Exception as e:
                print('export productos warning:', e)
            return jsonify({'success': True}), 200

        return jsonify({'success': False, 'message': 'Producto no encontrado.'}), 404

    except Exception as e:
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        return jsonify({'success': False, 'message': f'Error al eliminar: {e}'}), 500


@app.route('/api/productos')
def api_productos():
    # ⬇️ Por defecto ocultamos los MANUAL. Para incluirlos: ?incluir_manuales=1
    incluir_manuales = (request.args.get('incluir_manuales') == '1')
    productos = productos_listar()
    if not incluir_manuales:
        productos = [p for p in productos if (p.get('categoria') or '').upper() != 'MANUAL']

    salida = {}
    for p in productos:
        codigo = str(p.get('id') or '')
        salida[codigo] = {
            'nombre': p.get('nombre'),
            'precio': float(p.get('precio') or 0),
            'cantidad': int(p.get('stock') or 0),
            'seccion': p.get('categoria') or ''
        }
    return jsonify(salida)


@app.route('/api/historial')
def api_historial():
    items_salida = []
    with get_db() as conn:
        ventas = conn.execute(
            'SELECT id, fecha, total, extra FROM ventas ORDER BY fecha ASC, id ASC'
        ).fetchall()

        for v in ventas:
            fecha_txt = v['fecha'] or ''
            if ' ' in fecha_txt:
                fecha_str, hora_str = fecha_txt.split(' ', 1)
            else:
                fecha_str, hora_str = fecha_txt, ''

            redondeo = 0.0
            if v['extra']:
                try:
                    extra = json.loads(v['extra'])
                    redondeo = float(extra.get('redondeo', 0))
                    if not hora_str and extra.get('hora'):
                        hora_str = str(extra['hora'])
                except Exception:
                    pass

            det = conn.execute(
                'SELECT vi.cantidad, COALESCE(p.nombre, vi.producto_id) AS nombre '
                'FROM venta_items vi LEFT JOIN productos p ON p.id = vi.producto_id '
                'WHERE vi.venta_id=?',
                (v['id'],)
            ).fetchall()

            resumen = {}
            for r in det:
                nom = r['nombre']
                cant = int(r['cantidad'] or 0)
                if nom in resumen:
                    resumen[nom]['cantidad'] += cant
                else:
                    resumen[nom] = {'nombre': nom, 'cantidad': cant}

            items_salida.append({
                'fecha': fecha_str,
                'hora': hora_str,
                'total': float(v['total'] or 0),
                'redondeo': float(redondeo),
                'productos': list(resumen.values())
            })

    return jsonify(items_salida)


@app.route('/centavos')
def centavos():
    centavos_list = []
    total_centavos = 0.0
    with get_db() as conn:
        ventas = conn.execute(
            'SELECT fecha, total, extra FROM ventas ORDER BY fecha ASC, id ASC'
        ).fetchall()
        for v in ventas:
            redondeo = 0.0
            fecha_txt = v['fecha'] or ''
            if ' ' in fecha_txt:
                fecha_str, hora_str = fecha_txt.split(' ', 1)
            else:
                fecha_str, hora_str = fecha_txt, ''
            if v['extra']:
                try:
                    extra = json.loads(v['extra'])
                    redondeo = float(extra.get('redondeo', 0))
                except Exception:
                    pass
            if redondeo > 0:
                centavos_list.append({
                    'fecha': fecha_str,
                    'hora': hora_str,
                    'total': float(v['total'] or 0),
                    'redondeo': round(redondeo, 2)
                })
                total_centavos += redondeo

    return render_template('centavos.html', centavos=centavos_list, total_centavos=round(total_centavos, 2))


@app.route('/carrito/eliminar', methods=['POST'])
def carrito_eliminar():
    data = request.get_json(silent=True) or {}
    try:
        idx = int(data.get('index', -1))
    except (TypeError, ValueError):
        idx = -1

    carrito = session.get('carrito', [])
    if 0 <= idx < len(carrito):
        carrito.pop(idx)
        session['carrito'] = carrito
        nuevo_total = sum(item.get('precio', 0) for item in carrito)
        return jsonify({'ok': True, 'total': round(nuevo_total, 2), 'count': len(carrito)})

    return jsonify({'ok': False, 'error': 'index out of range'}), 400


@app.route('/historial')
def historial():
    return render_template('historial.html')


@app.route('/usuarios')
def usuarios():
    return render_template('usuarios.html')


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/proveedores')
def proveedores_view():
    return render_template('proveedores.html')


@app.route('/api/proveedores')
def api_proveedores():
    return jsonify(proveedores_listar())


@app.route('/guardar_proveedor', methods=['POST'])
def guardar_proveedor():
    data = request.get_json() or {}
    pid = proveedores_guardar(data)
    return jsonify({'success': True, 'id': pid})


@app.route('/eliminar_proveedor', methods=['POST'])
def eliminar_proveedor():
    data = request.get_json() or {}
    pid = str(data.get('id', ''))
    proveedores_eliminar(pid)
    return jsonify({'success': True})


# ===================== Ruta de ticket =====================
@app.route('/ticket/<vid>')
def ticket(vid):
    negocio = {
        "nombre": "PilotoPOS",
        "direccion": "Mi calle #123, Ciudad",
        "telefono": "Tel. 000-000-0000"
    }

    with get_db() as conn:
        v = conn.execute(
            "SELECT id, fecha, total, extra FROM ventas WHERE id=?",
            (vid,)
        ).fetchone()
        if not v:
            return "Ticket no encontrado", 404

        items = conn.execute(
            "SELECT COALESCE(p.nombre, vi.producto_id) AS nombre, vi.cantidad, vi.precio_unitario "
            "FROM venta_items vi LEFT JOIN productos p ON p.id = vi.producto_id "
            "WHERE vi.venta_id=? ORDER BY vi.id",
            (vid,)
        ).fetchall()

    redondeo = 0.0
    hora_str = ""
    if v["extra"]:
        try:
            extra = json.loads(v["extra"])
            redondeo = float(extra.get("redondeo", 0))
            hora_str = extra.get("hora") or ""
        except Exception:
            pass

    fecha_txt = v["fecha"] or ""
    if " " in fecha_txt and not hora_str:
        fecha_str, hora_str = fecha_txt.split(" ", 1)
    else:
        fecha_str = fecha_txt or ""

    lineas = []
    subtotal = 0.0
    for it in items:
        nombre = it["nombre"]
        cant = int(it["cantidad"] or 0)
        pu = float(it["precio_unitario"] or 0)
        imp = cant * pu
        subtotal += imp
        lineas.append({
            "nombre": nombre,
            "cantidad": cant,
            "pu": pu,
            "importe": imp,
        })

    total = float(v["total"] or 0)

    return render_template(
        "ticket.html",
        negocio=negocio,
        venta=dict(id=v["id"], fecha=fecha_str, hora=hora_str, total=total),
        lineas=lineas,
        redondeo=redondeo,
        subtotal=subtotal
    )


# ===================== PANEL DE DATOS (ADMIN) =====================
@app.route('/panel')
def panel():
    return render_template('admin_datos.html')


@app.get('/api/ventas')
def api_ventas():
    """
    Lista ventas con filtros opcionales:
    - q: busca por ID o por fecha (texto)
    - desde: 'YYYY-MM-DD'
    - hasta: 'YYYY-MM-DD'
    """
    q = (request.args.get('q') or '').strip()
    desde = (request.args.get('desde') or '').strip()
    hasta = (request.args.get('hasta') or '').strip()

    sql = "SELECT id, fecha, total, extra FROM ventas"
    conds = []
    params = []

    if q:
        conds.append("(id LIKE ? OR fecha LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if desde:
        conds.append("substr(fecha,1,10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(fecha,1,10) <= ?")
        params.append(hasta)

    if conds:
        sql += " WHERE " + " AND ".join(conds)

    sql += " ORDER BY fecha DESC, id DESC"

    salida = []
    with get_db() as conn:
        ventas = conn.execute(sql, params).fetchall()
        for v in ventas:
            vid = v['id']
            fecha_txt = v['fecha'] or ""
            if " " in fecha_txt:
                fecha_str, hora_str = fecha_txt.split(" ", 1)
            else:
                fecha_str, hora_str = fecha_txt, ""

            redondeo = 0.0
            if v['extra']:
                try:
                    extra = json.loads(v['extra'])
                    redondeo = float(extra.get('redondeo', 0))
                    if not hora_str and extra.get('hora'):
                        hora_str = str(extra['hora'])
                except Exception:
                    pass

            det = conn.execute(
                "SELECT COALESCE(p.nombre, vi.producto_id) AS nombre, SUM(vi.cantidad) AS cant "
                "FROM venta_items vi LEFT JOIN productos p ON p.id = vi.producto_id "
                "WHERE vi.venta_id=? "
                "GROUP BY COALESCE(p.nombre, vi.producto_id) "
                "ORDER BY nombre",
                (vid,)
            ).fetchall()
            productos_txt = ", ".join([f"{int(d['cant'] or 0)}x {d['nombre']}" for d in det]) if det else ""

            salida.append({
                "id": vid,
                "fecha": fecha_str,
                "hora": hora_str,
                "productos": productos_txt,
                "total": float(v['total'] or 0),
                "redondeo": float(redondeo),
            })

    return jsonify(salida)


@app.post('/ventas/update')
def ventas_update():
    """
    Actualiza una venta:
    JSON: { id, fecha?, hora?, total, redondeo }
    - No modifica los items, solo la cabecera (fecha/hora/total/extra.redondeo).
    """
    data = request.get_json(silent=True) or {}
    vid = (data.get('id') or '').strip()
    if not vid:
        return jsonify({"ok": False, "msg": "Falta id"}), 400

    nueva_fecha = (data.get('fecha') or '').strip()     # 'YYYY-MM-DD'
    nueva_hora  = (data.get('hora') or '').strip()      # 'HH:MM'
    try:
        nuevo_total = float(data.get('total'))
    except Exception:
        return jsonify({"ok": False, "msg": "Total inválido"}), 400

    try:
        nuevo_redondeo = float(data.get('redondeo'))
    except Exception:
        return jsonify({"ok": False, "msg": "Redondeo inválido"}), 400

    with get_db() as conn:
        v = conn.execute("SELECT fecha, extra FROM ventas WHERE id=?", (vid,)).fetchone()
        if not v:
            return jsonify({"ok": False, "msg": "Venta no encontrada"}), 404

        # Construir fecha completa
        fecha_actual = v['fecha'] or ""
        if " " in fecha_actual:
            f_exist, h_exist = fecha_actual.split(" ", 1)
        else:
            f_exist, h_exist = fecha_actual, ""

        f_final = nueva_fecha if nueva_fecha else f_exist
        h_final = nueva_hora if nueva_hora else h_exist
        fecha_hora = f_final.strip()
        if h_final:
            fecha_hora = f"{f_final.strip()} {h_final.strip()}"

        # Actualizar extra.redondeo (y opcionalmente hora)
        extra = {}
        if v['extra']:
            try:
                extra = json.loads(v['extra'])
            except Exception:
                extra = {}
        extra['redondeo'] = float(nuevo_redondeo)
        if h_final:
            extra['hora'] = h_final

        conn.execute(
            "UPDATE ventas SET fecha=?, total=?, extra=? WHERE id=?",
            (fecha_hora, float(nuevo_total), json.dumps(extra, ensure_ascii=False), vid)
        )

    return jsonify({"ok": True})


@app.post('/ventas/delete')
def ventas_delete():
    """
    Elimina la venta completa (cabecera + items).
    NOTA: No reajustamos inventario (como pediste).
    """
    data = request.get_json(silent=True) or {}
    vid = (data.get('id') or '').strip()
    if not vid:
        return jsonify({"ok": False, "msg": "Falta id"}), 400

    with get_db() as conn:
        v = conn.execute("SELECT id FROM ventas WHERE id=?", (vid,)).fetchone()
        if not v:
            return jsonify({"ok": False, "msg": "Venta no encontrada"}), 404

        conn.execute("DELETE FROM venta_items WHERE venta_id=?", (vid,))
        conn.execute("DELETE FROM ventas WHERE id=?", (vid,))

    return jsonify({"ok": True})


if __name__ == '__main__':
    # En producción (Render) usa gunicorn + gevent-websocket (ver startCommand del render.yaml).
    # Para correr localmente:
    #   python app.py
    socketio.run(app, debug=True)
