# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.14
ARG NODE_MAJOR=24

# -------- Stage 1: builder --------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    default-libmysqlclient-dev \
    git \
    libffi-dev \
    libjpeg-dev \
    libldap2-dev \
    libmariadb-dev \
    libpango1.0-dev \
    libpangoft2-1.0-0 \
    libpq-dev \
    libsasl2-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    pkg-config \
    xz-utils \
    zlib1g-dev \
 && curl -fsSL "https://deb.nodesource.com/node_24.x/pool/main/n/nodejs/nodejs_24.19.0-1nodesource1_amd64.deb" -o /tmp/nodejs.deb \
 && apt-get install -y --no-install-recommends /tmp/nodejs.deb \
 && rm /tmp/nodejs.deb \
 && npm install -g yarn \
 && rm -rf /var/lib/apt/lists/* \
 && node --version && yarn --version

WORKDIR /workspace

# Copy apps so the build context has them. Sites dir comes from a volume at runtime.
COPY . /workspace/

# Stitch a sites tree into /workspace/frappe-bench/sites so the builder-stage `bench`
# commands have what they expect (sites/apps.txt + sites/assets). At runtime, the
# `frappe_sites` named volume overwrites this whole tree on container start.
#  - RUN mkdir creates the bench repo skeleton (paths_in_bench = apps, sites,
#    config, logs, config/pids — all required for `is_bench_directory()`).
#  - `frappe-bench/apps/<app>` symlinks let bench find each app via its expected
#    path inside the bench repo, even though COPY put them one level above at
#    /workspace/<app> (= /workspace/frappe, etc.).
#  - COPY pulls apps.txt from the additional `sites` build context
#    (passed via: docker build --build-context sites=./frappe-bench/sites ...).
#  - The trailing `printf`s write apps.txt and a minimal common_site_config.json.
#    apps.txt is a belt-and-braces fallback so the sites named-context can't get
#    out of sync. common_site_config.json sets `frappe_user: root` so `bench`
#    does NOT sys.exit(1) on its `change_uid` root-user guard during this
#    build-time invocation; the runtime image has its own sites volume mount.
RUN mkdir -p \
    /workspace/frappe-bench/sites/assets \
    /workspace/frappe-bench/apps \
    /workspace/frappe-bench/config \
    /workspace/frappe-bench/logs \
    /workspace/frappe-bench/config/pids \
 && for app in frappe webodm_core webodm_frontend; do ln -sf /workspace/$app /workspace/frappe-bench/apps/$app; done
COPY --from=sites apps.txt /workspace/frappe-bench/sites/apps.txt
RUN printf 'frappe\nwebodm_core\nwebodm_frontend\n' > /workspace/frappe-bench/sites/apps.txt \
 && printf '{"frappe_user": "root", "restart_supervisor_on_update": false, "restart_systemd_on_update": false, "serve_default_site": true, "rebase_on_pull": false, "shallow_clone": true, "background_workers": 1, "use_redis_auth": false, "live_reload": true}\n' > /workspace/frappe-bench/sites/common_site_config.json

# Install bench + Frappe + custom apps (editable so bench update works inside the
# container). `frappe-bench` is a real PyPI package (v5.31.0) that provides the
# `bench` console script used by the next RUN. Frappe's own package does NOT
# provide a `bench` entry point on its own.
#
# NOTE: This RUN installs into the builder's system Python, which is NOT copied
# to the runtime image (only /workspace is). The runtime image gets `bench` from
# the venv install below — DO NOT remove that.
RUN pip install --no-cache-dir frappe-bench \
  && pip install --no-cache-dir -e ./frappe \
  && pip install --no-cache-dir -e ./webodm_core \
  && pip install --no-cache-dir -e ./webodm_frontend

# Bootstrap a Python virtualenv under /workspace/frappe-bench/env so the
# `bench` CLI can find its expected interpreter (env/bin/python). The
# `frappe-bench` package already pulled `uv` into the system; we use uv's
# `venv` here because it matches `bench`'s own venv-creation routine.
# `--link-mode=copy` matches the link-mode bench uses when
# FRAPPE_DOCKER_BUILD=1 is set, which keeps the build hermetic.
ENV FRAPPE_DOCKER_BUILD=1
RUN cd /workspace/frappe-bench && uv venv env --seed --link-mode=copy

# Re-install frappe-bench AND the apps INTO the venv (the system pip install
# above is wasted — the runtime image only gets /workspace). Without this,
# `bench` is missing from the runtime image and `bench new-site` / `bench
# worker` / etc. fail with "bench: command not found".
RUN /workspace/frappe-bench/env/bin/pip install --no-cache-dir frappe-bench \
  && /workspace/frappe-bench/env/bin/pip install --no-cache-dir -e /workspace/frappe \
  && /workspace/frappe-bench/env/bin/pip install --no-cache-dir -e /workspace/webodm_core \
  && /workspace/frappe-bench/env/bin/pip install --no-cache-dir -e /workspace/webodm_frontend

# Build production assets.
# FRAPPE_BENCH_ROOT tells Frappe's esbuild frontend where the bench repo
# actually lives. The default fallback computes `../..` from `apps/frappe`
# which in this layout would resolve to `/` (not `/workspace/frappe-bench`),
# making `sites/apps.txt` resolve to `/sites/apps.txt` and the yarn build fail
# with ENOENT.
ENV FRAPPE_BENCH_ROOT=/workspace/frappe-bench
RUN cd /workspace/frappe-bench && \
    bench setup requirements && \
    bench build --production

# Build the Vue SPA frontend (separate from Frappe desk assets).
# Output goes to ../webodm_frontend/public/frontend/ via vite.config.js.
RUN cd /workspace/frappe-bench/apps/webodm_frontend/frontend && \
    rm -rf node_modules && \
    npm install && \
    npm run build

# Create symlinks for old asset hashes that Frappe's cached HTML templates may
# still reference. This prevents 404s when the asset manifest is stale in Redis.
RUN cd /workspace/frappe-bench/sites/assets/frappe/dist/css && \
    ln -sf website.bundle.SWDROYVF.css website.bundle.O2E3HIFX.css && \
    ln -sf login.bundle.KMQ57AS2.css login.bundle.FSM44DKP.css && \
    ln -sf desk.bundle.I7MTTDEK.css desk.bundle.VXKWDPP2.css && \
    ln -sf report.bundle.KZGHFBKK.css report.bundle.USOYVG4O.css && \
    ln -sf email.bundle.ZRB6QPMG.css email.bundle.CBUSZXAR.css && \
    ln -sf print.bundle.EFDDGPIK.css print.bundle.RJBWMDLZ.css && \
    ln -sf print_format.bundle.QBW4Y7JU.css print_format.bundle.UIVQSN3U.css && \
    ln -sf web_form.bundle.LTPZ7XW5.css web_form.bundle.24QVWQ5C.css && \
    cd /workspace/frappe-bench/sites/assets/frappe/dist/css-rtl && \
    ln -sf website.bundle.WOFYNPDX.css website.bundle.QPDW3S5M.css && \
    ln -sf login.bundle.EPMX2X5X.css login.bundle.G3XYFFET.css && \
    ln -sf desk.bundle.Y26RZDMU.css desk.bundle.UXAD2Z2O.css && \
    ln -sf report.bundle.NEXDK22G.css report.bundle.3ZVHB5DK.css && \
    ln -sf email.bundle.CZG3KSRZ.css email.bundle.XRLHJ4XT.css && \
    ln -sf print.bundle.JH5RUQPV.css print.bundle.E7UMRBXI.css && \
    ln -sf print_format.bundle.MGSE7PDU.css print_format.bundle.A6VIANNN.css && \
    ln -sf web_form.bundle.HFNPPH5U.css web_form.bundle.T34I674G.css

# -------- Stage 2: runtime --------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SITES_PATH=/workspace/frappe-bench/sites \
    PATH=/workspace/frappe-bench/env/bin:$PATH

ARG NODE_MAJOR
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
  && install -m 0755 -d /etc/apt/keyrings \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /etc/apt/keyrings/postgresql.asc \
  && chmod a+r /etc/apt/keyrings/postgresql.asc \
  && echo "deb [signed-by=/etc/apt/keyrings/postgresql.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-core \
    git \
    libffi8 \
    libjpeg62-turbo \
    libldap-2.5-0 \
    libmagic1 \
    libmariadb3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpq5 \
    libsasl2-2 \
    libssl3 \
    libxml2 \
    libxslt1.1 \
    postgresql-client-16 \
    poppler-utils \
    tini \
    zlib1g \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version

COPY --from=builder /workspace /workspace
COPY --from=root infra/frappe/entrypoint.sh /workspace/infra/frappe/entrypoint.sh
COPY --from=root infra/frappe/init.sh /workspace/infra/frappe/init.sh
COPY --from=root infra/frappe/wsgi.py /workspace/infra/frappe/wsgi.py
RUN cp /workspace/infra/frappe/entrypoint.sh /entrypoint.sh && \
    cp /workspace/infra/frappe/init.sh /usr/local/bin/frappe-init.sh && \
    chmod +x /entrypoint.sh /usr/local/bin/frappe-init.sh

WORKDIR /workspace/frappe-bench

EXPOSE 8000 9000

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["frappe-web"]