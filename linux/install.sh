#!/bin/bash
# loco : installe le dépôt local (paquets Linux, Windows Update, logiciels Windows)
# sur Debian / Ubuntu / Rocky Linux / Alpine Linux (versions validées : README). Relançable sans risque pour mettre à jour
# ou reconfigurer.
# L'image Docker l'utilise aussi, avec --container (voir linux/docker/).
#
#   sudo ./install.sh [options]
#
#   --host IP                adresse annoncée aux postes (défaut : IP de la route par défaut)
#   --https-port N           port HTTPS de l'interface et des postes (défaut : 8443)
#   --http-port N            port HTTP d'amorçage des postes (défaut : 8088)
#   --no-http / --http       désactive / réactive le port HTTP (HTTPS seul : autorité déployée sur les postes)
#   --allow "CIDR ..."       réseaux autorisés à utiliser les dépôts et caches (défaut : réseaux privés)
#   --admin-allow "CIDR ..." réseaux autorisés à ouvrir l'interface (défaut : comme --allow)
#   --proxy URL / --no-proxy proxy HTTP d'entreprise pour sortir sur Internet (apt-cacher-ng, téléchargements)
#   --data DIR               dossier des données et des caches (défaut : /srv/loco)
#   --wu-cache-size T        taille max du cache Windows Update, ex. 50g (défaut : moitié de l'espace libre, max 200g)
#   --resolvers "A B"        DNS utilisés par le cache Windows Update (défaut : "1.1.1.1 9.9.9.9") ;
#                            ne pas mettre un DNS qui redirige lui-même les domaines Windows Update
#   --max-upload T           taille max d'un fichier envoyé au portail (défaut : 8g)
#   --backup-keep N          sauvegardes quotidiennes conservées (défaut : 3 ; 0 = désactivées)
#   --backup-dir DIR         dossier des sauvegardes (défaut : /var/backups/loco)
#   --github-repo SOURCE     source des mises à jour : « owner/repo » GitHub ou URL d'un projet GitLab (défaut : officiel)
#   --password MDP           définit le mot de passe du compte « admin »
#   --tls-cert F --tls-key F certificat fourni au lieu de l'autorité locale
#   --no-wu / --wu           sans / avec le cache Windows Update (ports 80 et 443)
#   --no-flatpak / --flatpak sans / avec Flatpak (cache Flathub et dépôt Flatpak local)
#   --flatpak-cache-size T   taille max du cache Flathub (défaut : quart de l'espace libre, max 100g)
#   --no-rpm / --rpm         sans / avec la famille Red Hat (cache Rocky/AlmaLinux/EPEL, dépôts RPM el8…)
#   --rpm-cache-size T       taille max du cache RPM (défaut : quart de l'espace libre, max 100g)
#   --apt-cache-size T       Alpine : taille max du cache APT nginx (défaut : quart de l'espace libre, max 100g)
set -euo pipefail

# Dossier linux/ du projet (installateur et fichiers propres à Linux) et racine du projet (portail, gabarits nginx).
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"
cd /
PREFIX=/opt/loco
ETC=/etc/loco
STATE=/var/lib/loco
APP_PORT=8089
PACKAGE_RECORD="$STATE/installed-packages"
PACKAGES=(nginx libnginx-mod-stream apt-cacher-ng apt-utils gnupg python3-flask python3-cryptography python3-qrcode gunicorn curl openssl ca-certificates)
PRIVATE_NETS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10"

# Langue du serveur : français si LANG/LC_* commence par « fr », espagnol par « es », anglais sinon.
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_LANG=fr ;; es*) LOCO_LANG=es ;; *) LOCO_LANG=en ;; esac
t() { case "$LOCO_LANG" in fr) printf '%s' "$1" ;; es) printf '%s' "${3:-$2}" ;; *) printf '%s' "$2" ;; esac; }
say()  { printf '\033[1;34m==>\033[0m %s\n' "$(t "$1" "${2:-$1}" "${3:-}")"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$(t "$1" "${2:-$1}" "${3:-}")" >&2; }
die()  { printf '\033[1;31m%s\033[0m %s\n' "$(t 'Erreur :' 'Error:' 'Error:')" "$(t "$1" "${2:-$1}" "${3:-}")" >&2; exit 1; }
trap 'die "échec inattendu ligne $LINENO : $BASH_COMMAND" "unexpected failure at line $LINENO: $BASH_COMMAND" "fallo inesperado en la línea $LINENO: $BASH_COMMAND"' ERR
LOG=/var/log/loco-install.log

[ "$(id -u)" -eq 0 ] || die "lancez ce script en root (sudo ./install.sh)." "run this script as root (sudo ./install.sh)." "ejecute este script como root (sudo ./install.sh)."
# La présence éventuelle d'APT sur Rocky ne doit pas sélectionner le mauvais gestionnaire.
# shellcheck disable=SC1091
. /etc/os-release
PLATFORM=debian
PYTHON=/usr/bin/python3
GUNICORN=/usr/bin/gunicorn
OS_MAJOR=${VERSION_ID%%.*}
NGINX_USER=www-data
SQUID_USER=proxy
LOG_GROUP=adm
CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
case "$ID" in
    debian|ubuntu) command -v apt-get >/dev/null || die "apt-get requis." "apt-get required." "apt-get requerido." ;;
    rocky)
        case "$OS_MAJOR" in 8|9|10) ;; *) die "Serveur Rocky Linux 8, 9 ou 10 requis." "Rocky Linux server 8, 9 or 10 required." "Se requiere un servidor Rocky Linux 8, 9 o 10." ;; esac
        PLATFORM=rocky
        PACKAGE_RECORD="$ETC/install-state/installed-packages"
        PYTHON=/usr/local/lib/loco/venv/bin/python
        GUNICORN=/usr/local/lib/loco/venv/bin/gunicorn
        NGINX_USER=nginx
        SQUID_USER=squid
        LOG_GROUP=nginx
        CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
        # shellcheck source=linux/lib/rocky.sh
        . "$SRC/lib/rocky.sh"
        ;;
    alpine)
        PLATFORM=alpine
        NGINX_USER=nginx
        SQUID_USER=squid
        LOG_GROUP=nginx
        # shellcheck source=linux/lib/alpine.sh
        . "$SRC/lib/alpine.sh"
        alpine_supported || die "Alpine Linux 3.24 ou plus récent requis." "Alpine Linux 3.24 or newer required." "Se requiere Alpine Linux 3.24 o posterior."
        ;;
    *) die "Serveur Debian, Ubuntu, Rocky Linux ou Alpine Linux requis." "Debian, Ubuntu, Rocky Linux or Alpine Linux server required." "Se requiere un servidor Debian, Ubuntu, Rocky Linux o Alpine Linux." ;;
esac
# Le code et les ressources publiques doivent rester lisibles même avec umask 077.
umask 022
# --container : exécution au démarrage de l'image Docker (paquets déjà installés, pas de systemd ;
# les processus sont lancés par supervisord, voir linux/docker/). Repéré avant le reste des options.
CONTAINER=0
for arg in "$@"; do if [ "$arg" = "--container" ]; then CONTAINER=1; fi; done
if [ "$CONTAINER" = 1 ]; then
    [ "${LOCO_RUNTIME:-}" = container ] || die "--container est réservé à l'image Docker (docker compose up -d)." "--container is reserved for the Docker image (docker compose up -d)." "--container está reservado a la imagen Docker (docker compose up -d)."
elif [ "$PLATFORM" = alpine ]; then
    command -v rc-service >/dev/null || die "OpenRC requis." "OpenRC required." "Se requiere OpenRC."
else
    command -v systemctl >/dev/null || die "systemd requis." "systemd required." "Se requiere systemd."
fi

# Python sert à valider la configuration : installé d'abord s'il manque (système minimal).
if ! command -v python3 >/dev/null; then
    mkdir -p "$STATE"
    if [ "$PLATFORM" = rocky ]; then install -d -m 700 -o root -g root "$ETC/install-state"; fi
    echo python3 >> "$PACKAGE_RECORD"
    if [ "$PLATFORM" = rocky ]; then
        dnf install -y python3 >/dev/null 2>&1 || die "impossible d'installer python3." "cannot install python3." "no se pudo instalar python3."
    elif [ "$PLATFORM" = alpine ]; then
        apk add --no-progress python3 >/dev/null 2>&1 || die "impossible d'installer python3." "cannot install python3." "no se pudo instalar python3."
    else
        { apt-get update -q -o APT::Update::Error-Mode=any && DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3; } >/dev/null 2>&1 \
            || die "impossible d'installer python3." "cannot install python3." "no se pudo instalar python3."
    fi
fi

# ---------------------------------------------------------------- réglages
# shellcheck source=linux/lib/migrate.sh
. "$SRC/lib/migrate.sh"
if [[ " $* " != *" --help "* && " $* " != *" -h "* ]]; then migrate_local_repo; fi
# Valeurs par défaut, puis celles de l'installation précédente, puis les options.
PORTAL_PORT=8088 HTTPS_PORT=8443 HTTP_ENABLED=1 HOST="" DATA=/srv/loco WU_CACHE_SIZE=""
RESOLVERS="1.1.1.1 9.9.9.9" WU_ENABLED=1 WU_PROXY_PORT=3128 ALLOW="$PRIVATE_NETS" ADMIN_ALLOW="" UPSTREAM_PROXY=""
MAX_UPLOAD=8g BACKUP_KEEP=3 BACKUP_DIR=/var/backups/loco FLATPAK_ENABLED=1 FLATPAK_CACHE_SIZE=""
RPM_ENABLED=1 RPM_CACHE_SIZE="" APT_CACHE_SIZE="" GITHUB_REPO=""
# shellcheck disable=SC1091
[ -f "$ETC/loco.conf" ] && . "$ETC/loco.conf"
PASSWORD="" TLS_CERT="" TLS_KEY=""
need() { [ $# -ge 2 ] || die "l'option $1 attend une valeur." "option $1 expects a value." "la opción $1 espera un valor."; }
while [ $# -gt 0 ]; do
    case "$1" in
        --host) need "$@"; HOST="$2"; shift 2 ;;
        --https-port) need "$@"; HTTPS_PORT="$2"; shift 2 ;;
        --http-port|--port) need "$@"; PORTAL_PORT="$2"; shift 2 ;;
        --no-http) HTTP_ENABLED=0; shift ;;
        --http) HTTP_ENABLED=1; shift ;;
        --allow) need "$@"; ALLOW="$2"; shift 2 ;;
        --admin-allow) need "$@"; ADMIN_ALLOW="$2"; shift 2 ;;
        --proxy) need "$@"; UPSTREAM_PROXY="$2"; shift 2 ;;
        --no-proxy) UPSTREAM_PROXY=""; shift ;;
        --data) need "$@"; DATA="$2"; shift 2 ;;
        --wu-cache-size) need "$@"; WU_CACHE_SIZE="$2"; shift 2 ;;
        --wu-proxy-port) need "$@"; WU_PROXY_PORT="$2"; shift 2 ;;
        --resolvers) need "$@"; RESOLVERS="$2"; shift 2 ;;
        --max-upload) need "$@"; MAX_UPLOAD="$2"; shift 2 ;;
        --backup-keep) need "$@"; BACKUP_KEEP="$2"; shift 2 ;;
        --backup-dir) need "$@"; BACKUP_DIR="$2"; shift 2 ;;
        --github-repo) need "$@"; GITHUB_REPO="$2"; shift 2 ;;
        --password) need "$@"; PASSWORD="$2"; shift 2 ;;
        --tls-cert) need "$@"; TLS_CERT="$2"; shift 2 ;;
        --tls-key) need "$@"; TLS_KEY="$2"; shift 2 ;;
        --no-flatpak) FLATPAK_ENABLED=0; shift ;;
        --flatpak) FLATPAK_ENABLED=1; shift ;;
        --flatpak-cache-size) need "$@"; FLATPAK_CACHE_SIZE="$2"; shift 2 ;;
        --no-rpm) RPM_ENABLED=0; shift ;;
        --rpm) RPM_ENABLED=1; shift ;;
        --rpm-cache-size) need "$@"; RPM_CACHE_SIZE="$2"; shift 2 ;;
        --apt-cache-size) need "$@"; APT_CACHE_SIZE="$2"; shift 2 ;;
        --container) shift ;;
        --no-wu) WU_ENABLED=0; shift ;;
        --wu) WU_ENABLED=1; shift ;;
        -h|--help) sed -n '2,/^set -euo/{/^#/p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "option inconnue : $1 (voir --help)" "unknown option: $1 (see --help)" "opción desconocida: $1 (ver --help)" ;;
    esac
done
if [ -z "$ADMIN_ALLOW" ]; then ADMIN_ALLOW="$ALLOW"; fi

# ---------------------------------------------------------------- vérifications
num() { [[ "$2" =~ ^[0-9]+$ ]] || die "$1 doit être un nombre." "$1 must be a number." "$1 debe ser un número."; }
num --https-port "$HTTPS_PORT"; num --http-port "$PORTAL_PORT"; num --backup-keep "$BACKUP_KEEP"
[ "$PORTAL_PORT" != "$HTTPS_PORT" ] || die "--http-port et --https-port doivent être différents." "--http-port and --https-port must differ." "--http-port y --https-port deben ser diferentes."
[[ "$MAX_UPLOAD" =~ ^[0-9]+[kKmMgG]?$ ]] || die "--max-upload : taille invalide (ex. 8g, 500m)." "--max-upload: invalid size (e.g. 8g, 500m)." "--max-upload: tamaño no válido (ej. 8g, 500m)."
[[ "$WU_CACHE_SIZE" =~ ^([0-9]+[kKmMgGtT]?)?$ ]] || die "--wu-cache-size : taille invalide (ex. 50g)." "--wu-cache-size: invalid size (e.g. 50g)." "--wu-cache-size: tamaño no válido (ej. 50g)."
[[ "$WU_PROXY_PORT" =~ ^[0-9]+$ && "$WU_PROXY_PORT" -ge 1 && "$WU_PROXY_PORT" -le 65535 ]] || die "--wu-proxy-port : port invalide." "--wu-proxy-port: invalid port." "--wu-proxy-port: puerto no válido."
[[ "$FLATPAK_CACHE_SIZE" =~ ^([0-9]+[kKmMgGtT]?)?$ ]] || die "--flatpak-cache-size : taille invalide (ex. 20g)." "--flatpak-cache-size: invalid size (e.g. 20g)." "--flatpak-cache-size: tamaño no válido (ej. 20g)."
[[ "$RPM_CACHE_SIZE" =~ ^([0-9]+[kKmMgGtT]?)?$ ]] || die "--rpm-cache-size : taille invalide (ex. 30g)." "--rpm-cache-size: invalid size (e.g. 30g)." "--rpm-cache-size: tamaño no válido (ej. 30g)."
[[ "$APT_CACHE_SIZE" =~ ^([0-9]+[kKmMgGtT]?)?$ ]] || die "--apt-cache-size : taille invalide (ex. 50g)." "--apt-cache-size: invalid size (e.g. 50g)." "--apt-cache-size: tamaño no válido (ej. 50g)."
if [[ ! "$DATA" =~ ^/[A-Za-z0-9._/-]+$ || "$DATA" == "/" ]]; then die "--data : chemin absolu simple attendu." "--data: a simple absolute path is expected." "--data: se espera una ruta absoluta simple."; fi
[[ "$BACKUP_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "--backup-dir : chemin absolu simple attendu." "--backup-dir: a simple absolute path is expected." "--backup-dir: se espera una ruta absoluta simple."
[ -z "$GITHUB_REPO" ] || [[ "$GITHUB_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || [[ "$GITHUB_REPO" =~ ^https://[A-Za-z0-9._-]+(:[0-9]+)?/[A-Za-z0-9._/-]+/[A-Za-z0-9._-]+$ ]] || die "--github-repo : « owner/repo » GitHub ou URL d'un projet GitLab attendu." "--github-repo: GitHub owner/repo or a GitLab project URL expected." "--github-repo: se espera «owner/repo» de GitHub o la URL de un proyecto GitLab."
if [ -n "$UPSTREAM_PROXY" ]; then
    [[ "$UPSTREAM_PROXY" =~ ^http://[^[:space:]\"\']+$ ]] || die "--proxy : adresse http://hôte:port attendue." "--proxy: an http://host:port address is expected." "--proxy: se espera una dirección http://host:puerto."
fi
if [ -n "$TLS_CERT$TLS_KEY" ]; then
    if [ ! -f "$TLS_CERT" ] || [ ! -f "$TLS_KEY" ]; then die "--tls-cert et --tls-key doivent désigner deux fichiers existants." "--tls-cert and --tls-key must point to two existing files." "--tls-cert y --tls-key deben apuntar a dos archivos existentes."; fi
fi
for r in $RESOLVERS; do [[ "$r" =~ ^[0-9a-fA-F.:]+$ ]] || die "--resolvers : adresse IP attendue ($r)." "--resolvers: an IP address is expected ($r)." "--resolvers: se espera una dirección IP ($r)."; done
# Réseaux : validés et normalisés par Python ; la boucle locale est toujours autorisée.
nets() {
    python3 -c '
import ipaddress, sys
out = ["127.0.0.0/8", "::1"]
for item in sys.argv[1].split():
    if "%" in item:
        sys.exit("zone IPv6 non prise en charge dans les réseaux autorisés")
    try:
        n = str(ipaddress.ip_network(item, strict=False))
    except ValueError:
        sys.exit(f"réseau invalide : {item}")
    if n not in out:
        out.append(n)
print(" ".join(out))' "$1" || die "$2 : réseau invalide." "$2: invalid network." "$2: red no válida."
}
ALLOW_LIST=$(nets "$ALLOW" --allow)
ADMIN_LIST=$(nets "$ADMIN_ALLOW" --admin-allow)

if [ -z "$HOST" ]; then
    HOST=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}' || true)
    if [ -z "$HOST" ]; then HOST=$(ip -4 -o addr show scope global | awk '{sub(/\/.*/, "", $4); print $4; exit}'); fi
fi
[ -n "$HOST" ] || die "impossible de détecter l'IP du serveur, utilisez --host." "cannot detect the server IP, use --host." "no se pudo detectar la IP del servidor, use --host."
ADMIN_URL="https://$HOST:$HTTPS_PORT"
HTTP_URL=""
if [ "$HTTP_ENABLED" = 1 ]; then HTTP_URL="http://$HOST:$PORTAL_PORT"; fi

port_owner() { ss -Hltnp "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -1 | cut -d'"' -f2 || true; }
check_port() {
    local owner
    if [ "$CONTAINER" = 1 ]; then
        # Réseau de l'hôte, rien n'est encore lancé dans le conteneur : tout port ouvert est pris par l'hôte.
        if ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; then
            die "le port $1 est déjà utilisé sur l'hôte (installation native de loco, autre service ?)." "port $1 is already in use on the host (native loco install, another service?)." "el puerto $1 ya está en uso en el anfitrión (instalación nativa de loco, ¿otro servicio?)."
        fi
        return 0
    fi
    owner=$(port_owner "$1")
    if [ -n "$owner" ] && [ "$owner" != "$2" ]; then
        die "le port $1 est déjà utilisé par « $owner »." "port $1 is already in use by \"$owner\"." "el puerto $1 ya está en uso por \"$owner\"."
    fi
}
say "Vérification des ports" "Checking ports" "Comprobación de los puertos"
check_port "$HTTPS_PORT" nginx
if [ "$HTTP_ENABLED" = 1 ]; then check_port "$PORTAL_PORT" nginx; fi
if [ "$PLATFORM" = debian ]; then check_port 3142 apt-cacher-ng; else check_port 3142 nginx; fi
check_port "$APP_PORT" gunicorn
if [ "$WU_ENABLED" = 1 ]; then
    check_port 80 nginx
    check_port 443 nginx
fi

# ---------------------------------------------------------------- paquets
stash_self_client
if [ "$FLATPAK_ENABLED" = 1 ]; then PACKAGES+=(flatpak ostree); fi
if [ "$RPM_ENABLED" = 1 ]; then PACKAGES+=(createrepo-c rpm); fi
if [ "$WU_ENABLED" = 1 ]; then PACKAGES+=(squid); fi
mkdir -p "$STATE"
if [ "$CONTAINER" = 0 ]; then
say "Installation des paquets" "Installing packages" "Instalación de los paquetes"
if [ "$PLATFORM" = rocky ]; then
    rocky_packages
elif [ "$PLATFORM" = alpine ]; then
    alpine_packages
else
touch "$PACKAGE_RECORD"
for p in "${PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
        grep -qx "$p" "$PACKAGE_RECORD" || echo "$p" >> "$PACKAGE_RECORD"
    fi
done
echo "apt-cacher-ng apt-cacher-ng/tunnelenable boolean false" | debconf-set-selections
export DEBIAN_FRONTEND=noninteractive
: > "$LOG"
if ! { apt-get update -q -o APT::Update::Error-Mode=any && apt-get install -y -q --no-install-recommends "${PACKAGES[@]}"; } >> "$LOG" 2>&1; then
    tail -15 "$LOG" >&2
    die "installation des paquets impossible (journal complet : $LOG)." "package installation failed (full log: $LOG)." "no se pudieron instalar los paquetes (registro completo: $LOG)."
fi
fi
fi

# ---------------------------------------------------------------- utilisateur et dossiers
say "Utilisateur et dossiers" "User and directories" "Usuario y carpetas"
if [ "$PLATFORM" = alpine ]; then alpine_user
else id loco >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin loco; fi
install -d -m 755 -o loco -g loco "$DATA" "$DATA/apt" "$DATA/apt/pool" "$DATA/win"
install -d -m 700 -o loco -g loco "$DATA/.tmp" "$STATE/gnupg"
install -d -m 755 -o loco -g loco "$STATE" "$STATE/nginx"
install -d -m 700 -o "$NGINX_USER" -g "$NGINX_USER" "$DATA/wucache" "$DATA/flatpakcache" "$DATA/rpmcache"
if [ "$WU_ENABLED" = 1 ] && id "$SQUID_USER" >/dev/null 2>&1; then
    install -d -m 750 -o "$SQUID_USER" -g "$SQUID_USER" "$DATA/wuproxy"
fi
install -d -m 755 -o loco -g loco "$DATA/flatpak" "$DATA/rpm"
chown -R loco:loco "$DATA/apt" "$DATA/win" "$STATE"
install -d -m 755 "$ETC"
install -d -m 750 -o root -g loco "$BACKUP_DIR"

if [ -z "$WU_CACHE_SIZE" ]; then
    free_g=$(df -BG --output=avail "$DATA" | tail -1 | tr -dc '0-9')
    WU_CACHE_SIZE="$(( free_g / 2 > 200 ? 200 : (free_g / 2 < 5 ? 5 : free_g / 2) ))g"
    if [ "$WU_ENABLED" = 1 ] && [ "$free_g" -lt 10 ]; then
        warn "seulement ${free_g} Go libres dans $DATA : le cache Windows Update ($WU_CACHE_SIZE) risque de remplir le disque." "only ${free_g} GB free in $DATA: the Windows Update cache ($WU_CACHE_SIZE) may fill the disk." "solo ${free_g} GB libres en $DATA: la caché de Windows Update ($WU_CACHE_SIZE) podría llenar el disco."
    fi
fi

if [ -z "$RPM_CACHE_SIZE" ]; then
    free_g=$(df -BG --output=avail "$DATA" | tail -1 | tr -dc '0-9')
    RPM_CACHE_SIZE="$(( free_g / 4 > 100 ? 100 : (free_g / 4 < 2 ? 2 : free_g / 4) ))g"
fi
if [ -z "$FLATPAK_CACHE_SIZE" ]; then
    free_g=$(df -BG --output=avail "$DATA" | tail -1 | tr -dc '0-9')
    FLATPAK_CACHE_SIZE="$(( free_g / 4 > 100 ? 100 : (free_g / 4 < 2 ? 2 : free_g / 4) ))g"
fi
if [ "$PLATFORM" = alpine ] && [ -z "$APT_CACHE_SIZE" ]; then
    free_g=$(df -BG --output=avail "$DATA" | tail -1 | tr -dc '0-9')
    APT_CACHE_SIZE="$(( free_g / 4 > 100 ? 100 : (free_g / 4 < 2 ? 2 : free_g / 4) ))g"
fi

cat > "$ETC/loco.conf" <<EOF
# Généré par install.sh : modifiez puis relancez sudo ./install.sh pour appliquer.
HOST=$HOST
HTTPS_PORT=$HTTPS_PORT
PORTAL_PORT=$PORTAL_PORT
HTTP_ENABLED=$HTTP_ENABLED
ALLOW="$ALLOW"
ADMIN_ALLOW="$ADMIN_ALLOW"
UPSTREAM_PROXY="$UPSTREAM_PROXY"
DATA=$DATA
PYTHON=$PYTHON
WU_ENABLED=$WU_ENABLED
WU_CACHE_SIZE=$WU_CACHE_SIZE
WU_PROXY_PORT=$WU_PROXY_PORT
FLATPAK_ENABLED=$FLATPAK_ENABLED
FLATPAK_CACHE_SIZE=$FLATPAK_CACHE_SIZE
RPM_ENABLED=$RPM_ENABLED
RPM_CACHE_SIZE=$RPM_CACHE_SIZE
APT_CACHE_SIZE=$APT_CACHE_SIZE
RESOLVERS="$RESOLVERS"
MAX_UPLOAD=$MAX_UPLOAD
BACKUP_KEEP=$BACKUP_KEEP
BACKUP_DIR=$BACKUP_DIR
SOURCE_DIR=$(printf '%q' "$ROOT")
GITHUB_REPO=$(printf '%q' "${GITHUB_REPO:-}")
EOF
chmod 644 "$ETC/loco.conf"

# ---------------------------------------------------------------- code
say "Copie du portail dans $PREFIX" "Copying the portal into $PREFIX" "Copia del portal en $PREFIX"
rm -rf "$PREFIX.new"
install -d "$PREFIX.new"
cp -r "$ROOT/portal" "$SRC/bin" "$ROOT/conf" "$SRC/lib" "$SRC/remove.sh" "$PREFIX.new/"
[ -f "$ROOT/CHANGELOG.md" ] && cp "$ROOT/CHANGELOG.md" "$PREFIX.new/"      # journal des versions, consultable depuis le portail
cp "$SRC/conf/"* "$PREFIX.new/conf/"    # unités systemd et réglages apt-cacher-ng, avec les gabarits nginx communs
find "$PREFIX.new" -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$PREFIX.new/bin/"* "$PREFIX.new/remove.sh"
rm -rf "$PREFIX"
mv "$PREFIX.new" "$PREFIX"
install -d /usr/local/sbin    # absent sur Alpine minimal
ln -sf "$PREFIX/bin/loco-user" /usr/local/sbin/loco-user
ln -sf "$PREFIX/bin/loco-fleet" /usr/local/sbin/loco-fleet
ln -sf "$PREFIX/bin/loco-backup" /usr/local/sbin/loco-backup
ln -sf "$PREFIX/bin/loco-apt" /usr/local/sbin/loco-apt

cli() {
    (cd "$PREFIX/portal" && runuser -u loco -- env LOCO_DATA="$DATA" LOCO_STATE="$STATE" LOCO_ETC="$ETC" \
        LOCO_PASSWORD="${LOCO_PASSWORD:-}" LOCO_MUST_CHANGE="${LOCO_MUST_CHANGE:-0}" "$PYTHON" -m loco.cli "$@")
}

# ---------------------------------------------------------------- secrets et comptes
say "Comptes du portail" "Portal accounts" "Cuentas del portal"
(umask 077
python3 - "$ETC/portal.secret" <<'EOF'
import json, secrets, sys
path = sys.argv[1]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
data.setdefault("secret_key", secrets.token_hex(32))
json.dump(data, open(path, "w"))
EOF
)
chown root:loco "$ETC/portal.secret"
chmod 640 "$ETC/portal.secret"
cli init    # migrations ; une ancienne installation à mot de passe unique devient le compte « admin »
python3 - "$ETC/portal.secret" <<'EOF'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data.pop("admin_hash", None)   # repris dans la base des comptes
json.dump(data, open(path, "w"))
EOF
rm -f "$ETC/admin-password"    # plus de mot de passe en clair sur le disque
ISSUED=""
if [ -n "$PASSWORD" ]; then
    LOCO_PASSWORD="$PASSWORD" cli ensure-admin >/dev/null
    LOCO_PASSWORD="$PASSWORD" cli user set-password admin
else
    ISSUED=$(python3 -c 'import secrets, string; a = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"; print("-".join("".join(secrets.choice(a) for _ in range(5)) for _ in range(4)))')
    if [ "$(LOCO_PASSWORD="$ISSUED" LOCO_MUST_CHANGE=1 cli ensure-admin)" != "created" ]; then
        ISSUED=""
    fi
fi

# ---------------------------------------------------------------- clé de signature APT
if ! runuser -u loco -- env GNUPGHOME="$STATE/gnupg" gpg --list-secret-keys 2>/dev/null | grep -q sec; then
    say "Création de la clé de signature du dépôt APT" "Creating the APT repository signing key" "Creación de la clave de firma del repositorio APT"
    runuser -u loco -- env GNUPGHOME="$STATE/gnupg" gpg --batch --quiet --pinentry-mode loopback --passphrase '' \
        --quick-gen-key "loco ($(hostname)) <loco@$(hostname)>" rsa4096 sign never
fi
runuser -u loco -- env GNUPGHOME="$STATE/gnupg" gpg --batch --yes --export -o "$DATA/apt/loco.gpg"
chmod 644 "$DATA/apt/loco.gpg"
cli rebuild-apt >/dev/null
runuser -u loco -- gpgconf --homedir "$STATE/gnupg" --kill gpg-agent 2>/dev/null || true
cli write-maps
if [ "$FLATPAK_ENABLED" = 1 ]; then
    cli flatpak-init
    runuser -u loco -- gpgconf --homedir "$STATE/gnupg" --kill gpg-agent 2>/dev/null || true
fi
if [ "$RPM_ENABLED" = 1 ]; then
    cli rpm-init
    runuser -u loco -- gpgconf --homedir "$STATE/gnupg" --kill gpg-agent 2>/dev/null || true
fi

# ---------------------------------------------------------------- certificats HTTPS
say "Certificat HTTPS" "HTTPS certificate" "Certificado HTTPS"
TLS=$ETC/tls
install -d -m 755 "$TLS"
TLS_ENTERPRISE=""
if [ -n "$TLS_CERT" ]; then
    install -m 644 "$TLS_CERT" "$TLS/server.crt"
    install -m 600 "$TLS_KEY" "$TLS/server.key"
    touch "$TLS/custom"
elif [ ! -f "$TLS/custom" ]; then
    # Autorité locale (10 ans) : à importer une fois sur les postes (GPO, ou page Système).
    if [ ! -f "$TLS/ca.key" ]; then
        # OpenSSL 1.1 de Rocky 8 cumule les extensions du fichier système et -addext.
        # Une configuration minimale évite une CA invalide avec deux basicConstraints.
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
            -config <(printf '[req]\ndistinguished_name=dn\n[dn]\n') \
            -subj "/O=loco/CN=loco CA ($(hostname))" \
            -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
            -keyout "$TLS/ca.key" -out "$TLS/ca.crt" 2>>"$LOG"
        chmod 600 "$TLS/ca.key"; chmod 644 "$TLS/ca.crt"
        rm -f "$TLS/server.crt" "$TLS/mode"
    fi
    # Autorité d'entreprise choisie dans le portail : son certificat est gardé tant qu'il est valable ; sinon retour à
    # l'autorité loco, à laquelle les agents font toujours confiance (elle reste en tête de leur faisceau).
    if [ "$(cat "$TLS/mode" 2>/dev/null)" = enterprise ]; then
        if [ -s "$TLS/server.key" ] && openssl x509 -in "$TLS/server.crt" -noout -checkend 0 >/dev/null 2>&1 \
           && case "$(openssl x509 -in "$TLS/server.crt" -noout -checkip "$HOST" -checkhost "$HOST" 2>/dev/null)" in
                  *"does match"*) true ;; *) false ;; esac; then
            TLS_ENTERPRISE=1
        else
            warn "certificat de l'autorité d'entreprise expiré, absent ou ne couvrant plus $HOST : retour à l'autorité loco." \
                 "enterprise authority certificate expired, missing or no longer covering $HOST: back to the loco authority." \
                 "certificado de la autoridad de empresa caducado, ausente o que ya no cubre $HOST: vuelta a la autoridad loco."
            rm -f "$TLS/mode" "$TLS/server.crt"
        fi
    fi
fi
if [ ! -f "$TLS/custom" ] && [ -z "$TLS_ENTERPRISE" ]; then
    SAN="IP:$HOST,IP:127.0.0.1,DNS:$(hostname),DNS:localhost"
    FQDN=$(hostname -f 2>/dev/null || true)
    if [ -n "$FQDN" ] && [ "$FQDN" != "$(hostname)" ] && [ "$FQDN" != localhost ]; then SAN="$SAN,DNS:$FQDN"; fi
    # Certificat serveur (825 jours) : refait s'il manque, expire dans moins de 30 jours ou si l'IP a changé.
    if [ ! -f "$TLS/server.crt" ] || ! openssl x509 -in "$TLS/server.crt" -noout -checkend 2592000 >/dev/null 2>&1 \
       || ! openssl x509 -in "$TLS/server.crt" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address:$HOST\b"; then
        openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
            -subj "/O=loco/CN=$(hostname)" -keyout "$TLS/server.key" -out "$TLS/server.csr" 2>>"$LOG"
        openssl x509 -req -in "$TLS/server.csr" -CA "$TLS/ca.crt" -CAkey "$TLS/ca.key" \
            -set_serial "0x$(openssl rand -hex 16)" -days 825 -out "$TLS/server.crt" \
            -extfile <(printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' "$SAN") 2>>"$LOG"
        rm -f "$TLS/server.csr"
        chmod 600 "$TLS/server.key"; chmod 644 "$TLS/server.crt"
    fi
    openssl verify -CAfile "$TLS/ca.crt" "$TLS/server.crt" >> "$LOG" 2>&1 \
        || die "le certificat serveur n'est pas vérifiable avec l'autorité locale (journal : $LOG)." "the server certificate cannot be verified against the local authority (log: $LOG)." "el certificado del servidor no es verificable con la autoridad local (registro: $LOG)."
fi
# Faisceau épinglé par les agents et servi en /ca.crt : autorité loco en tête, puis l'autorité d'entreprise importée.
if [ ! -f "$TLS/custom" ]; then
    "$PYTHON" -I "$PREFIX/portal/locoadmin.py" tls-trust >> "$LOG" 2>&1 || cp -f "$TLS/ca.crt" "$TLS/trust.pem"
    chmod 644 "$TLS/trust.pem"
fi

# ---------------------------------------------------------------- génération des configurations
# Remplace @CLÉ@ par la variable LRT_CLÉ (valeurs quelconques, y compris avec des caractères spéciaux).
render() {
    python3 -c '
import os, sys
text = open(sys.argv[1]).read()
for key, value in os.environ.items():
    if key.startswith("LRT_"):
        text = text.replace("@" + key[4:] + "@", value)
sys.stdout.write(text)' "$1"
}
acl() { for n in $1; do echo "allow $n;"; done; echo "deny all;"; }
PROXY_ENV=""
if [ -n "$UPSTREAM_PROXY" ]; then
    PROXY_ENV=$(printf 'Environment=http_proxy=%s\nEnvironment=https_proxy=%s\nEnvironment=no_proxy=localhost,127.0.0.1,%s' \
        "$UPSTREAM_PROXY" "$UPSTREAM_PROXY" "$HOST")
fi
SHORT_HOSTNAME=$(hostname)
GUNICORN_FLAGS=""
# Les versions récentes ajoutent une socket de contrôle dans le dossier de travail (confiné en lecture seule).
# Désactiver ce canal facultatif lorsqu'il existe ; les anciennes versions gardent leurs options habituelles.
if "$GUNICORN" --help | grep -- '--no-control-socket' >/dev/null; then GUNICORN_FLAGS="--no-control-socket"; fi
# Proxy Windows Update (Squid) : taille du cache en Mo et réseaux autorisés, pour la configuration rendue.
WU_PROXY_CACHE_MB=$(python3 -c 'import sys,re
m=re.match(r"(\d+)([kKmMgGtT]?)$", sys.argv[1] or "0"); n=int(m.group(1)); u=m.group(2).lower()
print(max(1, {"":n//1048576,"k":n//1024,"m":n,"g":n*1024,"t":n*1048576}[u]))' "$WU_CACHE_SIZE")
SQUID_SRC=$(for n in $ALLOW_LIST; do echo "acl loco_clients src $n"; done)
export LRT_WU_PROXY="$WU_ENABLED" LRT_WU_PROXY_PORT="$WU_PROXY_PORT" LRT_WU_PROXY_CACHE_MB="$WU_PROXY_CACHE_MB" \
       LRT_SQUID_USER="$SQUID_USER" LRT_SQUID_SRC="$SQUID_SRC"
export LRT_PORTAL_PORT="$PORTAL_PORT" LRT_HTTPS_PORT="$HTTPS_PORT" LRT_APP_PORT="$APP_PORT" LRT_DATA="$DATA" \
       LRT_HOST="$HOST" LRT_HOSTNAME="$SHORT_HOSTNAME" LRT_ADMIN_URL="$ADMIN_URL" LRT_HTTP_URL="$HTTP_URL" \
       LRT_WU_CACHE_SIZE="$WU_CACHE_SIZE" LRT_RESOLVERS="$RESOLVERS" LRT_MAX_UPLOAD="$MAX_UPLOAD" \
       LRT_BACKUP_DIR="$BACKUP_DIR" LRT_PROXY_ENV="$PROXY_ENV" LRT_ALLOW_SPACE="$ALLOW_LIST" \
       LRT_FLATPAK_CACHE_SIZE="$FLATPAK_CACHE_SIZE" LRT_FLATPAK="$FLATPAK_ENABLED" LRT_GUNICORN_FLAGS="$GUNICORN_FLAGS" \
       LRT_RPM="$RPM_ENABLED" LRT_RPM_CACHE_SIZE="$RPM_CACHE_SIZE" LRT_LOG_GROUP="$LOG_GROUP" LRT_CA_BUNDLE="$CA_BUNDLE" LRT_GUNICORN="$GUNICORN"
# Emplacements Flatpak insérés dans les deux serveurs du portail (déjà rendus).
LRT_FLATPAK_LOCATIONS=""
if [ "$FLATPAK_ENABLED" = 1 ]; then LRT_FLATPAK_LOCATIONS=$(render "$PREFIX/conf/nginx-flatpak-locations.conf"); fi
export LRT_FLATPAK_LOCATIONS
LRT_RPM_LOCATIONS=""
if [ "$RPM_ENABLED" = 1 ]; then LRT_RPM_LOCATIONS=$(render "$PREFIX/conf/nginx-rpm-locations.conf"); fi
export LRT_RPM_LOCATIONS
# Programme Squid pour supervisord (conteneur et Alpine) : présent seulement si le cache WU est actif.
LRT_SQUID_PROGRAM=""
if [ "$WU_ENABLED" = 1 ]; then
    LRT_SQUID_PROGRAM=$(cat <<'EOS'

[program:squid]
command=/usr/sbin/squid -N -f /etc/squid/squid.conf
priority=35
autorestart=true
stopsignal=INT
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
redirect_stderr=true
EOS
)
fi
export LRT_SQUID_PROGRAM

# ---------------------------------------------------------------- nginx
say "Configuration de nginx" "Configuring nginx" "Configuración de nginx"
install -d /etc/nginx/snippets /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/modules-enabled
{ echo "# loco : réseaux autorisés pour les postes (--allow)."; acl "$ALLOW_LIST"; } > /etc/nginx/snippets/loco-clients.conf
{ echo "# loco : réseaux autorisés pour l'interface (--admin-allow)."; acl "$ADMIN_LIST"; } > /etc/nginx/snippets/loco-admin.conf
render "$PREFIX/conf/nginx-proxy-snippet.conf" > /etc/nginx/snippets/loco-proxy.conf
render "$PREFIX/conf/nginx-flathub-proxy-snippet.conf" > /etc/nginx/snippets/loco-flathub-proxy.conf
render "$PREFIX/conf/nginx-rpm-proxy-snippet.conf" > /etc/nginx/snippets/loco-rpm-proxy.conf
{
    if [ "$FLATPAK_ENABLED" = 1 ]; then render "$PREFIX/conf/nginx-flatpak-cache.conf"; fi
    if [ "$RPM_ENABLED" = 1 ]; then render "$PREFIX/conf/nginx-rpm-cache.conf"; fi
    render "$PREFIX/conf/nginx-portal.conf"
    if [ "$HTTP_ENABLED" = 1 ]; then render "$PREFIX/conf/nginx-portal-http.conf"; fi
} > /etc/nginx/sites-available/loco-portal.conf
ln -sf ../sites-available/loco-portal.conf /etc/nginx/sites-enabled/loco-portal.conf
# Certificat importé depuis le portail : son bloc (SNI) est dérivé du bloc ci-dessus, donc refait à chaque installation.
"$PYTHON" -I "$PREFIX/portal/locoadmin.py" tls-sni >> "$LOG" 2>&1 || warn "certificat importé : bloc nginx non régénéré (journal : $LOG)." "imported certificate: nginx block not regenerated (log: $LOG)." "certificado importado: bloque nginx no regenerado (registro: $LOG)."
# Site nginx par défaut (port 80) : désactivé si nginx a été installé par loco,
# ou si le cache Windows Update a besoin du port 80 ; sinon on le laisse (ou le remet) en place.
if [ "$WU_ENABLED" = 1 ] || [ "$CONTAINER" = 1 ] || grep -qx nginx "$PACKAGE_RECORD"; then
    if [ -L /etc/nginx/sites-enabled/default ]; then
        rm /etc/nginx/sites-enabled/default
        touch "$STATE/nginx-default-disabled"
    fi
elif [ -f "$STATE/nginx-default-disabled" ] && [ -e /etc/nginx/sites-available/default ]; then
    ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
    rm -f "$STATE/nginx-default-disabled"
fi
if [ "$WU_ENABLED" = 1 ]; then
    render "$PREFIX/conf/nginx-wucache.conf" > /etc/nginx/sites-available/loco-wucache.conf
    ln -sf ../sites-available/loco-wucache.conf /etc/nginx/sites-enabled/loco-wucache.conf
    render "$PREFIX/conf/nginx-stream.conf" > /etc/nginx/modules-enabled/99-loco-stream.conf
else
    rm -f /etc/nginx/sites-enabled/loco-wucache.conf /etc/nginx/sites-available/loco-wucache.conf \
          /etc/nginx/modules-enabled/99-loco-stream.conf
fi

# Proxy-cache Windows Update (Squid) : atteint par le réglage proxy WinHTTP des postes (sans DNS). Config autonome.
if [ "$WU_ENABLED" = 1 ] && command -v squid >/dev/null 2>&1; then
    install -d -m 755 /etc/squid
    render "$PREFIX/conf/squid-wu.conf" > /etc/squid/squid.conf
    install -d -m 755 -o "$SQUID_USER" -g "$SQUID_USER" /var/log/squid 2>/dev/null || install -d -m 755 /var/log/squid
    install -d -m 755 /run/squid
    if [ ! -d "$DATA/wuproxy/00" ]; then squid -z -f /etc/squid/squid.conf >/dev/null 2>&1 || true; fi
fi
if [ "$PLATFORM" = rocky ]; then rocky_nginx; rocky_access; fi
if [ "$PLATFORM" = alpine ]; then alpine_nginx; fi
if ! nginx -t >/dev/null 2>&1; then
    nginx -t 2>&1 | grep -v -e "syntax is ok" -e "test is successful" || true
    # On retire notre configuration pour ne pas laisser nginx dans un état cassé.
    rm -f /etc/nginx/sites-enabled/loco-portal.conf /etc/nginx/sites-enabled/loco-wucache.conf \
          /etc/nginx/modules-enabled/99-loco-stream.conf
    if [ -f "$STATE/nginx-default-disabled" ] && [ -e /etc/nginx/sites-available/default ]; then
        ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
        rm -f "$STATE/nginx-default-disabled"
    fi
    die "configuration nginx refusée (voir ci-dessus). Un autre site occupe peut-être le port 80 : essayez --no-wu." "nginx configuration refused (see above). Another site may be using port 80: try --no-wu." "configuración de nginx rechazada (ver arriba). Puede que otro sitio ocupe el puerto 80: pruebe --no-wu."
fi

# ---------------------------------------------------------------- apt-cacher-ng
# Alpine : pas d'apt-cacher-ng, le cache APT est servi par nginx (voir alpine_nginx).
if [ "$PLATFORM" != alpine ]; then
say "Configuration d'apt-cacher-ng" "Configuring apt-cacher-ng" "Configuración de apt-cacher-ng"
if [ "$PLATFORM" = debian ]; then
ldd /usr/sbin/apt-cacher-ng | grep libwrap >/dev/null || die "apt-cacher-ng sans libwrap : impossible de garantir le filtrage des postes sur cette distribution." "apt-cacher-ng without libwrap: cannot guarantee client filtering on this distribution." "apt-cacher-ng sin libwrap: no se puede garantizar el filtrado de los equipos en esta distribución."
fi
if [ ! -s "$ETC/acng-admin" ]; then
    (umask 077; echo "admin:$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')" > "$ETC/acng-admin")
fi
{
    echo "# Géré par loco (install.sh) : ne pas modifier."
    echo "AdminAuth: $(cat "$ETC/acng-admin")"
    if [ "$PLATFORM" = rocky ]; then
        echo "UseWrap: 0"
        echo "BindAddress: 127.0.0.1"
        echo "Port: 23142"
    else echo "UseWrap: 1"; fi
    if [ -n "$UPSTREAM_PROXY" ]; then echo "Proxy: $UPSTREAM_PROXY"; fi
} > /etc/apt-cacher-ng/zz-loco.conf
if [ "$PLATFORM" = debian ]; then
# Filtrage par apt-cacher-ng lui-même (tcpwrappers) : fonctionne aussi là où systemd ne peut pas filtrer (LXC).
WRAP_NETS=$(python3 -c '
import ipaddress, sys
out = []
for item in sys.argv[1].split():
    n = ipaddress.ip_network(item)
    out.append(f"{n.network_address}/{n.netmask}" if n.version == 4 else f"[{n.network_address}]/{n.prefixlen}")
print(" ".join(out))' "$ALLOW_LIST")
for f in /etc/hosts.allow /etc/hosts.deny; do
    touch "$f"
    sed -i '/# loco$/d' "$f"
done
echo "apt-cacher-ng: $WRAP_NETS # loco" >> /etc/hosts.allow
echo "apt-cacher-ng: ALL # loco" >> /etc/hosts.deny
fi
chown root:apt-cacher-ng /etc/apt-cacher-ng/zz-loco.conf
chmod 640 /etc/apt-cacher-ng/zz-loco.conf
if [ "$CONTAINER" = 0 ]; then
    install -d /etc/systemd/system/apt-cacher-ng.service.d
    render "$PREFIX/conf/apt-cacher-ng-override.conf" > /etc/systemd/system/apt-cacher-ng.service.d/loco.conf
fi
fi

# ---------------------------------------------------------------- services
say "Services" "Services" "Servicios"
if [ "$PLATFORM" = alpine ]; then
    alpine_services
    alpine_check
elif [ "$CONTAINER" = 1 ]; then
    # Conteneur : supervisord (lancé par linux/docker/entrypoint.sh) remplace systemd.
    install -d -o apt-cacher-ng -g apt-cacher-ng /run/apt-cacher-ng /var/log/apt-cacher-ng
    chown apt-cacher-ng:apt-cacher-ng /var/cache/apt-cacher-ng
    SUP_PROXY_ENV=""
    if [ -n "$UPSTREAM_PROXY" ]; then
        sup_proxy=${UPSTREAM_PROXY//%/%%}   # « % » est spécial pour supervisord (identifiants encodés)
        SUP_PROXY_ENV=",http_proxy=\"$sup_proxy\",https_proxy=\"$sup_proxy\",no_proxy=\"localhost,127.0.0.1,$HOST\""
    fi
    LRT_SUP_PROXY_ENV="$SUP_PROXY_ENV" LRT_SUP_PASSWORD=$(openssl rand -hex 24) \
        render "$SRC/docker/supervisord.conf" > /etc/supervisor/loco.conf
    chmod 600 /etc/supervisor/loco.conf
else
# Agent root des mises à jour du système (portal/locoadmin.py) : le portail y dépose ses demandes, dossier créé
# avant le portail (ProtectSystem=strict ne lui ouvre que des dossiers existants).
install -d -m 755 -o root -g root /var/lib/loco-agent /var/lib/loco-agent/jobs
install -d -m 730 -o root -g loco /var/lib/loco-agent/requests
ln -sf "$PREFIX/bin/loco-update" /usr/local/sbin/loco-update
render "$PREFIX/conf/loco-portal.service" > /etc/systemd/system/loco-portal.service
cp "$PREFIX/conf/loco-stats.service" "$PREFIX/conf/loco-stats.timer" \
   "$PREFIX/conf/loco-nginx.service" "$PREFIX/conf/loco-nginx.path" \
   "$PREFIX/conf/loco-backup.service" "$PREFIX/conf/loco-backup.timer" /etc/systemd/system/
# Même interpréteur que le portail (Rocky 8 : le python3 du système, 3.6, est trop ancien).
LRT_PYTHON="$PYTHON" render "$PREFIX/conf/loco-agent.service" > /etc/systemd/system/loco-agent.service
systemctl daemon-reload
if [ "$PLATFORM" = rocky ] && [ "$OS_MAJOR" = 10 ]; then
    systemctl enable --now loco-acng-maintenance.timer >/dev/null 2>&1
fi
systemctl enable apt-cacher-ng nginx >/dev/null 2>&1
systemctl restart apt-cacher-ng || die "apt-cacher-ng ne démarre pas (journalctl -u apt-cacher-ng)." "apt-cacher-ng does not start (journalctl -u apt-cacher-ng)." "apt-cacher-ng no arranca (journalctl -u apt-cacher-ng)."
if systemctl is-active -q nginx; then
    systemctl reload nginx
else
    systemctl start nginx || die "nginx ne démarre pas (journalctl -u nginx)." "nginx does not start (journalctl -u nginx)." "nginx no arranca (journalctl -u nginx)."
fi
if [ "$WU_ENABLED" = 1 ] && command -v squid >/dev/null 2>&1; then
    systemctl enable squid >/dev/null 2>&1
    systemctl restart squid || warn "squid (proxy Windows Update) ne démarre pas (journalctl -u squid) ; le cache par proxy est indisponible." "squid (Windows Update proxy) does not start (journalctl -u squid); the proxy cache is unavailable." "squid (proxy de Windows Update) no arranca (journalctl -u squid); la caché por proxy no está disponible."
else
    systemctl disable --now squid >/dev/null 2>&1 || true
fi
systemctl enable loco-portal loco-stats.timer loco-nginx.path loco-agent >/dev/null 2>&1
systemctl start loco-nginx.path
systemctl restart loco-portal
# KillMode=process : une mise à jour du système lancée par l'agent survit à cette relance.
systemctl restart loco-agent || warn "l'agent d'administration ne démarre pas (journalctl -u loco-agent) : mises à jour du système indisponibles depuis le portail." "the admin agent does not start (journalctl -u loco-agent): system updates unavailable from the portal." "el agente de administración no arranca (journalctl -u loco-agent): actualizaciones del sistema no disponibles desde el portal."
systemctl start loco-stats.timer loco-stats.service
if [ "$BACKUP_KEEP" -gt 0 ]; then
    systemctl enable --now loco-backup.timer >/dev/null 2>&1
else
    systemctl disable --now loco-backup.timer >/dev/null 2>&1 || true
fi

for _ in $(seq 1 30); do
    curl -fsk --connect-timeout 2 --max-time 5 "https://127.0.0.1:$HTTPS_PORT/healthz" >/dev/null 2>&1 && break
    sleep 0.5
done
curl -fsk --connect-timeout 2 --max-time 5 "https://127.0.0.1:$HTTPS_PORT/healthz" >/dev/null || die "le portail ne répond pas (journalctl -u loco-portal)." "the portal does not respond (journalctl -u loco-portal)." "el portal no responde (journalctl -u loco-portal)."
systemctl is-active --quiet apt-cacher-ng nginx loco-portal || die "un service ne reste pas actif (journalctl -u apt-cacher-ng -u nginx -u loco-portal)." "a service does not stay active (journalctl -u apt-cacher-ng -u nginx -u loco-portal)." "un servicio no permanece activo (journalctl -u apt-cacher-ng -u nginx -u loco-portal)."
cache_status=$(curl -s --connect-timeout 2 --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3142/acng-report.html) || die "le cache APT ne répond pas." "the APT cache does not respond." "la caché APT no responde."
case "$cache_status" in 200|401) ;; *) die "le cache APT ne répond pas correctement (HTTP $cache_status)." "the APT cache does not respond correctly (HTTP $cache_status)." "la caché APT no responde correctamente (HTTP $cache_status)." ;; esac
fi

# ---------------------------------------------------------------- résumé
echo
say "loco est installé" "loco is installed" "loco está instalado"
row()  { printf '    %-20s : %s\n' "$(t "$1" "$2" "$3")" "$4"; }
cont() { printf '    %-20s   %s\n' "" "$1"; }
row "Interface" "Web interface" "Interfaz web" "$ADMIN_URL"
if [ -n "$ISSUED" ]; then
    row "Compte initial" "Initial account" "Cuenta inicial" "admin / $ISSUED   $(t '(à changer à la première connexion ; affiché une seule fois)' '(change at first sign-in; shown only once)' '(cambiar en la primera conexión; mostrado una sola vez)')"
fi
if [ "$CONTAINER" = 1 ]; then
    row "Accès perdu" "Lost access" "Acceso perdido" "docker compose exec loco loco-user passwd admin"
elif [ "$PLATFORM" = alpine ]; then
    row "Accès perdu" "Lost access" "Acceso perdido" "loco-user passwd admin   $(t '(en root, sur la console)' '(as root, on the console)' '(como root, en la consola)')"
else
    row "Accès perdu" "Lost access" "Acceso perdido" "sudo loco-user passwd admin"
fi
if [ ! -f "$TLS/custom" ]; then
    row "Autorité HTTPS" "HTTPS authority" "Autoridad HTTPS" "$ETC/tls/ca.crt $(t '(à déployer sur les postes, par GPO par exemple)' '(deploy on the machines, e.g. via GPO)' '(desplegar en los equipos, por GPO por ejemplo)')"
    cont "$(t 'empreinte' 'fingerprint' 'huella') $(openssl x509 -in "$TLS/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
fi
row "Postes Linux" "Linux machines" "Equipos Linux" "curl -fsSL $ADMIN_URL/linux/setup.sh | sudo sh"
if [ -n "$HTTP_URL" ]; then cont "$(t "sans l'autorité :" 'without the authority:' 'sin la autoridad:') curl -fsSL $HTTP_URL/linux/setup.sh | sudo sh -s -- --trust-ca"; fi
row "Réseaux autorisés" "Allowed networks" "Redes autorizadas" "$(t 'postes' 'clients' 'equipos') $ALLOW | interface $ADMIN_ALLOW"
if [ -n "$UPSTREAM_PROXY" ]; then row "Proxy sortant" "Outbound proxy" "Proxy saliente" "$UPSTREAM_PROXY $(t '(le cache Windows Update sort en direct)' '(the Windows Update cache goes out directly)' '(la caché de Windows Update sale directa)')"; fi
if [ "$WU_ENABLED" = 1 ]; then
    row "Cache Windows Upd." "Windows Update cache" "Caché Windows Upd." "$WU_CACHE_SIZE max, $(t 'DNS à configurer depuis le portail' 'DNS to configure from the portal' 'DNS por configurar desde el portal')"
fi
if [ "$RPM_ENABLED" = 1 ]; then
    row "Famille Red Hat" "Red Hat family" "Familia Red Hat" "curl -fsSL $ADMIN_URL/rpm/setup.sh | sudo sh   $(t '(cache RPM' '(RPM cache' '(caché RPM') $RPM_CACHE_SIZE max)"
fi
if [ "$FLATPAK_ENABLED" = 1 ]; then
    row "Flatpak" "Flatpak" "Flatpak" "curl -fsSL $ADMIN_URL/flatpak/setup.sh | sudo sh   $(t '(cache Flathub' '(Flathub cache' '(caché Flathub') $FLATPAK_CACHE_SIZE max)"
fi
if [ "$BACKUP_KEEP" -gt 0 ]; then
    row "Sauvegardes" "Backups" "Copias de seg." "$(t 'quotidiennes dans' 'daily in' 'diarias en') $BACKUP_DIR ($BACKUP_KEEP $(t 'conservées' 'kept' 'conservadas'))"
fi
if [ "$CONTAINER" = 1 ]; then
    row "Arrêter" "Stop" "Detener" "docker compose down   $(t '(les données restent dans les volumes)' '(data stays in the volumes)' '(los datos permanecen en los volúmenes)')"
else
    row "Désinstaller" "Uninstall" "Desinstalar" "$([ "$PLATFORM" = alpine ] || printf 'sudo ')$PREFIX/remove.sh"
fi
