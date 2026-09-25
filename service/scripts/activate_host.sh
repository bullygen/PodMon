#!/usr/bin/env bash
# Run on the host only after podmon-setup has installed dependencies and cloned code.
# Host requirements: Podman, user systemd/loginctl and standard shell tools.
set -euo pipefail
LAN_IP="${1:?Usage: activate_host.sh LAN_IP}"
if [[ "$EUID" -eq 0 ]]; then echo 'Run as the rootless Podman user, not root' >&2; exit 1; fi
if [[ ! "$LAN_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then echo 'LAN_IP must be an IPv4 address' >&2; exit 1; fi
[[ "$(podman inspect podmon-setup --format '{{.State.Status}}')" == running ]] || { echo 'Start podmon-setup first' >&2; exit 1; }
podman exec podmon-setup test -x /opt/podmon-venv/bin/uvicorn
podman exec podmon-setup test -f /srv/podmon/service/app/main.py
podman exec --workdir /srv/podmon --env PYTHONPATH=/srv/podmon/service podmon-setup \
  /opt/podmon-venv/bin/python -m unittest discover -s service/tests -q
if [[ "$(loginctl show-user "$USER" -p Linger --value)" != yes ]]; then
  echo "Enable linger as administrator: loginctl enable-linger '$USER'" >&2
  exit 1
fi
systemctl --user enable --now podman.socket
podman commit --include-volumes=false podmon-setup localhost/podmon:runtime
podman run --rm --pull=never --entrypoint /bin/sh localhost/podmon:runtime \
  -c 'test -x /opt/podmon-venv/bin/uvicorn && test -f /srv/podmon/service/app/main.py && test ! -e /root/.ssh/podmon_deploy_ed25519'
HOST_MOUNTS=""
if podman run --rm --pull=never --userns=keep-id --user "$(id -u):$(id -g)" \
  -v /proc/stat:/host/proc/stat:ro \
  -v /proc/meminfo:/host/proc/meminfo:ro \
  -v /proc/uptime:/host/proc/uptime:ro \
  -v /proc/loadavg:/host/proc/loadavg:ro \
  -v /etc/hostname:/host/hostname:ro \
  --entrypoint /opt/podmon-venv/bin/python localhost/podmon:runtime \
  -c "from pathlib import Path; assert Path('/host/proc/stat').read_text(); assert Path('/host/hostname').read_text()" >/dev/null 2>&1; then
  HOST_MOUNTS=$'Volume=/proc/stat:/host/proc/stat:ro\nVolume=/proc/meminfo:/host/proc/meminfo:ro\nVolume=/proc/uptime:/host/proc/uptime:ro\nVolume=/proc/loadavg:/host/proc/loadavg:ro\nVolume=/etc/hostname:/host/hostname:ro'
else
  echo 'Host metric file mounts unavailable; host fields will be unknown.' >&2
fi
template_file="$(mktemp)"
trap 'rm -f "$template_file"' EXIT
podman cp podmon-setup:/srv/podmon/service/quadlet/podmon.container "$template_file"
template="$(<"$template_file")"
template="${template//@@UID@@/$(id -u)}"
template="${template//@@GID@@/$(id -g)}"
template="${template//@@LAN_IP@@/$LAN_IP}"
template="${template//@@HOST_MOUNTS@@/$HOST_MOUNTS}"
unit_dir="$HOME/.config/containers/systemd"
mkdir -p "$unit_dir"
printf '%s\n' "$template" > "$unit_dir/podmon.container"
systemctl --user daemon-reload
systemctl --user restart podmon.service
socket_ok=no
for attempt in {1..10}; do
  if podman exec podmon /opt/podmon-venv/bin/python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/containers',timeout=2)); assert d['socket']=='reachable'" >/dev/null 2>&1; then
    socket_ok=yes
    break
  fi
  sleep 2
done
if [[ "$socket_ok" != yes ]]; then
  echo 'podmon cannot read the socket; inspect podman logs podmon and journalctl --user -u podmon.service.' >&2
  exit 1
fi
podman stop podmon-setup >/dev/null
systemctl --user --no-pager status podmon.service
echo "Dashboard: http://$LAN_IP:8080"
