[English](../linux.md) · [Français](../fr/linux.md) · **Español**

# Guía de explotación: servidor Linux

Esta guía cubre el despliegue de loco en una red de empresa (laboratorio, sede, sucursal) y su
explotación diaria. Para una presentación rápida, vea el [README](../../README.es.md).
La rutina, las copias de seguridad, las actualizaciones y los incidentes, para todo tipo de instalación: [Operar loco en el día a día](operations.md).

## 1. Requisitos

| Elemento | Valor |
|---|---|
| Sistema | Debian 12/13, Ubuntu 22.04/24.04/26.04, Rocky Linux 8/9/10 (amd64 verificado, VM recomendada, vea § 14 para LXC) |
| Recursos | 2 vCPU, 2 GB de RAM; disco según las cachés (50 GB o más aconsejados) |
| Permisos | root (instalación), ningún contenedor Docker |
| Salida a Internet | HTTP/HTTPS hacia los repositorios de Debian/Ubuntu, Microsoft (Windows Update), `dl.flathub.org`, réplicas RPM (§ 12); directa o por proxy (§ 5) |

Puertos abiertos en el servidor:

| Puerto | Servicio | Quién accede |
|---|---|---|
| 8443/tcp | Interfaz (HTTPS) + repositorios, instaladores y scripts de los equipos | equipos (`--allow`), administradores (`--admin-allow`) |
| 8088/tcp | Arranque HTTP de los equipos (desactivable: `--no-http`) | equipos |
| 3142/tcp | Caché APT (apt-cacher-ng) | equipos Linux |
| 80/tcp, 443/tcp | Caché de Windows Update (desactivable: `--no-wu`) | equipos Windows (a través del DNS) |

## 2. Instalación

```bash
git clone https://github.com/ShaNouhAr/loco.git
cd loco
sudo ./install.sh --allow "10.20.0.0/16" --admin-allow "10.20.99.0/24"
```

La instalación muestra la dirección de la interfaz, la huella de la autoridad de certificación y una
**contraseña inicial** para la cuenta `admin`, que cambiar en el primer inicio de sesión. No se escribe en ningún lugar
del disco; si se pierde: `sudo loco-user passwd admin`.

Todas las opciones: `sudo ./install.sh --help`. Los ajustes se guardan en
`/etc/loco/loco.conf`; volver a lanzar `install.sh` (con o sin opciones) aplica los cambios
y actualiza el software. La salida detallada de apt está en `/var/log/loco-install.log`.

## 3. Redes autorizadas

- `--allow`: redes que usan los repositorios y cachés (apt-cacher-ng, caché de Windows Update, Flathub,
  repositorios locales, scripts). Por defecto: redes privadas (RFC 1918, ULA IPv6).
- `--admin-allow`: redes que abren la interfaz y `/metrics`. Por defecto: como `--allow`.
  Recomendado: solo la red de administración.

El filtrado lo aplican nginx (todos los puertos web) y el propio apt-cacher-ng (tcpwrappers). El propio servidor (127.0.0.1) siempre está autorizado. Sigue siendo aconsejable un cortafuegos de red por delante.

## 4. Autoridad de certificación y HTTPS

La instalación crea una autoridad local (`/etc/loco/tls/ca.crt`, 10 años) que firma el certificado
del servidor (825 días, renovado por `install.sh` si caduca en menos de 30 días o si la IP cambia).
Los equipos deben confiar en esta autoridad para usar HTTPS.

**Por GPO (recomendado en un dominio)**: Administración de directivas de grupo › Configuración del equipo ›
Directivas › Configuración de Windows › Configuración de seguridad › Directivas de clave pública ›
Entidades de certificación raíz de confianza › Importar `ca.crt`.

**A mano**: comandos de la página *Administración › Sistema* (Windows y Linux). Compruebe la huella
SHA-256 que muestra la instalación antes de importar.

**Linux, primera conexión sin la autoridad**: `curl -fsSL http://SERVIDOR:8088/linux/setup.sh | sudo sh -s -- --trust-ca`
importa la autoridad (huella mostrada, que comparar) y luego configura el repositorio en HTTPS.

**Certificado de empresa**: `sudo ./install.sh --tls-cert servidor.crt --tls-key servidor.key`
(volver a la autoridad local: eliminar `/etc/loco/tls/custom` y volver a lanzarlo).

Una vez desplegada la autoridad en todas partes, `--no-http` cierra el puerto 8088.

## 5. Proxy de empresa

```bash
sudo ./install.sh --proxy http://proxy.empresa:3128
```

apt-cacher-ng y las descargas del portal (programas Windows, paquetes por enlace) pasan por el proxy.
La **caché de Windows Update** y la **caché de Flathub** salen en directo: autorice al servidor a llegar a
`*.windowsupdate.com`, `*.dl.delivery.mp.microsoft.com` y `dl.flathub.org` en los puertos 80 y 443.

La caché de Windows Update resuelve los nombres con `--resolvers` (por defecto 1.1.1.1 y 9.9.9.9). Si el DNS público
está bloqueado, indique resolutores internos **que no redirijan** los dominios de Windows Update a este
servidor (por ejemplo los reenviadores de Active Directory); si no, la caché se consultaría a sí misma.

## 6. Windows Update

El DNS de la red debe responder la IP del servidor para `windowsupdate.com` y `dl.delivery.mp.microsoft.com`
(subdominios incluidos). Para un DNS de Active Directory, la página *Windows Update* proporciona `loco-dns.ps1`
que ejecutar en un controlador de dominio (`-Remove` para volver atrás).

Solo se retransmiten estos dominios; solo los archivos inmutables (nombre que contiene una huella o un identificador
único) se guardan en caché, y las listas de certificados y los metadatos siempre pasan por Microsoft.

## 7. Cuentas y auditoría

- Perfiles incluidos: **Administrador** (todo), **Operador** (inventario, actualizaciones, despliegues; sin terminal,
  escritorio ni reinicio) y **Solo lectura**; se crean otros en *Administración › Perfiles de acceso*
  (vea [Cuentas, perfiles y SSO](accounts.md)).
- Creación en *Administración › Cuentas*: se muestra una contraseña temporal una sola vez y debe
  cambiarse en el primer inicio de sesión (12 caracteres como mínimo, sin el nombre de la cuenta).
- Una sesión inactiva 8 h se cierra; cambiar la contraseña cierra las demás sesiones de la cuenta.
- Tras 5 fallos en una cuenta (o 20 desde una dirección) en 15 min, el inicio de sesión queda bloqueado 15 min.
- *Administración › Diario*: inicios de sesión, fallos, modificaciones, con cuenta y dirección; también en
  journald (`journalctl -u loco-portal`).

En línea de comandos (root): `loco-user list | roles | add NOMBRE [PERFIL] | set-role NOMBRE PERFIL | passwd NOMBRE | enable NOMBRE | disable NOMBRE`.

## 8. Copia de seguridad y restauración

Cada día (03:30) se escribe una copia de seguridad en `/var/backups/loco`; se conservan las 3 más
recientes (`--backup-keep`, `--backup-dir`). Contiene configuración, cuentas, diario, claves (autoridad,
GPG), repositorio APT, catálogo de Windows y repositorio Flatpak, **sin las cachés**. Los archivos contienen claves
privadas: cópielos a un almacenamiento protegido.

```bash
sudo loco-backup create            # bajo demanda
sudo loco-backup list
```

**Restauración (mismo servidor o servidor nuevo)**:

```bash
sudo ./install.sh                        # en el servidor nuevo, el mismo --data que antes
sudo loco-backup restore loco-ANTIGUO-20261013-033000.tar.gz
sudo ./install.sh                        # regenera los servicios y el certificado del servidor para la nueva IP
```

La configuración propia del servidor (IP, puertos, redes) se conserva; la autoridad se restaura, así que los
equipos no tienen nada que rehacer. Recuerde actualizar el DNS (Windows Update) si la IP cambia.

## 9. Actualización, supervisión, desinstalación

- **Actualización**: `git pull && sudo ./install.sh`. Las migraciones de la base de datos son automáticas.
  Antes de una actualización mayor del sistema o de la aplicación, cree una copia de seguridad y valide
  el destino en una VM de prueba. El soporte de una versión futura debe comprobarse con las pruebas.
- **Sistema del servidor** (núcleo, OpenSSL, Squid, nginx, Python…): **Administración → Actualizaciones** lista los
  paquetes pendientes, con los parches de seguridad primero (repositorio `*-security` de Debian/Ubuntu, avisos de seguridad DNF de
  Rocky), con el núcleo en uso y el instalado, los motivos de un reinicio y los servicios que todavía usan
  bibliotecas antiguas. *Instalar los parches de seguridad* o *Actualizar todo*, y luego *Reiniciar el
  servidor* si hace falta. Un agente root separado (`loco-agent`) vuelve a validar y ejecuta las peticiones: el portal sigue sin
  privilegios. Los archivos de configuración modificados por loco se conservan (`--force-confold`) y los servicios de
  loco afectados se reinician. Comprobación automática cada día. En consola: `loco-update status|check|security|
  system|reboot`. Permisos: *Actualizar el servidor*, *Reiniciar el servidor*. En un contenedor LXC, el núcleo es
  el del anfitrión (que actualizar en Proxmox).
- **Supervisión**: `https://SERVIDOR:8443/metrics` (formato Prometheus, redes `--admin-allow`): estado de los
  servicios, tamaños de las cachés, disco, días antes de que caduque el certificado, fecha de la última copia de seguridad.
  Comprobación sencilla: `https://SERVIDOR:8443/healthz` → `ok`.
- **Desinstalación**: `sudo /opt/loco/remove.sh` (conserva los datos) o `--purge` (lo borra todo salvo
  las copias de seguridad). Solo se retiran los paquetes instalados por loco.

## 10. Solución de problemas

| Síntoma | Pista |
|---|---|
| La instalación se detiene | El mensaje indica la línea en cuestión; detalles de apt: `/var/log/loco-install.log` |
| «el puerto N ya está en uso» | Liberar el puerto o cambiar `--https-port` / `--http-port`; `--no-wu` libera 80/443 |
| Interfaz: 403 | La dirección del equipo no está en `--admin-allow` |
| Equipo Linux: error de certificado | Autoridad no importada: `setup.sh … --trust-ca` o GPO |
| Windows: `irm` falla en HTTPS | Falta la autoridad en el almacén «Entidades de certificación raíz de confianza» de la máquina |
| Programa Windows bloqueado | Sin argumento silencioso: indíquelo (a menudo `/S`); plazo: `$env:LOCO_TIMEOUT` |
| Equipo Red Hat: «Repositorio elN no disponible» | Crear la versión en *Linux › Paquetes*, o `--no-local` |
| Equipo Red Hat: paquete no encontrado tras `--remove` | Normal: se restablecen los repositorios de origen; `dnf makecache` |
| Flatpak en Ubuntu 24.04 **en contenedor LXC**: «Couldn't connect» | El perfil AppArmor `flatpak` de Ubuntu bloquea la red con un núcleo Proxmox: `ln -s /etc/apparmor.d/flatpak /etc/apparmor.d/disable/ && apparmor_parser -R /etc/apparmor.d/flatpak`, o usar una VM |
| Registros | `journalctl -u loco-portal -u nginx -u apt-cacher-ng`, `/var/log/nginx/loco-*.log` |

## 11. Repositorios APT y nuevas versiones de Linux

En **Linux › Paquetes**, añada un repositorio con su distribución (`debian` o `ubuntu`), su **nombre en clave
exacto** y, opcionalmente, el número de versión mostrado. Por ejemplo `debian/trixie` o
`ubuntu/noble`. El repositorio empieza vacío; suba los `.deb` compatibles al repositorio seleccionado.
Los índices firmados y los archivos son independientes bajo `/srv/loco/apt/repositories/DISTRIBUCIÓN/SUITE/`.
Las arquitecturas son las de los paquetes (`amd64`, `arm64`, `all`…) y APT elige las del equipo.

Para añadir una próxima versión, basta con el mismo procedimiento: la aplicación no fija ningún número de
versión. **Crear un repositorio no certifica la compatibilidad de la aplicación ni de los paquetes**:
valide las dependencias, la instalación y la restauración en la distribución de destino antes del despliegue.
Los nombres en clave vienen de las [versiones de Debian](https://www.debian.org/releases/) y de las
[versiones de Ubuntu](https://ubuntu.com/project/docs/release-team/list-of-releases/).

En línea de comandos:

```bash
sudo loco-apt add debian trixie 13
sudo loco-apt add ubuntu noble 24.04
sudo loco-apt list
sudo loco-apt rebuild
```

El script de los equipos detecta `ID` y `VERSION_CODENAME` en `/etc/os-release`, y luego descarga una
fuente deb822 dedicada. Un repositorio ausente provoca una parada antes de sustituir la fuente existente.
Para una derivada, elija explícitamente su base:

```bash
curl -fsSL https://SERVIDOR:8443/linux/setup.sh | sudo sh -s -- --distribution ubuntu --suite noble
```

`--cache-only` configura la caché APT sin repositorio local. `--no-proxy` configura el repositorio sin caché.
Los repositorios oficiales del equipo siguen configurados como antes; los que se sirven en HTTPS salen en directo.
Tras una actualización mayor del equipo, vuelva a lanzar el script para seleccionar su nuevo repositorio.
Al cambiar de repositorio, se limpian los `.deb` en caché en el equipo para evitar reutilizar
un archivo procedente de la versión anterior. Los paquetes ya instalados no se reinstalan
automáticamente; para una compilación diferente, use una nueva versión del paquete.

### Transición desde el repositorio histórico

La actualización 1.1.0 conserva `/apt/`, su contenido, la clave GPG y las configuraciones de los equipos ya
conectados. No se adivina ninguna compatibilidad de los paquetes antiguos. Use *Copiar a otro repositorio*
tras la validación; el original sigue disponible para los demás equipos. Vuelva a lanzar después `setup.sh`
en los equipos afectados. `--legacy` conserva explícitamente el repositorio histórico compartido.

La copia de seguridad contiene todos los repositorios y su registro. La restauración, seguida de `install.sh`,
regenera sus índices y firmas. Dos archivos con el mismo nombre/versión/arquitectura pueden
coexistir en repositorios diferentes; dentro de un mismo repositorio, un contenido diferente exige una nueva
versión del paquete.

### Mantenimiento de la matriz de compatibilidad

`tests/platforms.json` es la lista de los sistemas verificados por el workflow: distribución, nombre en clave,
número, imagen oficial y manifiesto de huellas. Una nueva versión se añade en este archivo;
no hay ninguna lista duplicada que mantener en el workflow.
Las VM de prueba son desechables y sus registros se archivan. El workflow activo comprueba
cada push/PR, cada lunes y cada lanzamiento manual.

Para reproducir una prueba, instale `qemu-system-x86`, `qemu-utils`, `genisoimage`, `curl`, `openssh-client`
y `git`, y luego lance `tests/vm-e2e.sh debian-13` desde un clon con las etiquetas. KVM acelera la prueba
si `/dev/kvm` es accesible; si no, QEMU usa la emulación. Los resultados están en `test-results/`.
Las imágenes solo arrancan tras compararlas con la huella descargada por HTTPS del servidor oficial.

## 12. Familia Red Hat (RHEL, Rocky Linux, AlmaLinux)

**Caché de los repositorios oficiales.** Rocky Linux (`dl.rockylinux.org`), AlmaLinux (`repo.almalinux.org`) y EPEL
(`dl.fedoraproject.org`) se retransmiten bajo `/rpmcache/` con verificación de sus certificados. Los `.rpm`
y los metadatos con nombre único se guardan 6 meses (`--rpm-cache-size`), `repomd.xml` dos minutos.
Los equipos comprueban cada paquete con las claves GPG de su distribución, como en acceso directo. Esta caché
sale en directo, incluso con `--proxy`: autorice estas tres réplicas en el puerto 443.

**Repositorios locales.** Un repositorio por versión principal, común a toda la familia: `el9` sirve a RHEL 9, Rocky
Linux 9 y AlmaLinux 9. `el8`, `el9` y `el10` se crean en la instalación; una versión futura se añade
en *Linux › Paquetes* («+ Nuevo repositorio», Rocky, AlmaLinux, RHEL), sin modificar la aplicación. Los metadatos
(`repomd.xml`) están firmados por la clave del repositorio y dnf los comprueba (`repo_gpgcheck=1`); contienen
la huella de cada paquete. Un paquete marcado para otra versión (`.el8` enviado a `el9`) se rechaza,
igual que un paquete con el mismo nombre/versión y un contenido diferente.

**Equipos.**

```bash
curl -fsSL https://SERVIDOR:8443/rpm/setup.sh | sudo sh               # repositorio local + caché
curl -fsSL https://SERVIDOR:8443/rpm/setup.sh | sudo sh -s -- --epel  # instala EPEL, a través de la caché
curl -fsSL http://SERVIDOR:8088/rpm/setup.sh | sudo sh -s -- --trust-ca   # autoridad todavía no importada
curl -fsSL https://SERVIDOR:8443/rpm/setup.sh | sudo sh -s -- --remove    # vuelta al estado de origen
```

El script lee `/etc/os-release`, elige `elN` y comprueba que existe antes de modificar el equipo. En
Rocky Linux y AlmaLinux, crea `/etc/yum.repos.d/loco-cache.repo` (BaseOS, AppStream,
CRB o PowerTools, extras y EPEL a través de la caché, mismas claves GPG) y desactiva los repositorios equivalentes
de origen, anotados en `/etc/loco-rpm.state`; `--remove` los restablece de forma idéntica.
`--no-cache` solo añade el repositorio local; `--no-local`, solo la caché.

**RHEL.** Los repositorios de Red Hat están reservados a los sistemas suscritos (certificados propios de cada máquina):
siguen en acceso directo, o pasan por Red Hat Satellite si lo hay. En RHEL, loco proporciona
el repositorio local `elN` y EPEL a través de la caché (`--epel` instala `epel-release`; active también CodeReady Builder
con `subscription-manager`). CentOS Stream y Oracle Linux se tratan como RHEL.

**Validación.** `tests/vm-rpm-e2e.sh` arranca un servidor Debian 13 y un equipo por cada entrada de
`tests/rpm-platforms.json` (Rocky Linux y AlmaLinux 8, 9 y 10), instala desde el repositorio local, desde
la caché y desde EPEL, comprueba el servicio a través de la caché y luego verifica la vuelta exacta al estado de origen.
Una nueva versión de Rocky Linux o de AlmaLinux se añade en este archivo (imagen cloud oficial y
manifiesto de huellas).


## 13. Despliegue con Docker

La imagen retoma el mismo código y la misma configuración que la instalación nativa: en cada arranque,
`linux/docker/entrypoint.sh` traduce las variables de `.env` a opciones de `linux/install.sh --container`, que regenera
la configuración (nginx, apt-cacher-ng, portal), y luego `supervisord` lanza los servicios.

Con la imagen publicada en cada versión (`ghcr.io/shanouhar/loco`), `docker-compose.yml` basta — no hace falta
clonar el proyecto:

```bash
mkdir loco && cd loco
curl -fsSLO https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/docker/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/docker/.env.example -o .env   # como mínimo LOCO_HOST, la dirección anunciada a los equipos
docker compose up -d
docker compose logs loco  # dirección, huella de la autoridad, contraseña inicial (una sola vez)
```

`LOCO_IMAGE` elige la imagen: por defecto la última publicada (`…:latest`), o una versión concreta
(`ghcr.io/shanouhar/loco:2.165.0`). La imagen se reconstruye con cada versión de loco, con los paquetes de Debian del
momento.

Desde las fuentes, `sudo ./up.sh` (en `linux/docker`) construye la imagen en el sitio en lugar de descargarla:
añade `docker-compose.build.yml` a `docker-compose.yml`.

**Variables.** Corresponden a las opciones de `install.sh` (`LOCO_ALLOW` ↔ `--allow`, `LOCO_PROXY` ↔ `--proxy`,
`LOCO_WU=0` ↔ `--no-wu`, `LOCO_HTTP=0` ↔ `--no-http`…, lista comentada en `.env.example`) y son las que valen: una
variable retirada vuelve a su valor por defecto en el siguiente arranque (`docker compose up -d`). Solo los
tamaños de caché no indicados se calculan en el primer arranque y luego se conservan. Certificado
de empresa: monte la carpeta (línea comentada de `docker-compose.yml`) e indique `LOCO_TLS_CERT` y
`LOCO_TLS_KEY`; para volver a la autoridad local, elimine `/etc/loco/tls/custom` en el contenedor.

**Red.** El contenedor usa la red del anfitrión (`network_mode: host`): la caché de Windows Update escucha
en 80 y 443, y el filtrado por red ve la dirección real de los equipos. Puertos que abrir y requisitos
de red: idénticos a la instalación nativa (secciones 1 y 3). Al arrancar, un puerto ya ocupado en el anfitrión
detiene el contenedor con un mensaje explícito (`docker compose logs`).

**Datos.** Volúmenes con nombre: `loco-data` (`/srv/loco`, repositorios y cachés, el más voluminoso),
`loco-state` (base de datos, clave de firma), `loco-etc` (configuración, autoridad, certificados),
`loco-apt-cache`, `loco-logs` (registros de nginx, para las estadísticas de Windows Update) y
`loco-backups`. `LOCO_DATA_DIR` y `LOCO_BACKUP_DIR` los sustituyen por carpetas del anfitrión. Las cuentas
`loco` (990) y `apt-cacher-ng` (991) tienen identificadores fijos: los volúmenes siguen siendo válidos de una
imagen a otra.

**Explotación.**

```bash
docker compose exec loco loco-user passwd admin       # acceso perdido
docker compose exec loco loco-backup create           # copia de seguridad inmediata
docker compose exec loco loco-backup restore /var/backups/loco/ARCHIVO
docker compose restart loco                                  # tras una restauración
docker compose pull && docker compose up -d            # actualización (imagen publicada)
git pull && sudo ./up.sh                           # actualización (desde las fuentes)
docker compose down                                                # parada (datos conservados)
```

Las tareas de los temporizadores de systemd las hace `loco-cron`: tamaño de las cachés cada
15 minutos, recarga de nginx cuando cambia la lista de dominios de Windows Update, rotación de los registros
y luego copia de seguridad (si `LOCO_BACKUP_KEEP` > 0) cada día a las 03:30, en la hora de `TZ`. Registros de los servicios:
`docker compose logs`. Estado: `/healthz` (comprobación de salud de Docker), página *Sistema* y `/metrics`.

**Seguridad.** El contenedor arranca sin ninguna capacidad de Linux, salvo las necesarias para los procesos root
(cambio de propietario y de usuario, puertos 80/443), con `no-new-privileges`. El portal, nginx
(procesos de trabajo) y apt-cacher-ng se ejecutan con sus cuentas dedicadas, como en nativo. En cambio, el
confinamiento systemd del portal (sistema de archivos de solo lectura, filtrado de llamadas al sistema) no existe
en el contenedor: la instalación nativa sigue siendo la recomendada cuando es posible.

**Validación.** `tests/vm-docker-e2e.sh` instala Docker (paquetes de Debian) en una VM Debian 13 desechable,
construye la imagen, conecta la propia VM como equipo (repositorio local y caché), comprueba los servicios, la
copia de seguridad, la conservación de los datos y de la autoridad cuando se recrea el contenedor, la aplicación
de las variables (Windows Update y HTTP desactivados y luego restablecidos), y luego el arranque con `docker-compose.yml`
solo, fuera de las fuentes.

## 14. Contenedor LXC de Proxmox VE

`linux/lxc/proxmox-lxc.sh` se lanza como root en el anfitrión Proxmox VE (7, 8 o 9, amd64), solo o desde una copia
del proyecto. Crea un contenedor **sin privilegios** (`nesting=1`, arrancado con el anfitrión), espera a la red, y luego
hace la instalación nativa de `install.sh` en el contenedor: mismos servicios systemd, mismas opciones, mismo
portal que en una máquina Debian.

```bash
curl -fsSLO https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/lxc/proxmox-lxc.sh
bash proxmox-lxc.sh --help
bash proxmox-lxc.sh --hostname loco --ip 10.20.0.15/24 --gw 10.20.0.1 --vlan 20 --disk 400 \
    -- --allow "10.20.0.0/16" --admin-allow "10.20.99.0/24" --proxy http://proxy.ejemplo.lan:3128
```

**Elecciones automáticas.** Siguiente número libre, almacenamiento del disco `local-lvm`, luego `local-zfs`, luego el
primer almacenamiento activo que acepte contenedores; plantillas en `local` o en el primer almacenamiento compatible;
puente `vmbr0` o el primer puente `vmbr*`; plantilla Debian 13 (Debian 12 en Proxmox 7 o si Debian 13 no se
ofrece), descargada por `pveam` si falta, con control de huella. Cada elección se fuerza con una
opción (`--storage`, `--template-storage`, `--bridge`, `--ctid`…).

**Versión instalada.** Lanzado solo, el script instala la última versión publicada en GitHub (archivo
descargado por el contenedor en HTTPS); `--version v1.4.0` fija una versión, `--source DIR` instala una
copia local del proyecto (anfitrión sin acceso a GitHub, versión de prueba). Lanzado desde una copia del proyecto,
instala esa copia.

**Disco.** Repositorios y cachés están en el disco del contenedor (`--disk`, 100 GB por defecto); los tamaños
máximos de las cachés se calculan en la instalación según el espacio libre. Para ampliarlo después:
`pct resize N rootfs +200G`, y luego ajuste los tamaños con `install.sh` (opciones `--wu-cache-size`…).

**Explotación desde el anfitrión.**

```bash
pct exec N -- loco-user passwd admin              # acceso perdido
pct exec N -- loco-backup create                  # copia de seguridad inmediata
bash proxmox-lxc.sh --ctid N --update                   # actualización (última versión publicada)
bash proxmox-lxc.sh --ctid N --update -- --no-wu        # actualización y cambio de opción
pct destroy N --purge                                   # eliminación completa
```

Las copias de seguridad de Proxmox (`vzdump`) del contenedor se suman a las de loco. Las cachés se
reconstituyen solas: excluirlas evita respaldar decenas de gigabytes, por ejemplo
`vzdump N --exclude-path /srv/loco/wucache --exclude-path /srv/loco/flatpakcache
--exclude-path /srv/loco/rpmcache --exclude-path /var/cache/apt-cacher-ng`.

**Particularidades.** El cortafuegos de Proxmox está activado en la interfaz (`firewall=1`) pero solo tiene efecto si
está activado para el contenedor: abra entonces los puertos de la sección 1. El filtrado de apt-cacher-ng se apoya
en tcpwrappers, que funciona en contenedor. En un anfitrión cuyo AppArmor bloquea Flatpak en los contenedores,
instale con `-- --no-flatpak`.

**Validación.** Probado en Proxmox VE 9.2: creación en DHCP desde el script solo (descarga de la
versión publicada), actualización `--update` desde una copia local, y luego prueba de extremo a extremo completa
(`tests/e2e.sh`: equipos, Flatpak, copia de seguridad, restauración, desinstalación) en el contenedor.


## 15. Servidor nativo en Rocky Linux

El mismo `sudo ./install.sh` admite Rocky Linux 8, 9 y 10 (amd64 validado). El instalador usa
DNF, activa PowerTools (8) o CRB (9/10) e instala EPEL si hace falta. El portal dispone de un entorno Python aislado,
con dependencias y huellas SHA-256 fijadas en `lib/requirements-rocky.txt` (Python 3.11 en Rocky 8 y 9).
Las bases de datos, claves, rutas, opciones y comandos de administración son los mismos que en Debian.

SELinux sigue en modo estricto: se configuran los contextos de los archivos y cachés, los puertos HTTP y las salidas de nginx.
Si firewalld está activo, el servicio `loco` abre solo los puertos de las funciones activadas,
en las zonas activas. Las ACL de nginx siguen limitando las redes de clientes y de administradores.
El backend de apt-cacher-ng solo escucha en `127.0.0.1:23142`; nginx filtra las conexiones al puerto
3142. El puerto 23142 nunca debe exponerse.

En Rocky 10, al no estar apt-cacher-ng disponible en EPEL, la fuente Debian 3.7.5-1.3 y sus
parches se descargan con huellas fijadas y luego se compilan en `/usr/local/lib/loco/acng`.
Un relanzamiento reutiliza esa compilación. Un temporizador se encarga de la caducidad diaria de la caché y logrotate conserva siete registros. Las versiones de las fuentes y dependencias deben actualizarse
con las próximas versiones del proyecto; no siguen a DNF automáticamente.

Los metadatos de desinstalación están reservados a root en `/etc/loco/install-state` y siguen siendo propios del servidor durante una restauración.

`sudo ./remove.sh` retira los paquetes añadidos por el proyecto y restituye los ajustes de nginx, SELinux y
firewalld que añadió. Los paquetes ya presentes siguen instalados. Los datos y las copias de seguridad
se conservan, salvo que se pida purgar los datos. Las modificaciones hechas a mano en
`nginx.conf` después de la instalación se conservan durante la desinstalación.

Validación: `tests/vm-rocky-server-e2e.sh rocky-8 rocky-9 rocky-10`, lanzado también por la CI. Las VM oficiales
se verifican por huella; las pruebas cubren SELinux activo, firewalld, importación APT/RPM, instalación
DNF de un paquete local, caché APT, rechazo de un cliente fuera de la ACL, relanzamiento, copia de seguridad/restauración y purga.


## 16. Appliance ISO (Alpine Linux, BusyBox)

La ISO x86_64 instala Alpine 3.24 y loco en un solo procedimiento, con un
asistente visual o un archivo de respuestas. Disco, DHCP o dirección fija,
teclado, contraseñas de root/admin, redes autorizadas y funciones se eligen
antes de la instalación. El disco se borra tras la confirmación. Los paquetes
van incluidos: la instalación funciona sin Internet.

Tras expulsar la ISO y reiniciar, la consola muestra el portal y
la huella de la CA. `setup-loco` cambia la red, el nombre, las contraseñas
y el acceso SSH. En el portal, **Administración → Configuración** y
**Actualizaciones** tienen sus equivalentes en línea de comandos, `loco-config` y `loco-update`.
Las operaciones las vuelve a validar un agente root separado; el portal sigue
sin privilegios. Las instalaciones de loco y los ajustes fallidos tienen una
vuelta atrás automática de la configuración y de la base de datos. Las actualizaciones
del sistema siguen `apk upgrade` en la rama de Alpine instalada.

La caché APT pasa por nginx; OpenRC y supervisord sustituyen a systemd.
Esta caché (appliance y servidor Windows) sale hacia los repositorios por HTTP o por HTTPS: los repositorios que se sabe que responden
en HTTPS (Debian, Ubuntu, PPA de Launchpad, Zorin, Raspberry Pi) se contactan así primero, los demás en HTTP, y
la otra vía se prueba cuando la primera no responde — una red que solo deja salir el HTTPS sirve
por tanto. Un índice (`InRelease`, `Packages`) nunca se vuelve a servir caducado cuando el repositorio ya no responde: el equipo
recibe un fallo claro (504), conserva sus listas y actualiza el resto. Un repositorio fuera de esta lista, contactado en HTTP
detrás de un cortafuegos que se traga el HTTP sin responder, cuesta cinco segundos por archivo antes del respaldo: abra entonces
el HTTP hacia ese repositorio.
Mínimo: 2 GB de RAM, disco de 8 GiB, BIOS o UEFI sin Secure Boot.
Los detalles, el archivo de respuestas, la construcción y las pruebas BIOS/UEFI
están en la [guía de la appliance](../../linux/iso/README.es.md).

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
