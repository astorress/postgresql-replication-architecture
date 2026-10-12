# PostgreSQL Streaming Replication

Proyecto para entender cómo funciona la **replicación por streaming de PostgreSQL**: tres nodos con la misma copia de
los datos (un PRIMARY y dos STANDBY), failover manual sin detener la aplicación y una aplicación en Python
(`psycopg`) que escribe, lee, consulta y agrega datos sobre una sola tabla (`employees`).

A lo largo de las lecciones se compara con el modelo de replica set de MongoDB para dejar claro qué resuelve
PostgreSQL de forma nativa y qué delega en herramientas externas.

![Arquitectura de la replicación](docs/architecture.png)

> Diagrama editable: [`docs/architecture.drawio`](docs/architecture.drawio).

## 1. Conceptos de PostgreSQL

### ¿Qué es PostgreSQL?

PostgreSQL es una base de datos **relacional** de código abierto. Guarda los datos en **tablas** con columnas de
tipo fijo, garantiza transacciones **ACID** y se consulta con **SQL**. También admite tipos ricos como arrays, JSON
(`jsonb`), rangos y tipos geométricos.

| Concepto | Qué es | En este proyecto |
| --- | --- | --- |
| **Clúster** | En PostgreSQL, un *cluster* es **una instancia** (`postgres`) con su directorio de datos y todas sus bases. No es un grupo de servidores. | Cada nodo es un clúster |
| **Nodo** | Un servidor `postgres` que forma parte de la replicación. Aquí, cada nodo es un contenedor. | `pg1`, `pg2`, `pg3` |
| **Primary / Standby** | El primary acepta escrituras; los standby reciben su WAL y lo aplican. Son de solo lectura. | `pg1` / `pg2`, `pg3` |
| **Database** | Contenedor lógico de esquemas y tablas. Una instancia puede tener muchas. | `company` |
| **Schema** | Espacio de nombres dentro de una base. | `public` |
| **Tabla** | Filas con columnas de tipo fijo, definidas antes de insertar. | `employees` |
| **Fila** | Un registro. Se identifica por su **clave primaria**. | Un empleado |
| **Restricción** | Regla que la base hace cumplir: `PRIMARY KEY`, `UNIQUE`, `NOT NULL`, `CHECK`, `FOREIGN KEY`. | `email` único, `salary > 0` |
| **Índice** | Estructura ordenada (B-tree por defecto, GIN para arrays) que evita recorrer toda la tabla. | `email`, `department`, `salary`, `skills` |

La tabla del proyecto:

```sql
CREATE TABLE employees (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- lo genera la base
    employee_id text        NOT NULL UNIQUE,                       -- E0001
    name        text        NOT NULL,
    email       text        NOT NULL UNIQUE,
    department  text        NOT NULL,
    title       text,
    city        text,
    salary      integer     CHECK (salary > 0),
    skills      text[]      NOT NULL DEFAULT '{}',                 -- array: {python,postgresql,aws}
    hired_at    timestamptz,                                       -- fecha con zona horaria (se guarda en UTC)
    active      boolean     NOT NULL DEFAULT true
);
```

### Programas de PostgreSQL

| Programa | Qué hace |
| --- | --- |
| **`postgres`** | El **servidor**: guarda los datos, ejecuta las consultas y replica. Lanza un proceso por conexión y procesos auxiliares (`checkpointer`, `walwriter`, `walsender`, `walreceiver`...). |
| **`psql`** | El **cliente interactivo**: `\dt` (tablas), `\d employees` (estructura), `\x` (vista vertical). |
| **`pg_basebackup`** | Copia completa de un servidor en marcha. Con ella nace cada standby. |
| **`pg_rewind`** | Sincroniza un primary antiguo con el nuevo copiando solo los bloques que cambiaron, para reincorporarlo como standby. |
| **`pg_ctl`** | Arranca, detiene y promueve un servidor (`pg_ctl promote`). |
| **libpq / `psycopg`** | La biblioteca de cliente de PostgreSQL y el driver de Python que la usa. libpq admite varios hosts y elige el adecuado con `target_session_attrs`. |
| `pg_dump` / `pg_restore` | Respaldo y restauración lógicos (SQL o formato propio). |

### Cómo funciona la base de datos por dentro

- **Páginas y shared buffers**:
  - Las tablas e índices se guardan en páginas de 8 kB.
  - Las más usadas viven en memoria (`shared_buffers`).
- **MVCC**:
  - Un `UPDATE` no sobrescribe la fila: crea una versión nueva y marca la anterior como muerta.
  - Así, los lectores nunca bloquean a los escritores ni al revés.
  - `VACUUM` (automático: *autovacuum*) recupera el espacio de las versiones muertas.
- **WAL (Write-Ahead Log)**:
  - Todo cambio se escribe primero en el WAL y luego, en cada *checkpoint*, en los archivos de datos.
  - Si el proceso se cae, al reiniciar reproduce el WAL y no pierde nada confirmado.
  - **Ese mismo WAL es lo que se envía a los standby.**
- **Índices y planificador**:
  - El planificador estima costos con estadísticas (`ANALYZE`) y elige el plan.
  - `EXPLAIN ANALYZE` muestra el plan elegido: `Seq Scan` recorre la tabla completa; `Index Scan` o `Bitmap Index Scan` usan un índice.

## 2. Replicación

```text
                     escrituras
  aplicación ─────────────────────► PRIMARY (pg1)
       │                               │  WAL (walsender)
       │ lecturas               ┌──────┴──────┐
       └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─► ▼             ▼
                          STANDBY (pg2)   STANDBY (pg3)
                          (walreceiver)   (walreceiver)
```

- **PRIMARY**:
  - Es el único nodo que acepta escrituras.
  - Por cada standby conectado corre un proceso **walsender**, que le envía el WAL en cuanto se genera.
- **STANDBY**:
  - Arranca en modo recuperación (archivo `standby.signal`).
  - Su **walreceiver** se conecta al primary (`primary_conninfo`), recibe el WAL y lo aplica sin parar.
  - Con `hot_standby=on` acepta consultas de solo lectura mientras tanto.
- **Replicación física**:
  - El WAL describe cambios a nivel de página y byte, no instrucciones SQL.
  - Por eso un standby es una copia **idéntica** del primary entero: todas las bases y la misma versión mayor.
  - No se puede replicar solo una tabla. Para eso existe la **replicación lógica** (lección 07).
- **Slots de replicación**:
  - El primary guarda el WAL hasta que cada standby lo confirma.
  - Así, un standby desconectado puede ponerse al día al volver.
  - `max_slot_wal_keep_size` limita cuánto WAL se retiene.
- **Timelines**:
  - Al promover un standby, la historia se bifurca: el nuevo primary empieza el timeline 2.
  - Los demás standby lo siguen gracias a `recovery_target_timeline = 'latest'`.

### La gran diferencia con MongoDB: no hay failover automático

En un replica set de MongoDB, si el PRIMARY cae, los nodos votan y eligen otro en ~10 s. **PostgreSQL no trae
elecciones ni consenso**: si el primary cae, los standby siguen sirviendo lecturas, pero nadie acepta escrituras
hasta que alguien promueve uno (`pg_promote()`).

En producción esto lo hace una herramienta externa: **Patroni** (con etcd, Consul o Kubernetes como fuente de
verdad), **repmgr**, **pg_auto_failover** o el servicio administrado (RDS, Cloud SQL, Azure). Este proyecto hace el
failover **a mano** (lección 06) para ver cada paso. Automatizarlo queda fuera del alcance de este proyecto.

| | MongoDB replica set | PostgreSQL streaming replication |
| --- | --- | --- |
| Registro que se replica | oplog (lógico: operaciones) | WAL (físico: páginas) |
| Nodo de escritura | PRIMARY | primary |
| Nodos de lectura | SECONDARY | standby (hot standby) |
| Elección del primary | Automática por votación (mayoría) | Manual o con herramienta externa |
| Confirmación de escrituras | `writeConcern` (`w=1`, `majority`) | `synchronous_standby_names` + `synchronous_commit` |
| A qué nodo va una lectura | `readPreference` | `target_session_attrs` / `load_balance_hosts` en libpq |
| Leer cambios como eventos | Change streams + resume token | Decodificación lógica + slot lógico |
| Volver a sumar el primary caído | Automático (rollback + sync) | `pg_rewind` o `pg_basebackup` |

### Opciones de consistencia

| Opción | Qué decide | Valores |
| --- | --- | --- |
| **`synchronous_standby_names`** | Qué standby deben confirmar cada COMMIT | `''` (asíncrona, por defecto), `'ANY 1 (pg1, pg2, pg3)'` (quórum), `'FIRST 1 (pg2, pg3)'` (por prioridad) |
| **`synchronous_commit`** | Hasta dónde se espera esa confirmación; se ajusta por sesión o por transacción | `off`, `local`, `remote_write`, `on` (por defecto: en disco del standby), `remote_apply` (ya visible en el standby) |
| **`target_session_attrs`** | A qué tipo de nodo se conecta la aplicación | `read-write`, `primary`, `standby`, `prefer-standby`, `read-only`, `any` |

## 3. Cómo está armado el proyecto

| Contenedor | Imagen / proceso | Puerto en el host | Rol |
| --- | --- | --- | --- |
| `PgNode1` (`pg1`) | `postgres:18.4` · `node.sh` | 5441 | PRIMARY inicial: `initdb` y crea el rol `replicator` y los slots |
| `PgNode2` (`pg2`) | `postgres:18.4` · `node.sh` | 5442 | STANDBY: se clona de pg1 con `pg_basebackup` |
| `PgNode3` (`pg3`) | `postgres:18.4` · `node.sh` | 5443 | STANDBY: se clona de pg1 con `pg_basebackup` |
| `PgReplicaLab` | Python 3.13 · psycopg · psql 18 · JupyterLab | 8889 | Donde se ejecutan las lecciones y el sandbox |

**Arranque:**

1. `pg1` arranca con el entrypoint oficial. Este ejecuta `initdb` y luego `initdb/10-replication.sh`, que crea el
   usuario de replicación, un slot por standby y la regla en `pg_hba.conf`.
2. Cuando `pg1` responde, `pg2` y `pg3` encuentran su directorio de datos vacío. Entonces lo clonan con
   `pg_basebackup`, escriben `standby.signal` y `primary_conninfo` y arrancan como standby.
3. `PgReplicaLab` espera a que algún nodo acepte escrituras.

Si un nodo ya tiene datos, arranca con el rol que tenía: un standby promovido sigue siendo primary tras reiniciarse.

La aplicación se conecta con las variables estándar de libpq (`.envs/postgres.env`): `PGHOST=pg1,pg2,pg3` y
`PGTARGETSESSIONATTRS=read-write`. libpq prueba los nodos en orden y se queda con el que acepta escrituras. Por eso
`psql` y `psycopg.connect("")` funcionan sin argumentos, y siguen funcionando después de un failover.

```text
.docker/
  Dockerfile                    # img-base (Python + psql) -> img-lab
  docker-compose.yml            # Contenedor PgReplicaLab
  replication.yml               # Los 3 nodos
  replication/node.sh           # Arranque de cada nodo: initdb o pg_basebackup + parámetros de postgres
  replication/initdb/           # Rol de replicación, slots y pg_hba (solo el primary inicial)
  replication/rejoin.sh         # Reincorpora un nodo como standby (pg_rewind o pg_basebackup)
  entrypoint.sh                 # Instala requirements y espera el PRIMARY
.envs/postgres.env              # Conexión de la aplicación (variables PG* de libpq)
.envs/postgres-nodes.env        # Usuarios, claves y topología inicial de los nodos
docs/architecture.drawio        # Diagrama
src/lab/                        # Conexión, topología y funciones para imprimir
lessons/                        # Lecciones 00-07
notebooks/sandbox.ipynb         # Sandbox para experimentar con todo lo anterior
```

## 4. Puesta en marcha

**Con VS Code**: `Dev Containers: Reopen in Container`.

**Con la terminal**:

```bash
cd .docker
docker compose -f docker-compose.yml -f replication.yml up -d --build
docker exec PgReplicaLab .docker/entrypoint.sh        # solo la primera vez
docker exec -it PgReplicaLab bash                     # entrar al contenedor
```

Dentro del contenedor (`/app`):

```bash
python lessons/00_seed.py      # siempre primero
python lessons/01_crud.py      # y así con las demás, en orden
psql                           # shell interactivo conectado al primary
```

Apagar (`stop`) o borrar todo, incluidos los datos (`down -v`):

```bash
cd .docker
docker compose -f docker-compose.yml -f replication.yml down -v
```

### Abrir los nodos con un cliente gráfico

Cada nodo está publicado en un puerto distinto de tu equipo. Sirven pgAdmin, DBeaver, TablePlus o la extensión
PostgreSQL de VS Code. Usuario `postgres`, clave `postgres`, base `company`:

| Nodo | Conexión |
| --- | --- |
| pg1 (PRIMARY) | `postgresql://postgres:postgres@localhost:5441/company` |
| pg2 (STANDBY) | `postgresql://postgres:postgres@localhost:5442/company` |
| pg3 (STANDBY) | `postgresql://postgres:postgres@localhost:5443/company` |

Qué puedes comprobar:

- Los tres nodos muestran la tabla `employees` con las mismas filas.
- **Solo el primary acepta escrituras**: un `UPDATE` en 5442 o 5443 falla con
  `cannot execute UPDATE in a read-only transaction`.
- **La replicación es inmediata**: edita un empleado en 5441 y vuelve a consultar en 5442; el cambio ya está ahí.
- **Sin failover automático**: con `docker stop PgNode1`, los standby siguen respondiendo lecturas, pero ninguno
  pasa a primary solo (ver Experimentos).

## 5. Lecciones

| Lección | Qué hace | Qué observar |
| --- | --- | --- |
| `00_seed.py` | Crea la tabla `employees`, carga 1.000 filas con `COPY` y crea los índices. | Las escrituras van al primary y los standby ya tienen las 1.000 filas. |
| `01_crud.py` | **Create**: `INSERT ... RETURNING`, `executemany`. **Read**: `SELECT` con filtro, orden y límite. **Update**: aritmética, `array_append`, `UPDATE ... RETURNING`, actualización masiva, upsert con `ON CONFLICT`. **Delete**. Además, una **transacción** que se revierte completa. | Las restricciones rechazan email repetido (`UniqueViolation`), salario negativo (`CheckViolation`) y nombre vacío (`NotNullViolation`). |
| `02_queries.py` | `BETWEEN`, `IN`, `OR`, arrays (`ANY`, `@>`, `cardinality`), `ILIKE`, fechas, `DISTINCT`, paginación con `OFFSET` y por clave, `EXPLAIN ANALYZE` con y sin índice, índice GIN. | Sin índice: `Seq Scan` descarta 967 filas. Con índice: `Bitmap Index Scan` va directo a las 33 que coinciden. |
| `03_aggregations.py` | `GROUP BY`, `FILTER`, `HAVING`, `unnest` de arrays, `width_bucket`, funciones de ventana (`rank`, `avg OVER`) y una vista materializada. | La vista materializada también se replica: se lee desde un standby. |
| `04_replication.py` | Rol de cada nodo, `pg_stat_replication`, slots, `pg_stat_wal_receiver`. Escribe en el primary y lee la misma fila **directamente en cada nodo**. Muestra los registros del WAL con `pg_walinspect`. | Los tres nodos devuelven el mismo dato. Un `UPDATE` genera un registro en la tabla, uno por cada índice y un `COMMIT`. |
| `05_consistency.py` | Pasa de replicación asíncrona a síncrona por quórum. Mide la latencia de cada nivel de `synchronous_commit`. Pide más standby de los que hay y cuenta lecturas desactualizadas en un standby. Reparte conexiones con `target_session_attrs`. | Con `local` casi todas las lecturas en el standby llegan tarde; con `remote_apply`, ninguna. Un COMMIT sin standby suficientes se queda esperando, pero **ya está guardado** en el primary. |
| `06_failover.py` | Un hilo escribe sin parar mientras el programa aísla al primary, espera a que un standby aplique todo el WAL, lo **promueve** y apunta el otro standby al nuevo primary. | El primary cambia de pg1 a pg2 con 2-3 escrituras fallidas y ninguna fila perdida. El timeline pasa de 1 a 2. |
| `07_logical_decoding.py` | Crea un slot lógico (`test_decoding`), lee los cambios sin consumirlos, consume una transacción, se detiene y **reanuda** desde donde quedó. Compara `REPLICA IDENTITY DEFAULT` y `FULL`. | No se pierde ni se repite ningún evento al reanudar; cada transacción llega completa. |

Para volver a cargar los datos: `python lessons/00_seed.py`. Para volver a la topología inicial (pg1 primary):
`docker compose ... down -v` y `up` de nuevo.

### Sandbox

[`notebooks/sandbox.ipynb`](notebooks/sandbox.ipynb) reúne todo en un solo lugar para experimentar:
- conexión y topología, con funciones listas (`sql`, `en_cada_nodo`, `plan`, `replicacion`, `wal_generado`);
- planes con y sin índice, funciones de ventana con gráficos;
- versiones de fila (MVCC) y `VACUUM`, frenado por `hot_standby_feedback`, y niveles de aislamiento con dos sesiones;
- el retraso de los standby graficado durante una carga masiva;
- la latencia y las lecturas atrasadas con cada `synchronous_commit`;
- el WAL que genera cada operación (y las tablas `UNLOGGED`, que no se replican);
- tus cambios como eventos de decodificación lógica;
- una tabla `sandbox_items` con **tu propio esquema** y `jsonb`.

Las celdas marcadas **Juega aquí** están hechas para cambiarlas.

Necesita el seed cargado. Hay dos formas de abrirlo:

- **VS Code (Dev Container)**: abre el archivo y elige el kernel de Python 3.13 del contenedor. No usa puertos.
- **Navegador (JupyterLab)**: el contenedor publica el puerto 8889 solo para esto (no el 8888, para no chocar con
  otro Jupyter que tengas abierto en tu equipo).

  ```bash
  docker exec -it PgReplicaLab jupyter lab --ip 0.0.0.0 --port 8889 --no-browser --notebook-dir /app
  ```

  Abre en tu equipo la URL `http://127.0.0.1:8889/lab?token=…` que imprime el comando, y `Ctrl+C` para cerrarlo.

### Después de la lección 06: reincorporar el primary antiguo

Tras el failover, el primary antiguo queda **aislado**: vivo y en solo lectura, pero sin recibir cambios. Esa es la
forma de evitar un *split-brain*, es decir, dos primary aceptando escrituras distintas. Para devolverlo al
clúster como standby del nuevo primary, desde `.docker/` en el host:

```bash
docker stop PgNode1
docker compose -f docker-compose.yml -f replication.yml run --rm --no-deps --entrypoint /replication/rejoin.sh pg1 pg2
docker start PgNode1
```

`rejoin.sh` usa `pg_rewind`, que solo copia los bloques que cambiaron desde que los nodos se separaron. Para eso
necesita `wal_log_hints` (o checksums) y el WAL del nodo desde el último checkpoint común (`wal_keep_size`). Si no
puede, clona el nodo completo con `pg_basebackup`.

## 6. Experimentos

Desde el host (`cd .docker`):

```bash
# Apagar el primary de golpe: los standby siguen leyendo, pero nadie acepta escrituras
docker stop PgNode1
docker exec PgReplicaLab psql -c "SELECT 1"                    # falla: ningún nodo es read-write
docker exec PgReplicaLab psql "target_session_attrs=standby" -c "SELECT count(*) FROM employees"

# Promover un standby a mano (lo que haría Patroni)
docker exec PgNode2 psql -U postgres -c "SELECT pg_promote()"
docker exec PgReplicaLab psql -c "SELECT current_setting('cluster_name')"   # ahora responde pg2

# pg3 sigue apuntando a pg1: hay que repuntarlo (la lección 06 lo hace) o reincorporarlo con rejoin.sh
docker stop PgNode3
docker compose -f docker-compose.yml -f replication.yml run --rm --no-deps --entrypoint /replication/rejoin.sh pg3 pg2
docker start PgNode3

# Apagar un standby y ver cómo su slot retiene WAL en el primary
docker stop PgNode3
docker exec PgReplicaLab psql -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots"
docker start PgNode3
```

Consultas útiles en `psql`:

```sql
SELECT pg_is_in_recovery();                              -- true en un standby
SELECT * FROM pg_stat_replication;                       -- en el primary: un walsender por standby
SELECT * FROM pg_stat_wal_receiver;                      -- en un standby: de quién recibe
SELECT * FROM pg_replication_slots;                      -- slots y WAL retenido
SELECT pg_current_wal_lsn(), pg_walfile_name(pg_current_wal_lsn());
SHOW synchronous_standby_names;
\d employees
EXPLAIN ANALYZE SELECT * FROM employees WHERE city = 'Cali';
```

## Notas

- **Credenciales de laboratorio**: `postgres/postgres` y `replicator/replicator`, en texto plano en `.envs/`. Los
  nodos se publican en `localhost` (5441-5443) solo para abrirlos con un cliente gráfico. En producción se usan
  secretos, TLS (`hostssl` en `pg_hba.conf`) y reglas de red restringidas.
- Todos los nodos usan `wal_level=logical`, que incluye lo necesario para la replicación física y además permite la
  decodificación lógica de la lección 07.
- Cada nodo limita `shared_buffers` a 128 MB para no consumir demasiada memoria.
