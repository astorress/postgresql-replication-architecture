#!/bin/bash

COLOR_GREEN=$(tput setaf 2)
COLOR_BLUE=$(tput setaf 4)
COLOR_MAGENTA=$(tput setaf 5)
COLOR_DEFAULT=$(tput sgr0)

EXEC_PATH=$(dirname $(readlink -f "$0"))
HOME_PATH=$EXEC_PATH/..
SRC_PATH=$HOME_PATH/src
PYTHON_DIST_PACKAGES_PATH=/usr/local/lib/python3.13/site-packages
STEP=1
STEP_COUNT=3

# Generate home path file and add it to Python search path
echo "${COLOR_MAGENTA}${STEP}/$STEP_COUNT:${COLOR_GREEN} Generating ${COLOR_BLUE}postgresql-replication.pth${COLOR_GREEN} file and adding it to Python search path.${COLOR_DEFAULT}"
echo "$(readlink -f $SRC_PATH)" | sudo tee $PYTHON_DIST_PACKAGES_PATH/postgresql-replication.pth
let STEP++

# Install Python requirements.txt once. If requirements are satisfied, installation is skipped
pip freeze | sort > /tmp/installed.txt
sed -E 's/#.*//; s/^[[:space:]]+//; s/[[:space:]]+$//' "$HOME_PATH/requirements.txt" \
  | sed '/^$/d' \
  | sort > /tmp/required.txt
MISSING_LINES=$(comm -23 /tmp/required.txt /tmp/installed.txt | wc -l)
if [ "$MISSING_LINES" -eq 0 ]; then
  echo "${COLOR_MAGENTA}${STEP}/$STEP_COUNT:${COLOR_GREEN} Python requirements already satisfied. Skipping installation.${COLOR_DEFAULT}"
else
  echo "${COLOR_MAGENTA}${STEP}/$STEP_COUNT:${COLOR_GREEN} Installing Python requirements.txt dependencies...${COLOR_DEFAULT}"
  sudo pip install --upgrade --upgrade-strategy only-if-needed -r "$HOME_PATH/requirements.txt"
fi
rm /tmp/installed.txt /tmp/required.txt
let STEP++

# Wait until a node accepts writes (PGHOST lists every node and PGTARGETSESSIONATTRS=read-write picks the PRIMARY)
echo "${COLOR_MAGENTA}${STEP}/$STEP_COUNT:${COLOR_GREEN} Waiting for a PRIMARY in ${COLOR_BLUE}$PGHOST${COLOR_GREEN}...${COLOR_DEFAULT}"
until psql -tAc "SELECT 1" >/dev/null 2>&1; do
  sleep 2
done
for NODE in ${PG_NODES//,/ }; do
  ROLE=$(psql "host=$NODE target_session_attrs=any connect_timeout=3" -tAc \
    "SELECT CASE WHEN pg_is_in_recovery() THEN 'STANDBY' ELSE 'PRIMARY' END" 2>/dev/null || echo "DOWN")
  echo "  $NODE -> $ROLE"
done

echo "${COLOR_GREEN}Ready. Next step: ${COLOR_BLUE}python lessons/00_seed.py${COLOR_DEFAULT}"
