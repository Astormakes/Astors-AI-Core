#!/usr/bin/env bash
# Copy custom_components/aac to a Home Assistant over SSH and restart Core.
# For testing the working tree without going through GitHub/HACS.
#
#   AAC_HA_SSH=root@<ha-host> [AAC_SSH_KEY=~/.ssh/key] scripts/deploy_ha.sh [--no-restart]
#
# Needs the "Terminal & SSH" add-on (has the `ha` CLI and /config).
set -euo pipefail

: "${AAC_HA_SSH:?set AAC_HA_SSH, e.g. root@homeassistant.local}"
ssh_opts=(-o ConnectTimeout=5)
[[ -n "${AAC_SSH_KEY:-}" ]] && ssh_opts+=(-i "$AAC_SSH_KEY")

cd "$(dirname "$0")/../custom_components"
version=$(python3 -c 'import json;print(json.load(open("aac/manifest.json"))["version"])')

# v1 is built next to the running v0: it reaches the owner's HA only on purpose (the switch, with a backup)
if [[ "$version" == 1.* && -z "${AAC_ALLOW_V1:-}" ]]; then
  echo "Refusing: $version is v1. The real HA runs v0 until the switch (set AAC_ALLOW_V1=1 to deploy anyway)." >&2
  exit 1
fi

echo "Deploying aac $version ($(git rev-parse --short HEAD)$(git diff --quiet -- . || echo '+dirty')) to $AAC_HA_SSH"
tar --exclude=__pycache__ -cf - aac | ssh "${ssh_opts[@]}" "$AAC_HA_SSH" \
  'rm -rf /config/custom_components/aac && tar -C /config/custom_components -xf - && chown -R root:root /config/custom_components/aac'

if [[ "${1:-}" != "--no-restart" ]]; then
  echo "Checking config and restarting HA Core ..."
  ssh "${ssh_opts[@]}" "$AAC_HA_SSH" 'ha core check && ha core restart'
fi
echo "Done."
