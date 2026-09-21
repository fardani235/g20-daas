FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# rasterio wheels bundle GDAL but link libexpat dynamically; slim lacks it.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# Plugins run as this unprivileged user (the whole service does, so a plugin
# can never gain more than the service has). The sandbox volume is the only
# writable location; Frappe creates each run directory world-writable so this
# uid can write into it. The rootfs is mounted read-only by docker-compose.
RUN useradd --system --uid 10001 --create-home --home-dir /home/runner runner \
 && mkdir -p /sandbox && chown runner:runner /sandbox
USER runner

ENV SANDBOX_DIR=/sandbox \
    PLUGIN_MAX_CONCURRENT_RUNS=1

EXPOSE 5001

# A single uvicorn worker: concurrency is governed by PLUGIN_MAX_CONCURRENT_RUNS
# (an in-process semaphore), and the blocking subprocess wait runs in a
# threadpool so /health keeps answering during a run.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5001"]
