#!/bin/sh
# Runs only on the first start, with an empty volume. The application role
# owns its database and is not a superuser.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v app_db="$DB_NAME" -v app_user="$DB_USER" -v app_password="$DB_PASSWORD" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password';
CREATE DATABASE :"app_db" OWNER :"app_user";
SQL
