FROM python:3.11-slim

WORKDIR /app

# API only — no OpenCV/PyTorch (those are pipeline-only dependencies).
# This keeps the image ~200MB smaller and docker compose up starts in ~30s.
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY app/ ./app/
COPY data/ ./data/
COPY assertions.py .

WORKDIR /app/app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
