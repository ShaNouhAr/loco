#!/bin/bash
# Poste de la famille Red Hat (VM jetable uniquement), lancé en root : branchement au serveur de test
# (10.0.2.2), installations par le dépôt local, le cache des dépôts officiels et EPEL, puis retour à l'état d'origine.
set -euo pipefail
SERVER=10.0.2.2
fail() { echo "ÉCHEC : $*" >&2; exit 1; }
step() { printf '\n--- %s\n' "$*"; }
enabled() { dnf repolist --enabled 2>/dev/null | awk 'NR > 1 { print $1 }'; }
from_repo() { dnf repoquery --installed --qf '%{from_repo}' "$1" 2>/dev/null | tail -1; }
# shellcheck disable=SC1091
. /etc/os-release
MAJOR=${VERSION_ID%%.*}
before=$(enabled | sort)
echo "Dépôts actifs avant : $(echo "$before" | tr '\n' ' ')"

step "Branchement (autorité importée, EPEL)"
curl -fsSL "http://$SERVER:8088/rpm/setup.sh" | sh -s -- --trust-ca --epel
now=$(enabled)
for repo in loco loco-baseos loco-appstream loco-epel; do
    echo "$now" | grep -qx "$repo" || fail "dépôt $repo absent"
done
if echo "$now" | grep -qx baseos; then fail "baseos d'origine encore actif"; fi
curl -fsS "https://$SERVER:8443/healthz" >/dev/null || fail "autorité non reconnue"

step "Dépôt local el$MAJOR"
dnf -y -q install lr-rpm-e2e
[ "$(cat /usr/share/lr-rpm-e2e/content)" = "loco-rpm-el$MAJOR" ] || fail "mauvais paquet local"

step "Dépôts officiels via le cache"
if rpm -q tree >/dev/null 2>&1; then dnf -y -q remove tree; fi
dnf -y -q install tree
case "$(from_repo tree)" in loco-baseos|loco-appstream) ;; *) fail "tree installé depuis $(from_repo tree)" ;; esac
url=$(dnf repoquery --location tree 2>/dev/null | grep -m1 "^https://$SERVER:8443/rpmcache/")
[ -n "$url" ] || fail "tree ne passe pas par le cache"
curl -fsS -o /dev/null "$url"
curl -fsS -D - -o /dev/null "$url" | grep -i "x-loco-cache: HIT" > /dev/null || fail "paquet non servi par le cache"

step "EPEL via le cache"
dnf -y -q install htop
[ "$(from_repo htop)" = loco-epel ] || fail "htop installé depuis $(from_repo htop)"

step "Retour à l'état d'origine"
curl -fsSL "https://$SERVER:8443/rpm/setup.sh" | sh -s -- --remove
dnf -y -q remove lr-rpm-e2e
# EPEL reste installé (epel-release) : ses dépôts ne comptent pas dans la comparaison.
after=$(enabled | grep -vE '^epel($|-)' | sort || true)
[ "$after" = "$before" ] || fail "dépôts différents après retrait : $(echo "$after" | tr '\n' ' ')"
if [ -e /etc/yum.repos.d/loco.repo ] || [ -e /etc/yum.repos.d/loco-cache.repo ]; then fail "fichiers restants"; fi
[ ! -e /etc/pki/ca-trust/source/anchors/loco.crt ] || fail "autorité restante"
dnf -y -q makecache >/dev/null || fail "dépôts d'origine inutilisables"
echo
echo "Poste $ID $VERSION_ID validé."
