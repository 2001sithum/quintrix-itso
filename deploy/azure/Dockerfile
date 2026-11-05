FROM mwader/static-ffmpeg:latest AS ffmpeg
FROM python:3.11-slim-bookworm

# Vendor high-performance static ffmpeg/ffprobe binary — eliminates 195 apt
# packages, bypasses Debian mirror network flakiness, and keeps image lean.
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/

WORKDIR /app

# Copy vendored Linux x86_64 wheels and install without external network overhead
COPY vendor_wheels /wheels
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl /wheels/*.tar.gz && rm -rf /wheels

COPY . .

RUN mkdir -p /data /models/torch && \
    mv /app/quintrix_itso.db /data/quintrix_itso.db && \
    mv /app/archive /data/archive && \
    ln -sfn /data/archive /app/archive && \
    ln -sfn /data/quintrix_itso.db /app/quintrix_itso.db

# Track 2's YOLO11m is public — fetch it here so a CI checkout (*.pt is
# gitignored) does not fall back to the older YOLOv8s.
RUN python -m models.fetch_weights

ENV TORCH_HOME=/models/torch
ENV YOLO_WEIGHTS=/models/yolov8n.pt
ENV QUINTRIX_DB=/data/quintrix_itso.db
ENV QUINTRIX_ARCHIVE=/data/archive

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
