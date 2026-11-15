#!/bin/bash
# VM JETABLE uniquement : déploiement Docker (docker compose), poste client, persistance, sauvegarde.
set -euo pipefail
cd "$(dirname "$0")/.."
PW="E2e-Docker-$(date +%s)"
work=$(mktemp -d)
trap 'sudo rm -rf -- "$work"' EXIT
trap 'echo "Échec du test ligne $LINENO : $BASH_COMMAND" >&2' ERR
step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }
# Projet Docker : linux/docker (docker-compose.yml, sa variante construite depuis les sources, et .env).
dc() { sudo docker compose --project-directory linux/docker -f linux/docker/docker-compose.yml -f linux/docker/docker-compose.build.yml "$@"; }
healthy() {
    local i
    for (( i=0; i<90; i++ )); do
        [ "$(sudo docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q loco)")" = healthy ] && return 0
        sleep 4
    done
    dc logs --tail 80 loco >&2
    return 1
}
fail() { echo "ÉCHEC : $*" >&2; exit 1; }

mkdir -p "$work/deb/DEBIAN" "$work/deb/usr/bin"
printf '#!/bin/sh\necho loco-docker-ok\n' > "$work/deb/usr/bin/lr-e2e"
chmod 755 "$work/deb/usr/bin/lr-e2e"
printf 'Package: lr-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: ci <ci@example.invalid>\nDescription: test\n' > "$work/deb/DEBIAN/control"
dpkg-deb --root-owner-group -b "$work/deb" "$work/lr-e2e.deb" >/dev/null

step "Construction et premier démarrage"
printf 'LOCO_BACKUP_KEEP=2\nTZ=Europe/Paris\n' > linux/docker/.env
sudo linux/docker/up.sh
healthy
dc logs loco > "$work/first.log"
# Le journal est dans la langue du conteneur (anglais par défaut).
grep -Eq "(Compte initial|Initial account) +: admin / " "$work/first.log" || fail "mot de passe initial absent du journal"
# shellcheck disable=SC2016  # expansions faites dans le conteneur
HOST=$(dc exec -T loco sh -c '. /etc/loco/loco.conf && echo "$HOST"')
[ -n "$HOST" ] || fail "adresse non détectée"
# shellcheck disable=SC2016
dc exec -T -e LOCO_PASSWORD="$PW" loco sh -c 'cd /opt/loco/portal && runuser -u loco -- env LOCO_DATA=/srv/loco LOCO_STATE=/var/lib/loco LOCO_ETC=/etc/loco LOCO_PASSWORD="$LOCO_PASSWORD" python3 -m loco.cli user set-password admin'

step "Processus, comptes et services"
dc exec -T loco python3 - https://127.0.0.1:8443 < tests/https_redirect.py
dc exec -T loco ps -eo user:16,args > "$work/ps"
grep -E '^loco .*gunicorn' "$work/ps" >/dev/null || fail "portail pas lancé sous loco"
grep -E '^apt-cacher-ng .*apt-cacher-ng' "$work/ps" >/dev/null || fail "apt-cacher-ng pas lancé sous son compte"
curl -fsk https://127.0.0.1:8443/metrics > "$work/metrics"
for unit in loco-portal nginx apt-cacher-ng loco-cron; do
    grep -qx "loco_service_up{unit=\"$unit\"} 1" "$work/metrics" || fail "service inactif : $unit"
done
for (( i=0; i<20; i++ )); do dc exec -T loco test -s /var/lib/loco/stats.json && break; sleep 2; done
dc exec -T loco test -s /var/lib/loco/stats.json || fail "tâches périodiques inactives"

step "Dépôt APT, poste client via le cache"
# shellcheck disable=SC1091
. /etc/os-release
dc exec -T loco loco-apt add "$ID" "$VERSION_CODENAME" "$VERSION_ID"
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/lr-e2e.deb" linux "$ID/$VERSION_CODENAME"
curl -fsSL "http://$HOST:8088/linux/setup.sh" | sudo sh -s -- --trust-ca
sudo apt-get install -y -q lr-e2e >/dev/null
[ "$(lr-e2e)" = loco-docker-ok ] || fail "paquet du dépôt local"
grep -q "https://$HOST:8443/apt/repositories/$ID/$VERSION_CODENAME/" /etc/apt/sources.list.d/loco.sources

step "Famille Red Hat, Flatpak, Windows Update, sécurité"
curl -fsS "https://$HOST:8443/rpm/el9/repodata/repomd.xml.asc" -o /dev/null
curl -fsS "https://$HOST:8443/rpm/loco.asc" | grep "BEGIN PGP PUBLIC KEY BLOCK" > /dev/null
curl -fsS "https://$HOST:8443/rpmcache/rocky/9/BaseOS/x86_64/os/repodata/repomd.xml" -o /dev/null
curl -fsS "https://$HOST:8443/flatpak/repo/config" -o /dev/null
curl -fsS "https://$HOST:8443/flathub/repo/summary.idx" -o /dev/null
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: example.com' http://127.0.0.1/)" = 403 ] || fail "relais Windows Update ouvert"
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3142/acng-report.html?doCount=1)" = 401 ] || fail "rapport apt-cacher-ng accessible"
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST:8088/")" = 301 ] || fail "HTTP doit renvoyer vers HTTPS"
curl -sk -D - -o /dev/null https://127.0.0.1:8443/login | grep -i "content-security-policy: default-src 'none'" > /dev/null

step "Sauvegarde"
dc exec -T loco loco-backup create | tee "$work/backup"
dc exec -T loco loco-backup list | grep -q "loco-.*\.tar\.gz"

step "Recréation du conteneur : données, comptes et autorité conservés"
CA_BEFORE=$(curl -fsS "http://$HOST:8088/ca.crt" | sha256sum | cut -d' ' -f1)
dc down
dc up -d
healthy
[ "$(curl -fsS "http://$HOST:8088/ca.crt" | sha256sum | cut -d' ' -f1)" = "$CA_BEFORE" ] || fail "autorité HTTPS régénérée"
if dc logs loco | grep -Eq "Compte initial|Initial account"; then fail "nouveau compte initial après recréation"; fi
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/lr-e2e.deb" linux "$ID/$VERSION_CODENAME"
sudo apt-get update -q >/dev/null
sudo apt-get install -y -q --reinstall lr-e2e >/dev/null

step "Réglages par variables : sans Windows Update ni HTTP"
printf 'LOCO_BACKUP_KEEP=2\nLOCO_WU=0\nLOCO_HTTP=0\n' > linux/docker/.env
dc up -d
healthy
if ss -Hltn | grep -qE ':(80|443|8088) '; then fail "ports 80, 443 ou 8088 encore ouverts"; fi
grep -q "WU_ENABLED=0" <(dc exec -T loco cat /etc/loco/loco.conf)
printf 'LOCO_BACKUP_KEEP=2\n' > linux/docker/.env
dc up -d
healthy
ss -Hltn | grep -qE ':80 ' || fail "Windows Update non réactivé quand la variable est retirée"

step "Adresse du portail par variable : certificat, agents et enrôlement inchangés"
printf 'LOCO_BACKUP_KEEP=2\nLOCO_PORTAL_URL=https://loco.e2e.test:8443\n' > linux/docker/.env
dc up -d
healthy
grep -q 'PORTAL_URL="https://loco.e2e.test:8443"' <(dc exec -T loco cat /etc/loco/loco.conf) || fail "adresse du portail absente de loco.conf"
openssl s_client -connect 127.0.0.1:8443 -servername loco.e2e.test </dev/null 2>/dev/null | openssl x509 -noout -checkhost loco.e2e.test | grep -q "does match" \
    || fail "certificat sans l'adresse du portail"
curl -fsS "https://$HOST:8443/linux/setup.sh" -o "$work/setup.sh"
grep -q "https://$HOST:8443" "$work/setup.sh" || { sed -n 1,20p "$work/setup.sh" >&2; fail "script d'installation : adresse des agents perdue"; }
printf 'LOCO_BACKUP_KEEP=2\n' > linux/docker/.env
dc up -d
healthy
grep -q 'PORTAL_URL=""' <(dc exec -T loco cat /etc/loco/loco.conf) || fail "adresse du portail conservée après retrait de la variable"

step "Nettoyage"
sudo apt-get purge -y -q lr-e2e >/dev/null
curl -fsSL "https://$HOST:8443/linux/setup.sh" | sudo sh -s -- --remove
dc down -v
rm -f linux/docker/.env
if ss -Hltn | grep -qE ':(8443|8088|3142) '; then fail "ports encore ouverts"; fi

step "Fichier Compose seul, hors des sources : démarrage sur une image déjà prête"
# Ce que fait un utilisateur qui ne clone pas le projet : un dossier, docker-compose.yml, docker compose up -d.
# L'image vient ici de la construction ci-dessus (LOCO_IMAGE) au lieu du registre.
mkdir "$work/alone"
cp linux/docker/docker-compose.yml "$work/alone/"
ac() { sudo env LOCO_IMAGE=loco:latest docker compose --project-directory "$work/alone" -f "$work/alone/docker-compose.yml" "$@"; }
ac up -d
for (( i=0; i<90; i++ )); do
    [ "$(sudo docker inspect -f '{{.State.Health.Status}}' "$(ac ps -q loco)")" = healthy ] && break
    sleep 4
done
[ "$(sudo docker inspect -f '{{.State.Health.Status}}' "$(ac ps -q loco)")" = healthy ] || { ac logs --tail 80 loco >&2; fail "fichier Compose seul : loco ne devient pas sain"; }
[ "$(sudo docker inspect -f '{{.Config.Image}}' "$(ac ps -q loco)")" = loco:latest ] || fail "fichier Compose seul : image inattendue"
curl -fsk --max-time 8 https://127.0.0.1:8443/healthz | grep -qx ok || fail "fichier Compose seul : portail muet"
ac logs loco | grep -Eq "Compte initial|Initial account" || fail "fichier Compose seul : compte initial absent du journal"
ac down -v
if ss -Hltn | grep -qE ':(8443|8088|3142) '; then fail "ports encore ouverts"; fi
echo 'Test Docker de bout en bout réussi.'
