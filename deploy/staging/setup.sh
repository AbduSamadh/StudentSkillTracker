#!/usr/bin/env bash
# Starts or updates the staging site on this server: the app in staging mode behind Caddy, with a
# free HTTPS certificate for STAGING_DOMAIN, holding only the fictional demo school.
#
# First run (deploy/staging/azure-create.sh does this for you on a new Azure server):
#   STAGING_DOMAIN=stemtrack-ab12cd.uaenorth.cloudapp.azure.com deploy/staging/setup.sh
# Later runs need no settings: strong secrets are generated once and kept in .env (never in git),
# images are rebuilt from the current code, migrations run, and seeding skips the existing school.
set -euo pipefail
cd "$(dirname "$0")/../.."
compose=(docker compose -f docker-compose.yml -f deploy/staging/docker-compose.staging.yml)

if [ ! -f .env ]; then
  : "${STAGING_DOMAIN:?set STAGING_DOMAIN to the DNS name of this server}"
  secret() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-40; }
  fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }
  umask 077
  cat > .env <<EOF
# Staging settings, generated $(date -u +%Y-%m-%dT%H:%M:%SZ). Keep this file on the server only.
STEM_ENVIRONMENT=staging
STAGING_DOMAIN=${STAGING_DOMAIN}
STEM_PUBLIC_BASE_URL=https://${STAGING_DOMAIN}
POSTGRES_PASSWORD=$(secret)
STEM_OWNER_PASSWORD=$(secret)
STEM_APP_PASSWORD=$(secret)
STEM_BACKUP_PASSWORD=$(secret)
STEM_JWT_SECRET=$(secret)
STEM_BLIND_INDEX_KEY=$(secret)
STEM_FIELD_ENCRYPTION_KEY=$(fernet_key)
EOF
  echo "Wrote .env with generated secrets for ${STAGING_DOMAIN}."
fi
domain="$(sed -n 's/^STAGING_DOMAIN=//p' .env)"

echo "Building and starting the staging site. The first time takes about 10 minutes..."
"${compose[@]}" up -d --build

echo "Waiting for the app to answer..."
api_healthy() {
  "${compose[@]}" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)" \
    >/dev/null 2>&1
}
for _ in $(seq 1 90); do api_healthy && break; sleep 2; done
api_healthy || {
  echo "The app did not start. Recent logs:" >&2
  "${compose[@]}" logs --no-color --tail 80 >&2
  exit 1
}

"${compose[@]}" run --rm api python -m app.cli seed-demo

# A real DNS name needs a publicly trusted certificate, which can take a minute to issue.
# "localhost" (CI) uses Caddy's own test CA, so only there is certificate checking relaxed.
curl_opts=(-sf --max-time 10)
[ "$domain" = localhost ] && curl_opts+=(-k)
for _ in $(seq 1 30); do
  curl "${curl_opts[@]}" "https://${domain}/api/v1/health" >/dev/null && break
  sleep 4
done
if curl "${curl_opts[@]}" "https://${domain}/api/v1/health" >/dev/null; then
  https_status="Ready: https://${domain}"
else
  https_status="The app is running, but https://${domain} is not answering yet. If it still fails in
 10 minutes, check that ports 80 and 443 are open: docker compose logs caddy"
fi

cat <<EOF

============================================================================
 ${https_status}

 School code: demo        Password for every account: Demo-Password-2026!
   Teacher  sara.haddad@demo.school.example
   Parent   parent@family.example
   Leader   leader@demo.school.example   (asks for a 6-digit code)
   Admin    admin@demo.school.example    (asks for a 6-digit code)

 6-digit codes: add the key to an authenticator app (Enter a setup key):
   Leader STEMLEADERDEMOSECRETKEYA      Admin STEMADMINDEMOSECRETKEYAB
============================================================================
EOF
