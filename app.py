from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'clave_secreta_para_sesiones'

# ------------------------- Ruta base -------------------------
@app.route('/')
def index():
    return redirect(url_for('venta'))

# ------------------------- Ruta: VENTA -------------------------
@app.route('/venta')
def venta():
    if 'carrito' not in session:
        session['carrito'] = []

    carrito = session.get('carrito', [])
    total = sum(item['precio'] for item in carrito)
    total_redondeado = round(total + 0.5)

    return render_template(
        'venta.html',
        carrito=carrito,
        total=total,
        total_redondeado=total_redondeado,
        mensaje=session.pop('mensaje', None)
    )

# --------------------- Ruta: AGREGAR PRODUCTO ---------------------
@app.route('/agregar-producto', methods=['POST'])
def agregar_producto():
    codigo = request.form.get('codigo').strip()

    ruta_json = os.path.join('static', 'productos.json')
    if not os.path.exists(ruta_json):
        productos = {}
    else:
        with open(ruta_json, 'r') as f:
            productos = json.load(f)

    if codigo in productos:
        producto = productos[codigo]
        carrito = session.get('carrito', [])
        carrito.append(producto)
        session['carrito'] = carrito
        return redirect(url_for('venta'))
    else:
        return redirect(url_for('almacen', codigo=codigo))

# --------------------- Ruta: REDONDEAR ---------------------
@app.route('/redondear', methods=['POST'])
def redondear():
    aceptado = request.form.get('aceptado')
    carrito = session.get('carrito', [])
    total = sum(item['precio'] for item in carrito)
    redondeo = round(total + 0.5) - total if aceptado == 'si' else 0
    total_final = round(total + 0.5) if aceptado == 'si' else total

    # Actualizar stock
    ruta_json = os.path.join('static', 'productos.json')
    if os.path.exists(ruta_json):
        with open(ruta_json, 'r') as f:
            productos = json.load(f)
    else:
        productos = {}

    for vendido in carrito:
        for codigo, info in productos.items():
            if info['nombre'] == vendido['nombre']:
                productos[codigo]['cantidad'] = max(0, productos[codigo].get('cantidad', 0) - 1)
                break

    with open(ruta_json, 'w') as f:
        json.dump(productos, f, indent=4)

    # Guardar en historial.json
    historial_path = 'historial.json'
    if os.path.exists(historial_path):
        with open(historial_path, 'r') as f:
            historial = json.load(f)
    else:
        historial = []

    # Agrupar productos repetidos
    resumen = {}
    for item in carrito:
        nombre = item['nombre']
        if nombre in resumen:
            resumen[nombre]['cantidad'] += 1
        else:
            resumen[nombre] = {
                'nombre': nombre,
                'cantidad': 1
            }

    historial.append({
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "hora": datetime.now().strftime("%H:%M"),
        "total": round(total_final, 2),
        "redondeo": round(redondeo, 2),
        "productos": list(resumen.values())
    })

    with open(historial_path, 'w') as f:
        json.dump(historial, f, indent=4)

    session['mensaje'] = (
        f'✅ Venta completada con redondeo de ${redondeo:.2f}.' if aceptado == 'si'
        else '✅ Venta completada sin redondeo.'
    )

    session['carrito'] = []
    return redirect(url_for('venta'))

# ------------------------- Ruta: ALMACÉN -------------------------
@app.route('/almacen')
def almacen():
    codigo = request.args.get('codigo', '')
    return render_template('almacen.html', codigo=codigo)

# --------------------- Ruta: GUARDAR PRODUCTO ---------------------
@app.route('/guardar_producto', methods=['POST'])
def guardar_producto():
    data = request.get_json()
    codigo = data.get('codigo')
    nombre = data.get('nombre')
    precio = float(data.get('precio'))
    cantidad = int(data.get('cantidad'))

    ruta_json = os.path.join('static', 'productos.json')
    productos = {}

    if os.path.exists(ruta_json):
        with open(ruta_json, 'r') as f:
            productos = json.load(f)

    productos[codigo] = {
        "nombre": nombre,
        "precio": precio,
        "cantidad": cantidad
    }

    with open(ruta_json, 'w') as f:
        json.dump(productos, f, indent=4)

    return {'success': True}

# --------------------- Ruta: ELIMINAR PRODUCTO ---------------------
@app.route('/eliminar_producto', methods=['POST'])
def eliminar_producto():
    data = request.get_json()
    codigo = data.get('codigo')

    ruta_json = os.path.join('static', 'productos.json')
    if os.path.exists(ruta_json):
        with open(ruta_json, 'r') as f:
            productos = json.load(f)

        if codigo in productos:
            del productos[codigo]

            with open(ruta_json, 'w') as f:
                json.dump(productos, f, indent=4)

            return {'success': True}
    return {'success': False}

# --------------------- Ruta: API PRODUCTOS (tabla JS) ---------------------
@app.route('/api/productos')
def api_productos():
    ruta_json = os.path.join('static', 'productos.json')
    if not os.path.exists(ruta_json):
        return jsonify({})
    with open(ruta_json, 'r') as f:
        productos = json.load(f)
    return jsonify(productos)

# --------------------- Ruta: API HISTORIAL ---------------------
@app.route('/api/historial')
def api_historial():
    historial_path = 'historial.json'
    if not os.path.exists(historial_path):
        return jsonify([])
    with open(historial_path, 'r') as f:
        historial = json.load(f)
    return jsonify(historial)

# --------------------- Ruta: CENTAVOS ---------------------
@app.route('/centavos')
def centavos():
    historial_path = 'historial.json'
    centavos_list = []
    total_centavos = 0

    if os.path.exists(historial_path):
        with open(historial_path, 'r') as f:
            historial = json.load(f)
        for venta in historial:
            if "redondeo" in venta and venta["redondeo"] > 0:
                centavos_list.append(venta)
                total_centavos += venta["redondeo"]

    return render_template('centavos.html', centavos=centavos_list, total_centavos=round(total_centavos, 2))

# --------------------- NUEVO: eliminar item del carrito (SESSION) ---------------------
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
        return jsonify({"ok": True, "total": round(nuevo_total, 2), "count": len(carrito)})

    return jsonify({"ok": False, "error": "index out of range"}), 400

# --------------------- Vistas adicionales ---------------------
@app.route('/historial')
def historial():
    return render_template('historial.html')

@app.route('/usuarios')
def usuarios():
    return render_template('usuarios.html')

@app.route('/login')
def login():
    return render_template('login.html')

# --------- NUEVO: LOGOUT (para reparar url_for('logout') en login.html) ---------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --------------------- Inicio ---------------------
if __name__ == '__main__':
    app.run(debug=True)
