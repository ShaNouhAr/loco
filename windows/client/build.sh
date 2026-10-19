#!/bin/sh
# Agent Windows loco-client : compilation (Mono, cible .NET Framework 4.x, présent dans Windows) et installeur MSI
# (msitools/wixl), sous Linux. Usage : windows/client/build.sh VERSION DOSSIER_DE_SORTIE
# Paquets Debian : mono-devel wixl. Produit loco-client.exe et loco-client.msi.
set -eu
VERSION=${1:?version attendue, par exemple 2.2.0}
OUT=${2:?dossier de sortie attendu}
HERE=$(cd "$(dirname "$0")" && pwd)
case "$VERSION" in *[!0-9.]*|'') echo "Version invalide : $VERSION" >&2; exit 1 ;; esac
mkdir -p "$OUT"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
printf '[assembly: System.Reflection.AssemblyVersion("%s.0")]\n[assembly: System.Reflection.AssemblyFileVersion("%s.0")]\n' \
    "$VERSION" "$VERSION" > "$WORK/version.cs"
mcs -nologo -target:exe -platform:anycpu -optimize+ -warnaserror+ -sdk:4.6.2 -out:"$WORK/loco-client.exe" \
    -r:System.ServiceProcess.dll -r:System.Management.dll -r:System.Web.Extensions.dll -r:Microsoft.CSharp.dll \
    -r:System.Core.dll "$WORK/version.cs" "$HERE/LocoClient.cs" "$HERE/DesktopIndicator.cs" "$HERE/DesktopStatus.cs" \
    "$HERE/RemoteControl.cs" "$HERE/NativeTerminal.cs"
mcs -nologo -target:winexe -platform:anycpu -optimize+ -warnaserror+ -sdk:4.6.2 -out:"$WORK/loco-tray.exe" \
    -r:System.Windows.Forms.dll -r:System.Drawing.dll -r:System.Web.Extensions.dll \
    "$WORK/version.cs" "$HERE/LocoTray.cs" "$HERE/DesktopStatus.cs"
mcs -nologo -target:winexe -platform:anycpu -optimize+ -warnaserror+ -sdk:4.6.2 -out:"$WORK/loco-desktop.exe" \
    -r:System.Windows.Forms.dll -r:System.Drawing.dll -r:System.Core.dll "$WORK/version.cs" "$HERE/LocoDesktop.cs" "$HERE/DesktopResolution.cs" "$HERE/RfbFramebuffer.cs" "$HERE/DesktopDuplication.cs"
cp "$HERE/loco-client.exe.config" "$WORK/"
cp "$HERE/loco-client.exe.config" "$WORK/loco-tray.exe.config"
cp "$HERE/loco-client.exe.config" "$WORK/loco-desktop.exe.config"
wixl -a x64 -D Version="$VERSION" -D SourceDir="$WORK" -o "$OUT/loco-client.msi" "$HERE/loco-client.wxs"
cp "$WORK/loco-client.exe" "$WORK/loco-tray.exe" "$WORK/loco-desktop.exe" "$OUT/"
echo "$OUT/loco-client.msi ($(wc -c < "$OUT/loco-client.msi") octets)"
