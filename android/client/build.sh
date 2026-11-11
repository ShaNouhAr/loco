#!/bin/bash
# Construit l'APK de debug de l'agent Android loco.
# Prérequis : SDK Android (ANDROID_SDK_ROOT, build-tools + platforms;android-34) et Java 17+.
# usage : build.sh [DOSSIER_SORTIE]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$HERE/out}
# Version = celle de loco (l'agent Android évolue avec loco). Lue depuis config.py si présent, sinon argument.
VERSION=${2:-${LOCO_VERSION:-}}
if [ -z "$VERSION" ]; then
    for f in "$HERE/../../portal/loco/config.py" "$HERE/config-version.txt"; do
        [ -f "$f" ] && VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$f" | head -1) && [ -n "$VERSION" ] && break
    done
fi
VERSION=${VERSION:-0.0.1}
: "${ANDROID_SDK_ROOT:=$HOME/android-sdk}"
export ANDROID_SDK_ROOT ANDROID_HOME="$ANDROID_SDK_ROOT"

GRADLE_VERSION=8.9
GRADLE_HOME=$HOME/gradle-$GRADLE_VERSION
if [ ! -x "$GRADLE_HOME/bin/gradle" ]; then
    echo "téléchargement de Gradle $GRADLE_VERSION..."
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/g.zip" "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
    unzip -q "$tmp/g.zip" -d "$HOME"
    rm -rf "$tmp"
fi

# Mode de build : release signé (par défaut si un keystore est présent), sinon debug (essais).
MODE=${3:-${LOCO_BUILD_MODE:-auto}}
export LOCO_KEYSTORE_DIR=${LOCO_KEYSTORE_DIR:-$HOME/android-keystore}
if [ "$MODE" = auto ]; then
    [ -f "$LOCO_KEYSTORE_DIR/keystore.properties" ] && MODE=release || MODE=debug
fi

cd "$HERE"
echo "version de l'agent Android : $VERSION ($MODE)"
# Enregistrement auprès de Google (vérification des développeurs Android) : le jeton donné par l'Android Developer
# Console pour prouver la possession de la clé est embarqué dans l'APK, dans assets/adi-registration.properties. Il
# accompagne la clé de signature (même dossier) : présent, il entre dans chaque APK de release ; absent, rien ne change.
ADI="$HERE/app/src/main/assets/adi-registration.properties"
rm -f "$ADI"
if [ "$MODE" = release ] && [ -f "$LOCO_KEYSTORE_DIR/adi-registration.properties" ]; then
    cp "$LOCO_KEYSTORE_DIR/adi-registration.properties" "$ADI"
    trap 'rm -f "$ADI"' EXIT
    echo "jeton d'enregistrement Google embarqué (assets/adi-registration.properties)"
fi
mkdir -p "$OUT"
if [ "$MODE" = release ]; then
    "$GRADLE_HOME/bin/gradle" --no-daemon --console=plain -PappVersion="$VERSION" assembleRelease
    SRC=app/build/outputs/apk/release/app-release.apk
    [ -f "$SRC" ] || SRC=app/build/outputs/apk/release/app-release-unsigned.apk
    DST="$OUT/loco-agent-$VERSION.apk"
else
    "$GRADLE_HOME/bin/gradle" --no-daemon --console=plain -PappVersion="$VERSION" assembleDebug
    SRC=app/build/outputs/apk/debug/app-debug.apk
    DST="$OUT/loco-agent.apk"
fi
cp "$SRC" "$DST"
# Vérifie la signature si l'outil est là (ne bloque pas un build debug local).
APKSIGNER=$(find "$ANDROID_SDK_ROOT/build-tools" -name apksigner 2>/dev/null | sort | tail -1 || true)
if [ -n "$APKSIGNER" ] && [ "$MODE" = release ]; then
    "$APKSIGNER" verify --print-certs "$DST" | grep -i "SHA-256" | head -1 || { echo "APK non signé !" >&2; exit 1; }
fi
echo "APK : $DST ($(wc -c < "$DST") octets)"
