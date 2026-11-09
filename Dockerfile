# Ajax Submittal Manager - single container (backend serves the prebuilt UI)
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ocrmypdf tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY . .
COPY --from=ui /ui/dist frontend/dist
ENV ASM_DATA_DIR=/app/data
EXPOSE 8765
WORKDIR /app/backend
CMD ["python", "-m", "asm.main", "--host", "0.0.0.0", "--no-browser"]
