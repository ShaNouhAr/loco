#!/bin/bash
# Postes de la famille Red Hat : un serveur loco (Debian 13) et des postes Rocky Linux / AlmaLinux,
# chacun dans une VM QEMU jetable (sans Docker). Les postes joignent le serveur par 10.0.2.2.
# Usage : tests/vm-rpm-e2e.sh [rocky-9 almalinux-10 …]   (par défaut : tests/rpm-platforms.json)
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command in python3 qemu-system-x86_64 qemu-img genisoimage curl ssh ssh-keygen tar ss; do
    command -v "$command" >/dev/null || { echo "Commande requise : $command" >&2; exit 1; }
done
# shellcheck disable=SC1091  # bibliothèque des tests, vérifiée séparément
. "$ROOT/tests/lib-vm.sh"
WORK=$(mktemp -d)
LOG_DIR="${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/rpm"
mkdir -p "$LOG_DIR"
VMS=()
cleanup() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -d "$WORK/vm-server" ]; then
        vm_ssh server 'sudo journalctl -u loco-portal -u nginx --no-pager -n 150; sudo tail -60 /var/log/nginx/error.log' \
            > "$LOG_DIR/server-errors.log" 2>&1 || true
    fi
    for vm in "${VMS[@]}"; do vm_stop "$vm"; done
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for port in 8443 8088; do
    if ss -Hltn "sport = :$port" | grep -q .; then echo "Port $port déjà utilisé sur cette machine." >&2; exit 1; fi
done
if [ "$#" -gt 0 ]; then CLIENTS=("$@"); else
    mapfile -t CLIENTS < <(python3 -c 'import json,sys; [print(r["id"]) for r in json.load(open(sys.argv[1]))]' "$ROOT/tests/rpm-platforms.json")
fi
PW="Rpm-E2e-$(date +%s)-x"

echo "=== Serveur (Debian 13)"
image=$(vm_image "$ROOT/tests/platforms.json" debian-13)
VMS+=(server)
vm_start server "$image" "$(free_port)" "tcp:127.0.0.1:8443-:8443" "tcp:127.0.0.1:8088-:8088"
tar --exclude=.git --exclude=./loco --exclude=__pycache__ --exclude=./test-results -czf - -C "$ROOT" . | \
    vm_ssh server 'mkdir -p loco && tar -xzf - -C loco'
# shellcheck disable=SC2029  # mot de passe généré ci-dessus, transmis volontairement à la VM.
vm_ssh server "cd loco && sudo tests/rpm-server.sh '$PW'" 2>&1 | tee "$LOG_DIR/server.log"

for id in "${CLIENTS[@]}"; do
    [[ "$id" =~ ^[a-z0-9][a-z0-9.-]+$ ]] || { echo "Plateforme invalide : $id" >&2; exit 1; }
    echo "=== Poste $id"
    image=$(vm_image "$ROOT/tests/rpm-platforms.json" "$id")
    VMS+=("$id")
    vm_start "$id" "$image" "$(free_port)"
    expected=$(python3 -c 'import json,sys; r=next(r for r in json.load(open(sys.argv[1])) if r["id"]==sys.argv[2]); print(r["os_id"], r["version"])' \
        "$ROOT/tests/rpm-platforms.json" "$id")
    # Vérifie l'identité du système : une mauvaise image ne doit pas compter comme une validation.
    # shellcheck disable=SC2016  # expansion faite dans la VM.
    actual=$(vm_ssh "$id" '. /etc/os-release; echo "$ID ${VERSION_ID%%.*}"')
    [ "$actual" = "$expected" ] || { echo "Système inattendu : $actual (attendu : $expected)" >&2; exit 1; }
    vm_ssh "$id" 'sudo bash -s' < "$ROOT/tests/rpm-client.sh" 2>&1 | tee "$LOG_DIR/$id.log"
    vm_stop "$id"
done
echo
echo "Validation RPM réussie : ${CLIENTS[*]}"
