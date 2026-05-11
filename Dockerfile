FROM python:3.11-slim AS base

WORKDIR /app

# System deps for psycopg binary and general build
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY kodosumi/ kodosumi/

# Install with all optional dependency groups
RUN pip install --no-cache-dir -e ".[postgres,redis,temporal]" 2>/dev/null || \
    pip install --no-cache-dir -e "."

# Create data directories
RUN mkdir -p /app/data/execution /app/data/uploads /app/data/config

EXPOSE 3370

# Default: run the panel server
CMD ["koco", "serve", "--address", "http://0.0.0.0:3370"]
