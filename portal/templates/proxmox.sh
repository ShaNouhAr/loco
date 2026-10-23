#!/bin/sh
# loco : hôte Proxmox VE — passe les dépôts par le cache de loco et choisit l'abonnement ou le sans-abonnement.
#   curl -fsSL {{ base }}/linux/proxmox.sh | sudo sh                      (sans-abonnement, mis en cache)
#   ... | sudo sh -s -- --enterprise                                      (dépôt entreprise, abonnement requis)
#   ... | sudo sh -s -- --trust-ca                                        (+ importe l'autorité HTTPS de loco)
#   ... | sudo sh -s -- --remove                                          (retire la configuration loco)
# Remplace (ne duplique pas) le dépôt Proxmox VE trouvé dans /etc/apt/sources.list.d ; Debian passe par le cache.
set -eu
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_LANG=fr ;; es*) LOCO_LANG=es ;; *) LOCO_LANG=en ;; esac
t() { case "$LOCO_LANG" in fr) printf '%s' "$1" ;; es) printf '%s' "$3" ;; *) printf '%s' "$2" ;; esac; }
[ "$(id -u)" -eq 0 ] || { echo "{{ sh_t('Lancez ce script en root (sudo).') }}" >&2; exit 1; }
BASE="{{ base }}"
CA=/usr/local/share/ca-certificates/loco.crt
PROXY=/etc/apt/apt.conf.d/01loco-proxy
MANAGED=/etc/apt/sources.list.d/loco-proxmox.sources
FLAVOR=no-subscription TRUST_CA=0 REMOVE=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --enterprise) FLAVOR=enterprise; shift ;;
        --no-subscription) FLAVOR=no-subscription; shift ;;
        --trust-ca) TRUST_CA=1; shift ;;
        --remove) REMOVE=1; shift ;;
        *) echo "{{ sh_t('Option inconnue : %(opt)s', opt='$1') }}" >&2; exit 1 ;;
    esac
done
if [ "$REMOVE" = 1 ]; then
    rm -f "$MANAGED" "$PROXY"
    if [ -f "$CA" ]; then rm -f "$CA"; update-ca-certificates --fresh >/dev/null 2>&1 || true; fi
    echo "{{ sh_t('Configuration Proxmox de loco retirée ; reconfigurez vos dépôts depuis Proxmox.') }}"
    exit 0
fi
command -v apt-get >/dev/null 2>&1 || { echo "{{ sh_t('Ce poste doit utiliser APT (Proxmox VE sur Debian).') }}" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "{{ sh_t('python3 est requis (présent sur Proxmox).') }}" >&2; exit 1; }
if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; elif command -v wget >/dev/null 2>&1; then GET="wget -qO-"; else
    echo "{{ sh_t('Installez curl ou wget.') }}" >&2; exit 1
fi
# Version Debian sous-jacente (trixie, bookworm…) pour les suites.
SUITE=""
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    SUITE=${VERSION_CODENAME:-}
fi
[ -n "$SUITE" ] || { echo "{{ sh_t('Version Debian introuvable (/etc/os-release).') }}" >&2; exit 1; }
if [ "$TRUST_CA" = 1 ]; then
    TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
    $GET "$BASE/ca.crt" > "$TMP/ca"
    command -v openssl >/dev/null 2>&1 && openssl x509 -in "$TMP/ca" -noout >/dev/null
    mkdir -p "$(dirname "$CA")"
    install -m 644 "$TMP/ca" "$CA"
    update-ca-certificates >/dev/null
    if command -v openssl >/dev/null 2>&1; then
        echo "{{ sh_t('Autorité importée, empreinte SHA-256 : %(fp)s', fp='$(openssl x509 -in \"$CA\" -noout -fingerprint -sha256 | cut -d= -f2)') }}"
    fi
fi
# Cache : tout le trafic APT HTTP (Debian et le dépôt Proxmox sans-abonnement) passe par loco ; HTTPS en direct.
cat > "$PROXY" <<CONF
Acquire::http::Proxy "http://{{ host }}:{{ acng_port }}";
Acquire::https::Proxy "DIRECT";
Acquire::http::Proxy::{{ host }} "DIRECT";
CONF
# Remplace le dépôt Proxmox VE (quel que soit son emplacement dans sources.list.d) par la variante choisie.
python3 - "$FLAVOR" "$SUITE" "$MANAGED" <<'PY'
import sys, os, glob, re
flavor, suite, managed = sys.argv[1], sys.argv[2], sys.argv[3]
SIGN = "/usr/share/keyrings/proxmox-archive-keyring.gpg"
managed_abs = os.path.abspath(managed)

def uris_of(stanza):
    for line in stanza.splitlines():
        if line.lower().startswith("uris:"):
            return line.split(":", 1)[1].strip()
    return ""

# Dépôts deb822 (.sources) : retirer les blocs du dépôt Proxmox VE, garder le reste (Debian, Ceph…).
for path in glob.glob("/etc/apt/sources.list.d/*.sources"):
    if os.path.abspath(path) == managed_abs:
        continue
    try:
        text = open(path).read()
    except OSError:
        continue
    stanzas = [s for s in re.split(r"\n\s*\n", text) if s.strip()]
    kept = [s for s in stanzas if not ("proxmox.com" in uris_of(s) and "/debian/pve" in uris_of(s))]
    if len(kept) != len(stanzas):
        if kept:
            open(path, "w").write("\n\n".join(kept) + "\n")
        else:
            os.remove(path)

# Dépôts à l'ancienne (.list) : commenter/retirer les lignes du dépôt Proxmox VE.
for path in glob.glob("/etc/apt/sources.list.d/*.list") + ["/etc/apt/sources.list"]:
    try:
        lines = open(path).read().splitlines()
    except OSError:
        continue
    kept, changed = [], False
    for line in lines:
        bare = line.strip().lstrip("#").strip()
        if bare.startswith("deb") and "proxmox.com" in bare and "/debian/pve" in bare:
            changed = True
        else:
            kept.append(line)
    if changed:
        if not kept and os.path.dirname(path).endswith("/sources.list.d"):
            os.remove(path)
        else:
            open(path, "w").write("\n".join(kept) + ("\n" if kept else ""))

if flavor == "enterprise":
    uri, comp = "https://enterprise.proxmox.com/debian/pve", "pve-enterprise"
else:
    uri, comp = "http://download.proxmox.com/debian/pve", "pve-no-subscription"
open(managed, "w").write(
    "# Géré par loco : dépôt Proxmox VE. Relancez proxmox.sh pour changer de variante ; ne pas modifier à la main.\n"
    "Types: deb\nURIs: %s\nSuites: %s\nComponents: %s\nSigned-By: %s\n" % (uri, suite, comp, SIGN))
os.chmod(managed, 0o644)
PY
apt-get update -o APT::Update::Error-Mode=any
if [ "$FLAVOR" = enterprise ]; then
    echo "{{ sh_t('Proxmox configuré : dépôt entreprise (abonnement requis ; non mis en cache, HTTPS). Debian passe par le cache de loco.') }}"
else
    echo "{{ sh_t('Proxmox configuré : dépôt sans-abonnement, mis en cache par loco. Debian aussi.') }}"
fi
