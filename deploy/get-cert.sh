#!/usr/bin/env bash
# Issue a Let's Encrypt cert and drop it where the nginx prod overlay expects it
# (./deploy/nginx/certs/{fullchain,privkey}.pem).
#
# Uses certbot in *standalone* mode: certbot binds :80 itself and answers the
# http-01 challenge, so it must run BEFORE `docker compose up` brings nginx up
# (nothing else may hold :80). A wildcard-DNS host needs no DNS setup:
#   ./deploy/get-cert.sh 203-0-113-7.sslip.io you@example.com
# (sslip.io resolves <ip-with-dashes>.sslip.io to that IP, and Let's Encrypt
#  will issue for it.)
#
# Certs are valid 90 days. To renew: stop nginx, re-run this, start nginx again
# — or wire a `certbot renew` cron + nginx reload for unattended renewal.
set -euo pipefail

DOMAIN="${1:?usage: get-cert.sh <domain> <email>}"
EMAIL="${2:?usage: get-cert.sh <domain> <email>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LE_DIR="$REPO/deploy/letsencrypt"      # certbot's state (account + archive)
CERTS_DIR="$REPO/deploy/nginx/certs"   # what nginx mounts
mkdir -p "$LE_DIR" "$CERTS_DIR"

echo ">> Issuing Let's Encrypt cert for $DOMAIN (http-01, standalone on :80)…"
echo "   Make sure nginx is NOT running yet (port 80 must be free)."
docker run --rm -p 80:80 \
  -v "$LE_DIR:/etc/letsencrypt" \
  certbot/certbot certonly --standalone \
    -d "$DOMAIN" \
    --email "$EMAIL" --agree-tos --no-eff-email --non-interactive

# cp -L dereferences certbot's live/ symlinks; run in a container to avoid host root.
echo ">> Copying cert into $CERTS_DIR …"
docker run --rm \
  -v "$LE_DIR:/etc/letsencrypt:ro" \
  -v "$CERTS_DIR:/out" \
  --entrypoint sh certbot/certbot -c "
    cp -L /etc/letsencrypt/live/$DOMAIN/fullchain.pem /out/fullchain.pem &&
    cp -L /etc/letsencrypt/live/$DOMAIN/privkey.pem  /out/privkey.pem &&
    chmod 644 /out/fullchain.pem /out/privkey.pem"

echo ">> Done. Now bring the stack up:"
echo "   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
