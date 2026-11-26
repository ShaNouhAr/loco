**English** · [Français](README.fr.md) · [Español](README.es.md)

# grim

`grim-amd64` and `grim-arm64` are the [grim](https://sr.ht/~emersion/grim/) binary
(version **1.3.1+ds-1**, extracted from the Debian packages `grim_1.3.1+ds-1_amd64.deb` and
`grim_1.3.1+ds-1_arm64.deb`), under the **MIT** license. grim captures the screen of wlroots
Wayland compositors (Sway, Hyprland, Wayfire, river…).

loco **embeds them in the agent's `.deb` package** (`agentpkg.build_deb`, installed under
`/usr/share/loco-client/`) so that the remote desktop of these compositors works **even on a
device without access to a repository that provides "grim"** (offline installation). `grim` is therefore
no longer a dependency of the package: the agent uses the system's `grim` if it is present, otherwise the
embedded binary matching its architecture (`portal/agents/loco-client.py`, `_grim_bin`).

These binaries only require **GLIBC 2.17** (compatible with Debian 11/12/13, Ubuntu 20.04+) and
rely at run time on `libwayland-client` and `libcairo`, present on any wlroots device.

sha256:

```
63887a37ae50c85a4c868b8dd0d0844b51688d849f4b561afce0a436d3007d75  grim-amd64
ae91bf8c2ca6e473d09a1e46ac85b8c6445fc7db3524c4d7c8e549b2cb7da1c2  grim-arm64
```
