# loco en conteneur : voir docker-compose.yml (même dossier) et la section Docker du README.
# Contexte de construction : la racine du projet. La configuration est faite au démarrage par
# linux/install.sh --container (linux/docker/entrypoint.sh).
FROM debian:13-slim
LABEL org.opencontainers.image.title="loco" \
      org.opencontainers.image.source="https://github.com/ShaNouhAr/loco" \
      org.opencontainers.image.licenses="MIT"

ARG DEBIAN_FRONTEND=noninteractive
# Comptes créés avant les paquets : identifiants fixes, les volumes restent valides d'une image à l'autre.
RUN groupadd --system --gid 990 loco \
 && useradd --system --uid 990 --gid 990 --groups adm --home-dir /var/lib/loco --shell /usr/sbin/nologin loco \
 && groupadd --system --gid 991 apt-cacher-ng \
 && useradd --system --uid 991 --gid 991 --home-dir /var/cache/apt-cacher-ng --shell /usr/sbin/nologin apt-cacher-ng \
 && printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d \
 && echo "apt-cacher-ng apt-cacher-ng/tunnelenable boolean false" | debconf-set-selections \
 && apt-get update -q \
 && apt-get install -y -q --no-install-recommends \
        nginx libnginx-mod-stream apt-cacher-ng apt-utils gnupg python3 python3-flask python3-cryptography gunicorn \
        curl openssl ca-certificates flatpak ostree createrepo-c rpm \
        supervisor logrotate iproute2 procps tzdata \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default /usr/sbin/policy-rc.d

COPY . /usr/src/loco
RUN install -m 755 /usr/src/loco/linux/docker/entrypoint.sh /usr/local/sbin/loco-entrypoint \
 && install -m 755 /usr/src/loco/linux/conf/loco-cron /usr/local/sbin/loco-cron \
 && install -m 644 /usr/src/loco/linux/conf/loco-logrotate.conf /etc/loco-logrotate.conf \
 && install -d /var/lib/logrotate

ENV LOCO_RUNTIME=container
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=3m --retries=3 \
    CMD . /etc/loco/loco.conf && curl -fsk --max-time 8 "https://127.0.0.1:$HTTPS_PORT/healthz" >/dev/null || exit 1
ENTRYPOINT ["/usr/local/sbin/loco-entrypoint"]
