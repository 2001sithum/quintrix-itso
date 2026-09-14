# Hugging Face Space image.
#
# Differs from the root Dockerfile in two ways that matter on Spaces:
#   1. Spaces run the container as uid 1000, not root, so every path the app
#      writes to must be created and chowned at build time — otherwise the
#      first request fails trying to create the SQLite file.
#   2. Storage is ephemeral here, so /data is a plain directory rather than a
#      mount point, and the app is told so via ITSO_EPHEMERAL_STORAGE.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Spaces execute as uid 1000. Create it before copying so ownership is right.
RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 torchvision==0.20.1 \
      --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

# Writable state. Ephemeral on a free Space — recreated on every rebuild.
RUN mkdir -p /data /models && chown -R user:user /data /models /app
USER user

# A free Space gets 2 vCPU, so QUINTRIX_MAX_JOBS=1: a second pipeline worker
# would contend with ffmpeg's own threads rather than add throughput.
ENV TORCH_HOME=/models/torch \
    QUINTRIX_DB=/data/quintrix_itso.db \
    QUINTRIX_ARCHIVE=/data/archive \
    ITSO_EPHEMERAL_STORAGE=1 \
    ITSO_DEMO_MODE=1 \
    ITSO_PLATFORM="Hugging Face Space (free CPU)" \
    QUINTRIX_MAX_JOBS=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
