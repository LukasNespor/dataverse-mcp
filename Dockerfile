FROM python:3.12-slim

# Create non-root user for security
RUN groupadd -r mcpuser && useradd -r -g mcpuser -d /app mcpuser

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY --chown=mcpuser:mcpuser src/ ./src/

# Create the token cache directory with correct ownership
RUN mkdir -p /data && chown mcpuser:mcpuser /data

# FastMCP OAuthProxy needs a writable directory for OAuth state storage
RUN mkdir -p /app/.local/share/fastmcp && chown -R mcpuser:mcpuser /app/.local

USER mcpuser

# /data is a volume mount point for persistent token cache
VOLUME ["/data"]

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# HTTP transport port for Azure/OBO mode
EXPOSE 8000

# Run the MCP server via stdio (default FastMCP transport)
CMD ["python", "src/main.py"]
