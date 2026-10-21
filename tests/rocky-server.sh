#!/bin/bash
# Serveur natif Rocky 8/9/10 : VM jetable uniquement (installation puis purge).
set -euo pipefail
cd "$(dirname "$0")/.."
. /etc/os-release
[ "$ID" = rocky ]
case "$VERSION_ID" in 8.*|9.*|10.*) ;; *) exit 1 ;; esac
# Les essais locaux peuvent choisir un miroir Rocky plus rapide ; signatures RPM conservées.
if [ -n "${LOCO_TEST_ROCKY_MIRROR:-}" ]; then
    interpreter=$(command -v python3 || echo /usr/libexec/platform-python)
    "$interpreter" - <<'PY'
from pathlib import Path
import os,re
mirror=os.environ['LOCO_TEST_ROCKY_MIRROR'].rstrip('/')+'/'
assert re.fullmatch(r'https://[a-zA-Z0-9./_-]+/',mirror)
for p in Path('/etc/yum.repos.d').glob('*.repo'):
    text=re.sub(r'^mirrorlist=', '#mirrorlist=',p.read_text(),flags=re.M)
    text=re.sub(r'^#baseurl=https?://dl.rockylinux.org/\$contentdir/', 'baseurl='+mirror,text,flags=re.M)
    p.write_text(text)
PY
fi
PW=Native-Rocky-E2e-Password-2026
dnf install -y nginx firewalld rpm-build >/dev/null
systemctl enable --now firewalld >/dev/null
[ "$(getenforce)" = Enforcing ]
original_nginx=$(sha256sum /etc/nginx/nginx.conf | cut -d' ' -f1)
original_bool=$(getsebool httpd_can_network_connect)
builder_repo=crb
if [ "${VERSION_ID%%.*}" = 8 ]; then builder_repo=powertools; fi
builder_enabled=$(dnf -q repolist --enabled | awk -v repo="$builder_repo" '$1 == repo {found=1} END {print found+0}')
install_log=/tmp/native-rocky-install.log
failure_log() {
    local rc=$?
    if [ "$rc" != 0 ]; then tail -40 "$install_log"; journalctl -u loco-portal -u apt-cacher-ng -u nginx --no-pager -n 70; fi
}
trap failure_log EXIT
if [ -n "${LOCO_E2E_LEGACY_SRC:-}" ]; then
    "$LOCO_E2E_LEGACY_SRC/install.sh" --host 10.0.2.15 --password "$PW" > "$install_log" 2>&1
    old_ca=$(sha256sum /etc/local-repo/tls/ca.crt | cut -d' ' -f1)
    old_uid=$(id -u localrepo)
fi
./install.sh --host 10.0.2.15 --password "$PW" > "$install_log" 2>&1
if [ -n "${old_ca:-}" ]; then
    [ "$(sha256sum /etc/loco/tls/ca.crt | cut -d' ' -f1)" = "$old_ca" ]
    [ "$(id -u loco)" = "$old_uid" ]
    [ ! -d /etc/local-repo ]
fi
CA=/etc/loco/tls/ca.crt
BASE=https://127.0.0.1:8443
health() { curl -fsS --cacert "$CA" "$BASE/healthz" | grep -x ok > /dev/null; }
health
python3 tests/linux_agent_smoke.py
/usr/local/lib/loco/venv/bin/python tests/https_redirect.py "$BASE"
apt_binary_stamp=""
if [ "${VERSION_ID%%.*}" = 8 ]; then
    apt_binary_stamp=$(stat -c '%Y:%i' /usr/local/lib/loco/apt/bin/apt-ftparchive)
fi
if runuser -u loco -- test -r /etc/loco/install-state/nginx-rocky-original.conf; then
    echo 'Les métadonnées de désinstallation doivent être réservées à root' >&2; exit 1
fi
systemctl is-active --quiet nginx apt-cacher-ng loco-portal loco-agent loco-backup.timer loco-stats.timer
for _ in $(seq 1 150); do grep -q '"package_manager": "dnf"' /var/lib/loco-agent/status.json 2>/dev/null && break; sleep 2; done
grep -q '"package_manager": "dnf"' /var/lib/loco-agent/status.json
if [ "${VERSION_ID%%.*}" = 10 ]; then
    systemctl is-active --quiet loco-acng-maintenance.timer
    systemctl start loco-acng-maintenance.service
fi
firewall-cmd --query-service=loco
[ "$(getenforce)" = Enforcing ]
/usr/local/lib/loco/venv/bin/python -m unittest discover -s tests -v

work=$(mktemp -d)
mkdir -p "$work/deb/DEBIAN" "$work/deb/usr/share/lr-native-e2e"
printf 'Package: lr-native-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: CI <ci@invalid>\nDescription: Test serveur Rocky\n' > "$work/deb/DEBIAN/control"
echo serveur-rocky > "$work/deb/usr/share/lr-native-e2e/content"
dpkg-deb --build "$work/deb" "$work/lr-native-e2e.deb"
/usr/local/sbin/loco-apt add debian trixie 13
python3 tests/e2e_portal.py "$BASE" "$PW" "$work/lr-native-e2e.deb" linux debian/trixie
curl -fsS --cacert "$CA" "$BASE/apt/repositories/debian/trixie/Packages" | grep 'Package: lr-native-e2e' > /dev/null
curl -fsS --cacert "$CA" "$BASE/apt/repositories/debian/trixie/InRelease" -o "$work/InRelease"
gpgv --keyring /srv/loco/apt/loco.gpg "$work/InRelease"
curl -fsS --cacert "$CA" "$BASE/linux/repositories/debian/trixie.sources" | grep 'Signed-By:' > /dev/null
curl -fsS --proxy http://127.0.0.1:3142 http://deb.debian.org/debian/dists/stable/Release -o "$work/debian-release"
grep -q '^Origin: Debian' "$work/debian-release"

major=${VERSION_ID%%.*}
# Sortie HTTPS de nginx, bundle de CA Rocky et écriture du cache sous SELinux.
curl -fsS --max-time 90 --cacert "$CA" "$BASE/rpmcache/rocky/$major/BaseOS/x86_64/os/repodata/repomd.xml" -o "$work/upstream-rpm.xml"
grep -q '<repomd' "$work/upstream-rpm.xml"
cat > "$work/test.spec" <<EOF
Name: lr-native-e2e
Version: 1.0
Release: 1.el$major
Summary: Test du serveur Rocky
License: MIT
BuildArch: noarch
%description
Test loco sur Rocky.
%install
mkdir -p %{buildroot}/usr/share/lr-native-rpm
echo serveur-rocky > %{buildroot}/usr/share/lr-native-rpm/content
%files
/usr/share/lr-native-rpm/content
EOF
rpmbuild --quiet --define "_topdir $work/rpm" --define '_build_id_links none' -bb "$work/test.spec"
python3 tests/e2e_portal.py "$BASE" "$PW" "$work/rpm/RPMS/noarch/lr-native-e2e-1.0-1.el$major.noarch.rpm" rpm "el$major"
curl -fsS http://127.0.0.1:8088/rpm/setup.sh -o "$work/rpm-setup.sh"
sh "$work/rpm-setup.sh" --no-cache --trust-ca
dnf install -y --disablerepo='*' --enablerepo=loco lr-native-e2e
grep -qx serveur-rocky /usr/share/lr-native-rpm/content
dnf remove -y lr-native-e2e >/dev/null
sh "$work/rpm-setup.sh" --remove
curl -fsS --cacert "$CA" "$BASE/flatpak/loco.flatpakrepo" | grep 'GPGKey=' > /dev/null

keys=$(sha256sum "$CA" /srv/loco/apt/loco.gpg)
users=$(python3 -c 'import sqlite3,hashlib; c=sqlite3.connect("/var/lib/loco/portal.db"); print(hashlib.sha256(repr(c.execute("SELECT * FROM users").fetchall()).encode()).hexdigest())')
./install.sh --allow 127.0.0.1 > "$install_log" 2>&1
[ "$(curl -sS -o /dev/null -w '%{http_code}' --interface 10.0.2.15 --cacert "$CA" https://10.0.2.15:8443/healthz)" = 403 ]
if curl -fsS --max-time 10 --interface 10.0.2.15 --proxy http://10.0.2.15:3142 http://deb.debian.org/debian/dists/stable/Release > /dev/null 2>&1; then
    echo 'Le cache APT accepte un client interdit' >&2; exit 1
fi
./install.sh --allow '10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10' > "$install_log" 2>&1
health
[ "$keys" = "$(sha256sum "$CA" /srv/loco/apt/loco.gpg)" ]
if [ -n "$apt_binary_stamp" ]; then
    [ "$apt_binary_stamp" = "$(stat -c '%Y:%i' /usr/local/lib/loco/apt/bin/apt-ftparchive)" ]
fi
[ "$users" = "$(python3 -c 'import sqlite3,hashlib; c=sqlite3.connect("/var/lib/loco/portal.db"); print(hashlib.sha256(repr(c.execute("SELECT * FROM users").fetchall()).encode()).hexdigest())')" ]
backup=$(/usr/local/sbin/loco-backup create | awk '{print $1}')
deb=$(find /srv/loco/apt/repositories/debian/trixie/pool -name '*.deb' -print -quit)
deb_sum=$(sha256sum "$deb")
rm "$deb"
/usr/local/sbin/loco-backup restore "$backup"
./install.sh > "$install_log" 2>&1
[ "$deb_sum" = "$(sha256sum "$deb")" ]
systemctl restart loco-portal nginx apt-cacher-ng
for _ in $(seq 1 30); do if health 2>/dev/null; then break; fi; sleep 1; done
health
for _ in 1 2 3; do
    systemctl restart apt-cacher-ng
    sleep 1
    curl -fsS --max-time 20 --proxy http://127.0.0.1:3142 http://deb.debian.org/debian/dists/stable/Release -o "$work/debian-release"
done
linux/remove.sh --purge -y
[ ! -e /opt/loco ]
[ ! -e /var/lib/loco-agent ]
[ ! -e /etc/loco ]
[ ! -e /srv/loco ]
[ ! -e /usr/local/lib/loco/acng ]
[ ! -e /usr/local/lib/loco/apt ]
rpm -q nginx firewalld >/dev/null
[ "$original_nginx" = "$(sha256sum /etc/nginx/nginx.conf | cut -d' ' -f1)" ]
[ "$original_bool" = "$(getsebool httpd_can_network_connect)" ]
[ "$builder_enabled" = "$(dnf -q repolist --enabled | awk -v repo="$builder_repo" '$1 == repo {found=1} END {print found+0}')" ]
if firewall-cmd --query-service=loco >/dev/null 2>&1; then exit 1; fi
[ "$(getenforce)" = Enforcing ]
rm -rf "$work"
if [ "${VERSION_ID%%.*}" = 8 ]; then
    # Second cas : machine sans nginx, sélection d'un flux puis restauration de son état.
    dnf remove -y --setopt=clean_requirements_on_remove=False nginx >/dev/null
    dnf module reset -y nginx >/dev/null
    module_before=absent
    if [ -f /etc/dnf/modules.d/nginx.module ]; then module_before=$(sha256sum /etc/dnf/modules.d/nginx.module | cut -d' ' -f1); fi
    ./install.sh --host 10.0.2.15 --password "$PW" --no-flatpak --no-rpm > "$install_log" 2>&1
    health
    nginx -V 2>&1 | grep -- '--with-stream_ssl_preread_module' >/dev/null
    linux/remove.sh --purge -y
    if rpm -q nginx >/dev/null; then echo 'Le nginx ajouté doit être retiré' >&2; exit 1; fi
    module_after=absent
    if [ -f /etc/dnf/modules.d/nginx.module ]; then module_after=$(sha256sum /etc/dnf/modules.d/nginx.module | cut -d' ' -f1); fi
    [ "$module_before" = "$module_after" ]
    [ "$(getenforce)" = Enforcing ]
fi
echo "Serveur natif Rocky $VERSION_ID : validation réussie."
