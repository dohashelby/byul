#!/bin/sh
# Install only on the approved host; CONFIG is an existing root-owned 0600 JSON.
set -eu
if [ "$(id -u)" != 0 ] || [ "$#" != 2 ]; then
    echo 'usage (root): install-native-observer.sh /approved/update-venv/bin/python /etc/serving-runtime/native-observer.json' >&2
    exit 2
fi
observer_python=$1
observer_config=$2
observer_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)
export PYTHONPATH="$observer_root/runtime:$observer_root/runtime/src:$observer_root/update/src"
observer_launcher="$observer_root/runtime/tools/native_observer_service.py"
"$observer_python" -B "$observer_launcher" check --config "$observer_config"
observer_unit=$(mktemp -d /run/byul-native-receipts.XXXXXX)
trap 'rm -f "$observer_unit/byul-native-receipts.service"; rmdir "$observer_unit"' EXIT HUP INT TERM
"$observer_python" -B "$observer_launcher" render-unit --config "$observer_config" > "$observer_unit/byul-native-receipts.service"
systemd-analyze verify "$observer_unit/byul-native-receipts.service"
install -o 0 -g 0 -m 0644 "$observer_unit/byul-native-receipts.service" /etc/systemd/system/byul-native-receipts.service
systemctl daemon-reload
systemctl enable byul-native-receipts.service
systemctl restart byul-native-receipts.service
systemctl is-active --quiet byul-native-receipts.service
