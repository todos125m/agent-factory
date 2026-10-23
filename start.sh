#!/bin/sh
# One command: build and start Agent Factory in Docker, wait until it answers, open it.
set -e
command -v docker >/dev/null 2>&1 || { echo "Docker is not installed (https://docs.docker.com/get-docker/)." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running: start Docker Desktop and run this again." >&2; exit 1; }
docker compose up -d --build
PORT="${AGENT_FACTORY_PORT:-8000}"
printf "Waiting for http://localhost:%s ..." "$PORT"
i=0
until curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 90 ] && { echo; echo "It did not answer in 3 minutes. See: docker compose logs api" >&2; exit 1; }
  printf "."; sleep 2
done
echo " ready."
echo "Open: http://localhost:$PORT/app     (stop: docker compose stop     logs: docker compose logs -f api)"
( xdg-open "http://localhost:$PORT/app" || open "http://localhost:$PORT/app" ) >/dev/null 2>&1 || true
