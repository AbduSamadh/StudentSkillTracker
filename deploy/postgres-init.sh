#!/bin/sh
# First-boot initialisation for the compose Postgres: the two roles and the database.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE ROLE stem_owner LOGIN PASSWORD '${STEM_OWNER_PASSWORD}';
CREATE ROLE stem_app LOGIN PASSWORD '${STEM_APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE stemtrack OWNER stem_owner;
SQL
