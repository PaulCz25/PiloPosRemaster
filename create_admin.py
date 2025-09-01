import os, psycopg
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()  # lee .env de la carpeta actual

USERNAME = os.getenv("ADMIN_USER", "admin")
PASSWORD = os.getenv("ADMIN_PASSWORD", "TuClaveFuerte123!")
TENANT   = os.getenv("TENANT_SCHEMA", "tnt_default")

hash_ = generate_password_hash(PASSWORD, method="pbkdf2:sha256", salt_length=16)

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    with conn.cursor() as cur:
        # asegúrate de operar en TU esquema (no en public)
        cur.execute(f'SET search_path TO "{TENANT}", public')
        # por si aún no existe la tabla
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios(
              id bigserial PRIMARY KEY,
              username text UNIQUE NOT NULL,
              hash text NOT NULL,
              activo boolean DEFAULT true,
              ultimo_acceso timestamptz
            )
        """)
        cur.execute("""
            INSERT INTO usuarios(username, hash, activo)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (username) DO UPDATE SET hash=EXCLUDED.hash, activo=TRUE
        """, (USERNAME, hash_))
        conn.commit()

print(f"Usuario creado/actualizado: {USERNAME}  (password: {PASSWORD})")
