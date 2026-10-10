import time

import psycopg
from psycopg.rows import dict_row

from lab.config import NODES


# Sin argumentos, libpq usa PGHOST=pg1,pg2,pg3 y PGTARGETSESSIONATTRS=read-write: prueba los nodos en orden y se
# queda con el que acepta escrituras (el PRIMARY). A diferencia de MongoClient, una conexión no sigue al primary si
# este cambia: hay que reconectar.
def connect(conninfo="", **kwargs):
    kwargs.setdefault("autocommit", True)
    kwargs.setdefault("connect_timeout", 3)
    return psycopg.connect(conninfo,
                           application_name="postgresql-replication",
                           row_factory=dict_row,
                           **kwargs)


# Conexión directa a un nodo concreto, sea primary o standby
def connect_node(node, **kwargs):
    return connect(host=node, target_session_attrs="any", **kwargs)


def node_name(conn):
    return conn.execute("SELECT current_setting('cluster_name') AS node").fetchone()["node"]


# Rol de cada nodo: primary, standby, primary bloqueado (solo lectura, lección 06) o caído
def topology():
    rows = []
    for node in NODES:
        try:
            with connect_node(node) as conn:
                state = conn.execute("""
                    SELECT pg_is_in_recovery() AS standby,
                           current_setting('default_transaction_read_only')::bool AS read_only,
                           (SELECT sender_host FROM pg_stat_wal_receiver) AS copies_from,
                           pg_postmaster_start_time()::timestamp(0) AS started_at
                """).fetchone()
            role = "standby" if state[
                "standby"] else "primary (bloqueado)" if state["read_only"] else "primary"
            rows.append({
                "node": node,
                "role": role,
                "copies_from": state["copies_from"] or "-",
                "started_at": state["started_at"]
            })
        except psycopg.OperationalError:
            rows.append({"node": node, "role": "caído", "copies_from": "-", "started_at": "-"})
    return rows


# Standby que están recibiendo WAL de este primary en este momento
def standbys(conn):
    return [
        r["application_name"] for r in conn.execute(
            "SELECT application_name FROM pg_stat_replication WHERE state = 'streaming' ORDER BY application_name"
        )
    ]


# Espera a que todos los standby conectados hayan aplicado el WAL que el primary tiene hasta ahora
def wait_for_standbys(conn, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = conn.execute("""
            SELECT count(*) AS n FROM pg_stat_replication
            WHERE replay_lsn IS NULL OR replay_lsn < pg_current_wal_lsn()
        """).fetchone()["n"]
        if pending == 0:
            return True
        time.sleep(0.05)
    return False
