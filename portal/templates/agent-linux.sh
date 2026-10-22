#!/bin/sh
# loco : agent du parc (loco-client {{ version }}) pour Debian, Ubuntu, Rocky Linux, AlmaLinux et RHEL.
#   curl -fsSL '{{ server }}/agent/linux.sh?key=…' | sudo sh
# Installe /usr/local/lib/loco-client, l'autorité du serveur (épinglée, sans toucher au magasin du système)
# et loco-client.timer (contact toutes les deux minutes). Retrait : sudo loco-client remove,
# ou « Retirer l'agent » dans le portail. Relancer cette commande réinscrit le poste (même identifiant).
set -eu
# Langue du poste : français si LANG/LC_* commence par « fr », sinon anglais (sudo conserve LANG).
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_EN=0 ;; *) LOCO_EN=1 ;; esac
t() { if [ "$LOCO_EN" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
[ "$(id -u)" -eq 0 ] || { echo "{{ sh_t('Lancez ce script en root (sudo).') }}" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "{{ sh_t('systemd requis.') }}" >&2; exit 1; }
if ! command -v python3 >/dev/null 2>&1; then
    echo "{{ sh_t('Installation de python3…') }}"
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -q >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q python3 >/dev/null
    else
        echo "{{ sh_t('Installez python3 puis relancez.') }}" >&2; exit 1
    fi
fi
install -d -m 755 /usr/local/lib/loco-client /var/lib/loco-client
install -d -m 700 /etc/loco-client
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'LOCO_CLIENT_EOF'
{{ agent }}
LOCO_CLIENT_EOF
python3 -c 'import sys; compile(open(sys.argv[1]).read(), "loco-client.py", "exec")' "$TMP"
install -m 755 "$TMP" /usr/local/lib/loco-client/loco-client.py
ln -sf /usr/local/lib/loco-client/loco-client.py /usr/local/sbin/loco-client
{% if ca %}cat > /etc/loco-client/ca.crt <<'LOCO_CA_EOF'
{{ ca }}
LOCO_CA_EOF
{% else %}rm -f /etc/loco-client/ca.crt
{% endif %}chmod 644 /etc/loco-client/ca.crt 2>/dev/null || true
/usr/local/lib/loco-client/loco-client.py enroll --server '{{ server }}' --key '{{ key }}'
cat > /etc/systemd/system/loco-client.service <<'UNIT_EOF'
[Unit]
Description=loco : agent du parc (inventaire, mises à jour)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/lib/loco-client/loco-client.py run
TimeoutStartSec=4h
Nice=10
UNIT_EOF
cat > /etc/systemd/system/loco-client.timer <<'UNIT_EOF'
[Unit]
Description=loco : contact du serveur toutes les deux minutes

[Timer]
OnBootSec=1min
OnUnitInactiveSec=2min
RandomizedDelaySec=20s

[Install]
WantedBy=timers.target
UNIT_EOF
systemctl daemon-reload
systemctl enable --now loco-client.timer >/dev/null 2>&1
systemctl start --no-block loco-client.service
echo "{{ sh_t('Agent loco installé : le poste apparaît dans « Mon parc » (%(url)s/parc).', url=server) }}"
