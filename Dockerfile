FROM python:3.12-slim

# Create non-root user for security
RUN groupadd -r mcpuser && useradd -r -g mcpuser -d /app mcpuser

WORKDIR /app

# Update system packages to patch known vulnerabilities
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --upgrade pip==26.0

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY --chown=mcpuser:mcpuser src/ ./src/

USER mcpuser

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# HTTP transport port
EXPOSE 8000

CMD ["python", "src/main.py"]
