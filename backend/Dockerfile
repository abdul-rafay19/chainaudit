FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt openai
COPY backend/ ./backend/
COPY demo_data/ ./demo_data/
WORKDIR /app/backend
# SQLite + in-process SSE: keep exactly ONE process and mount a persistent volume at /app/backend/data
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
