#!/bin/bash
# loco dans un conteneur LXC Proxmox VE (7, 8, 9) : crée un conteneur Debian non privilégié et y fait
# l'installation native (install.sh). À lancer en root sur n'importe quel hôte Proxmox :
#
#   curl -fsSLO https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/lxc/proxmox-lxc.sh
#   bash proxmox-lxc.sh [options] [-- options d'install.sh]
#   bash proxmox-lxc.sh --ctid N --update      met à jour le conteneur N (dernière version publiée)
#
#   --ctid N                 numéro du conteneur (défaut : prochain numéro libre)
#   --hostname NOM           nom du conteneur (défaut : loco)
#   --storage S              stockage du disque (défaut : local-lvm, local-zfs, sinon le premier compatible)
#   --disk G                 taille du disque en Go, caches compris (défaut : 100)
#   --cores N                processeurs (défaut : 2)
#   --memory Mo              mémoire (défaut : 2048)
#   --bridge B               pont réseau (défaut : vmbr0, sinon le premier pont)
#   --vlan N                 étiquette VLAN (défaut : aucune)
#   --ip CIDR --gw IP        adresse fixe, ex. --ip 192.168.10.20/24 --gw 192.168.10.1 (défaut : DHCP)
#   --dns IP                 serveur DNS du conteneur (défaut : celui de l'hôte)
#   --template-storage S     stockage des modèles (défaut : local, sinon le premier compatible)
#   --version vX.Y.Z         version de loco (défaut : la dernière publiée sur GitHub)
#   --source DIR             installer depuis une copie locale du projet plutôt que depuis GitHub
#
# Tout ce qui suit « -- » est transmis à install.sh, par exemple : -- --allow "10.0.0.0/8" --no-wu
set -euo pipefail

REPO=ShaNouhAr/loco
say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }
usage() {
    if [ -f "$0" ]; then sed -n '2,/^set -euo/{/^#/p;}' "$0" | sed 's/^# \{0,1\}//'
    else echo "Aide : https://github.com/$REPO/blob/main/docs/linux.md (conteneur LXC Proxmox)"; fi
}

[ "$(id -u)" -eq 0 ] || die "lancez ce script en root sur l'hôte Proxmox."
for command in pct pveam pvesh pvesm pveversion curl tar; do
    command -v "$command" >/dev/null || die "commande $command introuvable : ce script se lance sur un hôte Proxmox VE."
done
PVE_MAJOR=$(pveversion | sed -n 's#^pve-manager/\([0-9]*\)\..*#\1#p')
if [ -z "$PVE_MAJOR" ] || [ "$PVE_MAJOR" -lt 7 ]; then die "Proxmox VE 7 ou plus récent requis ($(pveversion))."; fi
[ "$(dpkg --print-architecture)" = amd64 ] || die "hôte amd64 requis."

# Copie locale du projet : celle qui contient ce script, s'il est lancé depuis le projet.
SOURCE=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    if [ -f "$here/install.sh" ] && [ -f "$here/linux/lxc/proxmox-lxc.sh" ]; then SOURCE=$here; fi
fi

CTID="" NAME=loco STORAGE="" DISK=100 CORES=2 MEMORY=2048 BRIDGE="" VLAN="" IP=dhcp GW="" DNS=""
TEMPLATE_STORAGE="" UPDATE=0 VERSION=""
need() { [ $# -ge 2 ] || die "l'option $1 attend une valeur."; }
while [ $# -gt 0 ]; do
    case "$1" in
        --ctid) need "$@"; CTID="$2"; shift 2 ;;
        --hostname) need "$@"; NAME="$2"; shift 2 ;;
        --storage) need "$@"; STORAGE="$2"; shift 2 ;;
        --disk) need "$@"; DISK="$2"; shift 2 ;;
        --cores) need "$@"; CORES="$2"; shift 2 ;;
        --memory) need "$@"; MEMORY="$2"; shift 2 ;;
        --bridge) need "$@"; BRIDGE="$2"; shift 2 ;;
        --vlan) need "$@"; VLAN="$2"; shift 2 ;;
        --ip) need "$@"; IP="$2"; shift 2 ;;
        --gw) need "$@"; GW="$2"; shift 2 ;;
        --dns) need "$@"; DNS="$2"; shift 2 ;;
        --template-storage) need "$@"; TEMPLATE_STORAGE="$2"; shift 2 ;;
        --version) need "$@"; VERSION="$2"; SOURCE=""; shift 2 ;;
        --source) need "$@"; SOURCE="$(cd "$2" && pwd)"; shift 2 ;;
        --update) UPDATE=1; shift ;;
        --) shift; break ;;
        -h|--help) usage; exit 0 ;;
        *) die "option inconnue : $1 (voir --help ; options d'install.sh après « -- »)" ;;
    esac
done
INSTALL_ARGS=("$@")

num() { [[ "$2" =~ ^[0-9]+$ ]] || die "$1 doit être un nombre."; }
if [ -n "$CTID" ]; then num --ctid "$CTID"; fi
num --disk "$DISK"; num --cores "$CORES"; num --memory "$MEMORY"
if [ -n "$VLAN" ]; then num --vlan "$VLAN"; fi
[[ "$NAME" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || die "--hostname : nom d'hôte invalide."
if [ "$IP" != dhcp ]; then
    [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die "--ip : adresse IPv4 avec masque attendue (ex. 192.168.10.20/24)."
    [[ "$GW" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--ip demande aussi --gw (passerelle IPv4)."
fi
if [ -n "$DNS" ]; then [[ "$DNS" =~ ^[0-9a-fA-F.:]+$ ]] || die "--dns : adresse IP attendue."; fi
if [ -n "$SOURCE" ]; then [ -f "$SOURCE/install.sh" ] || die "--source : install.sh introuvable dans $SOURCE."; fi

# Version publiée : vérifiée ici, téléchargée par le conteneur depuis GitHub (HTTPS).
if [ -z "$SOURCE" ]; then
    if [ -z "$VERSION" ]; then
        VERSION=$(curl -fsSI "https://github.com/$REPO/releases/latest" | tr -d '\r' \
            | sed -n 's#^[Ll]ocation: .*/releases/tag/\(v[0-9][0-9.]*\)$#\1#p' | tail -1)
        [ -n "$VERSION" ] || die "dernière version introuvable sur GitHub (accès Internet de l'hôte ?), utilisez --version."
    fi
    [[ "$VERSION" =~ ^v[0-9]+(\.[0-9]+)*$ ]] || die "--version : vX.Y.Z attendu."
    ARCHIVE_URL="https://github.com/$REPO/archive/refs/tags/$VERSION.tar.gz"
    curl -fsSIL -o /dev/null "$ARCHIVE_URL" || die "version $VERSION introuvable sur GitHub."
fi

# Commande dans le conteneur, sans la locale de l'hôte (souvent absente du modèle) ni question d'apt.
cexec() { pct exec "$CTID" -- env LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive "$@"; }

# Copie du projet dans le conteneur (/usr/local/src/loco), puis install.sh.
run_install() {
    cexec sh -c 'rm -rf /usr/local/src/loco && mkdir -p /usr/local/src/loco' || return 1
    if [ -n "$SOURCE" ]; then
        local archive
        archive=$(mktemp --suffix=.tar.gz)
        tar --exclude=.git --exclude=./loco --exclude=__pycache__ --exclude=./test-results --exclude=./.env \
            -czf "$archive" -C "$SOURCE" . || return 1
        pct push "$CTID" "$archive" /root/loco.tar.gz || return 1
        rm -f "$archive"
        say "Projet copié depuis $SOURCE"
    else
        cexec sh -c 'command -v curl >/dev/null || { apt-get update -q && apt-get install -y -q curl ca-certificates; } >/dev/null' || return 1
        cexec curl -fsSL --retry 3 -o /root/loco.tar.gz "$ARCHIVE_URL" || return 1
        say "loco $VERSION téléchargé depuis GitHub"
    fi
    # Archive GitHub : contenu dans un dossier loco-X.Y.Z, ramené à la racine.
    # shellcheck disable=SC2016  # expansions faites dans le conteneur
    cexec sh -c 'cd /usr/local/src/loco && tar -xzf /root/loco.tar.gz && rm -f /root/loco.tar.gz &&
        if [ ! -f install.sh ]; then d=$(echo loco-*); [ -f "$d/install.sh" ] &&
        find "$d" -mindepth 1 -maxdepth 1 -exec mv -t . {} + && rmdir "$d"; fi' || return 1
    cexec /usr/local/src/loco/install.sh "$@"
}

if [ "$UPDATE" = 1 ]; then
    [ -n "$CTID" ] || die "--update demande --ctid."
    pct status "$CTID" 2>/dev/null | grep -q running || die "le conteneur $CTID n'existe pas ou n'est pas démarré."
    cexec test -f /etc/loco/loco.conf || die "loco n'est pas installé dans le conteneur $CTID."
    say "Mise à jour de loco dans le conteneur $CTID"
    run_install "${INSTALL_ARGS[@]}"
    exit 0
fi

# ---------------------------------------------------------------- choix adaptés à l'hôte
pick() {   # préférences… | liste disponible sur l'entrée standard
    local available first p
    available=$(cat)
    first=$(echo "$available" | head -1)
    for p in "$@"; do
        if echo "$available" | grep -qx "$p"; then echo "$p"; return; fi
    done
    echo "$first"
}
ROOT_STORAGES=$(pvesm status --content rootdir 2>/dev/null | awk 'NR > 1 && $3 == "active" {print $1}')
TEMPLATE_STORAGES=$(pvesm status --content vztmpl 2>/dev/null | awk 'NR > 1 && $3 == "active" {print $1}')
BRIDGES=$(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -E '^vmbr' || true)
[ -n "$ROOT_STORAGES" ] || die "aucun stockage actif n'accepte les conteneurs (contenu « Conteneur » / rootdir)."
[ -n "$TEMPLATE_STORAGES" ] || die "aucun stockage actif n'accepte les modèles de conteneur (contenu vztmpl)."
if [ -z "$STORAGE" ]; then STORAGE=$(echo "$ROOT_STORAGES" | pick local-lvm local-zfs); fi
if [ -z "$TEMPLATE_STORAGE" ]; then TEMPLATE_STORAGE=$(echo "$TEMPLATE_STORAGES" | pick local); fi
if [ -z "$BRIDGE" ]; then BRIDGE=$(echo "$BRIDGES" | pick vmbr0); fi
echo "$ROOT_STORAGES" | grep -qx "$STORAGE" \
    || die "stockage « $STORAGE » absent ou sans contenu « Conteneur » ; disponibles : $(echo "$ROOT_STORAGES" | xargs)."
echo "$TEMPLATE_STORAGES" | grep -qx "$TEMPLATE_STORAGE" \
    || die "stockage de modèles « $TEMPLATE_STORAGE » absent ou sans contenu « Modèle de conteneur » ; disponibles : $(echo "$TEMPLATE_STORAGES" | xargs)."
if [ -z "$BRIDGE" ] || ! ip link show "$BRIDGE" >/dev/null 2>&1; then die "pont réseau introuvable : ${BRIDGE:-aucun pont vmbr}."; fi

if [ -z "$CTID" ]; then CTID=$(pvesh get /cluster/nextid); fi
if [ -e "/etc/pve/lxc/$CTID.conf" ] || [ -e "/etc/pve/qemu-server/$CTID.conf" ] || pvesh get /cluster/resources --type vm --output-format json 2>/dev/null | grep -q "\"vmid\":${CTID}[,}]"; then
    die "le numéro $CTID est déjà utilisé."
fi

# Debian 13 (Proxmox 8 et plus), sinon Debian 12 : les deux sont pris en charge par install.sh.
say "Modèle de conteneur"
series=(13 12)
if [ "$PVE_MAJOR" -lt 8 ]; then series=(12); fi
template=""
for v in "${series[@]}"; do
    template=$(pveam list "$TEMPLATE_STORAGE" | awk '{print $1}' | sed 's#^.*vztmpl/##' \
        | grep -E "^debian-$v-standard_.*_amd64\.tar\.(zst|gz|xz)$" | sort -V | tail -1 || true)
    if [ -n "$template" ]; then break; fi
done
if [ -z "$template" ]; then
    pveam update >/dev/null || warn "liste des modèles non mise à jour (pveam update)."
    for v in "${series[@]}"; do
        template=$(pveam available --section system | awk '{print $2}' \
            | grep -E "^debian-$v-standard_.*_amd64\.tar\.(zst|gz|xz)$" | sort -V | tail -1 || true)
        if [ -n "$template" ]; then break; fi
    done
    [ -n "$template" ] || die "aucun modèle Debian 12 ou 13 disponible (pveam available --section system)."
    pveam download "$TEMPLATE_STORAGE" "$template"   # empreinte vérifiée par pveam
fi
echo "    $template"

say "Création du conteneur $CTID ($NAME) : $STORAGE ${DISK} Go, $CORES processeurs, $MEMORY Mo, $BRIDGE"
net="name=eth0,bridge=$BRIDGE,firewall=1"
if [ "$IP" = dhcp ]; then net="$net,ip=dhcp"; else net="$net,ip=$IP,gw=$GW"; fi
if [ -n "$VLAN" ]; then net="$net,tag=$VLAN"; fi
create=(--hostname "$NAME" --ostype debian --unprivileged 1 --features nesting=1
        --cores "$CORES" --memory "$MEMORY" --swap 512 --rootfs "$STORAGE:$DISK"
        --net0 "$net" --onboot 1 --description "loco : https://github.com/$REPO")
if [ -n "$DNS" ]; then create+=(--nameserver "$DNS"); fi
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$template" "${create[@]}" >/dev/null
pct start "$CTID"

say "Attente du réseau"
for (( i=0; i<60; i++ )); do
    if cexec sh -c 'ip -4 route get 1.1.1.1 >/dev/null 2>&1 && getent hosts deb.debian.org >/dev/null' 2>/dev/null; then
        break
    fi
    sleep 2
done
cexec getent hosts deb.debian.org >/dev/null 2>&1 \
    || die "le conteneur $CTID n'a pas d'accès Internet (DHCP, --ip/--gw, --dns ?). Pour le supprimer : pct destroy $CTID --purge"

say "Installation de loco dans le conteneur $CTID"
run_install "${INSTALL_ARGS[@]}" \
    || die "installation interrompue ; conteneur conservé pour diagnostic (pct enter $CTID). Suppression : pct destroy $CTID --purge"
echo
echo "    Conteneur          : $CTID ($NAME), démarré avec l'hôte"
echo "    Console            : pct enter $CTID"
echo "    Accès perdu        : pct exec $CTID -- loco-user passwd admin"
echo "    Mise à jour        : bash proxmox-lxc.sh --ctid $CTID --update"
echo "    Suppression        : pct destroy $CTID --purge"
