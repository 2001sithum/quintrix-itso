FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 \
    --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/archive /models/torch

# Track 2's YOLO11m is public — fetch it here so a CI checkout (*.pt is
# gitignored) does not fall back to the older YOLOv8s.
RUN python -m models.fetch_weights

ENV TORCH_HOME=/models/torch
ENV YOLO_WEIGHTS=/models/yolov8n.pt
ENV QUINTRIX_DB=/data/quintrix_itso.db
ENV QUINTRIX_ARCHIVE=/data/archive

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
