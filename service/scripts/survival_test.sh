#!/usr/bin/env bash
set -euo pipefail
LAN_IP="${1:?Usage: survival_test.sh LAN_IP}"
[[ "$(loginctl show-user "$USER" -p Linger --value)" == yes ]]
systemctl --user is-active --quiet podmon.service
[[ "$(podman inspect podmon --format '{{.State.Status}}')" == running ]]
"$(dirname "$0")/smoke_test.sh" "http://$LAN_IP:8080"
echo 'Linger, systemd, container and API are healthy. Repeat after full SSH logout and reboot.'
