#!/bin/bash
# Provision the call box gateway device key on a running phone, after flashing. The key goes from
# Secret Manager straight into /etc/callbox/device.key (mode 600) over SSH; it is never printed,
# never written on the Mac and never part of an image.
#
# Usage: rootfs/provision-device-key.sh <phone address>
#   CALLBOX_KEY_PROJECT   the Google Cloud project holding the secret (required)
#   CALLBOX_KEY_SECRET    the secret's name (default callbox-device-key; a second phone gets its own)
#   CALLBOX_KEY_ACCOUNT   the gcloud account to read it with (default: gcloud's active account)
set -euo pipefail
PHONE=${1:?usage: rootfs/provision-device-key.sh <phone address>}
PROJECT=${CALLBOX_KEY_PROJECT:?set CALLBOX_KEY_PROJECT to the project holding the device key}
SECRET=${CALLBOX_KEY_SECRET:-callbox-device-key}
ACCOUNT=()
[ -n "${CALLBOX_KEY_ACCOUNT:-}" ] && ACCOUNT=(--account="$CALLBOX_KEY_ACCOUNT")
gcloud secrets versions access latest --secret="$SECRET" --project="$PROJECT" ${ACCOUNT[@]+"${ACCOUNT[@]}"} \
  | ssh "root@$PHONE" 'umask 077; t=/etc/callbox/device.key.new; cat > "$t"; [ -s "$t" ] || { rm -f "$t"; echo "empty key" >&2; exit 1; }; chmod 600 "$t"; mv "$t" /etc/callbox/device.key'
echo "device key written to $PHONE:/etc/callbox/device.key (mode 600); the app picks it up on its next start"
ssh "root@$PHONE" 'pkill -f "python3 -m callbox.agent" || true; pkill -f "python3 -m callbox.updater" || true'
