FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# The service holds the provisioning credentials; run it as an unprivileged
# user with a read-only rootfs (docker-compose) and no volumes.
RUN useradd --system --uid 10002 --create-home --home-dir /home/provisioner provisioner
USER provisioner

ENV PROVISIONER_PROVIDER=none \
    PROVISIONER_NODEODM_PORT=3000

EXPOSE 5002

# One worker: calls are short provider API round trips plus a readiness
# probe; concurrency comes from the threadpool.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5002"]
