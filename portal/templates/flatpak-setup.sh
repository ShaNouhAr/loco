#!/bin/sh
# loco : Flatpak via le serveur local (Flathub en cache + dépôt local de l'entreprise).
#   curl -fsSL {{ base }}/flatpak/setup.sh | sudo sh
#   ... | sudo sh -s -- --remove     Flathub en accès direct, dépôt local retiré
# Les signatures GPG (clé de Flathub, clé du dépôt local) restent vérifiées par Flatpak.
set -e
# Langue du poste : français si LANG/LC_* commence par « fr », sinon anglais (sudo conserve LANG).
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_EN=0 ;; *) LOCO_EN=1 ;; esac
t() { if [ "$LOCO_EN" = 1 ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
[ "$(id -u)" -eq 0 ] || { {{ sh_t('Lancez ce script en root (sudo).') }} >&2; exit 1; }
BASE="{{ base }}"
FLATHUB="{{ flathub_url }}"
LOCAL='{{ "1" if local else "0" }}'

if [ "${1:-}" = "--remove" ]; then
    if command -v flatpak >/dev/null 2>&1; then
        if flatpak remotes --system --columns=name | grep -qx flathub; then
            flatpak remote-modify --system flathub --url="$FLATHUB"
        fi
        flatpak remote-delete --system --force loco 2>/dev/null || true
        flatpak remote-delete --system --force local-repo 2>/dev/null || true
    fi
    {{ sh_t('Configuration Flatpak loco retirée (Flathub en accès direct).') }}
    exit 0
fi
[ -z "${1:-}" ] || { {{ sh_t('Option inconnue : %(opt)s', opt='$1') }} >&2; exit 1; }

if ! command -v flatpak >/dev/null 2>&1; then
    apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q flatpak
fi
if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; else GET="wget -qO-"; fi
TMP=$(mktemp --suffix=.flatpakrepo)
trap 'rm -f "$TMP"' EXIT

# Flathub : dépôt officiel (clé de Flathub), téléchargements via le cache.
$GET "$BASE/flathub/repo/flathub.flatpakrepo" > "$TMP"
flatpak remote-add --system --if-not-exists flathub "$TMP"
flatpak remote-modify --system flathub --url="$BASE/flathub/repo/"

if [ "$LOCAL" = 1 ]; then
# Dépôt local de l'entreprise (clé loco).
$GET "$BASE/flatpak/loco.flatpakrepo" > "$TMP"
flatpak remote-add --system --if-not-exists loco "$TMP"
flatpak remote-modify --system loco --url="$BASE/flatpak/repo/"
# Réinstaller depuis le même dépôt signé change l'origine sans manipuler l'état interne de Flatpak.
if flatpak remotes --system --columns=name | grep -qx local-repo; then
    flatpak list --system --columns=ref,origin | awk '$2 == "local-repo" {print $1}' |
    while IFS= read -r ref; do
        flatpak install --system --noninteractive --assumeyes --no-deps --reinstall loco "$ref"
    done
    flatpak remote-delete --system --force local-repo
fi
fi

flatpak remotes --system --show-details
{{ sh_t('Flatpak configuré pour loco (%(base)s).', base='$BASE') }}
