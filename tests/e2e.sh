#!/bin/bash
# VM JETABLE uniquement : installation, migration, clients, isolation, restauration, désinstallation.
set -euo pipefail
cd "$(dirname "$0")/.."
PW="E2e-Password-$(date +%s)"
work=$(mktemp -d)
trap 'sudo rm -rf -- "$work"' EXIT
trap 'echo "Échec du test ligne $LINENO : $BASH_COMMAND" >&2' ERR
step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }
ready() { curl -fsk --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 https://127.0.0.1:8443/healthz; }
cli() { (cd /opt/loco/portal && sudo -u loco env LOCO_DATA=/srv/loco LOCO_STATE=/var/lib/loco LOCO_ETC=/etc/loco python3 -m loco.cli "$@"); }
# Les commandes du scénario partagent le verrou des opérations d'administration avec l'agent.
# Son inventaire automatique démarre à l'installation et peut sinon occuper le verrou APT
# pendant le test de migration ou la relance de l'installateur. Aucun verrou APT n'est supprimé.
with_admin_lock() {
    sudo sh -c 'umask 077; exec flock -o -w 900 /var/lib/loco-agent/lock "$@"' sh "$@"
}
build_deb() {
    local kind=$1
    mkdir -p "$work/$kind/DEBIAN" "$work/$kind/usr/bin"
    printf '#!/bin/sh\necho loco-%s-ok\n' "$kind" > "$work/$kind/usr/bin/lr-e2e"
    chmod 755 "$work/$kind/usr/bin/lr-e2e"
    printf 'Package: lr-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: ci <ci@example.invalid>\nDescription: test\n' > "$work/$kind/DEBIAN/control"
    dpkg-deb --root-owner-group -b "$work/$kind" "$work/$kind.deb" >/dev/null
}
build_deb native
build_deb foreign
build_deb legacy
if [ -n "${LOCO_E2E_LEGACY_SRC:-}" ]; then
    step "Installation local-repo 1.9.0 et ancien dépôt"
    sudo "$LOCO_E2E_LEGACY_SRC/install.sh" --password "$PW" --no-flatpak
    ready
    old_uid=$(id -u localrepo)
    python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/legacy.deb" linux legacy
    curl -fsSL http://127.0.0.1:8088/linux/setup.sh | sudo sh -s -- --legacy --trust-ca
    sudo sha256sum /etc/local-repo/tls/ca.crt | cut -d' ' -f1 > "$work/old-ca"
fi
step "Installation / migration vers la version actuelle"
sudo ./install.sh --password "$PW" --flatpak
ready
# Vérifie aussi les redémarrages successifs du serveur applicatif (nouvelles versions de Gunicorn).
for _ in 1 2 3; do sudo systemctl restart loco-portal; ready; done
python3 tests/https_redirect.py https://127.0.0.1:8443
# shellcheck disable=SC1091
HOST=$(. /etc/loco/loco.conf && echo "$HOST")
if [ -f "$work/old-ca" ]; then
    [ "$(id -u loco)" = "$old_uid" ]
    [ ! -d /etc/local-repo ]
    with_admin_lock apt-get update -o APT::Update::Error-Mode=any >/dev/null
    [ "$(sudo sha256sum /etc/loco/tls/ca.crt | cut -d' ' -f1)" = "$(cat "$work/old-ca")" ]
    sudo cmp "$work/legacy.deb" /srv/loco/apt/pool/lr-e2e_1.0_all.deb
    grep -q 'lr-e2e' /srv/loco/apt/Packages
fi
sudo /opt/loco/bin/loco-user list
step "Relance (idempotence)"
with_admin_lock ./install.sh >/dev/null
ready
step "Agent d'administration : première vérification des paquets du système"
sudo systemctl is-active --quiet loco-agent
for _ in $(seq 1 90); do grep -q '"package_manager": "apt"' /var/lib/loco-agent/status.json 2>/dev/null && break; sleep 2; done
grep -q '"package_manager": "apt"' /var/lib/loco-agent/status.json
sudo /usr/local/sbin/loco-update status
# shellcheck disable=SC1091
. /etc/os-release
DIST=$ID SUITE=${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}
REPO="$DIST/$SUITE"
step "Création du dépôt du poste et d'un dépôt distinct"
sudo loco-apt add "$DIST" "$SUITE" "$VERSION_ID"
sudo loco-apt add ubuntu isolation-e2e test
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/native.deb" linux "$REPO"
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/foreign.deb" linux ubuntu/isolation-e2e
sudo cmp "$work/native.deb" "/srv/loco/apt/repositories/$REPO/pool/lr-e2e_1.0_all.deb"
sudo cmp "$work/foreign.deb" /srv/loco/apt/repositories/ubuntu/isolation-e2e/pool/lr-e2e_1.0_all.deb
step "Poste : détection automatique, source deb822, signatures et HTTPS"
curl -fsSL "http://$HOST:8088/linux/setup.sh" > "$work/setup.sh"
with_admin_lock sh "$work/setup.sh" --trust-ca
curl -fsSL "https://$HOST:8443/linux/setup.sh" > "$work/setup.sh"
SOURCE=/etc/apt/sources.list.d/loco.sources
grep -q "https://$HOST:8443/apt/repositories/$REPO/" "$SOURCE"
[ ! -e /etc/apt/sources.list.d/loco.list ]
curl -fsS "https://$HOST:8443/healthz" >/dev/null
with_admin_lock apt-get install -y -q lr-e2e >/dev/null
[ "$(lr-e2e)" = loco-native-ok ]
step "Version absente : échec sans changer le dépôt en place"
sudo cp "$SOURCE" "$work/source-before"
if with_admin_lock sh "$work/setup.sh" --distribution debian --suite not-created; then echo 'Une version absente doit être refusée.' >&2; exit 1; fi
sudo cmp "$SOURCE" "$work/source-before"
step "Cache seul et retour au dépôt détecté"
with_admin_lock sh "$work/setup.sh" --cache-only >/dev/null
[ ! -e "$SOURCE" ]
[ -e /etc/apt/apt.conf.d/01loco-proxy ]
with_admin_lock sh "$work/setup.sh" >/dev/null
if [ -f "$work/old-ca" ]; then
    step "Compatibilité explicite du dépôt historique"
    with_admin_lock apt-get purge -y -q lr-e2e >/dev/null
    with_admin_lock sh "$work/setup.sh" --legacy >/dev/null
    with_admin_lock apt-get install -y -q lr-e2e >/dev/null
    [ "$(lr-e2e)" = loco-legacy-ok ]
    with_admin_lock apt-get purge -y -q lr-e2e >/dev/null
    with_admin_lock sh "$work/setup.sh" >/dev/null
    with_admin_lock apt-get install -y -q lr-e2e >/dev/null
    [ "$(lr-e2e)" = loco-native-ok ]
fi
step "Flatpak : application locale et cache Flathub"
fp="$work/fp"; arch=$(flatpak --default-arch)
mkdir -p "$fp/app/files/bin" "$fp/app/export"
printf '#!/bin/sh\necho loco-flatpak-ok\n' > "$fp/app/files/bin/lr-e2e"
chmod 755 "$fp/app/files/bin/lr-e2e"
printf '[Application]\nname=org.loco.E2e\nruntime=org.freedesktop.Platform/%s/24.08\nsdk=org.freedesktop.Sdk/%s/24.08\ncommand=lr-e2e\n' "$arch" "$arch" > "$fp/app/metadata"
ostree init --mode=archive-z2 --repo="$fp/repo"
flatpak build-export "$fp/repo" "$fp/app" >/dev/null
flatpak build-bundle "$fp/repo" "$fp/e2e.flatpak" org.loco.E2e
python3 tests/e2e_portal.py "https://127.0.0.1:8443" "$PW" "$fp/e2e.flatpak" flatpak
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh >/dev/null
flatpak remote-ls --system loco --columns=application | grep -qx org.loco.E2e
sudo flatpak install --system --noninteractive --no-deps loco org.loco.E2e >/dev/null
flatpak info --system org.loco.E2e | grep -q loco
step "Migration d'une application Flatpak dont l'origine porte l'ancien nom"
sudo flatpak remote-add --system local-repo "https://$HOST:8443/flatpak/local-repo.flatpakrepo"
sudo flatpak install --system --noninteractive --assumeyes --no-deps --reinstall local-repo org.loco.E2e >/dev/null
sudo flatpak remote-delete --system --force loco
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh >/dev/null
[ "$(flatpak info --system --show-origin org.loco.E2e)" = loco ]
if flatpak remotes --system --columns=name | grep -qx local-repo; then echo 'Ancien remote Flatpak non migré.' >&2; exit 1; fi
flatpak remote-info --system flathub org.flatpak.Builder >/dev/null
curl -fsS "https://$HOST:8443/flathub/repo/summary.idx" -o /dev/null
sudo flatpak uninstall --system --noninteractive org.loco.E2e >/dev/null
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh -s -- --remove >/dev/null
step "Contrôles de sécurité"
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST:8088/")" = 301 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: example.com' http://127.0.0.1/)" = 403 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3142/acng-report.html?doCount=1)" = 401 ]
curl -fsS -x http://127.0.0.1:3142 http://deb.debian.org/debian/dists/stable/Release -o /dev/null
curl -sk -D - -o /dev/null https://127.0.0.1:8443/login | grep -i "content-security-policy: default-src 'none'" > /dev/null
step "Sauvegarde et restauration des dépôts / comptes / signatures"
pin_digest=$(printf 'loco-deployment-backup-test' | sha256sum | cut -d ' ' -f 1)
pin_dir="/srv/loco/win/_deployments/$pin_digest"
sudo mkdir -p /srv/loco/win/backup-fixture "$pin_dir"
printf 'loco-deployment-backup-test' | sudo tee /srv/loco/win/backup-fixture/setup.msi >/dev/null
sudo ln /srv/loco/win/backup-fixture/setup.msi "$pin_dir/installer.msi"
sudo loco-backup create "$work/backups"
archive=$(sudo find "$work/backups" -name '*.tar.gz' -type f | head -1)
sudo mv "$pin_dir" "$work/pinned-before-restore"
sudo rm "/srv/loco/apt/repositories/$REPO/pool/lr-e2e_1.0_all.deb"
cli rebuild-apt
sudo loco-backup restore "$archive"
sudo cmp /srv/loco/win/backup-fixture/setup.msi "$pin_dir/installer.msi"
with_admin_lock ./install.sh >/dev/null
ready
sudo cmp "$work/native.deb" "/srv/loco/apt/repositories/$REPO/pool/lr-e2e_1.0_all.deb"
sudo loco-apt list | grep -q "$REPO"
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/native.deb" linux "$REPO"
step "Désinstallation"
with_admin_lock apt-get purge -y -q lr-e2e >/dev/null
curl -fsSL "https://$HOST:8443/linux/setup.sh" | with_admin_lock sh -s -- --remove
[ ! -e /etc/apt/sources.list.d/loco.sources ]
sudo /opt/loco/remove.sh -y --purge
fail() { echo "ÉCHEC : $*" >&2; exit 1; }
if systemctl list-unit-files | grep -q loco; then fail 'unités systemd restantes'; fi
if ss -Hltn | grep -qE ':(8443|8088|3142) '; then fail 'ports encore ouverts'; fi
if id loco >/dev/null 2>&1; then fail 'utilisateur loco restant'; fi
for d in /etc/loco /srv/loco /opt/loco /var/lib/loco-agent /usr/local/sbin/loco-update; do
    if [ -e "$d" ]; then fail "$d restant"; fi
done
echo 'Test de bout en bout réussi.'
