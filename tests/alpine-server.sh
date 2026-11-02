#!/bin/bash
# VM JETABLE uniquement (Alpine Linux, root) : installation native, portail, dépôts signés, cache APT nginx,
# relance et désinstallation. Paquets d'essai : dossier $1 (tests/windows-fixtures.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
FIXTURES=${1:?dossier des paquets de test requis}
PW="Alpine-E2e-$(date +%s)x"
step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }
fail() { echo "ÉCHEC : $*" >&2; exit 1; }
trap 'echo "Échec du test ligne $LINENO : $BASH_COMMAND" >&2' ERR
ready() { curl -fsk --retry 20 --retry-connrefused --retry-delay 1 --max-time 5 https://127.0.0.1:8443/healthz >/dev/null; }

step "Installation (raccourci POSIX, bash installé si besoin)"
sh ./install.sh --password "$PW" > /tmp/alpine-install.log 2>&1 || { tail -40 /tmp/alpine-install.log; fail "installation"; }
tail -16 /tmp/alpine-install.log
ready
python3 tests/https_redirect.py https://127.0.0.1:8443
rc-service loco status | grep -q started || fail "service OpenRC loco"
if rc-update show default | grep -qw nginx; then fail "le service nginx d'Alpine ne doit pas être actif"; fi
metrics=$(curl -fsk https://127.0.0.1:8443/metrics)
for unit in loco-portal nginx loco-cron; do    # Alpine : le cache APT est servi par nginx
    grep -qx "loco_service_up{unit=\"$unit\"} 1" <<< "$metrics" || fail "service inactif : $unit"
done
grep -q 'loco_info{version=' <<< "$metrics"
pgrep -u loco -f gunicorn >/dev/null || fail "portail pas lancé sous le compte loco"

step "Dépôts APT (gzip, xz, zstd) et RPM, signatures"
loco-apt add debian trixie 13 >/dev/null
for c in gzip xz zstd; do
    loco-apt add debian "native-$c" test >/dev/null
    python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$FIXTURES/$c.deb" linux "debian/native-$c"
    root=/srv/loco/apt/repositories/debian/native-$c
    grep -q '^Package: lr-native-e2e$' "$root/Packages"
    gpgv --keyring /srv/loco/apt/loco.gpg "$root/InRelease" >/dev/null 2>&1 || fail "signature InRelease ($c)"
done
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$FIXTURES/test.rpm" rpm el9
gpgv --keyring /srv/loco/apt/loco.gpg /srv/loco/rpm/el9/repodata/repomd.xml.asc /srv/loco/rpm/el9/repodata/repomd.xml >/dev/null 2>&1 \
    || fail "signature repomd.xml"

step "Cache APT nginx (MISS puis HIT), filtrage"
url=http://deb.debian.org/debian/dists/stable/Release
first=$(curl -fsS -x http://127.0.0.1:3142 -D - -o /dev/null "$url" | tr -d '\r' | awk -F': ' 'tolower($1) == "x-loco-cache" {print $2}')
second=$(curl -fsS -x http://127.0.0.1:3142 -D - -o /dev/null "$url" | tr -d '\r' | awk -F': ' 'tolower($1) == "x-loco-cache" {print $2}')
echo "Release : $first puis $second"
/opt/loco/bin/loco-stats
python3 -c 'import json; assert json.load(open("/var/lib/loco/stats.json"))["acng"] > 0, "taille du cache APT"'
[ "$second" = HIT ] || fail "cache APT (attendu HIT, obtenu $second)"
[ "$(curl -s -o /dev/null -w '%{http_code}' -x http://127.0.0.1:3142 http://example.com/)" = 403 ] || fail "proxy APT ouvert"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: example.com' http://127.0.0.1/)" = 403 ] || fail "relais Windows Update ouvert"
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/)" = 301 ] || fail "HTTP doit renvoyer vers HTTPS"

step "Tests unitaires (Python et bibliothèques d'Alpine)"
# Les comparaisons d'index avec apt-ftparchive et createrepo_c tournent sur Debian et Rocky (outils absents ici).
(cd tests && python3 -W error::ResourceWarning -m unittest test_portal test_appliance test_iso test_openpgp test_windows_runtime) \
    > /tmp/alpine-unit.log 2>&1 \
    || { tail -80 /tmp/alpine-unit.log; fail "tests unitaires"; }
tail -3 /tmp/alpine-unit.log

step "SSH : mot de passe sans clé, clé seule et désactivation"
LOCO_SSH_TEST_VM=1 bash tests/ssh-modes.sh
step "Agent d'administration : demande du portail, loco-config, loco-update"
rc-service loco-agent status | grep -q started || fail "service OpenRC loco-agent"
[ "$(stat -c %U:%G:%a /var/lib/loco-agent/requests)" = root:loco:730 ] || fail "droits du dossier des demandes"
# Demande déposée comme le portail (compte loco), exécutée par l'agent root.
runuser -u loco -- python3 -I -c 'import sys; sys.path.insert(0, "/opt/loco/portal"); import locoadmin
locoadmin.request("configure", {"RPM_CACHE_SIZE": "25g"}, "e2e")'
for _ in $(seq 1 120); do grep -qx 'RPM_CACHE_SIZE=25g' /etc/loco/loco.conf && break; sleep 2; done
grep -qx 'RPM_CACHE_SIZE=25g' /etc/loco/loco.conf || { tail -30 /var/lib/loco-agent/jobs/*.log; fail "réglage du portail non appliqué"; }
for _ in $(seq 1 60); do grep -l '"action": "configure"' /var/lib/loco-agent/jobs/*.json | xargs grep -q '"state": "success"' && break; sleep 2; done
grep -l '"action": "configure"' /var/lib/loco-agent/jobs/*.json | xargs grep -q '"state": "success"' || fail "opération de l'agent"
# Le worker rafraîchit encore l'état des versions après le succès du réglage.
# Attendre la libération de son verrou avant de lancer une opération CLI concurrente.
flock -w 180 /var/lib/loco-agent/lock true || fail "agent encore occupé après le réglage"
ready
# Le compte loco ne doit rien pouvoir demander d'autre qu'une action connue aux valeurs validées.
# shellcheck disable=SC2016 # Code Python littéral, pas d'interpolation shell.
if runuser -u loco -- python3 -I -c 'import sys; sys.path.insert(0, "/opt/loco/portal"); import locoadmin
locoadmin.request("configure", {"UPSTREAM_PROXY": "http://$(touch /root/pwned):1"}, "e2e")' 2>/dev/null; then fail "valeur dangereuse acceptée"; fi
loco-config set WU_CACHE_SIZE=30g | tail -3
grep -qx 'WU_CACHE_SIZE=30g' /etc/loco/loco.conf || fail "loco-config"
if loco-config set 'ALLOW=10.0.0.0/8;id' >/dev/null 2>&1; then fail "loco-config accepte un réseau invalide"; fi
ready
loco-update check || true    # GitHub ou les dépôts Alpine peuvent être lents : l'état est vérifié ensuite
loco-update status
python3 -c 'import json; s = json.load(open("/var/lib/loco-agent/status.json")); assert s.get("current"), s'
[ ! -e /root/pwned ] || fail "commande injectée exécutée"

step "Sauvegarde et restauration"
archive=$(loco-backup create | awk '{print $1}')
loco-backup restore "$archive"
sh ./install.sh > /tmp/alpine-install.log 2>&1 || { tail -40 /tmp/alpine-install.log; fail "reconfiguration après restauration"; }
ready

step "Mise à jour réelle et retour arrière après échec"
python3 tests/appliance-update.py
ready

step "Changement de port : l'ancien reste servi, puis se ferme"
sh ./install.sh --https-port 9443 --https-old-ports 8443 > /tmp/alpine-install.log 2>&1 || { tail -40 /tmp/alpine-install.log; fail "ouverture du nouveau port"; }
curl -fsk --max-time 5 https://127.0.0.1:9443/healthz >/dev/null || fail "nouveau port muet"
ready
grep -qx 'HTTPS_OLD_PORTS="8443"' /etc/loco/loco.conf || fail "ancien port non conservé"
sh ./install.sh --https-port 8443 --https-old-ports "" > /tmp/alpine-install.log 2>&1 || { tail -40 /tmp/alpine-install.log; fail "fermeture de l'ancien port"; }
ready
if curl -fsk --max-time 5 https://127.0.0.1:9443/healthz >/dev/null 2>&1; then fail "le port 9443 devrait être fermé"; fi

step "Relance : clés et dépôts conservés"
ca=$(sha256sum /etc/loco/tls/ca.crt | cut -d' ' -f1)
sh ./install.sh > /tmp/alpine-install.log 2>&1 || { tail -40 /tmp/alpine-install.log; fail "relance"; }
ready
[ "$(sha256sum /etc/loco/tls/ca.crt | cut -d' ' -f1)" = "$ca" ] || fail "autorité HTTPS régénérée"
grep -q '^Package: lr-native-e2e$' /srv/loco/apt/repositories/debian/native-xz/Packages
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$FIXTURES/xz.deb" linux debian/native-xz

step "Désinstallation complète"
/opt/loco/remove.sh -y --purge
if rc-update show default | grep -qE '\bloco(-agent)?\b'; then fail "service loco restant"; fi
# netstat de BusyBox : iproute2 (ss) vient d'être retiré avec les autres paquets installés par loco.
if netstat -tln | grep -qE ':(8443|8088|3142) '; then fail "ports encore ouverts"; fi
for d in /etc/loco /srv/loco /opt/loco /etc/init.d/loco /etc/init.d/loco-agent /var/lib/loco-agent /usr/local/sbin/loco-update; do
    if [ -e "$d" ]; then fail "$d restant"; fi
done
if id loco >/dev/null 2>&1; then fail "compte loco restant"; fi
echo 'Serveur Alpine : installation, portail, dépôts signés, cache APT, relance et désinstallation OK.'
