#!/bin/sh
# Raccourci vers l'installateur Linux (linux/install.sh) : garde « sudo ./install.sh » et les mises à jour
# des installations existantes. Serveur Windows : windows\install.ps1.
dir=$(cd "$(dirname "$0")" && pwd)
# Langue du serveur : français si LANG/LC_* commence par « fr », espagnol par « es », anglais sinon.
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_LANG=fr ;; es*) LOCO_LANG=es ;; *) LOCO_LANG=en ;; esac
t() { case "$LOCO_LANG" in fr) printf '%s\n' "$1" ;; es) printf '%s\n' "${3:-$2}" ;; *) printf '%s\n' "$2" ;; esac; }
# Alpine minimal : bash n'est pas installé par défaut.
if ! command -v bash >/dev/null 2>&1 && [ -f /etc/alpine-release ]; then
    apk add --no-progress bash >/dev/null || { t "bash requis (apk add bash)." "bash required (apk add bash)." "bash requerido (apk add bash)." >&2; exit 1; }
fi
exec bash "$dir/linux/install.sh" "$@"
