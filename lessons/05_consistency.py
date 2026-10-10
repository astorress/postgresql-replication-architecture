# Lección 05 - Replicación síncrona, synchronous_commit y a qué nodo va cada conexión.
#
#   synchronous_standby_names -> qué standby deben confirmar un COMMIT (vacío = replicación asíncrona)
#   synchronous_commit        -> hasta qué punto se espera esa confirmación, por sesión o por transacción
#   target_session_attrs      -> a qué tipo de nodo se conecta la aplicación (primary, standby...)
import atexit
import threading
import time

from psycopg import sql

from lab.client import connect, connect_node, node_name, standbys
from lab.config import NODES
from lab.show import step, table, title

conn = connect()
conn.add_notice_handler(lambda notice: print(f"  {notice.severity}: {notice.message_primary}"))
primary = node_name(conn)
replicas = standbys(conn)
conn.execute("CREATE TABLE IF NOT EXISTS consistency_check (id int PRIMARY KEY, counter int)")
conn.execute("INSERT INTO consistency_check VALUES (1, 0) ON CONFLICT DO NOTHING")


def set_sync_standbys(value):
    conn.execute(sql.SQL("ALTER SYSTEM SET synchronous_standby_names = {}").format(sql.Literal(value)))
    conn.execute("SELECT pg_reload_conf()")
    expected = "async" if not value else None
    for _ in range(100):  # la recarga de configuración es asíncrona: esperar a que se refleje
        states = {r["sync_state"] for r in conn.execute("SELECT sync_state FROM pg_stat_replication")}
        if conn.execute("SHOW synchronous_standby_names").fetchone()["synchronous_standby_names"] == value \
                and (expected is None and "async" not in states or states == {expected}):
            return
        time.sleep(0.05)


def replication_state():
    return conn.execute("""
        SELECT application_name AS standby, sync_state, sync_priority
        FROM pg_stat_replication ORDER BY application_name
    """)


def ms_per_commit(level, commits=100):
    conn.execute(sql.SQL("SET synchronous_commit = {}").format(sql.Identifier(level)))
    start = time.perf_counter()
    for i in range(commits):
        conn.execute("UPDATE consistency_check SET counter = %s WHERE id = 1", (i, ))
    return (time.perf_counter() - start) * 1000 / commits


# Si la lección se interrumpe con standby síncronos configurados, las escrituras podrían quedar bloqueadas
@atexit.register
def restore_async():
    with connect() as c:
        c.execute("ALTER SYSTEM RESET synchronous_standby_names")
        c.execute("SELECT pg_reload_conf()")


title("05 - Consistencia")
print(f"PRIMARY: {primary} | STANDBY: {replicas}")

step("Por defecto la replicación es ASÍNCRONA: el COMMIT no espera a ningún standby")
set_sync_standbys("")
table(replication_state())
print("Si el primary se cae justo después de un COMMIT, ese cambio puede no haber llegado a ningún standby.")

step(f"Replicación síncrona por quórum: synchronous_standby_names = 'ANY 1 ({', '.join(NODES)})'")
set_sync_standbys(f"ANY 1 ({', '.join(NODES)})")
table(replication_state())
print("Cada COMMIT espera a que al menos 1 standby (cualquiera) lo confirme: con el primary, 2 de 3 copias.")
print("El propio primary puede estar en la lista: se ignora. Así la configuración sirve tras un failover.")

step("synchronous_commit: tiempo promedio de 100 COMMIT con cada nivel")
table([{
    "synchronous_commit": level,
    "espera a": waits,
    "ms por commit": ms_per_commit(level)
} for level, waits in (
    ("off", "nada (ni siquiera al disco local)"),
    ("local", "disco local del primary"),
    ("remote_write", "standby lo recibió (en memoria)"),
    ("on", "standby lo guardó en disco"),
    ("remote_apply", "standby lo aplicó (visible)"),
)])
print("'off' puede perder los últimos commits si el primary se cae, pero nunca corrompe la base.")
print("'on' es el valor por defecto: con standby síncronos espera su disco; sin ellos equivale a 'local'.")
print(
    "Aquí todo corre en un mismo equipo y las diferencias son de décimas de ms; entre datacenters son de ms.")

step(f"Todos los standby: 'ANY {len(replicas)} (...)'")
set_sync_standbys(f"ANY {len(replicas)} ({', '.join(NODES)})")
table([{
    "synchronous_commit": level,
    "ms por commit": ms_per_commit(level)
} for level in ("on", "remote_apply")])

step(f"Pedir más standby de los que hay: 'ANY {len(replicas) + 1}' con {len(replicas)} standby")
set_sync_standbys(f"ANY {len(replicas) + 1} ({', '.join(NODES)})")
conn.execute("SET synchronous_commit = on")
threading.Timer(3, conn.cancel_safe).start()  # otro hilo cancela la espera a los 3 s (como Ctrl+C en psql)
start = time.perf_counter()
conn.execute("UPDATE consistency_check SET counter = -1 WHERE id = 1")
print(f"El COMMIT quedó esperando {time.perf_counter() - start:.1f} s, hasta que se canceló la espera.")
print("counter en el primary:", conn.execute("SELECT counter FROM consistency_check").fetchone()["counter"])
print(
    "¡El cambio quedó guardado en el primary! Cancelar solo deja de esperar a los standby: el commit local ya"
)
print(
    "había ocurrido. Ni statement_timeout corta esta espera: si faltan standby síncronos, todas las escrituras"
)
print("se bloquean hasta que vuelvan o se cambie synchronous_standby_names.")

step(f"Leer justo después de escribir, desde un STANDBY ({replicas[0]}): 300 escrituras con cada nivel")
set_sync_standbys(f"ANY {len(replicas)} ({', '.join(NODES)})")
with connect_node(replicas[0]) as standby:
    rows = []
    for level in ("local", "on", "remote_apply"):
        conn.execute(sql.SQL("SET synchronous_commit = {}").format(sql.Identifier(level)))
        stale = 0
        for i in range(300):
            conn.execute("UPDATE consistency_check SET counter = %s WHERE id = 1", (i, ))
            stale += standby.execute("SELECT counter FROM consistency_check").fetchone()["counter"] != i
        rows.append({"synchronous_commit": level, "lecturas desactualizadas": stale})
    table(rows)
print("Solo 'remote_apply' garantiza que el standby ya muestra el cambio. 'on' asegura que está en su disco,")
print("aunque quizá no aplicado todavía (en este equipo aplica casi al instante; con carga o red lenta, no).")

step("target_session_attrs: a qué nodo llega cada conexión (20 conexiones, hosts en orden aleatorio)")
rows = []
for attrs in ("read-write", "standby", "prefer-standby", "any"):
    served_by = set()
    for _ in range(20):
        with connect(target_session_attrs=attrs, load_balance_hosts="random") as c:
            served_by.add(node_name(c))
    rows.append({"target_session_attrs": attrs, "nodos": sorted(served_by)})
table(rows)
print("Enviar las lecturas a los standby reparte la carga, pero pueden estar unos milisegundos atrás.")

step("Volver a replicación asíncrona")
set_sync_standbys("")
table(replication_state())
