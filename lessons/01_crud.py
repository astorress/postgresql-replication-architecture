# Lección 01 - CRUD: crear, leer, actualizar y borrar filas con psycopg.
#
# Los valores nunca se pegan en el texto del SQL: se pasan aparte (%s) y psycopg los envía como parámetros.
# Así se evita la inyección SQL y se convierten solos los tipos (list -> text[], datetime -> timestamptz).
from datetime import datetime, timezone

import psycopg

from lab.client import connect
from lab.show import show, step, table, title

conn = connect()
TEST_IDS = ["E9001", "E9002", "E9003", "E9004"]

title("01 - CRUD")

conn.execute("DELETE FROM employees WHERE employee_id = ANY(%s)", (TEST_IDS, ))

step("Create: INSERT ... RETURNING devuelve lo que generó la base (el id)")
row = conn.execute(
    """
    INSERT INTO employees (employee_id, name, email, department, title, city, salary, skills, hired_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id, employee_id
    """,
    ("E9001", "Laura Méndez", "laura.mendez@company.com", "Ingeniería", "Desarrollador", "Bogotá", 7_500_000,
     ["python", "postgresql"], datetime.now(timezone.utc)),
).fetchone()
print(row)

step("Create: executemany con parámetros con nombre")
with conn.cursor() as cur:
    cur.executemany(
        """
        INSERT INTO employees (employee_id, name, email, department, title, city, salary, skills)
        VALUES (%(employee_id)s, %(name)s, %(email)s, %(department)s, %(title)s, %(city)s, %(salary)s, %(skills)s)
        """,
        [
            {
                "employee_id": "E9002",
                "name": "Pedro Ruiz",
                "email": "pedro.ruiz@company.com",
                "department": "Ventas",
                "title": "Ejecutivo comercial",
                "city": "Cali",
                "salary": 4_200_000,
                "skills": ["crm"]
            },
            {
                "employee_id": "E9003",
                "name": "Marta Gil",
                "email": "marta.gil@company.com",
                "department": "Soporte",
                "title": "Agente de soporte",
                "city": "Medellín",
                "salary": 3_100_000,
                "skills": []
            },
        ],
    )

step("Las restricciones protegen los datos: UNIQUE en email, CHECK en salary, NOT NULL en name")
for label, sql in (
    ("email repetido", "INSERT INTO employees (employee_id, name, email, department) "
     "VALUES ('E9999', 'X', 'laura.mendez@company.com', 'Ventas')"),
    ("salario negativo", "UPDATE employees SET salary = -1 WHERE employee_id = 'E9001'"),
    ("sin nombre",
     "INSERT INTO employees (employee_id, email, department) VALUES ('E9999', 'x@x.com', 'Ventas')"),
):
    try:
        conn.execute(sql)
    except psycopg.errors.IntegrityError as error:
        print(f"{label:16} -> {type(error).__name__}: {error.diag.message_primary}")

step("Read: fetchone con columnas elegidas")
show(conn.execute("SELECT name, title, salary FROM employees WHERE employee_id = %s", ("E9001", )).fetchone())

step("Read: filtro, orden y límite")
table(
    conn.execute(
        """
    SELECT name, salary FROM employees
    WHERE department = %s AND city = %s
    ORDER BY salary DESC LIMIT 3
""", ("Ingeniería", "Bogotá")))

step("Update: SET con aritmética y array_append")
cur = conn.execute("""
    UPDATE employees
    SET title = 'Tech Lead', salary = salary + 1000000, skills = array_append(skills, 'aws')
    WHERE employee_id = 'E9001'
""")
print("filas actualizadas:", cur.rowcount)

step("Update ... RETURNING devuelve la fila ya actualizada (agrega 'excel' solo si no está)")
show(
    conn.execute("""
    UPDATE employees
    SET skills = CASE WHEN 'excel' = ANY(skills) THEN skills ELSE array_append(skills, 'excel') END
    WHERE employee_id = 'E9002'
    RETURNING name, skills
""").fetchone())

step("Update masivo: aumento de 200.000 a todo Soporte")
print("filas actualizadas:",
      conn.execute("UPDATE employees SET salary = salary + 200000 "
                   "WHERE department = 'Soporte'").rowcount)

step("Upsert: INSERT ... ON CONFLICT DO UPDATE - inserta si no existe, actualiza si existe")
for name in ("Nuevo Ingreso", "Nuevo Ingreso (actualizado)"):
    show(
        conn.execute(
            """
        INSERT INTO employees (employee_id, name, email, department)
        VALUES ('E9004', %s, 'nuevo@company.com', 'Ventas')
        ON CONFLICT (employee_id) DO UPDATE SET name = EXCLUDED.name
        RETURNING employee_id, name, (xmax = 0) AS inserted
    """, (name, )).fetchone())

step("Transacción: o se aplican todos los cambios o ninguno")
try:
    with conn.transaction():
        conn.execute("UPDATE employees SET salary = salary - 500000 WHERE employee_id = 'E9001'")
        conn.execute("UPDATE employees SET salary = salary + 500000 WHERE employee_id = 'E9999'")  # no existe
        raise RuntimeError("el segundo empleado no existe: se revierte también el primer UPDATE")
except RuntimeError as error:
    print(error)
print("salario de E9001 sin cambios:",
      conn.execute("SELECT salary FROM employees WHERE employee_id = 'E9001'").fetchone()["salary"])

step("Delete: DELETE con filtro")
print("borradas:", conn.execute("DELETE FROM employees WHERE employee_id = ANY(%s)", (TEST_IDS, )).rowcount)
