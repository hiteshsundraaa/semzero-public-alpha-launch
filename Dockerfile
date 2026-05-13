FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt psycopg2-binary

# Install package
COPY . .
RUN pip install --no-cache-dir -e ".[dev]"

ENV PYTHONPATH=/app/src
ENV SEMZERO_DATA_DIR=/app/data

RUN mkdir -p /app/data

CMD ["semzero", "--help"]
