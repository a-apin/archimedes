#!/bin/sh
# Runs once, as the `postgres` user, inside the postgres:18-alpine entrypoint
# (docker-entrypoint-initdb.d) before the server accepts connections.
#
# Why not generate the pair here: postgres:18-alpine ships no `openssl` CLI, so
# run.sh generates it on the host and mounts it read-only at /repro-ssl. The
# copy is what matters — postgres refuses a key file that is group/world
# readable or not owned by the server user, and a bind-mounted file is neither
# chmod-able nor postgres-owned.
set -eu

cp /repro-ssl/server.crt "$PGDATA/server.crt"
cp /repro-ssl/server.key "$PGDATA/server.key"
chmod 600 "$PGDATA/server.key"
chmod 644 "$PGDATA/server.crt"

# `ssl = on` is enabled HERE rather than via the compose `command:` because the
# entrypoint applies command-line options to the *bootstrap* server it runs
# before this script — and at that point the cert does not exist yet, so the
# bootstrap server dies with "could not load server certificate file". Writing
# postgresql.conf instead defers ssl to the final `exec postgres`, which starts
# after every initdb.d script has run.
cat >> "$PGDATA/postgresql.conf" <<'CONF'

# repro-1632: TLS, so libpq (sslmode=prefer, as in prod) negotiates a real
# session on psycopg2-binary's bundled OpenSSL.
ssl = on
ssl_cert_file = 'server.crt'
ssl_key_file = 'server.key'
CONF

echo "repro-1632: TLS key/cert installed in $PGDATA and ssl=on written to postgresql.conf"
