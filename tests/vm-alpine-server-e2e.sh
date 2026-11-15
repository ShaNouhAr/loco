#!/bin/bash
# Serveur loco natif sur Alpine Linux (image cloud officielle), VM QEMU jetable.
# Usage : tests/vm-alpine-server-e2e.sh [alpine-3.24]   ; paquets d'essai : LOCO_FIXTURES (sinon construits ici).
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=tests/lib-vm.sh
. "$ROOT/tests/lib-vm.sh"
WORK=$(mktemp -d)
LOG_DIR="${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/alpine-server"
mkdir -p "$LOG_DIR"
ID=${1:-alpine-3.24}
cleanup() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -d "$WORK/vm-$ID" ]; then
        vm_ssh "$ID" 'sudo tail -n 60 /var/log/loco-install.log; sudo tail -n 80 /var/log/loco/*.log; sudo tail -n 80 /var/lib/loco-agent/jobs/*.log' \
            > "$LOG_DIR/$ID-errors.log" 2>&1 < /dev/null || true
    fi
    vm_stop "$ID"
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[[ "$ID" =~ ^alpine-[0-9.]+$ ]] || { echo 'Plateforme Alpine attendue (ex. alpine-3.24).' >&2; exit 1; }
FIXTURES=${LOCO_FIXTURES:-}
if [ -z "$FIXTURES" ]; then
    FIXTURES="$WORK/fixtures"
    "$ROOT/tests/windows-fixtures.sh" "$FIXTURES" >/dev/null
fi
image=$(vm_image "$ROOT/tests/alpine-platforms.json" "$ID")
VM_SHELL=/bin/sh VM_UNLOCK=1 VM_PACKAGES="sudo, bash, curl" vm_start "$ID" "$image" "$(free_port)"
# cloud-init installe sudo après la création du compte : attendre qu'il soit là.
for (( i=0; i<60; i++ )); do vm_ssh "$ID" 'command -v sudo >/dev/null' < /dev/null && break; sleep 5; done
# shellcheck disable=SC2016 # Interpolation dans la VM.
actual=$(vm_ssh "$ID" '. /etc/os-release; echo "$ID-$(echo "$VERSION_ID" | cut -d. -f1-2)"' < /dev/null)
[ "$actual" = "$ID" ] || { echo "Système inattendu : $actual" >&2; exit 1; }
tar --exclude=.git --exclude=./loco --exclude=__pycache__ --exclude=./test-results -czf - -C "$ROOT" . |
    vm_ssh "$ID" 'mkdir -p loco && tar -xzf - -C loco'
tar -czf - -C "$FIXTURES" . | vm_ssh "$ID" 'mkdir -p fixtures && tar -xzf - -C fixtures'
vm_ssh "$ID" 'cd loco && sudo bash tests/alpine-server.sh /home/ci/fixtures' < /dev/null 2>&1 | tee "$LOG_DIR/$ID.log"
echo "Validation du serveur Alpine réussie : $ID"
