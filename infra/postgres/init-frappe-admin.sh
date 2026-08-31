#!/bin/bash
set -e

# Read the frappe_admin password from the mounted secret file.
# The secret is mounted at /run/secrets/frappe_admin_password by docker-compose.
PASSWORD_FILE="/run/secrets/frappe_admin_password"
if [ ! -f "$PASSWORD_FILE" ]; then
  echo "ERROR: $PASSWORD_FILE not found"
  exit 1
fi
PASSWORD=$(cat "$PASSWORD_FILE")

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER frappe_admin WITH SUPERUSER PASSWORD '$PASSWORD';
    ALTER USER frappe_admin SET client_encoding = 'UTF8';
    ALTER USER frappe_admin SET default_transaction_isolation = 'read committed';
    ALTER USER frappe_admin SET timezone = 'UTC';
    CREATE DATABASE frappe_admin OWNER frappe_admin;
EOSQL
