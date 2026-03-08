FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fly.io routes external traffic to internal port 8080.
# The app reads PORT at startup; PHASE1_INGEST_SECONDS=0 = continuous mode.
ENV PORT=8080
ENV PHASE1_INGEST_SECONDS=0

EXPOSE 8080

CMD ["python", "app.py"]
