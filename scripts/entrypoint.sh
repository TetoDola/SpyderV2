#!/bin/bash
set -e

echo "Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432}..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-postgres}" -q; do
  sleep 1
done
echo "PostgreSQL is ready."

echo "Waiting for Redis at ${CELERY_BROKER_URL:-redis://redis:6379/0}..."
until redis-cli -u "${CELERY_BROKER_URL:-redis://redis:6379/0}" ping 2>/dev/null | grep -q PONG; do
  sleep 1
done
echo "Redis is ready."

echo "Running migrations..."
uv run python manage.py migrate --noinput

exec "$@"
