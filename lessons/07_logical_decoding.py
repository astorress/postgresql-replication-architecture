# Lección 07 - Decodificación lógica: los cambios de la base como eventos (insert, update, delete).
#
# El WAL de la lección 04 es físico (páginas y bytes). Con wal_level=logical, PostgreSQL puede traducirlo a cambios
# fila por fila. Un slot lógico recuerda hasta dónde se leyó: si el consumidor se detiene, al volver sigue desde ahí
# sin perder eventos. Es la base de la replicación lógica (PUBLICATION / SUBSCRIPTION) y de herramientas de CDC
# (change data capture) como Debezium.
from lab.client import connect
from lab.show import step, table, title

SLOT = "lesson07"
conn = connect()


def changes(function, limit=None):
    rows = conn.execute(f"SELECT lsn, xid, data FROM {function}(%s, NULL, %s, 'skip-empty-xacts', '1')",
                        (SLOT, limit)).fetchall()
    return [{**r, "data": r["data"][:100] + ("..." if len(r["data"]) > 100 else "")} for r in rows]


title("07 - Decodificación lógica")

step("Crear un slot lógico con el plugin test_decoding (traduce cada cambio a texto)")
conn.execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = %s",
             (SLOT, ))
table(
    conn.execute("SELECT slot_name, lsn FROM pg_create_logical_replication_slot(%s, 'test_decoding')",
                 (SLOT, )))
print("Desde este LSN el slot registra todos los cambios de la base, aunque nadie los esté leyendo todavía.")

step("Cambios: INSERT, UPDATE y DELETE en una transacción, y luego un UPDATE aparte")
with conn.transaction():
    conn.execute("INSERT INTO employees (employee_id, name, email, department, salary) "
                 "VALUES ('E9100', 'Temporal', 'temporal@company.com', 'Ventas', 3000000)")
    conn.execute("UPDATE employees SET salary = 3500000 WHERE employee_id = 'E9100'")
    conn.execute("DELETE FROM employees WHERE employee_id = 'E9100'")
conn.execute("UPDATE employees SET salary = salary + 1 WHERE employee_id = 'E0003'")

step("peek: leer los eventos SIN consumirlos (se puede repetir cuantas veces se quiera)")
table(changes("pg_logical_slot_peek_changes"))
print(
    "Cada transacción llega completa (BEGIN ... COMMIT) y solo cuando ya hizo COMMIT: un ROLLBACK no aparece."
)

step("get con upto_nchanges=1: el consumidor procesa la primera transacción y se detiene")
table(changes("pg_logical_slot_get_changes", 1))
print("El corte se hace en el límite de una transacción: nunca se entrega media transacción.")

step("Mientras el consumidor está detenido, ocurren más cambios")
conn.execute("UPDATE employees SET active = NOT active WHERE employee_id = 'E0004'")
conn.execute("UPDATE employees SET active = NOT active WHERE employee_id = 'E0004'")

step("Reanudar: get devuelve desde donde se quedó, sin perder ni repetir eventos")
table(changes("pg_logical_slot_get_changes"))

step("REPLICA IDENTITY: qué se registra de la fila anterior en un UPDATE o DELETE")
conn.execute("ALTER TABLE employees REPLICA IDENTITY FULL")
conn.execute("UPDATE employees SET salary = salary - 1 WHERE employee_id = 'E0003'")
conn.execute("ALTER TABLE employees REPLICA IDENTITY DEFAULT")
table(changes("pg_logical_slot_get_changes"))
print("DEFAULT (lo normal) solo registra la clave primaria de la fila vieja: el DELETE de arriba mostró solo")
print("'id'. FULL registra la fila completa (old-key) a costa de un WAL más grande.")

step("Un slot que nadie consume retiene WAL en el primary indefinidamente: borrarlo al terminar")
table(
    conn.execute("""
    SELECT slot_name, slot_type, coalesce(plugin, '-') AS plugin,
           pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_wal
    FROM pg_replication_slots ORDER BY slot_type, slot_name
"""))
conn.execute("SELECT pg_drop_replication_slot(%s)", (SLOT, ))
print(f"slot {SLOT} borrado.")
print(
    "Un slot lógico vive en el primary. Desde PostgreSQL 17 puede sincronizarse a los standby (failover => true"
)
print("y sync_replication_slots = on) para que el consumidor siga leyendo después de un failover.")
