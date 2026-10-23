#!/bin/sh
# The master secret that encrypts the provider keys (app/credentials.py): taken from AGENT_FACTORY_SECRET_KEY when it is
# set; otherwise made once, kept in the data volume, and read back on every start. So `docker compose up` needs no setup,
# and the secret survives restarts and rebuilds (it dies only with the volume: back the volume up).
set -e
if [ -z "${AGENT_FACTORY_SECRET_KEY:-}" ]; then
  DATA_DIR="${AGENT_FACTORY_DATA_DIR:-/data}"
  KEYFILE="$DATA_DIR/secret_key"
  mkdir -p "$DATA_DIR"
  if [ ! -s "$KEYFILE" ]; then
    umask 077
    python -c "import secrets; print(secrets.token_urlsafe(48))" > "$KEYFILE"
    echo "agent-factory: made a master secret in $KEYFILE (the data volume): back that volume up." >&2
  fi
  AGENT_FACTORY_SECRET_KEY="$(cat "$KEYFILE")"
  export AGENT_FACTORY_SECRET_KEY
fi
exec "$@"
