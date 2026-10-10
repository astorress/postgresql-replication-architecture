# Lección 03 - Agregaciones: GROUP BY, funciones de ventana y una vista materializada.
#
# SQL se escribe en un orden pero se ejecuta en otro: FROM -> WHERE -> GROUP BY -> HAVING -> SELECT -> ORDER BY.
from lab.client import connect, connect_node, standbys, wait_for_standbys
from lab.show import step, table, title

conn = connect()

title("03 - Agregaciones")

step("GROUP BY: salario promedio, mínimo y máximo por departamento (solo activos)")
table(
    conn.execute("""
    SELECT department, count(*) AS employees, round(avg(salary))::int AS avg_salary,
           min(salary) AS min_salary, max(salary) AS max_salary
    FROM employees WHERE active
    GROUP BY department ORDER BY avg_salary DESC
"""))

step("FILTER: varios conteos condicionales en una sola pasada")
table(
    conn.execute("""
    SELECT city,
           count(*) AS total,
           count(*) FILTER (WHERE NOT active) AS inactive,
           count(*) FILTER (WHERE department = 'Ingeniería') AS engineering
    FROM employees GROUP BY city ORDER BY total DESC
"""))

step("Contrataciones por año (HAVING filtra grupos, WHERE filtra filas)")
table(
    conn.execute("""
    SELECT extract(year FROM hired_at)::int AS year, count(*) AS hires
    FROM employees GROUP BY year HAVING count(*) > 50 ORDER BY year
"""))

step("unnest: las skills más comunes (una fila por cada elemento del array)")
table(
    conn.execute("""
    SELECT skill, count(*) AS employees
    FROM employees, unnest(skills) AS skill
    GROUP BY skill ORDER BY employees DESC LIMIT 5
"""))

step("Rangos salariales con width_bucket (4 rangos iguales entre 2 y 20 millones)")
table(
    conn.execute("""
    SELECT width_bucket(salary, 2000000, 20000000, 4) AS bucket,
           min(salary) AS from_salary, max(salary) AS to_salary, count(*) AS employees
    FROM employees GROUP BY bucket ORDER BY bucket
"""))

step("Funciones de ventana: los 2 mejor pagados de cada departamento y su diferencia con el promedio")
table(
    conn.execute("""
    SELECT department, name, salary, rank, salary - dept_avg AS above_avg
    FROM (
        SELECT department, name, salary,
               rank() OVER (PARTITION BY department ORDER BY salary DESC) AS rank,
               round(avg(salary) OVER (PARTITION BY department))::int AS dept_avg
        FROM employees
    ) ranked
    WHERE rank <= 2 ORDER BY department, rank
"""))
print("A diferencia de GROUP BY, una ventana calcula sobre un grupo de filas sin colapsarlas en una sola.")
print("rank() da el mismo puesto a los empates, por eso algunos departamentos muestran más de 2 filas.")

step("Vista materializada: guardar el resultado de un reporte como una tabla")
conn.execute("DROP MATERIALIZED VIEW IF EXISTS department_report")
conn.execute("""
    CREATE MATERIALIZED VIEW department_report AS
    SELECT department, count(*) AS employees, round(avg(salary))::int AS avg_salary, now() AS generated_at
    FROM employees GROUP BY department
""")
conn.execute("REFRESH MATERIALIZED VIEW department_report")  # recalcula cuando cambian los datos
wait_for_standbys(conn)
standby = standbys(conn)[-1]
with connect_node(standby) as standby_conn:
    print(f"Leído desde {standby} (el reporte también se replica):")
    table(
        standby_conn.execute(
            "SELECT department, employees, avg_salary FROM department_report ORDER BY department"))
