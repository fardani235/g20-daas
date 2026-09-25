# WebODM Production Deployment Guide

**Target Environment:** Byteplus Cloud VM  
**Primary Domain:** `daas.g20tech.site`  
**Admin Domain:** `admin.g20tech.site`  
**TLS:** Let's Encrypt HTTP-01 (automatic via Caddy)  
**File Storage:** AWS S3 for outputs + backups (user uploads stay local for security)  
**Email:** Gmail SMTP (App Password)  
**Last Updated:** 2026-08-28

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Phase 1: VM Setup (Byteplus)](#3-phase-1-vm-setup-byteplus)
4. [Phase 2: DNS Configuration](#4-phase-2-dns-configuration)
5. [Phase 3: Project Configuration](#5-phase-3-project-configuration)
6. [Phase 4: Security Group & Firewall](#6-phase-4-security-group--firewall)
7. [Phase 5: Build & Deploy](#7-phase-5-build--deploy)
8. [Phase 6: Post-Deployment Verification](#8-phase-6-post-deployment-verification)
9. [Phase 7: Email Setup (Gmail)](#9-phase-7-email-setup-gmail)
10. [Phase 8: AWS S3 Storage with Multi-Tenant Security](#10-phase-8-aws-s3-storage-with-multi-tenant-security)
11. [Phase 9: Security Hardening](#11-phase-9-security-hardening)
12. [Phase 11: Backup & Recovery](#12-phase-11-backup--recovery)
13. [Troubleshooting](#13-troubleshooting)
14. [Rollback Procedure](#14-rollback-procedure)

---

## 1. Architecture Overview

```
Internet
    |
    v
+--------------------------------------------------+
|  Caddy (Reverse Proxy + TLS)                     |
|  - daas.g20tech.site  -> SPA + API               |
|  - admin.g20tech.site -> Frappe Desk (IP whitelisted)|
+--------------------------------------------------+
    |                    |                    |
    v                    v                    v
+----------+    +-------------+    +----------------+
|frappe-web|    |frappe-socketio|  |geospatial      |
| (gunicorn)|    | (SocketIO)   |    | (tile server) |
|          |    |              |    |                |
| Local    |    |              |    | S3 Outputs     |
| Volumes  |    |              |    | (read-only)    |
| - uploads|    |              |    |                |
| - outputs|    |              |    |                |
+----------+    +-------------+    +----------------+
    |                                        |
    |         +------------------------------+
    |         |
    v         v
+--------------------------------------------------+
|  AWS S3 (Restricted Mounts via S3FS)             |
|                                                  |
|  /files/outputs/  <- Processing results          |
|    (geospatial: read-only, nodeodm: no access)   |
|                                                  |
|  /nodeodm/        <- NodeODM working data        |
|    (nodeodm only, no user data access)           |
|                                                  |
|  /backups/        <- Database backups            |
|    (backup service only)                         |
+--------------------------------------------------+
    |
    +---> frappe-scheduler (cron/RQ workers)
    +---> frappe-worker (RQ job processor)
    +---> frappe-init (one-time bootstrap)

+----------+    +-------------+    +----------+
| postgres |    | redis-cache |    | redis-queue|
| (PostGIS)|    | (sessions)  |    | (jobs)    |
+----------+    +-------------+    +----------+

+----------+
| nodeodm  |
| (ODM 3.5)|
| S3 work  |
| dir only |
+----------+

+----------+
| backup   |
| S3 only  |
+----------+
```

**Services Summary:**

| Service | Image | Ports (internal) | Purpose |
|---------|-------|------------------|---------|
| caddy | `webodm-caddy:2` | 80, 443 | Reverse proxy, TLS termination, rate limiting |
| frappe-web | `webodm-frappe:<version>@sha256:…` | 8000 | WSGI app (gunicorn), API, file serving |
| frappe-socketio | `webodm-frappe:<version>@sha256:…` | 9000 | Real-time notifications |
| frappe-scheduler | `webodm-frappe:<version>@sha256:…` | - | Cron jobs, RQ scheduler |
| frappe-worker | `webodm-frappe:<version>@sha256:…` | - | Background job processor |
| frappe-init | `webodm-frappe:<version>@sha256:…` | - | One-time site bootstrap |
| postgres | `postgis/postgis:16-3.4` | 5432 | PostgreSQL + PostGIS |
| redis-cache | `redis:7-alpine` | 13000 | Sessions, cache |
| redis-queue | `redis:7-alpine` | 11000 | Job queues (RQ) |
| geospatial | `ghcr.io/fardani235/webodm-geospatial:1` | 5000 | Raster tile server |
| nodeodm | `opendronemap/nodeodm:latest` | 3000 | Photogrammetry engine |
| backup | `webodm-backup:1` | - | Scheduled backups + S3 sync |

---

## 2. Prerequisites

### 2.1 Byteplus VM Specifications

| Spec | Minimum | Recommended |
|------|---------|-------------|
| vCPU | 4 cores | 8 cores |
| RAM | 8 GB | 16 GB |
| Disk | 100 GB SSD | 200 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |

> **Note:** NodeODM photogrammetry is CPU and memory intensive. 8 cores + 16 GB is strongly recommended for production workloads.

### 2.2 Required Accounts & Credentials

Before starting, ensure you have:

1. **Byteplus account** with VM creation access
2. **Domain control** for `g20tech.site` (ability to add A records)
3. **Gmail account** with 2FA enabled (for App Password)
4. **AWS account** with:
   - **S3 bucket created** (e.g., `your-webodm-data`) in the same region as your VM
   - **IAM user** with S3 read/write access to that bucket
   - **Access Key ID + Secret Access Key** for that IAM user
5. **SSH key pair** for VM access

### 2.3 Information to Gather

Fill these in before deployment:

| Item | Your Value | Used In |
|------|------------|---------|
| VM public IP | `___.___.___.___` | DNS A records |
| Your office IP | `___.___.___.___` | Admin whitelist |
| Your home IP | `___.___.___.___` | Admin whitelist |
| Gmail address | `your-email@gmail.com` | SMTP / notifications |
| Gmail App Password | `xxxx xxxx xxxx xxxx` | SMTP authentication |
| AWS Access Key | `AKIA...` | S3 backup sync |
| AWS Secret Key | `...` | S3 data access |
| S3 bucket name | `your-webodm-data` | Primary file storage |
| Admin password | Strong password | Frappe Administrator account |

---

## 3. Phase 1: VM Setup (Byteplus)

### 3.1 Create VM

1. Log in to Byteplus console
2. Create ECS instance:
   - **Region:** Choose closest to your users
   - **Instance Type:** ecs.g7.2xlarge (8 vCPU, 16 GB) or equivalent
   - **Image:** Ubuntu 22.04 LTS (64-bit)
   - **Storage:** 200 GB SSD (expandable)
   - **Network:** Assign public IP
   - **Security Group:** Create new (see Phase 4)
   - **Key Pair:** Use existing or create new SSH key

3. Note the **public IP address** - you'll need it for DNS

### 3.2 Initial VM Setup

SSH into the VM and run:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Install utilities
sudo apt install -y git curl htop ncdu ufw fail2ban

# Verify Docker
docker --version
docker compose version

# Create deployment directory
mkdir -p ~/webodm-deploy && cd ~/webodm-deploy
```

---

## 4. Phase 2: DNS Configuration

### 4.1 Add A Records

At your DNS provider (where `g20tech.site` is managed), add:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | `daas` | `<VM_PUBLIC_IP>` | 300 |
| A | `admin` | `<VM_PUBLIC_IP>` | 300 |

This creates:
- `daas.g20tech.site`
- `admin.g20tech.site`

### 4.2 Verify DNS Propagation

```bash
# From your local machine
dig daas.g20tech.site +short
dig admin.g20tech.site +short
```

Both should return your VM's public IP. DNS propagation may take 5-60 minutes depending on your provider.

---

## 5. Phase 3: Project Configuration

### 5.1 Copy Project to VM

From your local development machine:

```bash
# Navigate to your project directory
cd /path/to/your/webodm-project

# Sync to VM (adjust user and IP)
rsync -avz --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='secrets/*.txt' \
  ./ user@<VM_PUBLIC_IP>:~/webodm-deploy/
```

> **Security note:** Never commit `.env` or `secrets/*.txt` to git. Transfer them separately or create them directly on the VM.

### 5.2 Create Production Environment File

On the VM, create `~/webodm-deploy/.env`:

```bash
cat > ~/webodm-deploy/.env << 'EOF'
# Domain & Site
SITE_DOMAIN=daas.g20tech.site
SITE_NAME=webodm.local
ADMIN_EMAIL=your-email@gmail.com

# Database (use strong passwords)
DB_NAME=webodm
DB_USER=webodm
DB_PASSWORD=<GENERATE_STRONG_PASSWORD>

# Frappe
ADMIN_PASSWORD=<GENERATE_STRONG_ADMIN_PASSWORD>

# Backup S3 Configuration
BACKUP_S3_BUCKET=your-webodm-backups
BACKUP_S3_ACCESS_KEY=AKIA...
BACKUP_S3_SECRET_KEY=...
BACKUP_S3_ENDPOINT=
BACKUP_SCHEDULE=0 3 * * *
BACKUP_RETENTION_DAYS=30

# Admin IP Whitelist (comma-separated, Caddy format)
ADMIN_WHITELIST=YOUR_OFFICE_IP YOUR_HOME_IP
EOF
```

Replace placeholders with actual values.

### 5.3 Generate Secrets

```bash
cd ~/webodm-deploy
mkdir -p secrets

# Generate cryptographically secure passwords
openssl rand -base64 32 > secrets/db_password.txt
openssl rand -base64 32 > secrets/redis_cache_password.txt
openssl rand -base64 32 > secrets/redis_queue_password.txt

# Set admin password (must match .env ADMIN_PASSWORD)
echo "your-strong-admin-password" > secrets/admin_password.txt

# Secure permissions
chmod 600 secrets/*
```

### 5.4 Update Caddyfile for Production

Edit `~/webodm-deploy/infra/caddy/Caddyfile`:

```caddy
# Auto-TLS via Let's Encrypt HTTP-01 challenge
{
    email admin@{$SITE_DOMAIN}
    admin off
}

# Admin subdomain - Frappe Desk UI with IP whitelist
admin.{$SITE_DOMAIN} {
    encode zstd gzip

    # IP whitelist - deny all except listed IPs
    @not_whitelisted {
        not remote_ip {$ADMIN_WHITELIST}
    }
    respond @not_whitelisted "Access Denied" 403

    # Root redirect to /desk
    @root path /
    redir @root /desk permanent

    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
        Content-Security-Policy "default-src 'self'; img-src 'self' data: blob: https:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; connect-src 'self' wss://admin.{$SITE_DOMAIN} blob:; worker-src 'self' blob:; font-src 'self' data: https://fonts.gstatic.com; frame-ancestors 'none'"
        -Server
    }

    # SocketIO
    @socketio path /socket.io/*
    reverse_proxy @socketio frappe-socketio:9000 {
        header_up Host {host}
        header_up X-Frappe-Site-Name {$SITE_DOMAIN}
    }

    # Static assets
    @assets path /assets/** /files/** /public/** /private/files/**
    reverse_proxy @assets frappe-web:8000 {
        header_up Host {$SITE_DOMAIN}
    }

    # Everything else
    reverse_proxy frappe-web:8000 {
        header_up Host {$SITE_DOMAIN}
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }
}

# Main domain - WebODM SPA
{$SITE_DOMAIN} {
    encode zstd gzip

    # Redirect root to SPA
    @root path /
    redir @root /assets/webodm_frontend/frontend/ permanent

    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
        Content-Security-Policy "default-src 'self'; img-src 'self' data: blob: https:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; connect-src 'self' wss://{$SITE_DOMAIN} blob:; worker-src 'self' blob:; font-src 'self' data: https://fonts.gstatic.com; frame-ancestors 'none'"
        -Server
    }

    # Rate limit login endpoint
    @login path /api/method/login
    rate_limit @login {
        zone login {
            match {
                path /api/method/login
            }
            key {remote_host}
            window 1s
            events 50
        }
    }

    # SocketIO
    @socketio path /socket.io/*
    reverse_proxy @socketio frappe-socketio:9000 {
        header_up Host {host}
    }

    # Do NOT add a route to geospatial:5000 here. The service has no auth of its
    # own; tiles are served through the session-authed Frappe proxy
    # (webodm_core.api.tiles), and the service is only on the backend network.

    # Static assets
    @assets path /assets/** /files/** /public/** /private/files/**
    reverse_proxy @assets frappe-web:8000

    # Everything else
    reverse_proxy frappe-web:8000 {
        header_up Host {host}
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

**Key changes from development:**
- Domains are now `daas.g20tech.site` and `admin.g20tech.site`
- IP whitelist on admin subdomain
- `header_up Host {$SITE_DOMAIN}` on admin subdomain's asset proxy (Frappe site resolution)
- `header_up X-Frappe-Site-Name {$SITE_DOMAIN}` on admin SocketIO proxy

### 5.5 Update Docker Compose

Edit `~/webodm-deploy/docker-compose.yml` - the following environment variables must be updated in the `caddy` service:

```yaml
services:
  caddy:
    # ... existing config ...
    environment:
      SITE_DOMAIN: ${SITE_DOMAIN:-daas.g20tech.site}
      ADMIN_EMAIL: ${ADMIN_EMAIL:-admin@daas.g20tech.site}
      ADMIN_WHITELIST: ${ADMIN_WHITELIST:-}
      # Remove Cloudflare/AWS DNS-01 vars if not using DNS challenge
      # CLOUDFLARE_API_TOKEN: ${CLOUDFLARE_API_TOKEN:-}
      # AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}
      # AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}
```

`live_reload` is managed by the entrypoint and defaults to off in containers
(set `FRAPPE_LIVE_RELOAD=1` in the Frappe services' environment to enable).

### 5.6 Update Site Configuration

The container's `sites/` directory is the `frappe_sites` **named volume**, not a
host bind mount, so editing the repo's `frappe-bench/sites/…` on the host has no
effect on the running container. Edit it through a container instead:

```bash
docker compose exec frappe-web bench --site webodm.local set-config <key> <value>
# or edit the file in place:
docker compose exec frappe-web vi /workspace/frappe-bench/sites/webodm.local/site_config.json
```

The resulting file should look like:

```json
{
  "db_host": "postgres",
  "db_name": "webodm",
  "db_password": "webodm",
  "db_port": 5432,
  "db_type": "postgres",
  "db_user": "webodm",
  "installed_apps": [
    "frappe",
    "webodm_core",
    "webodm_frontend"
  ],
  "max_file_size": 10737418240,
  "webserver_port": 8080,
  "allow_tests": false,
  "session_expiry": 3600,
  "password_reset_limit": 3,
  "deny_multiple_logins": false
}
```

**Changes:**
- `db_host`: `127.0.0.1` → `postgres` (Docker service name)
- Remove `allow_tests: true` (security)
- Add session expiry and password reset limits

### 5.7 Update Common Site Configuration

`common_site_config.json` also lives on the `frappe_sites` volume — edit it the
same way (`docker compose exec frappe-web vi /workspace/frappe-bench/sites/common_site_config.json`).
It should look like:

```json
{
  "background_workers": 1,
  "default_site": "webodm.local",
  "file_watcher_port": 6787,
  "frappe_user": "ridwan",
  "gunicorn_workers": 9,
  "live_reload": false,
  "rebase_on_pull": false,
  "redis_cache": "redis://127.0.0.1:13000",
  "redis_queue": "redis://127.0.0.1:11000",
  "redis_socketio": "redis://127.0.0.1:13000",
  "restart_supervisor_on_update": false,
  "restart_systemd_on_update": false,
  "root_login": "postgres",
  "root_password": "postgres",
  "serve_default_site": true,
  "shallow_clone": true,
  "socketio_port": 9000,
  "strip_exif_metadata_from_uploaded_images": 0,
  "use_redis_auth": false,
  "webserver_port": 8080,
  "geospatial_url": "http://geospatial:5000"
}
```

**Changes:**
- `live_reload`: `true` → `false`

---

## 6. Phase 4: Security Group & Firewall

### 6.1 Byteplus Security Group

Create or update the security group attached to your VM:

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | Your IP / 32 | SSH (restrict to your IP) |
| 80 | TCP | 0.0.0.0/0 | HTTP (Let's Encrypt challenge) |
| 443 | TCP | 0.0.0.0/0 | HTTPS |
| 8080 | TCP | Your IP / 32 | Frappe direct (emergency only) |

**Remove all other inbound rules.**

### 6.2 UFW (Host Firewall)

On the VM:

```bash
# Default deny
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (restrict to your IP)
sudo ufw allow from YOUR_IP to any port 22

# Allow HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable
sudo ufw enable

# Verify
sudo ufw status verbose
```

### 6.3 Fail2ban

```bash
# Install and configure fail2ban
sudo apt install -y fail2ban

# Create custom jail for Frappe login
sudo tee /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log

[frappe-login]
enabled = true
port = http,https
filter = frappe-login
logpath = /var/lib/docker/containers/*/*.log
maxretry = 10
EOF

# Create filter
sudo tee /etc/fail2ban/filter.d/frappe-login.conf << 'EOF'
[Definition]
failregex = ^.*POST /api/method/login.*403.*$
            ^.*POST /api/method/login.*401.*$
ignoreregex =
EOF

sudo systemctl restart fail2ban
sudo fail2ban-client status
```

---

## 7. Phase 5: Build & Deploy

### 7.1 Build Docker Images

```bash
cd ~/webodm-deploy

# Pull all images
docker compose pull
```

This pulls:
- `webodm-frappe:<version>@sha256:…` (includes bench, apps, and SPA assets)
- `webodm-caddy:2`
- `webodm-backup:1`
- `webodm-geospatial:1`

> **Note:** The `build:` blocks in `docker-compose.yml` are commented out, so
> `docker compose build` builds nothing — deploys pull prebuilt GHCR images. To
> rebuild locally after changing app code, see `frappe-bench/apps/Dockerfile`
> (and the `services/geospatial` Dockerfile for the geospatial image).

### 7.2 Start Infrastructure

```bash
# Start database and cache first
docker compose up -d postgres redis-cache redis-queue

# Wait for health checks (30-60 seconds)
sleep 30

# Verify
docker compose ps
```

### 7.3 Initialize Frappe Site

**First-time deployment only:**

```bash
docker compose run --rm frappe-init
```

Expected output:
```
[init] Creating site webodm.local on postgres://webodm:5432/webodm
...
*** Scheduler is disabled. Site name: webodm.local
```

**If re-deploying (site already exists):**

```bash
# Check if site exists
docker compose run --rm frappe-init
# Should output: "[init] Site webodm.local already exists; skipping bootstrap."
```

### 7.4 Start All Services

```bash
docker compose up -d

# Verify all services are healthy
docker compose ps

# Watch logs for errors
docker compose logs -f caddy
```

### 7.5 Set Frappe Administrator Password

```bash
docker compose exec frappe-web bench --site webodm.local set-admin-password "YOUR_STRONG_PASSWORD"
```

---

## 8. Phase 6: Post-Deployment Verification

### 8.1 Health Check Script

Create and run `~/webodm-deploy/verify.sh`:

```bash
#!/bin/bash
set -e

DOMAIN="daas.g20tech.site"
ADMIN_DOMAIN="admin.g20tech.site"

echo "=== WebODM Production Verification ==="
echo

# 1. HTTPS + API response
echo "[1/8] Testing main domain HTTPS..."
curl -fsSI "https://${DOMAIN}/api/method/ping" > /dev/null && echo "OK" || echo "FAIL"

# 2. Admin subdomain
echo "[2/8] Testing admin subdomain HTTPS..."
curl -fsSI "https://${ADMIN_DOMAIN}/api/method/ping" > /dev/null && echo "OK" || echo "FAIL"

# 3. Security headers
echo "[3/8] Checking security headers..."
curl -fsSI "https://${DOMAIN}/api/method/ping" | grep -iE '^(strict-transport|x-content|x-frame|referrer|permissions)' && echo "OK" || echo "FAIL"

# 4. SPA redirect
echo "[4/8] Testing SPA redirect..."
curl -fsSI "https://${DOMAIN}/" | grep -q "301" && echo "OK" || echo "FAIL"

# 5. Container health
echo "[5/8] Checking container health..."
docker compose ps | grep -E "( unhealthy|Exit )" && echo "FAIL" || echo "OK"

# 6. Database connectivity
echo "[6/8] Testing database..."
docker compose exec -T postgres pg_isready -U webodm -d webodm && echo "OK" || echo "FAIL"

# 7. Redis connectivity
echo "[7/8] Testing Redis cache..."
docker compose exec -T redis-cache redis-cli -p 13000 ping | grep -q PONG && echo "OK" || echo "FAIL"

# 8. NodeODM
echo "[8/8] Testing NodeODM..."
curl -fsS "http://localhost:3000/info" > /dev/null && echo "OK" || echo "FAIL"

echo
echo "=== Verification Complete ==="
```

```bash
chmod +x ~/webodm-deploy/verify.sh
~/webodm-deploy/verify.sh
```

### 8.2 Browser Verification

1. Open `https://daas.g20tech.site` - should redirect to SPA
2. Open `https://admin.g20tech.site` - should redirect to `/desk`
3. Login with Administrator / your password
4. Check browser console for:
   - No CSP errors
   - No SocketIO connection errors
   - No 404s for assets

### 8.3 Admin Whitelist Test

From a non-whitelisted network (e.g., mobile hotspot):
```bash
curl -I https://admin.g20tech.site
```
Should return `403 Access Denied`.

---

## 9. Phase 7: Email Setup (Gmail)

### 9.1 Generate Gmail App Password

1. Go to https://myaccount.google.com/security
2. Enable 2-Step Verification (required)
3. Go to https://myaccount.google.com/apppasswords
4. Generate app password for "Mail"
5. **Copy the 16-character password** (e.g., `abcd efgh ijkl mnop`)

### 9.2 Configure Frappe Email Account

**Option A: Via Frappe Desk (Recommended)**

1. Login to `https://admin.g20tech.site/desk`
2. Search "Email Account" in the awesome bar
3. Click "New"
4. Fill in:
   - **Email Account Name:** Notifications
   - **Email ID:** your-email@gmail.com
   - **Service:** Gmail
   - **Enable Outgoing:** checked
   - **Default Outgoing:** checked
   - **Password:** your-app-password (no spaces)
5. Save

**Option B: Via Bench CLI**

```bash
docker compose exec frappe-web bench --site webodm.local console << 'PYEOF'
import frappe

email = frappe.get_doc({
    "doctype": "Email Account",
    "email_account_name": "Notifications",
    "email_id": "your-email@gmail.com",
    "service": "GMail",
    "enable_outgoing": 1,
    "default_outgoing": 1,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "password": "your-app-password"
})
email.insert(ignore_permissions=True)
frappe.db.commit()
print("Email account created")
PYEOF
```

### 9.3 Test Email

```bash
# Send test email via bench
docker compose exec frappe-web bench --site webodm.local execute frappe.sendmail --kwargs "{'recipients':'your-email@gmail.com','subject':'WebODM Test','message':'Deployment successful!'}}"
```

---

## 10. Phase 8: AWS S3 Storage with Multi-Tenant Security

> **Superseded (2026-09).** The s3fs-based approach below predates native
> object storage support. The application now talks to S3 directly
> (`WEBODM_S3_*`, org-namespaced keys, serving cache, S3 → S3 COG
> conversion) and can provision processing nodes on demand. Follow
> [`docs/on-demand-processing/deployment.md`](../on-demand-processing/deployment.md)
> instead of this section; it is kept for deployments that already run it.

> **IMPORTANT:** S3FS must be configured **BEFORE** Phase 5 (Build & Deploy). Complete Section 10.3 (S3FS Setup) during Phase 3 (Project Configuration).

### 10.1 Security Warning: Multi-Tenant Data Isolation

This application supports **multiple users and organizations**. S3FS mounts the entire S3 bucket as a single POSIX filesystem, which means:

- **Any container with the mount** can access any file if it knows the path
- **Frappe's application-level permissions** (e.g., `task.check_permission("read")`) are the primary defense
- **File paths contain random hashes** (e.g., `rl8t2f25oe_orthophoto.tif`) — not guessable, but paths can leak via logs/errors

**To mitigate this risk, we use RESTRICTED MOUNTS — never mount the entire bucket into any container.**

| Container | What It Mounts | Why |
|-----------|---------------|-----|
| `frappe-web` | ❌ **Nothing from S3** | Keeps files in local Docker volume; Frappe permission checks are enforced at API level |
| `geospatial` | ✅ Read-only outputs | Only needs processed outputs for tile serving; cannot access uploads |
| `nodeodm` | ✅ Working directory only | Only its own temp processing data; cannot access user files |
| `backup` | ✅ Backups directory only | Writes backups; cannot read user data |

**Long-term:** Migrate to [Frappe S3 Attachment](https://github.com/zerodha/frappe-s3-attachment) for proper multi-tenancy with pre-signed URLs.

### 10.2 Why S3 for This Deployment?

| Data Type | Storage Location | Reason |
|-----------|-----------------|--------|
| **Processing outputs** (orthophoto, DSM, DTM, point cloud) | S3 `files/outputs/` | Large files, long-term retention |
| **NodeODM working data** | S3 `nodeodm/` | Survives container recreation |
| **Database backups** | S3 `backups/` | Disaster recovery |
| **User uploads** (drone images) | **Local Docker volume** | Fast access, protected by Frappe permissions |
| **Frappe site config** | Local Docker volume | Fast access, small files |
| **PostgreSQL database** | Local Docker volume | Low latency, transactional |

**Benefits:**
- **Durability:** S3 provides 99.999999999% durability for outputs and backups
- **Scalability:** No disk space limits for processing outputs
- **Portability:** Processing outputs survive VM recreation
- **Security:** User uploads stay in local volumes with Frappe permission enforcement

### 10.3 S3 Bucket Structure

Create the following structure in your S3 bucket (e.g., `s3://your-webodm-data/`):

```
your-webodm-data/
├── files/
│   └── outputs/           # Processing results (orthophoto.tif, etc.)
├── nodeodm/
│   └── data/              # NodeODM working directory
└── backups/
    └── webodm/            # Database backups (auto-synced)
```

> **No `uploads/` directory** — user uploads stay in the local Docker volume for security.

### 10.4 S3FS Setup on VM (Required)

S3FS mounts the S3 bucket as a local filesystem, making it transparent to Docker containers.

```bash
# Install S3FS
sudo apt install -y s3fs

# Create credentials file
echo "AKIA...:secret" > ~/.passwd-s3fs
chmod 600 ~/.passwd-s3fs

# Create mount point
sudo mkdir -p /mnt/s3-webodm
sudo chown $USER:$USER /mnt/s3-webodm

# Create local cache directory
sudo mkdir -p /tmp/s3fs-cache
sudo chown $USER:$USER /tmp/s3fs-cache

# Mount S3 bucket
s3fs your-webodm-data /mnt/s3-webodm \
  -o passwd_file=~/.passwd-s3fs \
  -o nonempty \
  -o allow_other \
  -o use_cache=/tmp/s3fs-cache \
  -o uid=$(id -u) \
  -o gid=$(id -g) \
  -o umask=022

# Create directory structure
mkdir -p /mnt/s3-webodm/files/outputs
mkdir -p /mnt/s3-webodm/nodeodm/data
mkdir -p /mnt/s3-webodm/backups/webodm

# Add to /etc/fstab for persistence
echo "s3fs#your-webodm-data /mnt/s3-webodm fuse _netdev,passwd_file=/home/$(whoami)/.passwd-s3fs,nonempty,allow_other,use_cache=/tmp/s3fs-cache,uid=$(id -u),gid=$(id -g),umask=022 0 0" | sudo tee -a /etc/fstab

# Verify mount
df -h /mnt/s3-webodm
ls -la /mnt/s3-webodm/
```

> **Note:** S3FS performance is acceptable for sequential reads/writes (large raster files) but slower for many small files. The local cache (`/tmp/s3fs-cache`) improves performance for repeated reads.

### 10.5 Update Docker Compose for Restricted S3 Mounts

Update `docker-compose.yml` — **only mount specific subdirectories per service**:

```yaml
services:
  frappe-web:
    volumes:
      # Keep ALL files local — Frappe enforces permissions at API level
      - frappe_sites:/workspace/frappe-bench/sites
      - frappe_assets:/workspace/frappe-bench/sites/assets
      - frappe_logs:/workspace/frappe-bench/logs
      - frappe_data:/data
      # ❌ DO NOT mount S3 uploads here — keep uploads local for security

  geospatial:
    volumes:
      # Read-only access to processed outputs only
      - /mnt/s3-webodm/files/outputs:/data/outputs:ro
      # frappe_sites for file path resolution (read-only)
      - frappe_sites:/workspace/frappe-bench/sites:ro

  nodeodm:
    volumes:
      # Only NodeODM's own working data
      - /mnt/s3-webodm/nodeodm:/var/www/data
      # ❌ DO NOT mount user uploads or outputs here

  backup:
    volumes:
      - .:/host:ro
      - frappe_sites:/workspace/frappe-bench/sites:ro
      # Backups written directly to S3
      - /mnt/s3-webodm/backups:/backups
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

**Key changes from default:**
- `frappe-web`: **No S3 mounts** — uploads and outputs stay in local Docker volume
- `geospatial`: **Read-only outputs** — can only read processed outputs, not uploads
- `nodeodm`: **Working data only** — cannot access user files or outputs
- `backup`: **Backups only** — writes to S3, cannot read user data

### 10.6 Sync Processing Outputs to S3

Since `frappe-web` keeps files locally, processing outputs must be **synced to S3** after NodeODM completes. This is handled by the `poll_task` job in `task_runner.py` — after downloading assets from NodeODM, it saves them as Frappe Files (local) and also copies to S3:

```python
# In webodm_core/processing/task_runner.py (existing behavior)
# After downloading orthophoto.tif, dsm.tif, etc.:

# 1. Save as Frappe File (local Docker volume)
file_doc = frappe.get_doc({
    "doctype": "File",
    "file_name": f"{task.name}_orthophoto.tif",
    "file_url": f"/private/files/outputs/{task.name}_orthophoto.tif",
    "attached_to_doctype": "WebODM Task",
    "attached_to_name": task.name,
})
file_doc.save()

# 2. Copy to S3 (mounted at /data/outputs via geospatial service)
# Or use aws cli from the worker container
```

**For now, outputs are served locally.** In a future update, implement a sync job that copies completed outputs to S3 and updates file URLs.

### 10.7 Backup to S3 (Already Configured)

The `backup` service writes database backups directly to S3:

```bash
# Check backup service logs
docker compose logs backup

# Trigger manual backup
docker compose exec backup /usr/local/bin/backup.sh

# Verify backup in S3
aws s3 ls s3://your-webodm-data/backups/webodm/ --recursive
```

### 10.8 Verify S3 Integration

After deployment, verify the restricted mounts work:

```bash
# 1. Verify geospatial can read outputs
docker compose exec geospatial ls -la /data/outputs/

# 2. Verify geospatial CANNOT read uploads (should fail)
docker compose exec geospatial ls /workspace/frappe-bench/sites/webodm.local/private/files/ 2>&1 | grep -q "No such file" && echo "PASS: Uploads not accessible" || echo "FAIL"

# 3. Verify nodeodm only has working data
docker compose exec nodeodm ls -la /var/www/data/

# 4. Verify backups are written to S3
docker compose exec backup touch /backups/test.txt
aws s3 ls s3://your-webodm-data/backups/webodm/test.txt && echo "PASS: Backups work" || echo "FAIL"
docker compose exec backup rm /backups/test.txt
```

### 10.9 Future Enhancement: Frappe S3 Attachment

For true multi-tenant isolation with pre-signed URLs:

1. Install [frappe-s3-attachment](https://github.com/zerodha/frappe-s3-attachment)
2. Configure per-user S3 prefixes
3. Frappe generates pre-signed URLs (time-limited, permission-checked)
4. No direct filesystem mounts needed

**Trade-off:** Requires rebuilding the Docker image and code changes. Plan for v2.0.

---

## 11. Phase 10: Security Hardening

### 11.1 Immediate Actions

```bash
# 1. Change default passwords
docker compose exec frappe-web bench --site webodm.local set-admin-password "NEW_STRONG_PASSWORD"

# 2. Disable test mode
docker compose exec frappe-web bench --site webodm.local set-config allow_tests false

# 3. Set session expiry (1 hour)
docker compose exec frappe-web bench --site webodm.local set-config session_expiry 3600

# 4. Enable CSRF protection (should already be enabled)
docker compose exec frappe-web bench --site webodm.local set-config ignore_csrf false
```

### 11.2 Regular Maintenance

```bash
# Weekly: Update system packages
sudo apt update && sudo apt upgrade -y

# Weekly: Update Docker images
cd ~/webodm-deploy
docker compose pull
docker compose up -d

# Weekly: Review logs
sudo journalctl -u docker --since "1 week ago" | tail -100

# Monthly: Rotate secrets
# Generate new passwords and update secrets/*.txt
# Restart containers: docker compose restart

# Monthly: Review fail2ban
cat /var/log/fail2ban.log | tail -50
```

### 11.3 Docker Security Best Practices

```bash
# Enable Docker Content Trust
export DOCKER_CONTENT_TRUST=1

# Review running containers
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"

# Check for exposed ports (should only see Caddy on 80/443)
docker ps --format "{{.Names}}: {{.Ports}}"

# Scan images for vulnerabilities (install trivy)
# curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh
# trivy image ghcr.io/fardani235/webodm-frappe:<version>
```

---

## 12. Phase 11: Backup & Recovery

### 12.1 Automated Backups

Backups run daily at 3 AM UTC via the `backup` container. The schedule is controlled by:

```bash
# In .env
BACKUP_SCHEDULE=0 3 * * *
```

### 12.2 Manual Backup

```bash
cd ~/webodm-deploy

# Trigger immediate backup
docker compose exec backup /usr/local/bin/backup.sh

# Check backup files
ls -la /var/lib/docker/volumes/g20-daas_backup_storage/_data/
```

### 12.3 Backup Verification

```bash
# List backups in S3
docker compose exec backup aws s3 ls s3://your-webodm-backups/webodm/ --recursive
```

### 12.4 Disaster Recovery

**Scenario: Complete data loss**

```bash
# 1. Stop all services
cd ~/webodm-deploy
docker compose down

# 2. Remove data volumes (DANGEROUS - only in disaster)
docker volume rm g20-daas_postgres_data g20-daas_frappe_sites g20-daas_frappe_data

# 3. Recreate volumes
docker compose up -d postgres redis-cache redis-queue
sleep 30

# 4. Initialize fresh site
docker compose run --rm frappe-init

# 5. Download latest backup from S3
docker compose exec backup aws s3 sync s3://your-webodm-backups/webodm/ /tmp/restore/

# 6. Restore database and files
docker compose exec frappe-web bench --site webodm.local restore \
  /tmp/restore/<timestamp>-webodm-local-database.sql.gz \
  --with-private-files /tmp/restore/<timestamp>-webodm-local-private-files.tar

# 7. Restart all services
docker compose up -d
```

### 12.5 Database-Only Backup (Quick)

```bash
docker compose exec frappe-web bench --site webodm.local backup --with-files
```

---

## 13. Troubleshooting

### 13.1 Caddy Won't Start (Port Binding)

```bash
# Check if ports 80/443 are already in use
sudo ss -tlnp | grep -E ':80|:443'

# If another process is using them, stop it
sudo systemctl stop apache2 nginx

# Or check Caddy logs
docker compose logs caddy
```

### 13.2 Let's Encrypt Certificate Failures

```bash
# Check DNS resolution from VM
dig daas.g20tech.site +short
dig admin.g20tech.site +short

# Force certificate renewal
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile

# Check Caddy's internal certificate storage
docker compose exec caddy ls -la /data/caddy/certificates/
```

### 13.3 Frappe Site Not Found (404 on /api/method/ping)

```bash
# Check if site exists
docker compose exec frappe-web ls -la /workspace/frappe-bench/sites/

# Verify site_config.json is present
docker compose exec frappe-web cat /workspace/frappe-bench/sites/webodm.local/site_config.json

# Check Frappe logs
docker compose logs frappe-web

# Re-run init if needed
docker compose run --rm frappe-init
```

### 13.4 SocketIO Connection Errors

Symptom: Browser console shows "Invalid namespace" or "Connection refused"

```bash
# Check SocketIO container
docker compose logs frappe-socketio

# Verify SocketIO is listening
docker compose exec frappe-socketio ss -tlnp | grep 9000

# Test from web container
docker compose exec frappe-web curl -s http://frappe-socketio:9000/socket.io/?EIO=4&transport=polling
```

**Common fixes:**
- Ensure `X-Frappe-Site-Name` header is set correctly in Caddyfile
- Verify `common_site_config.json` has correct `socketio_port`
- Check that `redis_socketio` is configured in `common_site_config.json`

### 13.5 CSS/JS Assets 404

Symptom: Desk UI shows plain HTML without styling

```bash
# Clear Frappe asset cache
docker compose exec frappe-web bench --site webodm.local clear-cache
docker compose exec frappe-web bench --site webodm.local clear-website-cache

# Rebuild assets if needed
docker compose exec frappe-web bench build --production

# Check asset symlinks
docker compose exec frappe-web ls -la /workspace/frappe-bench/sites/assets/
```

### 13.6 Database Connection Errors

```bash
# Check PostgreSQL
docker compose logs postgres
docker compose exec postgres pg_isready -U webodm

# Verify connection from Frappe
docker compose exec frappe-web python3 -c "
import psycopg2
conn = psycopg2.connect(host='postgres', dbname='webodm', user='webodm', password='webodm')
print('Connected')
conn.close()
"
```

### 13.7 Out of Disk Space

```bash
# Check disk usage
sudo ncdu /

# Clean Docker
docker system prune -a --volumes

# Check volume sizes
docker system df -v

# Resize VM disk (Byteplus console) then extend partition
sudo growpart /dev/vda 1
sudo resize2fs /dev/vda1
```

### 13.8 High Memory Usage

```bash
# Check container memory
docker stats --no-stream

# Adjust limits in docker-compose.yml if needed
# Add swap (if not already present)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 14. Rollback Procedure

### 14.1 Rolling Back a Bad Deployment

```bash
cd ~/webodm-deploy

# 1. Stop current containers
docker compose down

# 2. Revert git changes (if you version control your config)
git checkout -- docker-compose.yml infra/caddy/Caddyfile .env

# 3. Pull images and restart
docker compose pull
docker compose up -d

# 4. Verify
docker compose ps
```

### 14.2 Emergency Site Restore

If the site becomes corrupted:

```bash
cd ~/webodm-deploy

# 1. Stop web services
docker compose stop frappe-web frappe-scheduler frappe-worker

# 2. Find latest backup
LATEST_BACKUP=$(docker compose exec backup ls -t /backups/*-database.sql.gz | head -1)
echo "Restoring: $LATEST_BACKUP"

# 3. Restore
docker compose exec frappe-web bench --site webodm.local restore "$LATEST_BACKUP"

# 4. Restart
docker compose up -d
```

### 14.3 Complete Rebuild

**Nuclear option** - destroy everything and start fresh:

```bash
cd ~/webodm-deploy

# 1. Stop and remove everything
docker compose down -v

# 2. Remove all data (IRREVERSIBLE)
docker volume rm $(docker volume ls -q | grep g20-daas)

# 3. Pull images
docker compose pull

# 4. Start fresh
docker compose up -d postgres redis-cache redis-queue
sleep 30
docker compose run --rm frappe-init
docker compose up -d
```

---

## Appendix A: File Reference

| File | Purpose | Edited in Deploy? |
|------|---------|-------------------|
| `.env` | Environment variables | Yes |
| `docker-compose.yml` | Service orchestration | Yes (live_reload, Caddy env, **S3 volume mounts**) |
| `infra/caddy/Caddyfile` | Reverse proxy + TLS | Yes (domains, whitelist) |
| `frappe-bench/sites/webodm.local/site_config.json` | Per-site config (on `frappe_sites` named volume) | Yes (db_host, security — via container) |
| `frappe-bench/sites/common_site_config.json` | Global config (on `frappe_sites` named volume) | Yes (live_reload — via container) |
| `secrets/*.txt` | Passwords | Yes (generate new) |
| `~/.passwd-s3fs` | S3 credentials | Yes (new file) |
| `/etc/fstab` | S3FS persistence | Yes (add mount entry) |
| `frappe-bench/apps/Dockerfile` | Frappe image build | No |
| `frappe-bench/apps/webodm_frontend/frontend/vite.config.js` | Dev server config | No (production uses build output) |

## Appendix B: Useful Commands Cheat Sheet

```bash
# View logs
docker compose logs -f <service>
docker compose logs --tail 100 <service>

# Restart service
docker compose restart <service>

# Scale workers
docker compose up -d --scale frappe-worker=3

# Enter container shell
docker compose exec <service> bash

# Run bench commands
docker compose exec frappe-web bench --site webodm.local <command>

# Database console
docker compose exec postgres psql -U webodm -d webodm

# Redis CLI
docker compose exec redis-cache redis-cli -p 13000

# Check resource usage
docker stats

# Clean up
docker system prune -a --volumes
```

## Appendix C: Monitoring Setup (Optional)

### C.1 Docker Stats Dashboard

```bash
# Install ctop for container monitoring
sudo wget https://github.com/bcicen/ctop/releases/download/v0.7.7/ctop-0.7.7-linux-amd64 -O /usr/local/bin/ctop
sudo chmod +x /usr/local/bin/ctop
ctop
```

### C.2 Basic Log Monitoring

```bash
# Watch for errors in real-time
docker compose logs -f | grep -i error

# Count HTTP 500s
docker compose logs frappe-web | grep " 500 " | wc -l
```

### C.3 Disk Space Alert

Add to crontab:

```bash
# Alert if disk > 80%
crontab -l > /tmp/cron
echo "0 * * * * df -h / | awk '\$5 > 80 {print \"DISK ALERT: \" \$0}' | mail -s 'WebODM Disk Alert' admin@example.com" >> /tmp/cron
crontab /tmp/cron
```

---

**End of Deployment Guide**

*Last updated: 2026-08-28*  
*For questions or updates, refer to the project README or AGENTS.md*
