#!/bin/sh
# Runs once, when the database volume is first created. The app role is unprivileged so row-level security applies.
set -e
case "${MEMASO_APP_PASSWORD:-memaso_app}" in
  *[!A-Za-z0-9_]*) echo "MEMASO_APP_PASSWORD may contain only letters, digits and underscore (try: openssl rand -hex 24)" >&2; exit 1 ;;
esac
psql -v ON_ERROR_STOP=1 --username postgres --dbname "$POSTGRES_DB" <<EOSQL
CREATE ROLE memaso_app LOGIN PASSWORD '${MEMASO_APP_PASSWORD:-memaso_app}' NOSUPERUSER NOBYPASSRLS;
EOSQL
