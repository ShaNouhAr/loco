#!/bin/bash
# loco : désinstallation.
#
#   sudo ./remove.sh            retire les services, la configuration et les paquets installés par loco,
#                               mais garde les données (logiciels, paquets .deb, caches, clé de signature)
#   sudo ./remove.sh --purge    supprime aussi toutes les données
#   -y                          ne pas demander de confirmation
set -euo pipefail

ETC=/etc/loco
STATE=/var/lib/loco
PREFIX=/opt/loco

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "lancez ce script en root (sudo ./remove.sh)."

PURGE=0 YES=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -y|--yes) YES=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "option inconnue : $arg" ;;
    esac
done

DATA=/srv/loco
ACNG_SOURCE=0
ACNG_USER_CREATED=0
[ -d /usr/local/lib/loco/acng ] && ACNG_SOURCE=1
[ -f "$ETC/install-state/rocky-acng-user-created" ] && ACNG_USER_CREATED=1
# shellcheck disable=SC1091
[ -f "$ETC/loco.conf" ] && . "$ETC/loco.conf"
cd /

if [ "$YES" = 0 ]; then
    if [ "$PURGE" = 1 ]; then
        echo "loco va être désinstallé ET toutes ses données supprimées ($DATA, $STATE, cache apt-cacher-ng)."
    else
        echo "loco va être désinstallé (les données dans $DATA et $STATE sont conservées)."
    fi
    read -r -p "Continuer ? [o/N] " answer < /dev/tty
    [[ "$answer" =~ ^[oOyY]$ ]] || { echo "Annulé."; exit 1; }
fi

# shellcheck disable=SC1091
. /etc/os-release
say "Arrêt des services"
if [ "$ID" = alpine ]; then
    # shellcheck source=linux/lib/alpine.sh
    . "$PREFIX/lib/alpine.sh"
    alpine_remove
fi
systemctl disable --now loco-portal loco-stats.timer loco-nginx.path loco-backup.timer \
    >/dev/null 2>&1 || true
rm -f /etc/systemd/system/loco-portal.service /etc/systemd/system/loco-stats.service \
      /etc/systemd/system/loco-stats.timer /etc/systemd/system/loco-nginx.service \
      /etc/systemd/system/loco-nginx.path /etc/systemd/system/loco-backup.service \
      /etc/systemd/system/loco-backup.timer
rm -rf /etc/systemd/system/apt-cacher-ng.service.d/loco.conf
rmdir /etc/systemd/system/apt-cacher-ng.service.d 2>/dev/null || true
rm -f /etc/apt-cacher-ng/zz-loco.conf /usr/local/sbin/loco-user /usr/local/sbin/loco-fleet /usr/local/sbin/loco-backup /usr/local/sbin/loco-apt
for f in /etc/hosts.allow /etc/hosts.deny; do
    if [ -f "$f" ]; then sed -i '/# loco$/d' "$f"; fi
done
systemctl daemon-reload 2>/dev/null || true

say "Configuration nginx"
if [ -f "$ETC/install-state/nginx-rocky-original.conf" ]; then
    # shellcheck source=linux/lib/rocky.sh
    . "$PREFIX/lib/rocky.sh"
    rocky_remove
fi
rm -f /etc/nginx/sites-enabled/loco-portal.conf /etc/nginx/sites-available/loco-portal.conf \
      /etc/nginx/sites-enabled/loco-wucache.conf /etc/nginx/sites-available/loco-wucache.conf \
      /etc/nginx/modules-enabled/99-loco-stream.conf /etc/nginx/snippets/loco-clients.conf \
      /etc/nginx/snippets/loco-admin.conf /etc/nginx/snippets/loco-proxy.conf \
      /etc/nginx/snippets/loco-flathub-proxy.conf /etc/nginx/snippets/loco-rpm-proxy.conf \
      /etc/nginx/sites-enabled/loco-apt.conf /etc/nginx/sites-available/loco-apt.conf
if [ -f "$STATE/nginx-default-disabled" ]; then
    [ -e /etc/nginx/sites-available/default ] && ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
    rm -f "$STATE/nginx-default-disabled"
fi

# Paquets installés par loco uniquement (ceux déjà présents avant restent en place).
PKGS=()
PACKAGE_RECORD="$STATE/installed-packages"
if [ -f "$ETC/install-state/installed-packages" ]; then PACKAGE_RECORD="$ETC/install-state/installed-packages"; fi
if [ -f "$PACKAGE_RECORD" ]; then mapfile -t PKGS < "$PACKAGE_RECORD"; fi
if [ ${#PKGS[@]} -gt 0 ]; then
    say "Désinstallation des paquets : ${PKGS[*]}"
    # EPEL peut fournir apt-get sur Rocky : choisir d'après le système, jamais d'après sa présence.
    if [ "$ID" = alpine ]; then
        apk del --no-progress "${PKGS[@]}" >/dev/null || true
    elif [ "$ID" = rocky ]; then
        dnf remove -y --setopt=clean_requirements_on_remove=False "${PKGS[@]}" >/dev/null
    else
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -q "${PKGS[@]}" >/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y -q >/dev/null || true
    fi
fi
if [ "$PACKAGE_RECORD" = "$ETC/install-state/installed-packages" ]; then rm -f "$PACKAGE_RECORD"; fi
if systemctl is-active -q nginx 2>/dev/null; then
    if nginx -t >/dev/null 2>&1; then systemctl reload nginx; fi
fi
if systemctl is-active -q apt-cacher-ng 2>/dev/null; then systemctl restart apt-cacher-ng || true; fi

say "Suppression du programme"
rm -rf "$PREFIX" "$PREFIX.new"

if [ "$PURGE" = 1 ]; then
    say "Suppression des données"
    case "$DATA" in /|/srv|/var|/home|/opt|"") die "dossier de données suspect : '$DATA'" ;; esac
    rm -rf "$DATA" "$STATE" "$ETC"
    rm -f /var/log/nginx/loco-*.log* /var/log/loco-install.log
    # Cache et journaux d'apt-cacher-ng seulement s'il avait été installé par loco.
    if [ "$ACNG_SOURCE" = 1 ] || printf '%s\n' "${PKGS[@]}" | grep -qx apt-cacher-ng; then
        rm -rf /var/cache/apt-cacher-ng /var/log/apt-cacher-ng
    fi
    if [ "$ACNG_USER_CREATED" = 1 ]; then userdel apt-cacher-ng; fi
    if [ "$ID" = alpine ]; then deluser loco 2>/dev/null || true; delgroup loco 2>/dev/null || true
    else userdel loco 2>/dev/null || true; fi
    BACKUP_DIR=${BACKUP_DIR:-/var/backups/loco}
    if [ -d "$BACKUP_DIR" ]; then
        echo "    Sauvegardes conservées : $BACKUP_DIR (à supprimer à la main si inutiles)"
    fi
else
    echo "    Données conservées : $DATA, $STATE, $ETC (sudo ./remove.sh --purge pour tout effacer)"
fi

say "loco est désinstallé"
