-- Create frappe_admin superuser for Frappe root operations.
-- This user is used by Frappe's get_root_connection() to drop/create the
-- application database during bench new-site. Using a separate superuser
-- avoids the "can't drop database you're connected to" issue that occurs
-- when root_login matches the application database name.
CREATE USER frappe_admin WITH SUPERUSER PASSWORD '31qVUGR0BSyoKjnJhFDCksvT';
