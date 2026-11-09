# grim

`grim-amd64` et `grim-arm64` sont le binaire de [grim](https://sr.ht/~emersion/grim/)
(version **1.3.1+ds-1**, extrait des paquets Debian `grim_1.3.1+ds-1_amd64.deb` et
`grim_1.3.1+ds-1_arm64.deb`), sous licence **MIT**. grim capture l'écran des compositeurs
Wayland wlroots (Sway, Hyprland, Wayfire, river…).

loco les **embarque dans le paquet `.deb`** de l'agent (`agentpkg.build_deb`, installés sous
`/usr/share/loco-client/`) pour que le bureau distant de ces compositeurs marche **même sur un
poste sans accès à un dépôt qui fournit « grim »** (installation hors-ligne). `grim` n'est donc
plus une dépendance du paquet : l'agent utilise le `grim` du système s'il est présent, sinon le
binaire embarqué correspondant à son architecture (`portal/agents/loco-client.py`, `_grim_bin`).

Ces binaires n'exigent que **GLIBC 2.17** (compatibles Debian 11/12/13, Ubuntu 20.04+) et
s'appuient à l'exécution sur `libwayland-client` et `libcairo`, présents sur tout poste wlroots.

sha256 :

```
63887a37ae50c85a4c868b8dd0d0844b51688d849f4b561afce0a436d3007d75  grim-amd64
ae91bf8c2ca6e473d09a1e46ac85b8c6445fc7db3524c4d7c8e549b2cb7da1c2  grim-arm64
```
