#!/usr/bin/env bash
# Builds deploy/portal.zip: the portal backend, the SDK, and the built frontend, ready for `az webapp deploy`.
# Run from anywhere. Needs Python 3.12, pip, Node 20+ and npm.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$root/portal/frontend" && npm ci && npm run build)
mkdir -p "$work/app/wheels" "$work/app/static"
python3 -m pip wheel --no-deps -w "$work/app/wheels" "$root/foundry-finops" "$root/portal/backend"
cp -r "$root/portal/frontend/app/dist/." "$work/app/static/"

cat > "$work/app/requirements.txt" <<REQ
--find-links ./wheels
foundry-finops
foundry-finops-portal
REQ
(cd "$work/app" && zip -qr "$here/portal.zip" .)
echo "Wrote $here/portal.zip"
echo "Deploy it with: az webapp deploy -g <rg> -n <web-app-name> --src-path $here/portal.zip --type zip"
