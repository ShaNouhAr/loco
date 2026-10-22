# shellcheck shell=sh
# Appliance Alpine (ISO) : fonctions communes à l'installateur de l'ISO et à setup-loco (console).
# POSIX sh (ash de BusyBox) ; les validations d'adresses passent par Python (présent sur l'appliance).

# Langue de la console : français si LANG/LC_* commence par « fr », espagnol par « es », anglais sinon.
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in fr*) LOCO_LANG=fr ;; es*) LOCO_LANG=es ;; *) LOCO_LANG=en ;; esac
t() { case "$LOCO_LANG" in fr) printf '%s' "$1" ;; es) printf '%s' "${3:-$2}" ;; *) printf '%s' "$2" ;; esac; }

# Réponse oui/non ou valeur : « yes », « oui », « 1 », « true ».
appliance_true() {
    case "$1" in yes|oui|1|true|on) return 0 ;; *) return 1 ;; esac
}

appliance_ip_ok() {
    case "$1" in *%*) return 1 ;; esac
    python3 -c 'import ipaddress, sys; ipaddress.ip_address(sys.argv[1])' "$1" 2>/dev/null
}

# Adresse avec préfixe (192.168.1.10/24), IPv4 ou IPv6.
appliance_cidr_ok() {
    case "$1" in */*) ;; *) return 1 ;; esac
    python3 -c 'import ipaddress, sys; ipaddress.IPv4Interface(sys.argv[1])' "$1" 2>/dev/null
}

# Passerelle IPv4 sur le réseau choisi (une adresse fixe IPv4 est utilisée par l'appliance).
appliance_gateway_ok() {
    python3 -c '
import ipaddress, sys
net = ipaddress.IPv4Interface(sys.argv[1]).network
gateway = ipaddress.IPv4Address(sys.argv[2])
sys.exit(0 if gateway in net else 1)' "$1" "$2" 2>/dev/null
}

# Réseaux séparés par des espaces, normalisés (192.168.1.7/24 -> 192.168.1.0/24) ; échec si l'un est invalide.
appliance_networks() {
    python3 -c '
import ipaddress, sys
items = sys.argv[1].split()
if not items or any("%" in n for n in items):
    sys.exit(1)
print(" ".join(str(ipaddress.ip_network(n, strict=False)) for n in items))' "$1" 2>/dev/null
}

appliance_hostname_ok() {
    printf '%s' "$1" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'
}

# Mot de passe du portail : mêmes règles que le portail (12 caractères, 5 différents, sans « admin »).
appliance_password_problem() {
    python3 -c '
import sys
p = sys.argv[1]
if len(p) < 12:
    print("au moins 12 caractères")
elif "admin" in p.lower():
    print("ne doit pas contenir « admin »")
elif len(set(p)) < 5:
    print("trop répétitif")' "$1"
}

appliance_ssh_key_ok() {
    printf '%s\n' "$1" | grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com) [A-Za-z0-9+/=]+( [^[:cntrl:]]*)?$'
}

# auto garde la compatibilité des anciens fichiers de réponses (clé présente => SSH par clé).
appliance_ssh_mode() {
    case "$1" in
        ''|auto) if [ -n "$2" ]; then echo key; else echo off; fi ;;
        off|password|key) echo "$1" ;;
        *) return 1 ;;
    esac
}

# Configuration partagée par l'ISO (racine montée) et setup-loco (racine vide).
# Le mot de passe SSH est celui du compte root ; aucun second secret n'est enregistré.
appliance_write_ssh() {
    ssh_root=$1 ssh_mode=$2 ssh_key=${3:-}
    case "$ssh_mode" in
        off|password) ;;
        key) appliance_ssh_key_ok "$ssh_key" || return 1 ;;
        *) return 1 ;;
    esac
    install -d "$ssh_root/etc/ssh/sshd_config.d" "$ssh_root/etc/runlevels/default"
    case "$ssh_mode" in
        password) ssh_policy='PermitRootLogin yes
PasswordAuthentication yes' ;;
        key) ssh_policy='PermitRootLogin prohibit-password
PasswordAuthentication no' ;;
        off) ssh_policy='PermitRootLogin no
PasswordAuthentication no' ;;
    esac
    printf '%s\nPermitEmptyPasswords no\nKbdInteractiveAuthentication no\n' "$ssh_policy" \
        > "$ssh_root/etc/ssh/sshd_config.d/loco.conf"
    if ! grep -q '^Include /etc/ssh/sshd_config.d/' "$ssh_root/etc/ssh/sshd_config"; then
        sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' "$ssh_root/etc/ssh/sshd_config"
    fi
    if [ "$ssh_mode" = key ]; then
        install -d -m 700 "$ssh_root/root/.ssh"
        touch "$ssh_root/root/.ssh/authorized_keys"
        chmod 600 "$ssh_root/root/.ssh/authorized_keys"
        grep -qxF "$ssh_key" "$ssh_root/root/.ssh/authorized_keys" || \
            printf '%s\n' "$ssh_key" >> "$ssh_root/root/.ssh/authorized_keys"
    fi
    if [ "$ssh_mode" = off ]; then
        rm -f "$ssh_root/etc/runlevels/default/sshd"
    else
        ln -sf /etc/init.d/sshd "$ssh_root/etc/runlevels/default/sshd"
    fi
}

# Interfaces Ethernet (sans lo ni interfaces virtuelles), celles reliées en premier.
appliance_interfaces() {
    for path in /sys/class/net/*; do
        name=${path##*/}
        [ "$name" = lo ] && continue
        [ -e "$path/device" ] || continue
        if [ "$(cat "$path/carrier" 2>/dev/null)" = 1 ]; then echo "1 $name"; else echo "0 $name"; fi
    done | sort -r -k1,1 -s | cut -d' ' -f2
}

# Adresse IPv4 actuelle d'une interface (192.168.1.10/24) et passerelle par défaut.
appliance_current_cidr() {
    ip -4 -o addr show dev "$1" scope global 2>/dev/null | awk '{print $4; exit}'
}

appliance_current_gateway() {
    ip -4 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "via") {print $(i + 1); exit}}'
}

appliance_current_dns() {
    awk '$1 == "nameserver" {printf "%s%s", sep, $2; sep = " "} END {print ""}' /etc/resolv.conf 2>/dev/null
}

# appliance_write_network RACINE INTERFACE dhcp|static [ADRESSE/PRÉFIXE PASSERELLE "DNS…"]
appliance_write_network() {
    root=$1 iface=$2 mode=$3 cidr=${4:-} gw=${5:-} dns=${6:-}
    mkdir -p "$root/etc/network"
    {
        printf 'auto lo\niface lo inet loopback\n\nauto %s\n' "$iface"
        if [ "$mode" = dhcp ]; then
            printf 'iface %s inet dhcp\n' "$iface"
        else
            printf 'iface %s inet static\n\taddress %s\n' "$iface" "$cidr"
            if [ -n "$gw" ]; then printf '\tgateway %s\n' "$gw"; fi
        fi
    } > "$root/etc/network/interfaces"
    if [ "$mode" = static ] && [ -n "$dns" ]; then
        : > "$root/etc/resolv.conf"
        for d in $dns; do printf 'nameserver %s\n' "$d" >> "$root/etc/resolv.conf"; done
    fi
}

# Bannière de la console (/etc/issue) : adresse du portail et empreinte de l'autorité HTTPS à vérifier.
appliance_issue() {
    host="" port=8443
    if [ -f /etc/loco/loco.conf ]; then
        host=$(sed -n 's/^HOST=//p' /etc/loco/loco.conf)
        port=$(sed -n 's/^HTTPS_PORT=//p' /etc/loco/loco.conf)
    fi
    version=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' /opt/loco/portal/loco/config.py 2>/dev/null)
    fingerprint=$(openssl x509 -in /etc/loco/tls/ca.crt -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)
    current=""
    for iface in $(appliance_interfaces); do
        current=$(appliance_current_cidr "$iface")
        [ -n "$current" ] && break
    done
    {
        printf '\nloco %s, appliance Alpine Linux %s (\\n, \\l)\n\n' "${version:-?}" "$(cat /etc/alpine-release 2>/dev/null)"
        if [ -n "$host" ] && [ -n "$fingerprint" ]; then
            printf '  %-25s https://%s:%s\n' "$(t 'Portail' 'Portal' 'Portal')" "$host" "$port"
            printf '  %-25s %s\n' "$(t 'Autorité HTTPS (SHA-256)' 'HTTPS authority (SHA-256)' 'Autoridad HTTPS (SHA-256)')" "$fingerprint"
            if [ -n "$current" ] && [ "${current%/*}" != "$host" ]; then
                printf '\n  ! %s\n' "$(t "Adresse actuelle ${current%/*} différente de $host : lancez setup-loco." "Current address ${current%/*} differs from $host: run setup-loco." "Dirección actual ${current%/*} distinta de $host: ejecute setup-loco.")"
            fi
        elif [ -f /etc/init.d/loco-firstboot ]; then
            printf '  %s\n' "$(t 'Installation de loco en cours ou interrompue : journal /var/log/loco-firstboot.log' 'loco installation in progress or interrupted: log /var/log/loco-firstboot.log' 'instalación de loco en curso o interrumpida: registro /var/log/loco-firstboot.log')"
        fi
        printf '\n  %s\n\n' "$(t 'Console (root) : setup-loco (réseau, mots de passe), loco-update, loco-config' 'Console (root): setup-loco (network, passwords), loco-update, loco-config' 'Consola (root): setup-loco (red, contraseñas), loco-update, loco-config')"
    } > /etc/issue
}
