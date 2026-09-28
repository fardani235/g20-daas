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

# Managed models directory shared by the analysis operations (the variable
# name predates object detection moving out to a user plugin; it is kept so
# existing deployments that mount a volume here keep working).
ENV OBJECT_DETECTION_MODELS_DIR=/opt/webodm/models
RUN mkdir -p /opt/webodm/models

# Default semantic-segmentation model: a MIT-licensed SegFormer exported from
# Pranilllllll/segformer-satellite-segementation (7 satellite land-use classes).
# The exported graph accepts [0,1] RGB and returns per-class probabilities at
# input resolution, matching the segmentation op's contract. The upstream repo
# publishes no ONNX, so the export is committed and verified by checksum.
ENV SEGMENTATION_DEFAULT_MODEL=segformer-satellite-landcover.onnx
ENV SEGMENTATION_DEFAULT_LABELS=satellite-landcover.txt
COPY models/segformer-satellite-landcover.onnx /opt/webodm/models/segformer-satellite-landcover.onnx
COPY models/satellite-landcover.txt /opt/webodm/models/satellite-landcover.txt
RUN echo "e2351ed70749afcab18e3039110dd5b4d49152a87430d146db68060c4a12ff09  /opt/webodm/models/segformer-satellite-landcover.onnx" | sha256sum -c -

# Object storage (see app/utils/objectstore.py): S3_BUCKETS allow-list,
# optional S3_ENDPOINT_URL for MinIO, credentials via AWS_* (or *_FILE).
# Empty = local-only, which is the default.
ENV S3_BUCKETS="" \
    COG_SCRATCH_DIR=/tmp

EXPOSE 5000

# Multiple workers so one blocking request can't stall the service, and no
# --reload (dev-only, and incompatible with workers). Blocking work is also
# offloaded to a threadpool in the handlers.
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers ${WEB_CONCURRENCY}"]
