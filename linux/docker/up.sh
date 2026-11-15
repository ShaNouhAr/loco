#!/bin/bash
# Installation / mise à jour Docker depuis les sources : construit l'image, démarre loco et attend qu'il soit sain.
# Sans les sources, docker-compose.yml suffit : docker compose up -d télécharge l'image publiée.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
dc() { docker compose -f docker-compose.yml -f docker-compose.build.yml "$@"; }
dc build
dc up -d
for ((i=0; i<120; i++)); do
    id=$(dc ps -q loco)
    if [ -n "$id" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$id")" = healthy ]; then
        echo 'loco prêt.'
        exit 0
    fi
    sleep 2
done
echo 'loco ne devient pas sain : consulter docker compose logs.' >&2
exit 1
