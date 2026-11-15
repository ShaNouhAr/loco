#!/bin/bash
# Installation / mise à jour Docker : construit l'image, démarre loco et attend qu'il soit sain.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose build
docker compose up -d
for ((i=0; i<120; i++)); do
    id=$(docker compose ps -q loco)
    if [ -n "$id" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$id")" = healthy ]; then
        echo 'loco prêt.'
        exit 0
    fi
    sleep 2
done
echo 'loco ne devient pas sain : consulter docker compose logs.' >&2
exit 1
