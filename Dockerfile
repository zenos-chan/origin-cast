FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000 \
    MOVIES_DIR=/app/movies \
    TV_DIR=/app/tv \
    DATA_DIR=/app/data

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application source
COPY . /app

# Ensure media and persistent data directories exist
RUN mkdir -p /app/movies /app/tv /app/data

EXPOSE 5000

VOLUME ["/app/movies", "/app/tv", "/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)" || exit 1

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5000", "--no-debug"]
