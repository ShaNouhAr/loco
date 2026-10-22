#!/bin/sh
# loco : poste Debian / Ubuntu, dépôt détecté depuis /etc/os-release.
#   curl -fsSL {{ base }}/linux/setup.sh | sudo sh
#   ... | sudo sh -s -- --trust-ca         importe l'autorité et passe en HTTPS
#   ... | sudo sh -s -- --no-proxy         dépôt seulement
#   ... | sudo sh -s -- --cache-only       cache seulement, sans dépôt local
#   ... | sudo sh -s -- --distribution debian --suite trixie   choix explicite (dérivées)
#   ... | sudo sh -s -- --legacy           ancien dépôt, sans isolation par version
#   ... | sudo sh -s -- --remove           annule la configuration
#   --from-package : lancé par le paquet loco-client (agent et dépôts) : ni apt-get clean ni apt-get update,
#                    APT étant occupé par l'installation ; l'agent rafraîchit les listes juste après.
set -eu
# Langue du poste : français si LANG/LC_* commence par « fr », sinon anglais (sudo conserve LANG).
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_EN=0 ;; *) LOCO_EN=1 ;; esac
t() { if [ "$LOCO_EN" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
[ "$(id -u)" -eq 0 ] || { echo "{{ sh_t('Lancez ce script en root (sudo).') }}" >&2; exit 1; }
BASE="{{ base }}"
HTTPS_BASE="{{ https_url }}"
KEY=/etc/apt/keyrings/loco.gpg
LIST=/etc/apt/sources.list.d/loco.list
SOURCE=/etc/apt/sources.list.d/loco.sources
PROXY=/etc/apt/apt.conf.d/01loco-proxy
CA=/usr/local/share/ca-certificates/loco.crt
USE_PROXY=1 TRUST_CA=0 REMOVE=0 CACHE_ONLY=0 LEGACY=0 FROM_PACKAGE=0 DIST="" SUITE=""
need() { if [ "$#" -lt 2 ] || [ -z "$2" ]; then echo "{{ sh_t('Une valeur est attendue après %(opt)s.', opt='$1') }}" >&2; exit 1; fi; }
while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-proxy) USE_PROXY=0; shift ;;
        --trust-ca) TRUST_CA=1; shift ;;
        --cache-only) CACHE_ONLY=1; shift ;;
        --legacy) LEGACY=1; shift ;;
        --distribution) need "$@"; DIST=$2; shift 2 ;;
        --suite) need "$@"; SUITE=$2; shift 2 ;;
        --remove) REMOVE=1; shift ;;
        --from-package) FROM_PACKAGE=1; shift ;;
        *) echo "{{ sh_t('Option inconnue : %(opt)s', opt='$1') }}" >&2; exit 1 ;;
    esac
done
if [ "$REMOVE" = 1 ]; then
    rm -f "$KEY" "$LIST" "$SOURCE" "$PROXY"
    rm -f /etc/apt/keyrings/local-repo.gpg /etc/apt/sources.list.d/local-repo.list /etc/apt/sources.list.d/local-repo.sources
    rm -f /etc/apt/apt.conf.d/01local-repo-proxy
    if [ -f /usr/local/share/ca-certificates/local-repo.crt ]; then
        rm -f /usr/local/share/ca-certificates/local-repo.crt
        update-ca-certificates --fresh >/dev/null
    fi
    if [ -f "$CA" ]; then rm -f "$CA"; update-ca-certificates --fresh >/dev/null 2>&1 || true; fi
    echo "{{ sh_t('Configuration loco retirée.') }}"
    exit 0
fi
command -v apt-get >/dev/null 2>&1 || { echo "{{ sh_t('Ce poste doit utiliser APT.') }}" >&2; exit 1; }
if [ "$LEGACY" = 1 ] && { [ "$CACHE_ONLY" = 1 ] || [ -n "$DIST$SUITE" ]; }; then
    echo "{{ sh_t('--legacy ne se combine pas avec --cache-only, --distribution ou --suite.') }}" >&2; exit 1
fi
if [ "$CACHE_ONLY" = 0 ] && [ "$LEGACY" = 0 ]; then
    if [ -z "$DIST$SUITE" ]; then
        [ -r /etc/os-release ] || { echo "{{ sh_t('Distribution inconnue : précisez --distribution et --suite.') }}" >&2; exit 1; }
        # shellcheck disable=SC1091
        . /etc/os-release
        DIST=${ID:-}
        SUITE=${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}
    fi
    case "$DIST" in debian|ubuntu) ;; *) echo "{{ sh_t('Distribution non reconnue : précisez --distribution debian|ubuntu et --suite NOM.') }}" >&2; exit 1 ;; esac
    case "$SUITE" in ''|*[!a-z0-9-]*|[!a-z]* ) echo "{{ sh_t('Nom de version invalide ou absent.') }}" >&2; exit 1 ;; esac
    [ "$(printf '%s' "$SUITE" | wc -c)" -le 40 ] || { echo "{{ sh_t('Nom de version trop long.') }}" >&2; exit 1; }
fi
if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; elif command -v wget >/dev/null 2>&1; then GET="wget -qO-"; else
    echo "{{ sh_t('Installez curl ou wget avant de configurer ce poste.') }}" >&2; exit 1
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# Vérifier le dépôt et télécharger les fichiers avant de remplacer une configuration existante.
if [ "$CACHE_ONLY" = 0 ]; then
    if [ "$LEGACY" = 1 ]; then
        echo "{{ sh_t('Attention : dépôt historique partagé, sans isolation des versions.') }}"
        printf 'Types: deb\nURIs: %s/apt/\nSuites: ./\nSigned-By: %s\n' "$BASE" "$KEY" > "$TMP/source"
    else
        if ! $GET "$BASE/linux/repositories/$DIST/$SUITE.sources" > "$TMP/source"; then
            echo "{{ sh_t('Dépôt %(dist)s/%(suite)s indisponible : créez-le dans Linux > Paquets, ou utilisez --cache-only.', dist='$DIST', suite='$SUITE') }}" >&2
            exit 1
        fi
        EXPECTED=$(printf 'Types: deb\nURIs: %s/apt/repositories/%s/%s/\nSuites: ./\nSigned-By: %s\n' "$BASE" "$DIST" "$SUITE" "$KEY")
        [ "$(cat "$TMP/source")" = "$EXPECTED" ] || { echo "{{ sh_t('Configuration de dépôt inattendue.') }}" >&2; exit 1; }
    fi
    $GET "$BASE/apt/loco.gpg" > "$TMP/key"
    [ -s "$TMP/key" ] || { echo "{{ sh_t('Clé du dépôt vide.') }}" >&2; exit 1; }
fi
if [ "$TRUST_CA" = 1 ]; then
    $GET "$BASE/ca.crt" > "$TMP/ca"
    command -v openssl >/dev/null 2>&1 && openssl x509 -in "$TMP/ca" -noout >/dev/null
    command -v update-ca-certificates >/dev/null 2>&1 || { apt-get update -q >/dev/null && apt-get install -y -q ca-certificates >/dev/null; }
    mkdir -p "$(dirname "$CA")"
    install -m 644 "$TMP/ca" "$CA"
    update-ca-certificates >/dev/null
    if command -v openssl >/dev/null 2>&1; then
        echo "{{ sh_t('Autorité importée, empreinte SHA-256 : %(fp)s', fp='$(openssl x509 -in \"$CA\" -noout -fingerprint -sha256 | cut -d= -f2)') }}"
        echo "{{ sh_t('Comparez-la à celle de la page Système du portail.') }}"
    fi
    BASE=$HTTPS_BASE
fi
if [ "$CACHE_ONLY" = 0 ]; then
    PATH_REPO=apt
    if [ "$LEGACY" = 0 ]; then PATH_REPO="apt/repositories/$DIST/$SUITE"; fi
    mkdir -p /etc/apt/keyrings
    install -m 644 "$TMP/key" "$KEY"
    printf 'Types: deb\nURIs: %s/%s/\nSuites: ./\nSigned-By: %s\n' "$BASE" "$PATH_REPO" "$KEY" > "$TMP/final-source"
    # Le cache des .deb est partagé par nom/version/architecture, même entre dépôts.
    # Éviter de réutiliser un ancien binaire après un changement de distribution ou de dépôt.
    if ! grep -Fxq "URIs: $BASE/$PATH_REPO/" "$SOURCE" 2>/dev/null && [ "$FROM_PACKAGE" = 0 ]; then apt-get clean; fi
    install -m 644 "$TMP/final-source" "$SOURCE"
    rm -f "$LIST"
else
    rm -f "$SOURCE" "$LIST" "$KEY"
fi
# La nouvelle configuration est en place : retirer les anciennes sources pour éviter les doublons.
rm -f /etc/apt/sources.list.d/local-repo.list /etc/apt/sources.list.d/local-repo.sources
rm -f /etc/apt/keyrings/local-repo.gpg /etc/apt/apt.conf.d/01local-repo-proxy
if [ -f /usr/local/share/ca-certificates/local-repo.crt ]; then
    if [ "$TRUST_CA" = 0 ] && [ ! -f "$CA" ]; then mv /usr/local/share/ca-certificates/local-repo.crt "$CA";
    else rm -f /usr/local/share/ca-certificates/local-repo.crt; fi
    update-ca-certificates --fresh >/dev/null
fi
if [ "$USE_PROXY" = 1 ]; then
    cat > "$PROXY" <<CONF
Acquire::http::Proxy "http://{{ host }}:{{ acng_port }}";
Acquire::https::Proxy "DIRECT";
Acquire::http::Proxy::{{ host }} "DIRECT";
CONF
else
    rm -f "$PROXY"
fi
if [ "$FROM_PACKAGE" = 0 ]; then apt-get update -o APT::Update::Error-Mode=any; fi
if [ "$CACHE_ONLY" = 1 ]; then echo "{{ sh_t('Cache APT configuré (%(base)s).', base='$BASE') }}"; else echo "{{ sh_t('Poste configuré : dépôt %(repo)s (%(base)s).', repo='$PATH_REPO', base='$BASE') }}"; fi
