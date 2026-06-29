FROM python:3.11-slim

WORKDIR /srv

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app ./app
COPY scripts ./scripts

# Non-root user
RUN useradd -m appuser && chown -R appuser /srv
USER appuser

EXPOSE 8000 8080

# Default = backend. docker-compose overrides the command for the proxy service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
