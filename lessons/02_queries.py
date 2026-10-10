# Lección 02 - Consultas avanzadas e índices.
from lab.client import connect
from lab.show import step, table, title

conn = connect()


def count(where, params=()):
    return conn.execute(f"SELECT count(*) AS n FROM employees WHERE {where}", params).fetchone()["n"]


def explain(sql):
    plan = conn.execute(f"EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) {sql}").fetchall()
    print("\n".join("    " + row["QUERY PLAN"] for row in plan))


title("02 - Consultas avanzadas")

step("Comparación y lógicos: salario entre 8 y 12 millones, en Bogotá o Medellín, activos")
print(count("salary BETWEEN 8000000 AND 12000000 AND city IN ('Bogotá', 'Medellín') AND active"), "empleados")

step("OR: de Finanzas o con salario mayor a 17 millones")
print(count("department = 'Finanzas' OR salary > 17000000"), "empleados")

step("Arrays: skills contiene 'postgresql' / contiene todas {python, aws} / tiene exactamente 1 skill")
print("postgresql:", count("'postgresql' = ANY(skills)"))
print("python y aws:", count("skills @> ARRAY['python', 'aws']"))
print("una sola skill:", count("cardinality(skills) = 1"))

step("ILIKE: nombres que empiezan por 'Val' (sin distinguir mayúsculas)")
print([
    r["name"] for r in conn.execute("SELECT DISTINCT name FROM employees WHERE name ILIKE 'val%' "
                                    "ORDER BY name LIMIT 5")
])

step("Fechas: contratados en el último año, ordenados por fecha")
table(
    conn.execute("""
    SELECT name, department, hired_at::date AS hired_at FROM employees
    WHERE hired_at >= (SELECT max(hired_at) FROM employees) - interval '1 year'
    ORDER BY hired_at DESC LIMIT 5
"""))

step("DISTINCT: ciudades y cargos de Ingeniería")
print([r["city"] for r in conn.execute("SELECT DISTINCT city FROM employees ORDER BY city")])
print([
    r["title"] for r in conn.execute("SELECT DISTINCT title FROM employees WHERE department = 'Ingeniería' "
                                     "ORDER BY title")
])

step("Paginación con OFFSET: página 3 de 5 filas (la base igual recorre y descarta las 10 primeras)")
print([
    r["employee_id"] for r in conn.execute("SELECT employee_id FROM employees ORDER BY employee_id "
                                           "LIMIT 5 OFFSET 10")
])

step("Paginación por clave (keyset): continuar desde el último employee_id visto, usando el índice")
print([
    r["employee_id"] for r in conn.execute("SELECT employee_id FROM employees WHERE employee_id > 'E0015' "
                                           "ORDER BY employee_id LIMIT 5")
])

step("La misma consulta sin índice y con índice (EXPLAIN ANALYZE)")
conn.execute("DROP INDEX IF EXISTS employees_salary_idx")
query = "SELECT name FROM employees WHERE salary BETWEEN 17500000 AND 18000000"
print("Sin índice en salary:")
explain(query)
conn.execute("CREATE INDEX employees_salary_idx ON employees (salary)")
conn.execute("ANALYZE employees")
print("Con índice en salary:")
explain(query)
print("Seq Scan lee la tabla completa y descarta las filas que no cumplen ('Rows Removed by Filter').")
print("Con índice, el plan va directo a las filas que coinciden (Index Scan / Bitmap Index Scan).")

step("Índice GIN: acelera las búsquedas dentro de arrays (@>)")
conn.execute("CREATE INDEX IF NOT EXISTS employees_skills_idx ON employees USING gin (skills)")
conn.execute("ANALYZE employees")
explain("SELECT name FROM employees WHERE skills @> ARRAY['python', 'aws']")
