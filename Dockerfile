FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    SESSION_COOKIE_SECURE=1 \
    PREFERRED_URL_SCHEME=https \
    TRUSTED_PROXY_HOPS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure runtime dirs exist
RUN mkdir -p instance static/uploads/properties static/uploads/avatars

EXPOSE 8080

# Cloudflare Containers / generic: listen on PORT
CMD gunicorn -b 0.0.0.0:${PORT:-8080} -w 2 --threads 4 --timeout 120 "app:app"
