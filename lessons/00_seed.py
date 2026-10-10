# Lección 00 - Cargar el conjunto de datos: la tabla `employees` de la base `company`.
#
# La aplicación recibe la lista de nodos (PGHOST=pg1,pg2,pg3) y pide uno que acepte escrituras
# (target_session_attrs=read-write). libpq prueba los nodos en orden y se queda con el PRIMARY; los STANDBY reciben
# los cambios solos, a través del WAL.
import random
from datetime import datetime, timedelta, timezone

import psycopg

from lab.client import connect, connect_node, node_name, wait_for_standbys
from lab.config import NODES
from lab.show import step, table, title

FIRST_NAMES = [
    "Ana", "Luis", "Carla", "Diego", "Elena", "Felipe", "Gina", "Hugo", "Isabel", "Jorge", "Karen", "Mateo",
    "Natalia", "Óscar", "Paula", "Ricardo", "Sofía", "Tomás", "Valentina", "Andrés"
]
LAST_NAMES = [
    "Gómez", "Pérez", "Rodríguez", "Martínez", "López", "García", "Ramírez", "Torres", "Díaz", "Moreno",
    "Vargas", "Rojas", "Castro", "Ortiz", "Silva"
]
DEPARTMENTS = {
    "Ingeniería": (["Desarrollador", "Tech Lead", "Arquitecto",
                    "QA"], ["python", "java", "postgresql", "aws", "docker"]),
    "Ventas": (["Ejecutivo comercial", "Gerente de cuenta"], ["negociación", "crm", "excel"]),
    "Marketing": (["Analista de marketing", "Diseñador"], ["seo", "figma", "redes sociales", "excel"]),
    "Finanzas": (["Contador", "Analista financiero"], ["excel", "sql", "power bi"]),
    "Recursos Humanos": (["Analista de talento", "Generalista RRHH"], ["entrevistas", "excel", "nómina"]),
    "Soporte": (["Agente de soporte", "Coordinador de soporte"], ["atención al cliente", "crm", "sql"]),
}
CITIES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Bucaramanga", "Cartagena"]
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
COLUMNS = [
    "employee_id", "name", "email", "department", "title", "city", "salary", "skills", "hired_at", "active"
]


def employees(count=1000):
    rng = random.Random(42)
    rows = []
    for i in range(1, count + 1):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        department = rng.choice(list(DEPARTMENTS))
        titles, skills = DEPARTMENTS[department]
        rows.append((
            f"E{i:04d}",
            f"{first} {last}",
            f"{first}.{last}.{i}@company.com".lower(),
            department,
            rng.choice(titles),
            rng.choice(CITIES),
            rng.randrange(2_500_000, 18_000_000, 50_000),
            rng.sample(skills, rng.randint(1, len(skills))),
            NOW - timedelta(days=rng.randint(30, 3650)),
            rng.random() > 0.08,
        ))
    return rows


conn = connect()

title("00 - Seed de la tabla employees")
print("Conectado a:", conn.info.host, f"({node_name(conn)}, el PRIMARY)")

step("Borrando la tabla (si existe) y creándola con su esquema")
conn.execute("DROP TABLE IF EXISTS employees CASCADE")
conn.execute("""
    CREATE TABLE employees (
        id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        employee_id text        NOT NULL UNIQUE,
        name        text        NOT NULL,
        email       text        NOT NULL UNIQUE,
        department  text        NOT NULL,
        title       text,
        city        text,
        salary      integer     CHECK (salary > 0),
        skills      text[]      NOT NULL DEFAULT '{}',
        hired_at    timestamptz,
        active      boolean     NOT NULL DEFAULT true
    )
""")

step("COPY: carga masiva de 1.000 filas en un solo flujo (mucho más rápido que 1.000 INSERT)")
with conn.cursor() as cur, cur.copy(f"COPY employees ({', '.join(COLUMNS)}) FROM STDIN") as copy:
    for row in employees():
        copy.write_row(row)
print("insertadas:", conn.execute("SELECT count(*) AS n FROM employees").fetchone()["n"])

step("Índices: email y employee_id ya tienen uno por ser UNIQUE; se agrega department")
conn.execute("CREATE INDEX employees_department_idx ON employees (department)")
conn.execute("ANALYZE employees")  # estadísticas para que el planificador elija bien los planes
table(
    conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'employees' ORDER BY indexname"))

step("Empleados por departamento")
table(
    conn.execute("""
    SELECT department, count(*) AS employees
    FROM employees GROUP BY department ORDER BY employees DESC
"""))

step("Las escrituras fueron al PRIMARY; los STANDBY ya tienen la misma copia")
wait_for_standbys(conn)
rows = []
for node in NODES:
    try:
        with connect_node(node) as node_conn:
            rows.append(
                node_conn.execute(
                    """
                SELECT %s AS node, CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END AS role,
                       (SELECT count(*) FROM employees) AS employees
            """, (node, )).fetchone())
    except psycopg.OperationalError:
        rows.append({"node": node, "role": "caído", "employees": "-"})
table(rows)
print("\nSiguiente: python lessons/01_crud.py")
