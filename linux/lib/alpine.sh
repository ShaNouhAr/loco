#!/bin/bash
# Adaptation native Alpine Linux 3.24+ (appliance ISO), sourcée par install.sh (mêmes réglages et portail).
# Pas de systemd : OpenRC lance supervisord (portail, nginx, tâches périodiques) et l'agent root loco-agent
# (mises à jour et réglages demandés depuis le portail, voir portal/locoadmin.py).
# Pas d'apt-cacher-ng sur Alpine : le cache APT est servi par nginx (même gabarit que sous Windows),
# les index APT/RPM sont produits en Python (debpkg, rpmpkg).

ALPINE_PACKAGES=(bash coreutils findutils grep sed tar procps iproute2 util-linux runuser shadow
                 nginx nginx-mod-stream python3 py3-flask py3-gunicorn py3-cryptography py3-zstandard gnupg
                 curl openssl ca-certificates supervisor squid logrotate tzdata)

alpine_supported() {
    local major minor
    major=${VERSION_ID%%.*}
    minor=${VERSION_ID#*.}; minor=${minor%%.*}
    [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 24 ]; }
}

alpine_packages() {
    local p wanted=("${ALPINE_PACKAGES[@]}") missing=()
    if [ "$FLATPAK_ENABLED" = 1 ]; then wanted+=(flatpak ostree); fi
    touch "$PACKAGE_RECORD"
    for p in "${wanted[@]}"; do
        if ! apk info -e "$p" >/dev/null 2>&1; then
            missing+=("$p")
            grep -qx "$p" "$PACKAGE_RECORD" || echo "$p" >> "$PACKAGE_RECORD"
        fi
    done
    : > "$LOG"
    # Appliance (ISO) : tout est déjà installé depuis le média, éventuellement sans accès à Internet.
    if [ ${#missing[@]} -gt 0 ] && ! apk add --no-progress "${wanted[@]}" >> "$LOG" 2>&1; then
        tail -15 "$LOG" >&2
        die "installation des paquets impossible (journal complet : $LOG)."
    fi
    # nginx et le portail sont lancés par supervisord : le service nginx d'Alpine resterait en conflit.
    rc-update del nginx default >/dev/null 2>&1 || true
    rc-service nginx stop >/dev/null 2>&1 || true
}

alpine_user() {
    if ! getent group loco >/dev/null; then addgroup -S loco; fi
    if ! id loco >/dev/null 2>&1; then adduser -S -D -H -h "$STATE" -s /sbin/nologin -G loco loco; fi
    # Le portail lit le journal du cache Windows Update écrit par nginx.
    addgroup loco nginx >/dev/null 2>&1 || true
}

# Résolveurs du système (DHCP ou fixes) : utilisés par le cache APT pour joindre les dépôts, y compris internes.
alpine_dns() {
    local dns
    dns=$(awk '$1 == "nameserver" && $2 !~ /:/ {print $2}' /etc/resolv.conf 2>/dev/null | head -3 | xargs)
    echo "${dns:-$RESOLVERS}"
}

alpine_nginx() {
    # Disposition commune (sites-available/enabled, snippets) incluse depuis les dossiers d'Alpine.
    echo 'include /etc/nginx/sites-enabled/*.conf;' > /etc/nginx/http.d/loco.conf
    # Relais Windows Update : Alpine définit déjà le bloc stream (conf.d/stream.conf, qui inclut stream.d/).
    install -d /etc/nginx/stream.d
    if [ -f /etc/nginx/modules-enabled/99-loco-stream.conf ]; then
        sed '1,/^stream {/d; $d' /etc/nginx/modules-enabled/99-loco-stream.conf > /etc/nginx/stream.d/loco-wu.conf
    else
        rm -f /etc/nginx/stream.d/loco-wu.conf
    fi
    # Site d'accueil d'Alpine : réserve default_server:80, inutile sur l'appliance.
    if [ -f /etc/nginx/http.d/default.conf ]; then
        mv /etc/nginx/http.d/default.conf /etc/nginx/http.d/default.conf.loco-disabled
    fi
    local self
    # shellcheck disable=SC2153 # HOST vient de linux/install.sh.
    self=$(printf '%s\n' localhost 127.0.0.1 "$HOST" "$SHORT_HOSTNAME" | tr '[:upper:]' '[:lower:]' \
        | awk 'NF && !seen[$0]++ {out = out (out ? "\n    " : "") $0 " 1;"} END {print out}')
    LRT_APT_SELF_HOSTS="$self" LRT_APT_CACHE_SIZE="$APT_CACHE_SIZE" LRT_ACNG_PORT=3142 \
    LRT_LOGDIR=/var/log/nginx LRT_DNS="$(alpine_dns)" \
        render "$PREFIX/conf/nginx-apt-proxy.conf" > /etc/nginx/sites-available/loco-apt.conf
    ln -sf ../sites-available/loco-apt.conf /etc/nginx/sites-enabled/loco-apt.conf
    install -d -m 750 -o "$NGINX_USER" -g "$NGINX_USER" "$DATA/aptcache"
    install -d -m 755 -o "$NGINX_USER" -g "$NGINX_USER" /run/nginx
}

alpine_services() {
    local proxy_env="" sup_proxy
    if [ -n "$UPSTREAM_PROXY" ]; then
        sup_proxy=${UPSTREAM_PROXY//%/%%}   # « % » est spécial pour supervisord (identifiants encodés)
        proxy_env=",http_proxy=\"$sup_proxy\",https_proxy=\"$sup_proxy\",no_proxy=\"localhost,127.0.0.1,$HOST\""
    fi
    install -d -m 755 /etc/supervisor /var/log/loco /var/lib/logrotate
    LRT_SUP_PROXY_ENV="$proxy_env" LRT_SUP_PASSWORD=$(openssl rand -hex 24) \
        render "$SRC/conf/supervisord-alpine.conf" > /etc/supervisor/loco.conf
    chmod 600 /etc/supervisor/loco.conf
    install -m 755 "$SRC/conf/loco-cron" /usr/local/sbin/loco-cron
    install -m 644 "$SRC/conf/loco-logrotate.conf" /etc/loco-logrotate.conf
    install -m 755 "$SRC/conf/loco.initd" /etc/init.d/loco
    install -m 755 "$SRC/conf/loco-agent.initd" /etc/init.d/loco-agent
    ln -sf "$PREFIX/bin/loco-update" /usr/local/sbin/loco-update
    ln -sf "$PREFIX/bin/loco-config" /usr/local/sbin/loco-config
    rc-update add loco default >/dev/null
    rc-update add loco-agent default >/dev/null
    rc-service loco restart >/dev/null 2>&1 || rc-service loco start >/dev/null \
        || die "le service loco ne démarre pas (/var/log/loco/)."
    # Lancé depuis l'agent (mise à jour depuis le portail), l'agent se relance seul quand son code change.
    rc-service loco-agent status >/dev/null 2>&1 || rc-service loco-agent start >/dev/null 2>&1 || die "le service loco-agent ne démarre pas (/var/log/loco/agent.log)."
    # Appliance installée depuis l'ISO : console (setup-loco) et bannière avec l'adresse et l'empreinte.
    if [ -f "$ETC/appliance" ]; then
        ln -sf "$PREFIX/bin/setup-loco" /usr/local/sbin/setup-loco
        install -d /etc/local.d
        install -m 755 "$SRC/conf/loco-issue.start" /etc/local.d/loco-issue.start
        rc-update add local default >/dev/null 2>&1 || true
        # shellcheck source=linux/lib/appliance.sh
        (. "$SRC/lib/appliance.sh" && appliance_issue)
    fi
}

alpine_check() {
    local status
    for _ in $(seq 1 60); do
        curl -fsk --connect-timeout 2 --max-time 5 "https://127.0.0.1:$HTTPS_PORT/healthz" >/dev/null 2>&1 && break
        sleep 0.5
    done
    curl -fsk --connect-timeout 2 --max-time 5 "https://127.0.0.1:$HTTPS_PORT/healthz" >/dev/null \
        || die "le portail ne répond pas (journaux : /var/log/loco/)."
    # Cache APT nginx : refuse ce qui n'est pas un fichier de dépôt.
    status=$(curl -s --connect-timeout 2 --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3142/) || true
    [ "$status" = 403 ] || die "le cache APT ne répond pas correctement (HTTP $status)."
}

alpine_remove() {
    local s
    for s in loco-agent loco; do
        rc-service "$s" stop >/dev/null 2>&1 || true
        rc-update del "$s" default >/dev/null 2>&1 || true
    done
    rm -f /etc/init.d/loco /etc/init.d/loco-agent /etc/supervisor/loco.conf /usr/local/sbin/loco-cron \
          /usr/local/sbin/loco-update /usr/local/sbin/loco-config /usr/local/sbin/setup-loco \
          /etc/local.d/loco-issue.start /etc/loco-logrotate.conf \
          /etc/nginx/http.d/loco.conf /etc/nginx/stream.d/loco-wu.conf
    rm -rf /var/lib/loco-agent
    if [ -f /etc/nginx/http.d/default.conf.loco-disabled ]; then
        mv /etc/nginx/http.d/default.conf.loco-disabled /etc/nginx/http.d/default.conf
    fi
}
