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

# Default object-detection models. Fetched from pinned sources and verified by
# checksum; the operation reads assets from OBJECT_DETECTION_MODELS_DIR.
#  - yolov8n.onnx:   general COCO detector (default)
#  - visdrone-*.onnx: aerial/drone detector (VisDrone, 10 classes) — set the
#    plugin's model/labels (or OBJECT_DETECTION_DEFAULT_MODEL) to use it on
#    nadir imagery, where the COCO model is weak.
ARG YOLOV8N_URL=https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.onnx
ARG YOLOV8N_SHA256=b2bc52f40e8e1c532427d5bde3575a5d5b571b739fab2c6df443733ed1589cbd
ARG VISDRONE_URL=https://huggingface.co/RISEF/yolov11s-visdrone/resolve/main/weights/best.onnx
ARG VISDRONE_SHA256=5abd18e5b630c330e68fe56b2bdc3875b10fbea07fe6912bde3cb9e68b58ca23
ARG DEEPFOREST_URL=https://huggingface.co/kshitijrajsharma/fair-deepforest-tree-crowns/resolve/main/deepforest_tree_crowns.onnx
ARG DEEPFOREST_SHA256=2b65ed073df780a0bb9184d6e120066672fe3a08adc2b57d49cc2cd0a7d47c47
ENV OBJECT_DETECTION_MODELS_DIR=/opt/webodm/models
RUN mkdir -p /opt/webodm/models && \
    curl -fsSL -o /opt/webodm/models/yolov8n.onnx "$YOLOV8N_URL" && \
    echo "$YOLOV8N_SHA256  /opt/webodm/models/yolov8n.onnx" | sha256sum -c - && \
    curl -fsSL -o /opt/webodm/models/visdrone-yolov11s.onnx "$VISDRONE_URL" && \
    echo "$VISDRONE_SHA256  /opt/webodm/models/visdrone-yolov11s.onnx" | sha256sum -c - && \
    curl -fsSL -o /opt/webodm/models/deepforest.onnx "$DEEPFOREST_URL" && \
    echo "$DEEPFOREST_SHA256  /opt/webodm/models/deepforest.onnx" | sha256sum -c -
COPY models/coco.txt /opt/webodm/models/coco.txt
COPY models/visdrone.txt /opt/webodm/models/visdrone.txt
COPY models/tree.txt /opt/webodm/models/tree.txt

EXPOSE 5000

# Multiple workers so one blocking request can't stall the service, and no
# --reload (dev-only, and incompatible with workers). Blocking work is also
# offloaded to a threadpool in the handlers.
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers ${WEB_CONCURRENCY}"]
