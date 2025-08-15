PRAGMA foreign_keys=ON;

-- Productos del almacén
CREATE TABLE IF NOT EXISTS productos (
  id TEXT PRIMARY KEY,
  nombre TEXT NOT NULL,
  precio REAL NOT NULL DEFAULT 0,
  stock INTEGER NOT NULL DEFAULT 0,
  categoria TEXT,
  actualizado_en TEXT,
  extra TEXT
);

CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre);

-- Proveedores
CREATE TABLE IF NOT EXISTS proveedores (
  id TEXT PRIMARY KEY,
  nombre TEXT NOT NULL,
  telefono TEXT,
  email TEXT,
  direccion TEXT,
  extra TEXT
);

CREATE INDEX IF NOT EXISTS idx_proveedores_nombre ON proveedores(nombre);

-- Usuarios
CREATE TABLE IF NOT EXISTS usuarios (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  rol TEXT,
  extra TEXT
);

-- Ventas (historial)
CREATE TABLE IF NOT EXISTS ventas (
  id TEXT PRIMARY KEY,
  fecha TEXT NOT NULL,
  cliente TEXT,
  total REAL NOT NULL DEFAULT 0,
  extra TEXT
);

-- Ítems por venta
CREATE TABLE IF NOT EXISTS venta_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venta_id TEXT NOT NULL,
  producto_id TEXT NOT NULL,
  cantidad INTEGER NOT NULL,
  precio_unitario REAL NOT NULL,
  FOREIGN KEY (venta_id) REFERENCES ventas(id) ON DELETE CASCADE,
  FOREIGN KEY (producto_id) REFERENCES productos(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_venta_items_venta ON venta_items(venta_id);
