#!/bin/bash
# Configuration is parsed as data by Python; never source install.env.
set -euo pipefail
set +x
exec /usr/bin/python3 -B "$(dirname -- "${BASH_SOURCE[0]}")/install_runtime_node.py" "$@"
