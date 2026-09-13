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
ENV OBJECT_DETECTION_MODELS_DIR=/opt/webodm/models
RUN mkdir -p /opt/webodm/models && \
    curl -fsSL -o /opt/webodm/models/yolov8n.onnx "$YOLOV8N_URL" && \
    echo "$YOLOV8N_SHA256  /opt/webodm/models/yolov8n.onnx" | sha256sum -c - && \
    curl -fsSL -o /opt/webodm/models/visdrone-yolov11s.onnx "$VISDRONE_URL" && \
    echo "$VISDRONE_SHA256  /opt/webodm/models/visdrone-yolov11s.onnx" | sha256sum -c -
COPY models/coco.txt /opt/webodm/models/coco.txt
COPY models/visdrone.txt /opt/webodm/models/visdrone.txt

EXPOSE 5000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--reload"]
