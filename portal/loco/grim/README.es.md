[English](README.md) · [Français](README.fr.md) · **Español**

# grim

`grim-amd64` y `grim-arm64` son el binario de [grim](https://sr.ht/~emersion/grim/)
(versión **1.3.1+ds-1**, extraído de los paquetes de Debian `grim_1.3.1+ds-1_amd64.deb` y
`grim_1.3.1+ds-1_arm64.deb`), con licencia **MIT**. grim captura la pantalla de los compositores
Wayland wlroots (Sway, Hyprland, Wayfire, river…).

loco los **incluye en el paquete `.deb`** del agente (`agentpkg.build_deb`, instalados en
`/usr/share/loco-client/`) para que el escritorio remoto de estos compositores funcione **incluso en un
equipo sin acceso a un repositorio que proporcione «grim»** (instalación sin conexión). `grim` ya no es
por tanto una dependencia del paquete: el agente usa el `grim` del sistema si está presente; si no, el
binario incluido que corresponde a su arquitectura (`portal/agents/loco-client.py`, `_grim_bin`).

Estos binarios solo exigen **GLIBC 2.17** (compatibles con Debian 11/12/13, Ubuntu 20.04+) y
se apoyan en tiempo de ejecución en `libwayland-client` y `libcairo`, presentes en cualquier equipo wlroots.

sha256:

```
63887a37ae50c85a4c868b8dd0d0844b51688d849f4b561afce0a436d3007d75  grim-amd64
ae91bf8c2ca6e473d09a1e46ac85b8c6445fc7db3524c4d7c8e549b2cb7da1c2  grim-arm64
```
