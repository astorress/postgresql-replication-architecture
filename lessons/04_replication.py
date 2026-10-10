# Lección 04 - Anatomía de la replicación: roles, slots, WAL y cómo se copian los datos.
import psycopg

from lab.client import connect, connect_node, node_name, standbys, topology, wait_for_standbys
from lab.config import NODES
from lab.show import step, table, title

conn = connect()
primary = node_name(conn)

title("04 - Replicación por streaming")

step("Rol de cada nodo: pg_is_in_recovery() es true en los STANDBY (aplican WAL ajeno sin parar)")
table(topology())

step(f"pg_stat_replication en el PRIMARY ({primary}): un walsender por cada standby conectado")
table(
    conn.execute("""
    SELECT application_name AS standby, client_addr, state, sync_state,
           sent_lsn, replay_lsn,
           pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint AS lag_bytes,
           coalesce(replay_lag::text, '-') AS replay_lag
    FROM pg_stat_replication ORDER BY application_name
"""))
print("LSN (Log Sequence Number): posición en el WAL. sent = enviado, replay = ya aplicado en el standby.")
print("replay_lag queda vacío cuando no hubo escrituras recientes: no hay nada que medir.")

step("Slots de replicación: el PRIMARY guarda el WAL hasta que cada standby lo recibe")
table(
    conn.execute("""
    SELECT slot_name, slot_type, active, restart_lsn, wal_status,
           pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_wal
    FROM pg_replication_slots ORDER BY slot_name
"""))
print("Sin slot, si un standby se desconecta mucho tiempo, el primary recicla el WAL y el standby hay que")
print("clonarlo de nuevo. Con slot, el WAL se retiene (aquí hasta max_slot_wal_keep_size = 1GB).")

standby = standbys(conn)[0]
step(f"pg_stat_wal_receiver en un STANDBY ({standby}): de quién recibe y hasta dónde va")
with connect_node(standby) as standby_conn:
    table(
        standby_conn.execute("""
        SELECT status, sender_host, slot_name, written_lsn, pg_last_wal_replay_lsn() AS replayed_lsn,
               pg_last_xact_replay_timestamp()::time(0) AS last_replayed_commit
        FROM pg_stat_wal_receiver
    """))

step("Escribir en el PRIMARY y leer la misma fila DIRECTAMENTE en cada nodo")
conn.execute(
    "CREATE TABLE IF NOT EXISTS replication_check (id int PRIMARY KEY, note text, written_at timestamptz)")
conn.execute("""
    INSERT INTO replication_check VALUES (1, 'replicado', now())
    ON CONFLICT (id) DO UPDATE SET written_at = EXCLUDED.written_at
""")
wait_for_standbys(conn)
rows = []
for node in NODES:
    try:
        with connect_node(node) as node_conn:
            row = node_conn.execute(
                "SELECT note, written_at::time(3) AS written_at FROM replication_check").fetchone()
            standby_mode = node_conn.execute("SELECT pg_is_in_recovery() AS r").fetchone()["r"]
        rows.append({"node": node, "role": "standby" if standby_mode else "primary", **row})
    except psycopg.OperationalError:
        rows.append({"node": node, "role": "caído"})
table(rows, ["node", "role", "note", "written_at"])

step(f"Un STANDBY es de solo lectura: escribir en {standby} falla")
with connect_node(standby) as standby_conn:
    try:
        standby_conn.execute("UPDATE replication_check SET note = 'x'")
    except psycopg.errors.ReadOnlySqlTransaction as error:
        print("ReadOnlySqlTransaction:", error.diag.message_primary)

step("El WAL (Write-Ahead Log): todo cambio se escribe primero aquí; es lo que viaja a los standby")
conn.execute("CREATE EXTENSION IF NOT EXISTS pg_walinspect")
start = conn.execute("SELECT pg_current_wal_lsn() AS lsn").fetchone()["lsn"]
conn.execute("UPDATE employees SET salary = salary + 1 WHERE employee_id = 'E0001'")
conn.execute("INSERT INTO replication_check VALUES (2, 'temporal', now())")
conn.execute("DELETE FROM replication_check WHERE id = 2")
end = conn.execute("SELECT pg_current_wal_lsn() AS lsn").fetchone()["lsn"]
table(
    conn.execute(
        """
    SELECT start_lsn, resource_manager, record_type, record_length AS bytes,
           coalesce(pg_filenode_relation(0, substring(block_ref FROM 'rel \\d+/\\d+/(\\d+)')::oid)::text, '-')
               AS relation,
           coalesce(substring(block_ref FROM 'blk (\\d+)'), '-') AS page
    FROM pg_get_wal_records_info(%s, %s)
    WHERE resource_manager IN ('Heap', 'Btree', 'Transaction')
""", (start, end)))
print(
    "El WAL es físico: describe qué cambió en qué página de la tabla y de cada índice, no la instrucción SQL."
)
print("Por eso un standby es una copia idéntica, byte a byte, del primary. COMMIT cierra cada transacción.")
print(
    "El UPDATE escribe también en todos los índices porque cambió salary, que tiene índice (lección 02). Si")
print("ninguna columna indexada cambia, PostgreSQL hace un HOT update y solo toca la página de la tabla.")

step("Archivos WAL en disco (pg_wal): segmentos de 16 MB que se reciclan cuando ya no hacen falta")
show_wal = conn.execute("""
    SELECT pg_walfile_name(pg_current_wal_lsn()) AS current_file,
           (SELECT count(*) FROM pg_ls_waldir()) AS files,
           (SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir()) AS total_size
""").fetchone()
table([show_wal])
