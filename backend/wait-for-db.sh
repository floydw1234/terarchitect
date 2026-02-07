#!/bin/bash
# Wait for PostgreSQL to be ready before starting Flask

set -e

host="$POSTGRES_HOST"
if [ -z "$host" ]; then
    host="postgres"
fi

port="$POSTGRES_PORT"
if [ -z "$port" ]; then
    port="5432"
fi

echo "Waiting for PostgreSQL at $host:$port..."

until pg_isready -h "$host" -p "$port" -U terarchitect; do
    echo "PostgreSQL is unavailable - sleeping"
    sleep 2
done

echo "PostgreSQL is up - starting Flask..."
exec flask run --host=0.0.0.0 --port=5000
