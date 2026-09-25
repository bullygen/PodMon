#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAN_IP="${1:?Usage: install_server.sh LAN_IP}"
if [[ "$EUID" -eq 0 ]]; then echo 'Run as the rootless Podman user, not root' >&2; exit 1; fi
if [[ ! "$LAN_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then echo 'LAN_IP must be an IPv4 address' >&2; exit 1; fi
if [[ "$(loginctl show-user "$USER" -p Linger --value)" != yes ]]; then
  if command -v sudo >/dev/null; then sudo loginctl enable-linger "$USER"; else echo "Ask an administrator to run: loginctl enable-linger '$USER'" >&2; exit 1; fi
fi
[[ "$(loginctl show-user "$USER" -p Linger --value)" == yes ]] || { echo 'Linger must be yes' >&2; exit 1; }
systemctl --user enable --now podman.socket
podman build -t localhost/podmon:latest "$SERVICE_DIR"
HOST_MOUNTS=""
if podman run --rm --pull=never --userns=keep-id --user "$(id -u):$(id -g)" \
  -v /proc/stat:/host/proc/stat:ro \
  -v /proc/meminfo:/host/proc/meminfo:ro \
  -v /proc/uptime:/host/proc/uptime:ro \
  -v /proc/loadavg:/host/proc/loadavg:ro \
  -v /etc/hostname:/host/hostname:ro \
  --entrypoint python localhost/podmon:latest \
  -c "from pathlib import Path; assert Path('/host/proc/stat').read_text(); assert Path('/host/hostname').read_text()" >/dev/null 2>&1; then
  HOST_MOUNTS=$'Volume=/proc/stat:/host/proc/stat:ro\nVolume=/proc/meminfo:/host/proc/meminfo:ro\nVolume=/proc/uptime:/host/proc/uptime:ro\nVolume=/proc/loadavg:/host/proc/loadavg:ro\nVolume=/etc/hostname:/host/hostname:ro'
else
  echo 'Host metric file mounts unavailable; host fields will be unknown.' >&2
fi
UNIT_DIR="$HOME/.config/containers/systemd"
mkdir -p "$UNIT_DIR"
template="$(<"$SERVICE_DIR/quadlet/podmon.container")"
template="${template//@@UID@@/$(id -u)}"
template="${template//@@GID@@/$(id -g)}"
template="${template//@@LAN_IP@@/$LAN_IP}"
template="${template//@@HOST_MOUNTS@@/$HOST_MOUNTS}"
printf '%s\n' "$template" > "$UNIT_DIR/podmon.container"
systemctl --user daemon-reload
systemctl --user restart podmon.service
socket_ok=no
for attempt in {1..10}; do
  if podman exec podmon python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/containers',timeout=2)); assert d['socket']=='reachable'" >/dev/null 2>&1; then
    socket_ok=yes
    break
  fi
  sleep 2
done
if [[ "$socket_ok" != yes ]]; then
  echo 'podmon started but cannot read the Podman socket; inspect podman logs podmon and journalctl --user -u podmon.service.' >&2
  exit 1
fi
systemctl --user --no-pager status podmon.service
echo "Dashboard: http://$LAN_IP:8080"
