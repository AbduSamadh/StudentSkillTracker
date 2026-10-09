#!/usr/bin/env bash
# Starts the full demo stack (docker compose) and seeds the fictional demo school.
# Safe to run again: images are reused and seeding skips a school that already exists.
# The Codespaces / dev container runs this on start, and CI runs it to prove it works.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  # In a codespace the app is reached through a forwarded https address, not localhost; links in
  # parent messages (opt-out, magic sign-in) should point there.
  if [ -n "${CODESPACE_NAME:-}" ]; then
    url="https://${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
    sed -i "s#^STEM_PUBLIC_BASE_URL=.*#STEM_PUBLIC_BASE_URL=${url}#" .env
  fi
fi

echo "Building and starting the demo. The first time takes about 5-10 minutes..."
docker compose up -d --build

echo "Waiting for the app to answer..."
for _ in $(seq 1 120); do
  curl -sf http://localhost:8080/api/v1/health >/dev/null && break
  sleep 2
done
curl -sf http://localhost:8080/api/v1/health >/dev/null || {
  echo "The app did not start. Recent logs:" >&2
  docker compose logs --no-color --tail 80 >&2
  exit 1
}

docker compose run --rm api python -m app.cli seed-demo

cat <<'EOF'

============================================================================
 The demo is running on port 8080.
 In Codespaces: open the "Ports" tab and click the globe next to 8080.

 School code: demo        Password for every account: Demo-Password-2026!
   Teacher  sara.haddad@demo.school.example
   Parent   parent@family.example
   Leader   leader@demo.school.example   (asks for a 6-digit code)
   Admin    admin@demo.school.example    (asks for a 6-digit code)

 Get a 6-digit code (paste into this terminal):
   docker compose run --rm api python -m app.cli totp --secret STEMLEADERDEMOSECRETKEYA
   docker compose run --rm api python -m app.cli totp --secret STEMADMINDEMOSECRETKEYAB
============================================================================
EOF
