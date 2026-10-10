#!/bin/bash
# Reincorpora un nodo DETENIDO como standby de otro primary (por ejemplo, el primary antiguo tras un failover).
#
# Uso, desde .docker/ en el host:
#   docker stop PgNode1
#   docker compose -f docker-compose.yml -f replication.yml run --rm --no-deps --entrypoint /replication/rejoin.sh pg1 pg2
#   docker start PgNode1
#
# pg_rewind compara el historial (timeline) del nodo con el del nuevo primary y copia solo los bloques que
# cambiaron desde que se separaron. Es mucho más rápido que volver a clonar todo con pg_basebackup.
set -e

NEW_PRIMARY=$1
if [ -z "$NEW_PRIMARY" ]; then
  echo "Uso: rejoin.sh <nuevo primary>   (ejemplo: rejoin.sh pg2)"
  exit 1
fi
SOURCE="host=$NEW_PRIMARY user=$POSTGRES_USER password=$POSTGRES_PASSWORD dbname=$POSTGRES_DB"

echo "[$NODE_NAME] Creando el slot ${NODE_NAME}_slot en $NEW_PRIMARY"
psql "$SOURCE" -v ON_ERROR_STOP=1 -tAc "
  SELECT pg_create_physical_replication_slot('${NODE_NAME}_slot')
  WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = '${NODE_NAME}_slot')" >/dev/null

echo "[$NODE_NAME] pg_rewind contra $NEW_PRIMARY"
if ! gosu postgres pg_rewind --target-pgdata="$PGDATA" --source-server="$SOURCE" --progress; then
  # pg_rewind necesita el WAL de este nodo desde el último checkpoint común. Si el nodo siguió funcionando un buen
  # rato después del failover, ya lo recicló: no queda otra que clonarlo completo.
  echo "[$NODE_NAME] pg_rewind no pudo. Clonando $NEW_PRIMARY completo con pg_basebackup"
  rm -rf "${PGDATA:?}"/*
  gosu postgres env PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup -h "$NEW_PRIMARY" -U "$REPLICATION_USER" \
    -D "$PGDATA" -X stream -S "${NODE_NAME}_slot" --checkpoint=fast
fi

# Los slots del nodo cuando era primary ya no sirven; el bloqueo de solo lectura y la conexión anterior tampoco
rm -rf "$PGDATA"/pg_replslot/*
sed -i '/^default_transaction_read_only/d; /^primary_conninfo/d; /^primary_slot_name/d' "$PGDATA/postgresql.auto.conf"
cat >> "$PGDATA/postgresql.auto.conf" <<EOF
primary_conninfo = 'host=$NEW_PRIMARY user=$REPLICATION_USER password=$REPLICATION_PASSWORD application_name=$NODE_NAME'
primary_slot_name = '${NODE_NAME}_slot'
EOF
gosu postgres touch "$PGDATA/standby.signal"

echo "[$NODE_NAME] Listo: al arrancar será standby de $NEW_PRIMARY"
