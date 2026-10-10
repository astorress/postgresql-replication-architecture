# Lección 06 - Failover manual: un standby pasa a ser el primary sin detener la aplicación.
#
# PostgreSQL no elige un nuevo primary por sí solo (un replica set de MongoDB sí). Aquí se hace a mano lo que
# automatizan herramientas como Patroni o repmgr:
#   1. Aislar (fencing) el primary actual: solo lectura y sin conexiones, para que nadie más escriba en él
#   2. Esperar a que el standby elegido haya aplicado todo el WAL del primary: cero pérdida de datos
#   3. Promoverlo con pg_promote(): sale del modo recuperación y abre un nuevo timeline
#   4. Apuntar los demás standby al nuevo primary
# Mientras tanto un hilo escribe sin parar. Con PGHOST=pg1,pg2,pg3 y target_session_attrs=read-write, libpq
# encuentra al nuevo primary en cuanto la aplicación reconecta.
import re
import sys
import threading
import time

import psycopg
from psycopg import sql

from lab.client import connect, connect_node, node_name, standbys, topology, wait_for_standbys
from lab.config import NODES
from lab.show import step, table, title

conn = connect()
old_primary = node_name(conn)
replicas = standbys(conn)
if not replicas:
    sys.exit(f"{old_primary} no tiene standby conectados: no hay a quién promover.")

conn.execute("DROP TABLE IF EXISTS failover_log")
conn.execute("""
    CREATE TABLE failover_log (
        id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        node text NOT NULL DEFAULT current_setting('cluster_name'),
        at   timestamptz NOT NULL DEFAULT clock_timestamp()
    )
""")
wait_for_standbys(conn)
stop = threading.Event()
ticks = []


def writer():
    writer_conn, node = None, "-"
    second, ok, failed = int(time.time()), 0, 0
    while not stop.is_set():
        try:
            if writer_conn is None:
                writer_conn = connect(connect_timeout=1)
                node = writer_conn.info.host
            writer_conn.execute("INSERT INTO failover_log DEFAULT VALUES")
            ok += 1
        except psycopg.Error:
            failed += 1
            if writer_conn is not None:
                writer_conn.close()
            writer_conn, node = None, "-"
            time.sleep(0.1)
        now = int(time.time())
        if now != second:
            ticks.append({
                "second": len(ticks) + 1,
                "primary": node,
                "writes_ok": ok,
                "writes_failed": failed
            })
            second, ok, failed = now, 0, 0
        time.sleep(0.01)
    if writer_conn is not None:
        writer_conn.close()


def lsn_behind(node, lsn):
    with connect_node(node) as c:
        return c.execute("SELECT pg_wal_lsn_diff(%s::pg_lsn, pg_last_wal_replay_lsn())::bigint AS behind",
                         (lsn, )).fetchone()["behind"]


title("06 - Failover")

step(f"PRIMARY actual: {old_primary} | STANDBY: {replicas}. Escribiendo sin parar durante 12 s...")
started = time.monotonic()
thread = threading.Thread(target=writer)
thread.start()
time.sleep(3)

step(f"1. Segundo 3 - fencing: {old_primary} pasa a solo lectura y se cierran sus conexiones")
conn.execute("ALTER SYSTEM SET default_transaction_read_only = on")
conn.execute("SELECT pg_reload_conf()")
time.sleep(0.2)
conn.execute("""
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()
""")
final_lsn = conn.execute("SELECT pg_current_wal_lsn() AS lsn").fetchone()["lsn"]
print(f"Última posición del WAL en {old_primary}: {final_lsn}")

step("2. Elegir el standby más adelantado y esperar a que aplique todo ese WAL")
progress = [{"standby": r, "bytes_behind": lsn_behind(r, final_lsn)} for r in replicas]
table(progress)
candidate = min(progress, key=lambda p: p["bytes_behind"])["standby"]
while lsn_behind(candidate, final_lsn) > 0:
    time.sleep(0.05)
print(
    f"{candidate} ya aplicó todo el WAL de {old_primary}: promoverlo no pierde ninguna escritura confirmada.")

step(f"3. Promover {candidate}: SELECT pg_promote()")
new_conn = connect_node(candidate)
new_conn.execute("SELECT pg_promote(wait => true)")
state = new_conn.execute("""
    SELECT pg_is_in_recovery() AS standby, pg_walfile_name(pg_current_wal_lsn()) AS wal_file
""").fetchone()
print(f"{candidate}: en recuperación = {state['standby']}. Archivo WAL actual: {state['wal_file']}")
print("Los primeros 8 dígitos del archivo son el timeline: aumenta en 1 con cada promoción. Así PostgreSQL")
print("distingue la historia nueva de la del primary antiguo, aunque compartan el mismo pasado.")

step(f"4. Los demás standby pasan a recibir el WAL de {candidate}")
for other in (r for r in replicas if r != candidate):
    slot = f"{other}_slot"
    new_conn.execute(
        """
        SELECT pg_create_physical_replication_slot(%s, immediately_reserve => true)
        WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = %s)
    """, (slot, slot))
    with connect_node(other) as c:
        conninfo = re.sub(r"host=\S+", f"host={candidate}",
                          c.execute("SHOW primary_conninfo").fetchone()["primary_conninfo"])
        c.execute(sql.SQL("ALTER SYSTEM SET primary_conninfo = {}").format(sql.Literal(conninfo)))
        c.execute(sql.SQL("ALTER SYSTEM SET primary_slot_name = {}").format(sql.Literal(slot)))
        c.execute("SELECT pg_reload_conf()")
    print(f"{other}: primary_conninfo -> host={candidate}, slot {slot}")

time.sleep(max(0, 12 - (time.monotonic() - started)))
stop.set()
thread.join()

step("Lo que vio la aplicación, segundo a segundo")
table(ticks)
print(
    f"Escrituras fallidas: {sum(t['writes_failed'] for t in ticks)}, todas durante la ventana entre el fencing y la"
)
print(
    "promoción: ningún nodo aceptaba escrituras. En producción ese tiempo lo fija la herramienta de failover")
print("(Patroni espera ~30 s por defecto antes de dar por muerto al primary).")

step(f"Filas de failover_log en el nuevo primary ({candidate}), por nodo que las escribió")
time.sleep(1)  # dar tiempo a que los standby reconectados se pongan al día
table(new_conn.execute("SELECT node, count(*) AS rows FROM failover_log GROUP BY node ORDER BY min(id)"))
print(f"Las filas escritas en {old_primary} antes del fencing están todas en {candidate}: no se perdió nada.")
new_conn.execute("DROP TABLE failover_log")
new_conn.close()

step("Topología final")
table(topology())
container = f"PgNode{NODES.index(old_primary) + 1}"
print(f"{old_primary} sigue vivo pero aislado: no es standby de nadie. Para reincorporarlo como standby de "
      f"{candidate}")
print("(pg_rewind), desde .docker/ en el host:")
print(f"  docker stop {container}")
print("  docker compose -f docker-compose.yml -f replication.yml run --rm --no-deps \\")
print(f"    --entrypoint /replication/rejoin.sh {old_primary} {candidate}")
print(f"  docker start {container}")
