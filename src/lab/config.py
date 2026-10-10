import os

# La conexión a la base (host, usuario, clave, base, target_session_attrs) la leen libpq y psycopg de las variables
# PG* de .envs/postgres.env. Aquí solo va lo que libpq no conoce: la lista de nodos para conectarse a cada uno.
NODES = os.getenv("PG_NODES", "pg1,pg2,pg3").split(",")
