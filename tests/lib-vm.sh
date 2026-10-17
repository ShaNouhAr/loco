#!/bin/bash
# Fonctions communes des tests en VM QEMU jetables (sans Docker). À sourcer depuis un script de test.
# Variables : WORK (dossier de travail), LOG_DIR (journaux), LOCO_VM_IMAGE_CACHE (cache d'images, facultatif).

LIB_VM_HOSTS="cloud.debian.org cloud-images.ubuntu.com dl.rockylinux.org repo.almalinux.org dl-cdn.alpinelinux.org"
# VM_SHELL et VM_PACKAGES : shell du compte ci et paquets installés par cloud-init (images sans bash ni sudo).

# vm_image FICHIER_JSON ID : télécharge l'image officielle (HTTPS), vérifie l'empreinte publiée, affiche son chemin.
vm_image() {
    local row
    row=$(python3 - "$1" "$2" "$LIB_VM_HOSTS" <<'PY'
import json, sys, urllib.parse
row = next((r for r in json.load(open(sys.argv[1])) if r["id"] == sys.argv[2]), None)
if row is None:
    sys.exit(f"Plateforme absente de {sys.argv[1]} : {sys.argv[2]}")
for key in ("image", "checksums"):
    u = urllib.parse.urlsplit(row[key])
    if u.scheme != "https" or u.hostname not in sys.argv[3].split():
        sys.exit("Les images doivent provenir des serveurs officiels, en HTTPS.")
print(row["image"])
print(row["checksums"])
PY
) || return 1
    local url=${row%%$'\n'*} sums=${row#*$'\n'} name cache image expected
    name=${url##*/}
    cache=${LOCO_VM_IMAGE_CACHE:-$WORK/images}
    mkdir -p "$cache"
    curl --http1.1 -fsSL --retry 4 --retry-all-errors --connect-timeout 30 "$sums" -o "$WORK/$2.sums" || return 1
    # Formats GNU (« empreinte  fichier »), BSD (« SHA256 (fichier) = empreinte ») ou empreinte seule (Alpine).
    expected=$(python3 - "$WORK/$2.sums" "$name" <<'PY'
import re, sys
text = open(sys.argv[1]).read().split()
if len(text) == 1 and re.fullmatch(r"[0-9a-f]{64}|[0-9a-f]{128}", text[0]):
    print(text[0]); sys.exit()
for line in open(sys.argv[1]):
    m = re.match(r"^([0-9a-f]{64}|[0-9a-f]{128})\s+\*?(\S+)$", line.strip()) or None
    if m and m.group(2) == sys.argv[2]:
        print(m.group(1)); break
    m = re.match(r"^SHA(256|512) \((.+)\) = ([0-9a-f]+)$", line.strip())
    if m and m.group(2) == sys.argv[2]:
        print(m.group(3)); break
else:
    sys.exit("Empreinte absente du manifeste officiel")
PY
) || return 1
    image="$cache/$2-$name"
    local tool=sha256sum
    [ "${#expected}" -eq 128 ] && tool=sha512sum
    if [ ! -f "$image" ] || [ "$($tool "$image" | cut -d' ' -f1)" != "$expected" ]; then
        # Certains miroirs interrompent leurs gros transferts HTTP/2. Le fichier reste temporaire
        # jusqu'à validation ; une erreur curl dans $(vm_image ...) doit être propagée explicitement.
        curl --http1.1 -fsSL --retry 4 --retry-all-errors --connect-timeout 30 "$url" -o "$image.part" || return 1
        [ "$($tool "$image.part" | cut -d' ' -f1)" = "$expected" ] || { echo "Empreinte de l'image incorrecte : $2" >&2; return 1; }
        mv "$image.part" "$image" || return 1
    fi
    [ "$($tool "$image" | cut -d' ' -f1)" = "$expected" ] || { echo "Empreinte de l'image incorrecte : $2" >&2; return 1; }
    echo "$image"
}

# vm_start NOM IMAGE PORT_SSH [hostfwd supplémentaires…] : démarre la VM et attend SSH + cloud-init.
vm_start() {
    local name=$1 image=$2 port=$3 dir="$WORK/vm-$1" fwd="hostfwd=tcp:127.0.0.1:$3-:22" accel=(-accel tcg) i
    shift 3
    for i in "$@"; do fwd="$fwd,hostfwd=$i"; done
    mkdir -p "$dir"
    qemu-img create -q -f qcow2 -F qcow2 -b "$image" "$dir/disk.qcow2"
    qemu-img resize -q "$dir/disk.qcow2" 16G
    ssh-keygen -q -t ed25519 -N '' -f "$dir/key"
    printf '#cloud-config\nusers:\n  - name: ci\n    sudo: "ALL=(ALL) NOPASSWD:ALL"\n    shell: %s\n    ssh_authorized_keys:\n      - %s\nssh_pwauth: false\n' \
        "${VM_SHELL:-/bin/bash}" "$(cat "$dir/key.pub")" > "$dir/user-data"
    if [ -n "${VM_PACKAGES:-}" ]; then printf 'packages: [%s]\n' "$VM_PACKAGES" >> "$dir/user-data"; fi
    # Sans PAM (Alpine), OpenSSH refuse les comptes verrouillés, même par clé : compte sans mot de passe (« * »).
    if [ "${VM_UNLOCK:-0}" = 1 ]; then
        sed -i 's/^    shell: /    lock_passwd: false\n    hashed_passwd: "*"\n    shell: /' "$dir/user-data"
    fi
    printf 'instance-id: loco-%s\nlocal-hostname: %s\n' "$name" "$name" > "$dir/meta-data"
    (cd "$dir" && genisoimage -quiet -output seed.iso -volid cidata -joliet -rock user-data meta-data)
    if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then accel=(-accel kvm -cpu host); fi
    qemu-system-x86_64 "${accel[@]}" -m 2048 -smp 2 -display none -monitor none \
        -serial "file:$LOG_DIR/$name-serial.log" -drive "file=$dir/disk.qcow2,if=virtio,format=qcow2" \
        -drive "file=$dir/seed.iso,media=cdrom,readonly=on" \
        -netdev "user,id=net0,$fwd" -device virtio-net-pci,netdev=net0 > "$LOG_DIR/$name-qemu.log" 2>&1 &
    echo "$!" > "$dir/pid"
    echo "$port" > "$dir/port"
    for (( i=0; i<150; i++ )); do
        kill -0 "$(cat "$dir/pid")" 2>/dev/null || { cat "$LOG_DIR/$name-qemu.log" >&2; return 1; }
        if vm_ssh "$name" true 2>/dev/null; then
            vm_ssh "$name" 'cloud-init status --wait' > "$LOG_DIR/$name-cloud-init.log" 2>&1 || true
            return 0
        fi
        sleep 2
    done
    tail -40 "$LOG_DIR/$name-serial.log" >&2
    echo "SSH ne répond pas : $name" >&2
    return 1
}

vm_ssh() {
    local dir="$WORK/vm-$1"
    shift
    ssh -p "$(cat "$dir/port")" -i "$dir/key" -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 \
        -o ServerAliveInterval=15 -o ServerAliveCountMax=4 ci@127.0.0.1 "$@"
}

vm_stop() {
    local pid
    pid=$(cat "$WORK/vm-$1/pid" 2>/dev/null) || return 0
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -rf "$WORK/vm-$1"
}

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
