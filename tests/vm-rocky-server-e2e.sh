#!/bin/bash
# Installation native du serveur complet sur Rocky Linux 8, 9 et 10, VM QEMU jetables.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=tests/lib-vm.sh
. "$ROOT/tests/lib-vm.sh"
WORK=$(mktemp -d)
LOG_DIR="${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/rocky-server"
mkdir -p "$LOG_DIR"
VMS=()
cleanup() {
    rc=$?
    trap - EXIT
    for id in "${VMS[@]}"; do
        if [ "$rc" -ne 0 ]; then
            vm_ssh "$id" 'sudo tail -60 /tmp/native-rocky-install.log; sudo tail -60 /var/log/loco-install.log; sudo journalctl -u loco-portal -u nginx -u apt-cacher-ng --no-pager -n 100; sudo ausearch -m AVC -ts recent' \
                > "$LOG_DIR/$id-errors.log" 2>&1 < /dev/null || true
        fi
        vm_stop "$id"
    done
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [ "$#" = 0 ]; then set -- rocky-8 rocky-9 rocky-10; fi
for id in "$@"; do
    case "$id" in rocky-8|rocky-9|rocky-10) ;; *) echo 'Rocky 8, 9 ou 10 attendu' >&2; exit 1 ;; esac
    image=$(vm_image "$ROOT/tests/rpm-platforms.json" "$id")
    VMS+=("$id")
    vm_start "$id" "$image" "$(free_port)"
    # shellcheck disable=SC2016 # Interpolation dans la VM.
    actual=$(vm_ssh "$id" '. /etc/os-release; echo "$ID-${VERSION_ID%%.*}"' < /dev/null)
    [ "$actual" = "$id" ]
    tar --exclude=.git --exclude=./loco --exclude=__pycache__ --exclude=./test-results -czf - -C "$ROOT" . |
        vm_ssh "$id" 'mkdir -p loco && tar -xzf - -C loco'
    printf -v test_command 'cd loco && sudo env LOCO_TEST_ROCKY_MIRROR=%q tests/rocky-server.sh' "${LOCO_TEST_ROCKY_MIRROR:-}"
    vm_ssh "$id" "$test_command" < /dev/null 2>&1 | tee "$LOG_DIR/$id.log"
    vm_stop "$id"
done
