from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo  # ← zona horaria real

# ===== Seguridad y Auth (añadido) =====
from dotenv import load_dotenv; load_dotenv()
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
# =====================================

# Carpeta escribible en Render (no usar /var/data en Free/Starter)
DATA_DIR = os.environ.get("DATA_DIR", "/tmp/pilotopos")
STATIC_DIR = os.path.join(DATA_DIR, "static")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# WebSockets en Flask
from flask_socketio import SocketIO, join_room, emit

# Zona horaria por defecto: Baja California (Tijuana).
# Puedes sobreescribirla en Render con la env var APP_TZ
LOCAL_TZ = ZoneInfo(os.getenv("APP_TZ", "America/Tijuana"))

# Adaptadores y store
from store import (
    proveedores_listar, proveedores_guardar, proveedores_eliminar,
    productos_listar, productos_guardar, productos_eliminar,
)
from db import get_db, init_db

# ================== APP ==================
app = Flask(__name__)

from datetime import timedelta

# endurece cookies de sesión (útil en Render con HTTPS)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(int(os.getenv("SESSION_COOKIE_SECURE", "1"))),  # en prod=1
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# Endpoints que SÍ pueden verse sin login
_PUBLIC_ENDPOINTS = {
    "static", "login", "logout", "__probe",  # estáticos y login
}

@app.before_request
def _require_auth_everywhere():
    # Permite /venta?display=1 como kiosco SOLO lectura
    if request.endpoint == "venta" and request.args.get("display") == "1":
        return

    ep = (request.endpoint or "").split(".")[-1]
    if ep in _PUBLIC_ENDPOINTS:
        return

    # Si NO tienes flask-login, usa una bandera de sesión propia:
    if not session.get("uid"):  # <-- asegúrate que en tu POST /login hagas: session["uid"] = user_id
        # opcional: remember permanente
        session.permanent = True
        return redirect(url_for("login", next=request.full_path))

# Endurece sesión: requiere SECRET_KEY real (ya la verificaste)
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=bool(int(os.getenv("SESSION_COOKIE_SECURE", "0"))),  # 0 en dev, 1 en prod
)

# ---- Login manager (añadido) ----
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(uid):
    try:
        with get_db() as conn:
            r = conn.execute("SELECT id, username FROM usuarios WHERE id=? AND activo=TRUE", (uid,)).fetchone()
            if r:
                return User(r["id"], r["username"])
    except Exception:
        pass
    return None
# ---------------------------------

# ---- Socket.IO ----
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*")
if allowed_origins and allowed_origins != "*":
    allowed_origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
socketio = SocketIO(app, cors_allowed_origins=allowed_origins or "*")
_last_state = None

@socketio.on("join")
def on_join(data):
    role = data.get('role') if isinstance(data, dict) else str(data)
    if role == "display":
        join_room("display")
        if _last_state:
            emit("state", _last_state)
    elif role == "admin":
        join_room("admin")

@socketio.on("update-display")
def on_update_display(payload):
    global _last_state
    _last_state = payload
    emit("state", payload, to="display")

# --------------------- Inicializar DB de control ---------------------
try:
    with app.app_context():
        init_db()   # crea control.tenants si no existe (idempotente) – en nuestro db.py es no-op
except Exception as e:
    print('init_db warning:', e)

# --------------------- TENANT FIJO POR SITIO ---------------------
import psycopg

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL")
TENANT_SCHEMA = os.getenv("TENANT_SCHEMA", "tnt_default")  # p.ej. tnt_cliente1

def ensure_tenant_schema():
    """
    Crea el schema de ESTE sitio si no existe y tablas mínimas para que no truene.
    NOTA: 'extra' se maneja como TEXT (compat json.dumps/json.loads en este código).
    """
    ddl = f'''
    create schema if not exists "{TENANT_SCHEMA}";
    set search_path = "{TENANT_SCHEMA}", public;

    create table if not exists productos(
      id        text primary key,
      nombre    text not null,
      precio    numeric(12,2) not null default 0,
      stock     integer not null default 0,
      categoria text
    );
    create index if not exists idx_productos_nombre on productos(nombre);

    create table if not exists proveedores(
      id        text primary key,
      nombre    text not null,
      telefono  text,
      email     text,
      direccion text
    );

    create table if not exists ventas(
      id      text primary key,
      fecha   text not null,             -- guardas 'YYYY-MM-DD HH:MM' como texto
      cliente text,
      total   numeric(12,2) not null default 0,
      extra   text                       -- JSON serializado como texto
    );

    create table if not exists venta_items(
      id              bigserial primary key,
      venta_id        text not null references ventas(id) on delete cascade,
      producto_id     text not null references productos(id),
      cantidad        integer not null,
      precio_unitario numeric(12,2) not null
    );
    create index if not exists idx_venta_items_venta on venta_items(venta_id);

    -- ===== Usuarios mínimos para autenticación (añadido) =====
    create table if not exists usuarios(
      id            bigserial primary key,
      username      text unique not null,
      hash          text not null,
      activo        boolean default true,
      ultimo_acceso timestamptz
    );
    '''
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            conn.commit()

# Ejecutar una vez al arrancar el proceso (Flask 3: llamada directa)
ensure_tenant_schema()

def bootstrap_admin_user_if_needed():
    """
    Si no hay usuarios, crea uno inicial usando variables de entorno:
    ADMIN_USER y ADMIN_PASSWORD (en texto plano) o ADMIN_PASSWORD_HASH.
    """
    admin_user = os.getenv("ADMIN_USER", "admin").strip()
    admin_pwd = os.getenv("ADMIN_PASSWORD")
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    try:
        with get_db() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM usuarios").fetchone()
            if row and int(row["c"] or 0) == 0:
                if not admin_hash and not admin_pwd:
                    print("bootstrap_admin: no hay usuarios y falta ADMIN_PASSWORD o ADMIN_PASSWORD_HASH; omitiendo creación.")
                    return
                if not admin_hash and admin_pwd:
                    admin_hash = generate_password_hash(admin_pwd, method="pbkdf2:sha256", salt_length=16)
                conn.execute("INSERT INTO usuarios(username, hash, activo) VALUES (?, ?, TRUE)", (admin_user, admin_hash))
                print(f"bootstrap_admin: usuario inicial creado -> {admin_user}")
    except Exception as e:
        print("bootstrap_admin warning:", e)

bootstrap_admin_user_if_needed()

@app.before_request
def set_fixed_tenant():
    # Todas las requests usan el schema de este sitio
    g.tenant_schema = TENANT_SCHEMA
    g.r2_prefix = f"tenants/{TENANT_SCHEMA}/"

# ===================== EXPORTADORES (opcionales) =====================
def export_productos_json():
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

    with open(os.path.join(DATA_DIR, 'historial.json'), 'w', encoding='utf-8') as f:
        json.dump(items_salida, f, ensure_ascii=False, indent=4)

# ===================== Rutas =====================
@app.route('/')
def index():
    return redirect(url_for('venta'))

@app.route('/venta')
def venta():
    # Requiere login excepto en modo display (para el visor del cliente)
    display_mode = (request.args.get('display') == '1')
    if not display_mode and not current_user.is_authenticated:
        return redirect(url_for('login'))

    if 'carrito' not in session:
        session['carrito'] = []

    carrito = session.get('carrito', [])
    total = sum(item.get('precio', 0) for item in carrito)
    total_redondeado = round(total + 0.5)

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
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

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
@login_required
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
@login_required
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
            # NOTA: 'extra' es TEXT; guardamos el JSON serializado sin 'json(?)'
            conn.execute(
                'INSERT INTO ventas (id, fecha, cliente, total, extra) VALUES (?, ?, ?, ?, ?)',
                (venta_id, f'{fecha_str} {hora_str}', None, float(total_final), json.dumps(extra, ensure_ascii=False))
            )

            for nombre, info in resumen.items():
                cantidad = int(info['cantidad'])

                # ¿Existe en productos? → usar su id/precio.
                with_db = False
                prow = conn.execute(
                    'SELECT id, precio FROM productos WHERE nombre=? LIMIT 1',
                    (nombre,)
                ).fetchone()

                if prow:
                    pid = prow['id']
                    pu = float(prow['precio'] or 0.0)
                    with_db = True
                else:
                    # Crear producto "MANUAL" para respetar FK
                    pu = float(info.get('precio', 0.0))
                    pid = ahora.strftime('M%Y%m%d%H%M%S%f')  # id único
                    conn.execute(
                        'INSERT INTO productos (id, nombre, precio, stock, categoria) VALUES (?,?,?,?,?)',
                        (pid, nombre, pu, 0, 'MANUAL')
                    )

                conn.execute(
                    'INSERT INTO venta_items (venta_id, producto_id, cantidad, precio_unitario) VALUES (?, ?, ?, ?)',
                    (venta_id, pid, cantidad, pu)
                )

                # Si es un producto real del almacén, baja stock (usar GREATEST en Postgres)
                if with_db:
                    conn.execute(
                        'UPDATE productos SET stock = GREATEST(0, stock - ?) WHERE id = ?',
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

    # Espejos JSON opcionales
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
    session['ultimo_ticket'] = venta_id
    return redirect(url_for('venta'))

@app.route('/almacen')
@login_required
def almacen():
    codigo = request.args.get('codigo', '')
    return render_template('almacen.html', codigo=codigo)

@app.route('/guardar_producto', methods=['POST'])
@login_required
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
@login_required
def eliminar_producto():
    data = request.get_json() or {}
    codigo = str(data.get('codigo') or '').strip()
    if not codigo:
        return jsonify({'success': False, 'message': 'Falta el código.'}), 400

    cur = None
    try:
        with get_db() as conn:
            conn.execute('BEGIN')
            conn.execute('DELETE FROM venta_items WHERE producto_id = ?', (codigo,))
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
@login_required
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
@login_required
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
@login_required
def historial():
    return render_template('historial.html')

@app.route('/usuarios')
@login_required
def usuarios():
    return render_template('usuarios.html')

# ====== LOGIN/LOGOUT (modificado) ======
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    u = (request.form.get('username') or '').strip()
    p = request.form.get('password') or ''
    error = None
    try:
        with get_db() as conn:
            row = conn.execute("SELECT id, hash FROM usuarios WHERE username=? AND activo=TRUE", (u,)).fetchone()
            if not row or not check_password_hash(row["hash"], p):
                error = "Usuario o contraseña inválidos"
            else:
                conn.execute("UPDATE usuarios SET ultimo_acceso=now() WHERE id=?", (row["id"],))
                login_user(User(row["id"], u), remember=False)
                return redirect(url_for('panel'))
    except Exception as e:
        error = f"Error de autenticación: {e}"
    return render_template('login.html', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('login'))
# ======================================

@app.route('/proveedores')
@login_required
def proveedores_view():
    return render_template('proveedores.html')

@app.route('/api/proveedores')
@login_required
def api_proveedores():
    return jsonify(proveedores_listar())

@app.route('/guardar_proveedor', methods=['POST'])
@login_required
def guardar_proveedor():
    data = request.get_json() or {}
    pid = proveedores_guardar(data)
    return jsonify({'success': True, 'id': pid})

@app.route('/eliminar_proveedor', methods=['POST'])
@login_required
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
@login_required
def panel():
    return render_template('admin_datos.html')

@app.get('/api/ventas')
@login_required
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
@login_required
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
@login_required
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
# ===== PROBE DE DIAGNÓSTICO (tolerante a Postgres/SQLite) =====
@app.get('/__probe')
def __probe():
    info = {
        'env_TENANT_SCHEMA': TENANT_SCHEMA,
        'g_tenant_schema': getattr(g, 'tenant_schema', None),
        'db_url_kind': ('postgres' if 'postgresql://' in os.environ.get('DATABASE_URL','') else 'unknown')
    }
    try:
        with get_db() as conn:
            # 1) intenta leer el search_path (Postgres)
            try:
                row = conn.execute("select current_setting('search_path') as sp").fetchone()
                if row and ('sp' in row or 'search_path' in row):
                    info['search_path'] = row.get('sp') or row.get('search_path')
            except Exception as e:
                info['search_path_error'] = str(e)

            # 2) intenta leer algunos productos
            try:
                rows = conn.execute(
                    'select id, nombre, precio, stock, categoria from productos order by id limit 10'
                ).fetchall()
                info['rows'] = rows
            except Exception as e:
                info['rows_error'] = str(e)
    except Exception as e:
        info['conn_error'] = str(e)

    return jsonify(info)
# ===== FIN PROBE =====

if __name__ == '__main__':
    # En producción (Render) usa gunicorn + gevent-websocket (ver Start Command).
    socketio.run(app, debug=True)
