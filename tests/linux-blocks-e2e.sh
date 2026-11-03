#!/bin/bash
# Conteneur jetable : mises à jour bloquées avec de vrais paquets et le vrai apt ou dnf (image en argument).
set -euo pipefail
cd "$(dirname "$0")/.."
image=$1
case "$image" in
    debian*|ubuntu*) prepare='export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 dpkg-dev' ;;
    *) prepare='dnf -y -q install python3 rpm-build createrepo_c' ;;
esac
sudo docker run --rm -v "$PWD/portal/agents:/a:ro" -v "$PWD/tests:/t:ro" "$image" \
    sh -c "$prepare >/dev/null 2>&1 && python3 -I /t/linux_blocks_e2e.py /a/loco-client.py"
