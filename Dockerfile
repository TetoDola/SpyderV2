FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps: PostgreSQL client (pg_isready), Redis client (redis-cli),
# build tools for psycopg and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev postgresql-client redis-tools \
    libjpeg62-turbo-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install Python dependencies (cached layer — only busts when pyproject.toml changes)
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# Copy project
COPY . .

# Make entrypoint executable
RUN chmod +x /app/scripts/entrypoint.sh

# Collect static files (dummy key — DB not needed for collectstatic)
RUN DJANGO_SECRET_KEY=build-only DB_HOST=localhost \
    uv run python manage.py collectstatic --noinput || true

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]
