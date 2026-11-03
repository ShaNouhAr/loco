#!/bin/bash
# Lance les tests sur une VM QEMU jetable (systemd réel), sans Docker.
# Usage : tests/vm-e2e.sh debian-13 ; plateformes : tests/platforms.json.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PLATFORM=${1:?Usage : tests/vm-e2e.sh ID_PLATEFORME}
[[ "$PLATFORM" =~ ^[a-z0-9][a-z0-9.-]+$ ]] || { echo 'Plateforme invalide.' >&2; exit 1; }
for command in python3 qemu-system-x86_64 qemu-img genisoimage curl ssh ssh-keygen tar; do
    command -v "$command" >/dev/null || { echo "Commande requise : $command" >&2; exit 1; }
done
work=$(mktemp -d)
LOG_DIR="${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/$PLATFORM"
mkdir -p "$LOG_DIR"
VM_PID="" READY=0
cleanup() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ "$READY" = 1 ]; then
        ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 'sudo journalctl -u loco-portal -u nginx -u apt-cacher-ng --no-pager -n 150; sudo tail -100 /var/log/loco-install.log' > "$LOG_DIR/guest-errors.log" 2>&1 || true
    fi
    if [ -n "$VM_PID" ]; then kill "$VM_PID" 2>/dev/null || true; wait "$VM_PID" 2>/dev/null || true; fi
    rm -rf "$work"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 - "$ROOT/tests/platforms.json" "$PLATFORM" > "$work/platform" <<'PY'
import json,sys,urllib.parse
rows=json.load(open(sys.argv[1]))
row=next((r for r in rows if r['id']==sys.argv[2]),None)
if row is None:sys.exit('Plateforme absente de tests/platforms.json')
for key in ('image','checksums'):
    u=urllib.parse.urlsplit(row[key])
    if u.scheme!='https' or u.hostname not in ('cloud.debian.org','cloud-images.ubuntu.com'):
        sys.exit('Les images doivent provenir des serveurs officiels, en HTTPS.')
if row['algorithm'] not in ('sha256','sha512'):sys.exit('Algorithme invalide')
for k in ('distribution','suite','version','image','checksums','algorithm','upgrade_from'):print(row[k])
PY
mapfile -t platform < "$work/platform"
IMAGE_URL=${platform[3]} IMAGE_NAME=${IMAGE_URL##*/} ALGORITHM=${platform[5]}
CACHE="${LOCO_VM_IMAGE_CACHE:-$work/images}"
mkdir -p "$CACHE"
curl -fsSL --retry 3 "${platform[4]}" -o "$work/checksums"
checksum=$(python3 - "$work/checksums" "$IMAGE_NAME" <<'PY'
import sys
for line in open(sys.argv[1]):
    parts=line.split()
    if len(parts)==2 and parts[1].lstrip('*')==sys.argv[2]:print(parts[0]);break
else:sys.exit('Empreinte absente du manifeste officiel')
PY
)
[[ "$checksum" =~ ^[a-f0-9]+$ ]] || { echo 'Empreinte invalide.' >&2; exit 1; }
IMAGE="$CACHE/$PLATFORM-$IMAGE_NAME"
verify_image() { [ -f "$IMAGE" ] && [ "$("${ALGORITHM}sum" "$IMAGE" | cut -d' ' -f1)" = "$checksum" ]; }
if ! verify_image; then
    curl -fsSL --retry 3 "$IMAGE_URL" -o "$IMAGE.part"
    mv "$IMAGE.part" "$IMAGE"
fi
verify_image || { echo "Empreinte de l'image incorrecte." >&2; exit 1; }
qemu-img create -q -f qcow2 -F qcow2 -b "$IMAGE" "$work/disk.qcow2"
qemu-img resize -q "$work/disk.qcow2" 16G
ssh-keygen -q -t ed25519 -N '' -f "$work/key"
PUBKEY=$(cat "$work/key.pub")
cat > "$work/user-data" <<EOF
#cloud-config
users:
  - name: ci
    groups: [sudo]
    sudo: 'ALL=(ALL) NOPASSWD:ALL'
    shell: /bin/bash
    ssh_authorized_keys:
      - $PUBKEY
ssh_pwauth: false
EOF
printf 'instance-id: loco-%s\nlocal-hostname: loco-ci\n' "$PLATFORM" > "$work/meta-data"
(cd "$work" && genisoimage -quiet -output seed.iso -volid cidata -joliet -rock user-data meta-data)
PORT=${LOCO_VM_SSH_PORT:-$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')}
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -le 0 ] || [ "$PORT" -ge 65536 ]; then echo 'Port SSH invalide.' >&2; exit 1; fi
SSH_OPTIONS=(-p "$PORT" -i "$work/key" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
ACCEL=(-accel tcg)
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then ACCEL=(-accel kvm -cpu host); fi
qemu-system-x86_64 "${ACCEL[@]}" -m 2048 -smp 2 -display none -monitor none \
    -serial "file:$LOG_DIR/serial.log" -drive "file=$work/disk.qcow2,if=virtio,format=qcow2" \
    -drive "file=$work/seed.iso,media=cdrom,readonly=on" \
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$PORT-:22" -device virtio-net-pci,netdev=net0 > "$LOG_DIR/qemu.log" 2>&1 &
VM_PID=$!
for (( attempt=0; attempt<120; attempt++ )); do
    kill -0 "$VM_PID" 2>/dev/null || { cat "$LOG_DIR/qemu.log"; exit 1; }
    if ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 true 2>/dev/null; then READY=1; break; fi
    sleep 2
done
[ "$READY" = 1 ] || { tail -50 "$LOG_DIR/serial.log"; echo 'SSH ne répond pas.' >&2; exit 1; }
ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 'cloud-init status --wait' > "$LOG_DIR/cloud-init.log" 2>&1
# Vérifie l'identité du système : une mauvaise image ne doit pas compter comme une validation.
ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 'cat /etc/os-release' > "$LOG_DIR/os-release"
grep -qxE "ID=\"?${platform[0]}\"?" "$LOG_DIR/os-release"
grep -qxE "VERSION_ID=\"?${platform[2]}\"?" "$LOG_DIR/os-release"
tar --exclude=.git --exclude=./loco --exclude=./local-repo --exclude=__pycache__ --exclude=./test-results -czf - -C "$ROOT" . | \
    ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 'mkdir -p loco && tar -xzf - -C loco'
LEGACY_SRC=""
if [ -n "${platform[6]}" ]; then
    if [ -n "${LOCO_VM_UPGRADE_ARCHIVE:-}" ]; then
        cp "$LOCO_VM_UPGRADE_ARCHIVE" "$work/legacy.tar.gz"
    else
        git -C "$ROOT" archive "${platform[6]}" | gzip > "$work/legacy.tar.gz"
    fi
    ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 'mkdir -p loco-legacy && tar -xzf - -C loco-legacy' < "$work/legacy.tar.gz"
    LEGACY_SRC=/home/ci/loco-legacy
fi
# Les tests unitaires sur les bibliothèques de chaque distribution tournent avant, dans ci.yml (conteneurs).
# shellcheck disable=SC2029  # LEGACY_SRC est un chemin constant fixé ci-dessus, transmis volontairement à la VM.
ssh "${SSH_OPTIONS[@]}" ci@127.0.0.1 "cd loco && sudo apt-get update -q && sudo apt-get install -y -q python3-flask apt-utils gnupg createrepo-c rpm && LOCO_E2E_LEGACY_SRC=$LEGACY_SRC tests/e2e.sh" 2>&1 | tee "$LOG_DIR/tests.log"
echo "Validation VM réussie : $PLATFORM"
