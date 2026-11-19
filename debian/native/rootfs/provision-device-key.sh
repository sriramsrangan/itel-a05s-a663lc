#!/bin/bash
# Provision a flashed call box phone for its gateway: the device key and the gateway address. The
# image carries neither (make-rootfs.sh writes a placeholder address and no key), so this repository
# holds no deployment value. The key goes from Secret Manager straight into /etc/callbox/device.key
# (mode 600) over SSH; it is never printed, never written on the Mac and never part of an image. The
# address replaces base_url in /etc/callbox/agent.toml.
#
# Usage: rootfs/provision-device-key.sh <phone address> <gateway URL>
#   <gateway URL>         the gateway's https:// address (the Cloud Run URL, not a hosting front that
#                         cuts long polls), for example https://gateway.example.com
#   CALLBOX_KEY_PROJECT   the Google Cloud project holding the secret (required)
#   CALLBOX_KEY_SECRET    the secret's name (default callbox-device-key; a second phone gets its own)
#   CALLBOX_KEY_ACCOUNT   the gcloud account to read it with (default: gcloud's active account)
set -euo pipefail
USAGE="usage: rootfs/provision-device-key.sh <phone address> <gateway URL>"
PHONE=${1:?$USAGE}
URL=${2:?$USAGE}
# A plain https URL only: it is written into a TOML string and a remote command.
[[ "$URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$ ]] || { echo "the gateway URL must be a plain https:// address" >&2; exit 2; }
[[ "$URL" == *.invalid || "$URL" == *.invalid/* || "$URL" == *.invalid:* ]] && { echo "that is the image's placeholder, not a gateway" >&2; exit 2; }
URL=${URL%/}
[[ "$PHONE" =~ ^[A-Za-z0-9.:-]+$ ]] || { echo "the phone address must be a host name or IP address" >&2; exit 2; }
PROJECT=${CALLBOX_KEY_PROJECT:?set CALLBOX_KEY_PROJECT to the project holding the device key}
SECRET=${CALLBOX_KEY_SECRET:-callbox-device-key}
ACCOUNT=()
[ -n "${CALLBOX_KEY_ACCOUNT:-}" ] && ACCOUNT=(--account="$CALLBOX_KEY_ACCOUNT")
gcloud secrets versions access latest --secret="$SECRET" --project="$PROJECT" ${ACCOUNT[@]+"${ACCOUNT[@]}"} \
  | ssh "root@$PHONE" 'umask 077; t=/etc/callbox/device.key.new; cat > "$t"; [ -s "$t" ] || { rm -f "$t"; echo "empty key" >&2; exit 1; }; chmod 600 "$t"; mv "$t" /etc/callbox/device.key'
echo "device key written to $PHONE:/etc/callbox/device.key (mode 600)"
ssh "root@$PHONE" "f=/etc/callbox/agent.toml; grep -q '^base_url = ' \$f || { echo 'agent.toml has no base_url line' >&2; exit 1; }; cp -p \$f \$f.before-provision; sed -i 's#^base_url = .*#base_url = \"$URL\"#' \$f; grep -qx 'base_url = \"$URL\"' \$f"
echo "gateway address set in $PHONE:/etc/callbox/agent.toml (the file before is kept as agent.toml.before-provision)"
ssh "root@$PHONE" 'pkill -f "python3 -m callbox.agent" || true; pkill -f "python3 -m callbox.updater" || true'
echo "the app restarts with both; it does nothing until then"
