# PotreeConverter 2.x (LAS/LAZ -> Potree 2.0 octree) is built from source:
# there is no Linux package and the upstream binaries are Windows-only. The
# tag is pinned; 2.1.1 is the last release that compiles with the distro
# toolchain (later tags need GCC 14 + C++23 <print> and currently fail on
# nlohmann::json overloads). Only the binary and liblaszip end up in the
# runtime image.
FROM ubuntu:24.04 AS potree-builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake g++ make libtbb-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG POTREE_CONVERTER_REF=2.1.1
RUN git clone --depth 1 --branch ${POTREE_CONVERTER_REF} \
    https://github.com/potree/PotreeConverter.git /src/PotreeConverter
WORKDIR /src/PotreeConverter/build
RUN cmake .. && make -j"$(nproc)" PotreeConverter


FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    gdal-bin \
    libgdal-dev \
    libtbb12 \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# PotreeConverter (see the builder stage). POTREE_CONVERTER_BIN / timeouts /
# concurrency are read by app/utils/pointcloud.py.
COPY --from=potree-builder /src/PotreeConverter/build/PotreeConverter /usr/local/bin/PotreeConverter
COPY --from=potree-builder /src/PotreeConverter/build/liblaszip.so /usr/local/lib/liblaszip.so
# The converter canonicalises argv[0] at start-up, so it must be invoked by
# absolute path (app/utils/pointcloud.py resolves it with shutil.which).
RUN ldconfig && /usr/local/bin/PotreeConverter --help > /dev/null
ENV POTREE_CONVERTER_BIN=/usr/local/bin/PotreeConverter \
    POTREE_TIMEOUT_SECONDS=7200 \
    POTREE_MAX_CONCURRENT=1

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
# Empty = local-only, which is the default. COG_SCRATCH_DIR also hosts the
# S3 -> S3 point cloud conversions (input LAZ + octree, briefly).
ENV S3_BUCKETS="" \
    COG_SCRATCH_DIR=/tmp

EXPOSE 5000

# Multiple workers so one blocking request can't stall the service, and no
# --reload (dev-only, and incompatible with workers). Blocking work is also
# offloaded to a threadpool in the handlers. Point cloud conversions are
# additionally bounded to POTREE_MAX_CONCURRENT per worker.
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers ${WEB_CONCURRENCY}"]
