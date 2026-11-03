#!/bin/bash
# loco (image Docker) : à chaque démarrage, applique les variables d'environnement avec
# install.sh --container (même configuration que l'installation native), puis lance supervisord.
# Les variables font foi : une variable retirée revient à la valeur par défaut.
# Avec une commande (docker compose run --rm loco bash), l'exécute à la place.
set -euo pipefail

if [ $# -gt 0 ]; then exec "$@"; fi

# Les volumes copiés par up.sh sont montés aux nouveaux chemins, avec les mêmes UID fixes.
if [ -f /etc/loco/local-repo.conf ]; then
    [ ! -e /etc/loco/loco.conf ] || { echo 'Deux configurations présentes.' >&2; exit 1; }
    sed -e 's@/srv/local-repo@/srv/loco@g' -e 's@/var/backups/local-repo@/var/backups/loco@g' \
        /etc/loco/local-repo.conf > /etc/loco/loco.conf
    chmod 644 /etc/loco/loco.conf
    rm /etc/loco/local-repo.conf
fi

PRIVATE_NETS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10"

flag() {   # VARIABLE option-si-1 option-si-0 (défaut : 1)
    case "${!1:-1}" in
        1|yes|true|on) args+=("$2") ;;
        0|no|false|off) args+=("$3") ;;
        *) echo "$1 : 1 ou 0 attendu (valeur : ${!1})." >&2; exit 1 ;;
    esac
}

args=(--container
      --host "${LOCO_HOST:-}"
      --https-port "${LOCO_HTTPS_PORT:-8443}"
      --portal-url "${LOCO_PORTAL_URL:-}"
      --http-port "${LOCO_HTTP_PORT:-8088}"
      --allow "${LOCO_ALLOW:-$PRIVATE_NETS}"
      --admin-allow "${LOCO_ADMIN_ALLOW:-${LOCO_ALLOW:-$PRIVATE_NETS}}"
      --resolvers "${LOCO_RESOLVERS:-1.1.1.1 9.9.9.9}"
      --max-upload "${LOCO_MAX_UPLOAD:-8g}"
      --backup-keep "${LOCO_BACKUP_KEEP:-3}")
flag LOCO_HTTP --http --no-http
flag LOCO_WU --wu --no-wu
flag LOCO_FLATPAK --flatpak --no-flatpak
flag LOCO_RPM --rpm --no-rpm
if [ -n "${LOCO_PROXY:-}" ]; then args+=(--proxy "$LOCO_PROXY"); else args+=(--no-proxy); fi
# Tailles des caches : vides, elles sont calculées au premier démarrage puis conservées.
if [ -n "${LOCO_WU_CACHE_SIZE:-}" ]; then args+=(--wu-cache-size "$LOCO_WU_CACHE_SIZE"); fi
if [ -n "${LOCO_FLATPAK_CACHE_SIZE:-}" ]; then args+=(--flatpak-cache-size "$LOCO_FLATPAK_CACHE_SIZE"); fi
if [ -n "${LOCO_RPM_CACHE_SIZE:-}" ]; then args+=(--rpm-cache-size "$LOCO_RPM_CACHE_SIZE"); fi
# Certificat d'entreprise : fichiers montés en lecture seule dans le conteneur.
if [ -n "${LOCO_TLS_CERT:-}${LOCO_TLS_KEY:-}" ]; then args+=(--tls-cert "${LOCO_TLS_CERT:-}" --tls-key "${LOCO_TLS_KEY:-}"); fi

/usr/src/loco/linux/install.sh "${args[@]}"
exec /usr/bin/supervisord -c /etc/supervisor/loco.conf
