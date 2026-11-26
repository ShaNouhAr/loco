[English](../connecting-devices.md) · [Français](../fr/connecting-devices.md) · **Español**

# Conectar los equipos

Repositorios, cachés y software: un comando por equipo, o una directiva de grupo para todo el dominio. Para gestionar los propios equipos (inventario, actualizaciones, control remoto), vea [Mi parque](fleet.md).

En **Linux › Paquetes**, cree primero los repositorios necesarios: distribución, número de versión
opcional y nombre en clave (por ejemplo `debian/trixie` o `ubuntu/noble`). Una futura versión se añade
desde esta página, sin modificar el código. Cada repositorio tiene sus propios archivos e índices firmados.

Los equipos confían en la autoridad local (`ca.crt`, desplegada por GPO o desde la página
*Administración › Sistema*), y luego:

```bash
# Debian / Ubuntu: repositorio local + caché APT
curl -fsSL https://<ip>:8443/linux/setup.sh | sudo sh
# … primera conexión, autoridad aún no importada (muestra su huella)
curl -fsSL http://<ip>:8088/linux/setup.sh | sudo sh -s -- --trust-ca
# Solo la caché, sin repositorio local
curl -fsSL https://<ip>:8443/linux/setup.sh | sudo sh -s -- --cache-only

# Rocky Linux, AlmaLinux, RHEL 8+: repositorio local elN + repositorios oficiales a través de la caché (--epel: también EPEL)
curl -fsSL https://<ip>:8443/rpm/setup.sh | sudo sh

# Flatpak: Flathub a través de la caché + repositorio local «loco»
curl -fsSL https://<ip>:8443/flatpak/setup.sh | sudo sh
```

```powershell
# Windows, PowerShell de administrador: instala uno o varios programas del catálogo
irm 'https://<ip>:8443/win/i/7-zip,vlc' | iex
```

El script de Windows comprueba el SHA-256 de cada instalador, concede 30 minutos a cada uno (`$env:LOCO_TIMEOUT`)
y reconoce los códigos «reinicio necesario», «ya instalado» y «no aplicable».

Para Windows Update, hay dos formas de enviar las descargas de los equipos a loco, a elegir en la página
*Windows Update*: **por DNS** (el DNS de la red devuelve la IP del servidor para los dominios de actualización; se proporciona
un script para un DNS de Active Directory) o **por proxy, sin DNS** — loco incluye un proxy-caché (Squid) y
el agente ajusta el proxy WinHTTP de los equipos Windows gestionados hacia loco (por equipo, reversible, retirado si loco deja
de estar accesible). Ambos guardan en caché los mismos archivos inmutables y retransmiten el HTTPS sin descifrarlo. También se propone
el comando manual `netsh winhttp set proxy` (páginas *Windows Update* e *Inscripción*).

El script de Linux lee `/etc/os-release` y selecciona el repositorio que corresponde al equipo. Si ese repositorio
no existe, se detiene antes de sustituir la configuración. Las derivadas pueden elegir explícitamente
`--distribution ubuntu --suite noble`. Las fuentes APT usan el formato deb822 (`loco.sources`).

**Actualización desde 1.0.0**: el antiguo repositorio `/apt/` y los equipos ya conectados siguen funcionando.
La acción *Copiar a otro repositorio* conserva los originales. Compruebe la compatibilidad de cada paquete
y vuelva a lanzar el script en los equipos. `--legacy` permite conservar explícitamente el antiguo repositorio compartido.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
