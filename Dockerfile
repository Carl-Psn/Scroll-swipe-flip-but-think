FROM python:3.11-slim

WORKDIR /app

COPY requirements-fastapi.txt .
RUN pip install --no-cache-dir -r requirements-fastapi.txt

COPY . .

EXPOSE 8000

# Railway injecte la variable PORT ; 8000 par défaut en local
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
