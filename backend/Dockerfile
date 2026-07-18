# Backend Dockerfile — FastAPI contract comparison service
FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY resources/quality_cases/ ./resources/quality_cases/

# Create storage directory (mounted as volume at runtime)
RUN mkdir -p /data/storage

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "scripts/run_api.py"]
