#!/bin/bash
# Kubernetes edition of infra/postgres/init-frappe-admin.sh: creates the
# superuser Frappe's installer uses. The password arrives as an environment
# variable (from the credentials Secret) instead of a Docker secret file.
set -e

: "${FRAPPE_ROOT_PASSWORD:?FRAPPE_ROOT_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 -v pw="$FRAPPE_ROOT_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    CREATE USER frappe_admin WITH SUPERUSER PASSWORD :'pw';
    ALTER USER frappe_admin SET client_encoding = 'UTF8';
    ALTER USER frappe_admin SET default_transaction_isolation = 'read committed';
    ALTER USER frappe_admin SET timezone = 'UTC';
    CREATE DATABASE frappe_admin OWNER frappe_admin;
EOSQL
