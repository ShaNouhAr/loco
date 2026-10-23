#!/bin/bash
# ISO locale : installation automatique BIOS puis UEFI, éjection du média, boot disque,
# services, mots de passe, clés, configuration CLI et redémarrage. QEMU/KVM ou TCG.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=tests/lib-vm.sh
. "$ROOT/tests/lib-vm.sh"
ISO=$(realpath "${1:?fichier ISO requis}")
WORK=$(mktemp -d)
LOG_DIR=${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/alpine-iso
mkdir -p "$LOG_DIR"
pid=""
cleanup() {
    rc=$?
    trap - EXIT
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ssh-keygen -q -t ed25519 -N '' -f "$WORK/key"
# Secrets de test éphémères ; aucun mot de passe dans les journaux ou artefacts.
rootpw=$(openssl rand -hex 16)
adminpw=$(openssl rand -hex 16)
umask 077
printf '%s\n' "$rootpw" > "$WORK/password"
openssl rand -hex 16 > "$WORK/wrong-password"
cat > "$WORK/askpass" <<'SH'
#!/bin/sh
cat "$LOCO_ISO_TEST_PASSWORD_FILE"
SH
chmod 700 "$WORK/askpass"
export LOCO_ISO_TEST_PASSWORD_FILE="$WORK/password"
write_answers() {
cat > "$WORK/loco-answers.conf" <<EOF
DISK=auto
HOSTNAME=loco-iso-test
KEYMAP="us us"
TIMEZONE=UTC
INTERFACE=auto
NETWORK=static
ADDRESS=10.0.2.15/24
GATEWAY=10.0.2.2
DNS=10.0.2.3
ROOT_PASSWORD=$rootpw
ADMIN_PASSWORD=$adminpw
ALLOW=10.0.0.0/8
FEATURES="http wu rpm flatpak"
${ssh_mode:+SSH_MODE=$ssh_mode}
SSH_KEY=$ssh_key
AFTER=poweroff
EOF
genisoimage -quiet -R -V LOCO -o "$WORK/answers.iso" "$WORK/loco-answers.conf"
}
accel=(-accel tcg)
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then accel=(-accel kvm -cpu host); fi
access() {
    local auth=(-i "$WORK/key" -o BatchMode=yes)
    if [ "$ssh_mode" = password ]; then
        auth=(-o BatchMode=no -o PreferredAuthentications=password -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1)
    fi
    SSH_ASKPASS="$WORK/askpass" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 \
    ssh -p "$port" "${auth[@]}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=3 root@127.0.0.1 "$@"
}
wait_health() {
    local i
    for ((i=0;i<180;i++)); do
        kill -0 "$pid" 2>/dev/null || { tail -60 "$LOG_DIR/$mode-qemu.log"; return 1; }
        if access 'test ! -f /etc/loco/firstboot.args && curl -fsk --max-time 3 https://127.0.0.1:8443/healthz' >/dev/null 2>&1; then return 0; fi
        sleep 3
    done
    access 'tail -60 /var/log/loco-firstboot.log' 2>/dev/null || true
    tail -60 "$LOG_DIR/$mode-serial.log"
    return 1
}
for mode in ${LOCO_ISO_MODES:-bios uefi}; do
    ssh_mode="" ssh_key=$(cat "$WORK/key.pub")
    if [ "$mode" = uefi ]; then ssh_mode=password ssh_key=""; fi
    write_answers
    fw=()
    if [ "$mode" = uefi ]; then
        code="" vars=""
        for dir in /usr/share/OVMF /usr/share/pve-edk2-firmware; do
            if [ -f "$dir/OVMF_CODE_4M.fd" ]; then code=$dir/OVMF_CODE_4M.fd; vars=$dir/OVMF_VARS_4M.fd; break; fi
            if [ -f "$dir/OVMF_CODE.fd" ]; then code=$dir/OVMF_CODE.fd; vars=$dir/OVMF_VARS.fd; break; fi
        done
        [ -n "$code" ] || { echo 'OVMF requis pour le test UEFI.' >&2; exit 1; }
        cp "$vars" "$WORK/uefi-vars.fd"
        fw=(-drive "if=pflash,format=raw,readonly=on,file=$code" -drive "if=pflash,format=raw,file=$WORK/uefi-vars.fd")
    fi
    port=$(free_port)
    qemu-img create -q -f qcow2 "$WORK/$mode.qcow2" 12G
    echo "$mode : démarrage de l'ISO et installation sur disque jetable"
    qemu-system-x86_64 "${accel[@]}" "${fw[@]}" -m 2048 -smp 2 -display none -monitor none \
        -serial "file:$LOG_DIR/$mode-serial.log" -drive "file=$WORK/$mode.qcow2,if=virtio,format=qcow2" \
        -drive "file=$ISO,media=cdrom,readonly=on" -drive "file=$WORK/answers.iso,media=cdrom,readonly=on" \
        -boot d -netdev "user,id=n,hostfwd=tcp:127.0.0.1:$port-:22" -device virtio-net-pci,netdev=n \
        > "$LOG_DIR/$mode-qemu.log" 2>&1 &
    pid=$!
    # L'installateur doit éteindre la VM après succès.
    for ((i=0;i<360;i++)); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 3
    done
    if kill -0 "$pid" 2>/dev/null; then tail -80 "$LOG_DIR/$mode-serial.log"; echo 'Installation ISO expirée.' >&2; exit 1; fi
    wait "$pid"; pid=""
    grep -qE 'installation réussie|installation successful|instalación correcta' "$LOG_DIR/$mode-serial.log"
    echo "$mode : démarrage sur disque, sans ISO ni fichier de réponses"
    boot_disk() {
        # Pas de canal agent invité : un virtio-serial supplémentaire décale la topologie PCI et casse l'amorçage
        # UEFI (l'entrée OVMF du disque ne correspond plus). On vérifie seulement que qemu-guest-agent est installé
        # et activé au démarrage (le paquet et le service OpenRC sont posés par l'installateur).
        qemu-system-x86_64 "${accel[@]}" "${fw[@]}" -m 2048 -smp 2 -display none -monitor none \
            -serial "file:$LOG_DIR/$mode-serial.log" -drive "file=$WORK/$mode.qcow2,if=virtio,format=qcow2" \
            -netdev "user,id=n,hostfwd=tcp:127.0.0.1:$port-:22" -device virtio-net-pci,netdev=n \
            > "$LOG_DIR/$mode-qemu.log" 2>&1 &
        pid=$!
    }
    boot_disk
    wait_health
    if [ "$ssh_mode" = password ]; then
        access 'set -e; test ! -e /root/.ssh/authorized_keys; sshd -T | grep -qx "passwordauthentication yes"'
        if LOCO_ISO_TEST_PASSWORD_FILE="$WORK/wrong-password" access true 2>/dev/null; then
            echo 'SSH accepte un mauvais mot de passe.' >&2; exit 1
        fi
        echo 'SSH mot de passe root sans aucune clé publique : OK'
    else
        if ssh_mode=password access true 2>/dev/null; then echo 'SSH par clé accepte le mot de passe.' >&2; exit 1; fi
        echo 'Anciennes réponses SSH_KEY : clé acceptée, mot de passe refusé : OK'
    fi
    access python3 - https://10.0.2.15:8443 < "$ROOT/tests/https_redirect.py"
    access 'set -eu; rc-service loco status; rc-service loco-agent status; test -x /usr/bin/qemu-ga; test -L /etc/runlevels/default/qemu-guest-agent; test -x /usr/local/sbin/setup-loco; test -x /usr/local/sbin/loco-config; test -x /usr/local/sbin/loco-update; grep -q "https://10.0.2.15:8443" /etc/issue; test ! -e /etc/local.d/loco-installer.start; test ! -e /etc/loco-answers.conf; ! grep -q "root::" /etc/shadow; loco-config set BACKUP_KEEP=5 >/dev/null; grep -qx BACKUP_KEEP=5 /etc/loco/loco.conf; loco-backup create' \
        > "$LOG_DIR/$mode-installed.log" 2>&1
    # Vérifie réellement le mot de passe choisi et les deux pages avec la CA installée.
    access "python3 - '$adminpw'" <<'PY'
import http.cookiejar, re, ssl, sys, urllib.parse, urllib.request
ctx = ssl.create_default_context(cafile='/etc/loco/tls/ca.crt')
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.HTTPSHandler(context=ctx))
base = 'https://10.0.2.15:8443'
def get(p):
    with op.open(base+p) as r: return r.read().decode()
html = get('/login')
token = re.search(r'name="csrf" value="([^"]+)"', html)[1]
with op.open(base+'/login', urllib.parse.urlencode(dict(csrf=token, username='admin', password=sys.argv[1])).encode()) as r:
    assert r.geturl().rstrip('/') == base
assert 'Mises à jour' in get('/system/updates')
assert 'Réseaux autorisés' in get('/system/configuration')
print('Authentification et pages Configuration / Mises à jour : OK')
PY
    ca=$(access 'sha256sum /etc/loco/tls/ca.crt')
    access 'poweroff' || true
    wait "$pid"; pid=""
    boot_disk
    wait_health
    [ "$(access 'sha256sum /etc/loco/tls/ca.crt')" = "$ca" ]
    access 'set -eu; grep -qx BACKUP_KEEP=5 /etc/loco/loco.conf; poweroff'
    wait "$pid"; pid=""
    echo "$mode : installation, premier démarrage, GUI/CLI et deuxième démarrage OK"
done
