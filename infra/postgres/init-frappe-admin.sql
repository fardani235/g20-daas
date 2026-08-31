-- Create frappe_admin superuser for Frappe root operations.
-- This user is used by Frappe's get_root_connection() to drop/create the
-- application database during bench new-site. Using a separate superuser
-- avoids the "can't drop database you're connected to" issue that occurs
-- when root_login matches the application database name.
CREATE USER frappe_admin WITH SUPERUSER PASSWORD '31qVUGR0BSyoKjnJhFDCksvT' CONNECTION LIMIT -1;
ALTER USER frappe_admin WITH SUPERUSER;
ALTER USER frappe_admin SET client_encoding = 'UTF8';
ALTER USER frappe_admin SET default_transaction_isolation = 'read committed';
ALTER USER frappe_admin SET timezone = 'UTC';

-- Create a default database for frappe_admin so psycopg2 can connect
-- without specifying a database name (it defaults to a DB matching the username).
CREATE DATABASE frappe_admin OWNER frappe_admin;
