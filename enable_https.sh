#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

read -rp "Dashboard domain (example pbf-temp.company.com): " DOMAIN
read -rp "Viewer username [pbfviewer]: " VIEWUSER
VIEWUSER=${VIEWUSER:-pbfviewer}
read -rsp "Viewer password: " VIEWPASS
echo

if [ -z "$DOMAIN" ] || [ -z "$VIEWPASS" ]; then
  echo "Domain and password are required."
  exit 1
fi

HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$VIEWPASS")
cat > Caddyfile <<CADDY
$DOMAIN {
    encode zstd gzip
    basic_auth {
        $VIEWUSER $HASH
    }
    reverse_proxy dashboard:8000
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }
}
CADDY

unset VIEWPASS HASH

docker compose -f docker-compose.yml -f docker-compose.https.yml up -d

echo "HTTPS proxy started. DNS for $DOMAIN must point to this VPS public IPv4."
