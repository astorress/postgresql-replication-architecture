#!/bin/bash
# Arranque de cada nodo. Si su directorio de datos está vacío:
#   - el PRIMARY inicial se crea con initdb (lo hace el entrypoint oficial de la imagen, que luego ejecuta initdb/)
#   - los demás se clonan del PRIMARY con pg_basebackup y arrancan como STANDBY (archivo standby.signal)
# Si ya tiene datos, arranca con el rol que tenía (un standby promovido sigue siendo primary).
set -e

if [ ! -s "$PGDATA/PG_VERSION" ] && [ "$NODE_NAME" != "$INITIAL_PRIMARY" ]; then
  echo "[$NODE_NAME] Esperando al primary $INITIAL_PRIMARY"
  until pg_isready -h "$INITIAL_PRIMARY" -U "$POSTGRES_USER" -q; do sleep 1; done

  echo "[$NODE_NAME] Clonando $INITIAL_PRIMARY con pg_basebackup"
  mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"
  gosu postgres env PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup -h "$INITIAL_PRIMARY" -U "$REPLICATION_USER" \
    -D "$PGDATA" -X stream -S "${NODE_NAME}_slot" --checkpoint=fast

  # standby.signal -> arrancar en modo recuperación; primary_conninfo -> de dónde recibir el WAL
  gosu postgres touch "$PGDATA/standby.signal"
  cat >> "$PGDATA/postgresql.auto.conf" <<EOF
primary_conninfo = 'host=$INITIAL_PRIMARY user=$REPLICATION_USER password=$REPLICATION_PASSWORD application_name=$NODE_NAME'
primary_slot_name = '${NODE_NAME}_slot'
EOF
fi

# cluster_name identifica al nodo en pg_stat_replication, en los procesos y en las lecciones.
# wal_level=logical incluye lo necesario para la replicación física y además permite la decodificación lógica (lección 07).
# wal_log_hints y wal_keep_size permiten usar pg_rewind para reincorporar un primary antiguo como standby: necesita
# el WAL del nodo desde el último checkpoint que tenía en común con el nuevo primary.
exec docker-entrypoint.sh postgres \
  -c cluster_name="$NODE_NAME" \
  -c wal_level=logical \
  -c max_wal_senders=10 \
  -c max_replication_slots=10 \
  -c max_slot_wal_keep_size=1GB \
  -c wal_keep_size=256MB \
  -c hot_standby=on \
  -c hot_standby_feedback=on \
  -c wal_log_hints=on \
  -c shared_buffers=128MB
