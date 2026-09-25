#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${1:-http://127.0.0.1:8080}"
if command -v curl >/dev/null; then
  health="$(curl --fail --silent --show-error --max-time 5 "$BASE_URL/api/health")"
  containers="$(curl --fail --silent --show-error --max-time 5 "$BASE_URL/api/containers")"
else
  health="$(podman exec podmon /opt/podmon-venv/bin/python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/api/health').read().decode())")"
  containers="$(podman exec podmon /opt/podmon-venv/bin/python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/api/containers').read().decode())")"
fi
[[ "$health" == *'"status":"ok"'* ]]
[[ "$containers" == *'"socket":"reachable"'* ]]
echo 'Health OK; Podman socket reachable.'
