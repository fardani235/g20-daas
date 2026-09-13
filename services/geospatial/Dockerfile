FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    gdal-bin \
    libgdal-dev \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Default object-detection model: YOLOv8n (COCO) fetched from the pinned
# Ultralytics release and verified by checksum, plus its class labels. Kept out
# of the repo/build context; the operation reads assets from this directory.
ARG YOLOV8N_URL=https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.onnx
ARG YOLOV8N_SHA256=b2bc52f40e8e1c532427d5bde3575a5d5b571b739fab2c6df443733ed1589cbd
ENV OBJECT_DETECTION_MODELS_DIR=/opt/webodm/models
RUN mkdir -p /opt/webodm/models && \
    curl -fsSL -o /opt/webodm/models/yolov8n.onnx "$YOLOV8N_URL" && \
    echo "$YOLOV8N_SHA256  /opt/webodm/models/yolov8n.onnx" | sha256sum -c -
COPY models/coco.txt /opt/webodm/models/coco.txt

EXPOSE 5000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--reload"]
