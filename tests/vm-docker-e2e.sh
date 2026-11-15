#!/bin/bash
# Déploiement Docker (docker compose) dans une VM QEMU jetable Debian 13, Docker des paquets Debian.
# Usage : tests/vm-docker-e2e.sh
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command in python3 qemu-system-x86_64 qemu-img genisoimage curl ssh ssh-keygen tar; do
    command -v "$command" >/dev/null || { echo "Commande requise : $command" >&2; exit 1; }
done
# shellcheck disable=SC1091  # bibliothèque des tests, vérifiée séparément
. "$ROOT/tests/lib-vm.sh"
WORK=$(mktemp -d)
LOG_DIR="${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/docker"
mkdir -p "$LOG_DIR"
cleanup() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -d "$WORK/vm-docker" ]; then
        vm_ssh docker 'cd loco/linux/docker && sudo docker compose logs --no-color --tail 300; sudo docker compose exec -T loco tail -60 /var/log/nginx/error.log' \
            > "$LOG_DIR/guest-errors.log" 2>&1 || true
    fi
    vm_stop docker
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

image=$(vm_image "$ROOT/tests/platforms.json" debian-13)
vm_start docker "$image" "$(free_port)"
tar --exclude=.git --exclude=./loco --exclude=__pycache__ --exclude=./test-results --exclude=./linux/docker/.env -czf - -C "$ROOT" . | \
    vm_ssh docker 'mkdir -p loco && tar -xzf - -C loco'
vm_ssh docker 'sudo apt-get update -q && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io docker-compose >/dev/null && sudo docker version --format "Docker {{.Server.Version}}" && sudo docker compose version' \
    2>&1 | tee "$LOG_DIR/docker.log"
vm_ssh docker 'cd loco && tests/docker-e2e.sh' 2>&1 | tee "$LOG_DIR/tests.log"
echo "Validation Docker réussie."
