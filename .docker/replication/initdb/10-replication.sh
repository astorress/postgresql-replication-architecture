#!/bin/bash
# Solo se ejecuta en el PRIMARY inicial, una vez, justo después de initdb (los standby se clonan y no pasan por aquí).
#   - Rol con permiso REPLICATION para que los standby se conecten a recibir WAL
#   - Un slot de replicación física por standby: el primary no borra el WAL que un standby aún no ha recibido
#   - Regla en pg_hba.conf que permite conexiones de replicación desde la red de Docker
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE $REPLICATION_USER WITH REPLICATION LOGIN PASSWORD '$REPLICATION_PASSWORD';
SELECT slot_name FROM unnest(string_to_array('$STANDBYS', ',')) AS node,
                      pg_create_physical_replication_slot(node || '_slot');
SQL

echo "host replication $REPLICATION_USER all scram-sha-256" >> "$PGDATA/pg_hba.conf"
