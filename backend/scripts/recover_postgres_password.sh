#!/usr/bin/env bash
# Recover Postgres access without deleting data: reset terarchitect password from inside the container.
# Run from repo root: ./backend/scripts/recover_postgres_password.sh
# Requires: docker compose up -d (postgres container running).
# If this fails with "role postgres does not exist", run manually:
#   docker exec -it terarchitect-postgres psql -U terarchitect -d terarchitect
#   then at prompt: ALTER USER terarchitect WITH PASSWORD 'terarchitect';

set -e
CONTAINER="${CONTAINER:-terarchitect-postgres}"
USER="${PG_USER:-terarchitect}"
PASS="${PG_PASSWORD:-terarchitect}"

echo "Resetting password for user '$USER' in container '$CONTAINER'..."
if ! docker exec -i "$CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<EOF
-- Ensure user exists (idempotent)
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$USER') THEN
    CREATE USER $USER WITH PASSWORD '$PASS' SUPERUSER CREATEDB;
    CREATE DATABASE terarchitect OWNER $USER;
    RAISE NOTICE 'Created user and database terarchitect';
  ELSE
    ALTER USER $USER WITH PASSWORD '$PASS';
    RAISE NOTICE 'Updated password for user $USER';
  END IF;
END
\$\$;
EOF
then
  echo "Connection as postgres failed. Try manual recovery:"
  echo "  docker exec -it $CONTAINER psql -U $USER -d terarchitect"
  echo "  Then run: ALTER USER $USER WITH PASSWORD '$PASS';"
  exit 1
fi
echo "Done. Use DATABASE_URL=postgresql://$USER:$PASS@localhost:5433/terarchitect"
