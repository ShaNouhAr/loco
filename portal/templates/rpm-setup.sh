#!/bin/sh
# loco : poste de la famille Red Hat (Rocky Linux, AlmaLinux, RHEL, CentOS Stream, Oracle Linux).
#   curl -fsSL {{ base }}/rpm/setup.sh | sudo sh
#   ... | sudo sh -s -- --trust-ca   importe l'autorité de certification loco et passe en HTTPS
#   ... | sudo sh -s -- --epel       installe EPEL et le fait passer par le cache
#   ... | sudo sh -s -- --no-cache   dépôt local seulement (dépôts du système en accès direct)
#   ... | sudo sh -s -- --no-local   cache seulement, sans le dépôt local
#   ... | sudo sh -s -- --remove     annule la configuration et rétablit les dépôts du système
#   --from-package : lancé par le paquet loco-client (agent et dépôts) : aucun appel à dnf, occupé par
#                    l'installation ; dnf rafraîchit ses métadonnées de lui-même à la prochaine utilisation.
# Rocky Linux et AlmaLinux : BaseOS, AppStream, CRB/PowerTools et extras passent par le cache, avec les
# clés GPG d'origine. RHEL : ses dépôts exigent un abonnement et restent en direct ; seuls EPEL et le
# dépôt local passent par ce serveur.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Lancez ce script en root (sudo)." >&2; exit 1; }

BASE="{{ base }}"
HTTPS_BASE="{{ https_url }}"
LOCAL=/etc/yum.repos.d/loco.repo
CACHE=/etc/yum.repos.d/loco-cache.repo
STATE=/etc/loco-rpm.state
CA=/etc/pki/ca-trust/source/anchors/loco.crt

USE_CACHE=1 USE_LOCAL=1 TRUST_CA=0 EPEL=0 REMOVE=0 FROM_PACKAGE=0
for arg in "$@"; do
    case "$arg" in
        --trust-ca) TRUST_CA=1 ;;
        --epel) EPEL=1 ;;
        --no-cache) USE_CACHE=0 ;;
        --no-local) USE_LOCAL=0 ;;
        --remove) REMOVE=1 ;;
        --from-package) FROM_PACKAGE=1 ;;
        *) echo "Option inconnue : $arg" >&2; exit 1 ;;
    esac
done
command -v dnf >/dev/null 2>&1 || { echo "Ce poste doit utiliser dnf (famille Red Hat 8 ou plus)." >&2; exit 1; }
# Reprendre le journal des dépôts désactivés ; il sert aussi à restaurer le système avec --remove.
if [ -f /etc/local-repo-rpm.state ]; then
    [ ! -e "$STATE" ] || { echo 'Deux états de dépôts RPM existent ; migration refusée.' >&2; exit 1; }
    mv /etc/local-repo-rpm.state "$STATE"
fi
if [ -f /etc/pki/ca-trust/source/anchors/local-repo.crt ]; then
    if [ ! -f "$CA" ]; then mv /etc/pki/ca-trust/source/anchors/local-repo.crt "$CA";
    else rm -f /etc/pki/ca-trust/source/anchors/local-repo.crt; fi
    update-ca-trust extract
fi

# Valeur de « clé » dans la section [id] d'un fichier .repo (première ligne seulement).
get_opt() {
    awk -v sec="[$2]" -v key="$3" '
        /^\[/ { insec = ($0 == sec); next }
        insec { line = $0; sub(/^[ \t]+/, "", line)
                if (line ~ "^" key "[ \t]*=") { sub(/^[^=]*=[ \t]*/, "", line); print line; exit } }' "$1"
}

# Fixe enabled=VALEUR dans la section [id] d'un fichier .repo, sans toucher au reste.
set_enabled() {
    tmp=$(mktemp)
    awk -v sec="[$2]" -v val="$3" '
        function finish() { if (insec && !done) print "enabled=" val }
        /^\[/ { finish(); insec = ($0 == sec); done = 0; print; next }
        insec && /^[ \t]*enabled[ \t]*=/ { print "enabled=" val; done = 1; next }
        { print }
        END { finish() }' "$1" > "$tmp"
    cat "$tmp" > "$1"
    rm -f "$tmp"
}

# Fichier .repo du système qui définit [id] (hors fichiers de loco).
repo_file() {
    for f in /etc/yum.repos.d/*.repo; do
        case "$f" in "$LOCAL"|"$CACHE") continue ;; esac
        if grep -qx "\[$1\]" "$f" 2>/dev/null; then echo "$f"; return 0; fi
    done
    return 1
}

restore_system() {
    if [ -f "$STATE" ]; then
        while read -r file id enabled; do
            if [ -f "$file" ]; then set_enabled "$file" "$id" "$enabled"; fi
        done < "$STATE"
        rm -f "$STATE"
    fi
    rm -f "$CACHE"
}

if [ "$REMOVE" = 1 ]; then
    restore_system
    rm -f "$LOCAL"
    rm -f /etc/yum.repos.d/local-repo.repo /etc/yum.repos.d/local-repo-cache.repo
    if [ -f "$CA" ]; then rm -f "$CA"; update-ca-trust extract; fi
    [ "$FROM_PACKAGE" = 1 ] || dnf clean all >/dev/null 2>&1 || true
    echo "Configuration loco retirée ; dépôts du système rétablis."
    exit 0
fi

[ -r /etc/os-release ] || { echo "Distribution inconnue (/etc/os-release absent)." >&2; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
MAJOR=${VERSION_ID%%.*}
case "$MAJOR" in ''|*[!0-9]*) echo "Version de la distribution illisible : $VERSION_ID" >&2; exit 1 ;; esac
[ "$MAJOR" -ge 8 ] || { echo "Famille Red Hat 8 ou plus requise." >&2; exit 1; }
EL="el$MAJOR"
case "$ID" in
    rocky|almalinux) VENDOR=$ID ;;
    *) VENDOR="" ;;   # RHEL, CentOS Stream, Oracle Linux : dépôts du système en direct
esac

if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; else GET="wget -qO-"; fi
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ "$TRUST_CA" = 1 ]; then
    $GET "$BASE/ca.crt" > "$WORK/ca"
    install -m 644 "$WORK/ca" "$CA"
    update-ca-trust extract
    if command -v openssl >/dev/null 2>&1; then
        echo "Autorité importée, empreinte SHA-256 : $(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2)"
        echo "Comparez-la à celle de la page Système du portail."
    fi
    BASE=$HTTPS_BASE
fi

# Dépôt local : vérifié avant toute modification du poste.
if [ "$USE_LOCAL" = 1 ]; then
    if ! $GET "$BASE/rpm/$EL/repodata/repomd.xml" > /dev/null; then
        echo "Dépôt $EL indisponible : créez-le dans Linux › Paquets RPM, ou utilisez --no-local." >&2
        exit 1
    fi
    cat > "$WORK/local" <<EOF
[loco]
name=loco ($EL)
baseurl=$BASE/rpm/$EL/
enabled=1
gpgcheck=0
repo_gpgcheck=1
gpgkey=$BASE/rpm/loco.asc
metadata_expire=5m
EOF
    install -m 644 "$WORK/local" "$LOCAL"
else
    rm -f "$LOCAL"
fi

# Redirige un dépôt du système vers le cache : copie de ses clés GPG, original désactivé et noté.
redirect() {   # id chemin-amont
    file=$(repo_file "$1") || return 0
    prev=$(awk -v id="$1" '$2 == id { print $3 }' "$STATE" 2>/dev/null || true)
    if [ -z "$prev" ]; then
        prev=$(get_opt "$file" "$1" enabled)
        case "$prev" in 0|false|False|no) prev=0 ;; *) prev=1 ;; esac
        echo "$file $1 $prev" >> "$STATE"
    fi
    key=$(get_opt "$file" "$1" gpgkey)
    name=$(get_opt "$file" "$1" name)
    {
        echo "[loco-$1]"
        echo "name=${name:-$1} (via loco)"
        echo "baseurl=$2"
        echo "enabled=$prev"
        echo "gpgcheck=1"
        if [ -n "$key" ]; then echo "gpgkey=$key"; fi
        echo
    } >> "$WORK/cache"
    set_enabled "$file" "$1" 0
}

if [ "$USE_CACHE" = 1 ]; then
    if [ "$EPEL" = 1 ] && ! rpm -q epel-release >/dev/null 2>&1; then
        if [ -n "$VENDOR" ]; then
            dnf -y -q install epel-release
        else
            dnf -y -q install "$BASE/rpmcache/epel/epel-release-latest-$MAJOR.noarch.rpm"
            echo "RHEL : activez aussi CodeReady Builder (subscription-manager repos --enable codeready-builder-for-rhel-$MAJOR-\$(arch)-rpms)."
        fi
    fi
    restore_system
    : > "$WORK/cache"
    echo "# Géré par loco (rpm/setup.sh) ; « --remove » rétablit les dépôts d'origine." > "$WORK/cache.head"
    if [ -n "$VENDOR" ]; then
        U="$BASE/rpmcache/$VENDOR/\$releasever"
        redirect baseos "$U/BaseOS/\$basearch/os/"
        redirect appstream "$U/AppStream/\$basearch/os/"
        redirect extras "$U/extras/\$basearch/os/"
        redirect crb "$U/CRB/\$basearch/os/"
        redirect powertools "$U/PowerTools/\$basearch/os/"
    fi
    redirect epel "$BASE/rpmcache/epel/\$releasever/Everything/\$basearch/"
    if [ -s "$WORK/cache" ]; then
        cat "$WORK/cache.head" "$WORK/cache" > "$CACHE"
        chmod 644 "$CACHE"
    fi
else
    restore_system
fi

# Les nouveaux dépôts ont été vérifiés et écrits : l'ancien nom peut être retiré.
rm -f /etc/yum.repos.d/local-repo.repo /etc/yum.repos.d/local-repo-cache.repo
if [ "$FROM_PACKAGE" = 1 ]; then
    echo "Poste configuré pour loco ($BASE)."
else
    dnf -y -q makecache
    echo "Poste configuré pour loco ($BASE) :"
    dnf repolist --enabled
fi
