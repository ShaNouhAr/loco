[English](README.md) · [Français](README.fr.md) · **Español**

# Appliance loco: Alpine Linux y BusyBox

La ISO x86_64 instala Alpine 3.24 y loco en disco. Arranca en BIOS o
UEFI (Secure Boot desactivado). Los paquetes y las fuentes están en el medio:
la instalación funciona sin acceso a Internet. Mínimo: 2 GB de RAM y un
disco de 8 GiB; prevea más para los repositorios y las cachés.

## Instalación con asistente

Descargue `loco-VERSION-x86_64.iso` y `SHA256SUMS` de las
[releases](https://github.com/ShaNouhAr/loco/releases), y compruebe la huella:

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

1. Arranque la máquina o la VM con la ISO. El asistente visual se abre en la
   consola 7 (Alt+F7). `loco-installer` permite volver a lanzarlo desde Alt+F1.
2. Elija el teclado, el disco (**borrado por completo**), el nombre, la tarjeta de red,
   DHCP o dirección fija, la zona horaria y las contraseñas de root y admin.
3. Elija la subred del servidor, las redes privadas IPv4 para acceder desde
   varias subredes privadas o redes personalizadas. En el modo personalizado,
   el portal puede usar la lista de los equipos o una lista independiente.
   A continuación, elija las funciones: HTTP de los clientes,
   Windows Update, RPM, Flatpak. SSH está desactivado por defecto; al activarlo,
   elija **Contraseña root** (sin clave necesaria) o **Solo clave pública**.
4. Revise el resumen, instale, retire el medio y reinicie.

Si nginx devuelve **403 Forbidden** al abrir el portal, compruebe la dirección
de origen del navegador. En la consola root del servidor, ejecute
`loco-config show` y añada la dirección o subred a la lista web existente,
por ejemplo `loco-config set 'ADMIN_ALLOW=192.168.1.0/24 10.10.20.45/32'`.

El primer arranque termina la configuración de loco, y luego la consola muestra
la dirección del portal y la huella SHA-256 de su autoridad HTTPS. Compare esta
huella antes de distribuir la CA a los equipos. Las contraseñas introducidas no
se muestran. Los argumentos temporales que contienen la contraseña de admin se
eliminan tras una instalación correcta.

El asistente es una interfaz de texto con ventanas, utilizable con el teclado en la
consola local o en la consola de una VM. La administración diaria dispone de una
interfaz gráfica web y de comandos CLI.

En el asistente, **Esc** o **Inicio** vuelve al menú inicial sin lanzar
la instalación. La consola root solo se abre con **Consola de solución de problemas**;
escribir `exit` en ella devuelve al asistente. **Alt+F1** abre una consola de inicio de sesión
independiente, **Alt+F7** vuelve al asistente.

## Instalación automática y CLI

Copie [answers.example](answers.example), indique las dos contraseñas
y adapte los ajustes. Un valor por línea, comillas simples o dobles
opcionales: el archivo nunca se ejecuta como shell.

- Ponga el archivo `loco-answers.conf` en la raíz de un volumen llamado `LOCO`,
  y luego arranque con la ISO. Una pausa de diez segundos permite cancelar.
- O integre las respuestas en la construcción con `--answers`.
- O, desde la consola live:

```sh
loco-installer --answers /ruta/respuestas.conf --yes
```

`DISK=auto` exige exactamente un disco fijo de al menos 8 GiB. Con varios
discos, indique el nombre (por ejemplo `DISK=vda`). El disco del medio de arranque
y el de las respuestas se excluyen. Una respuesta no válida abre el asistente;
con `--yes`, provoca un fallo antes de cualquier borrado.

Conserve las respuestas en modo 600 y retire su soporte tras la instalación.
Una ISO construida con respuestas contiene sus contraseñas: debe
seguir siendo privada. La ISO pública no incluye ni respuestas ni clave SSH.

`SSH_MODE=password` activa la conexión `ssh root@direccion-del-servidor` con
`ROOT_PASSWORD`, sin `SSH_KEY`. `SSH_MODE=key` pide una clave en `SSH_KEY` y
rechaza las contraseñas SSH. `SSH_MODE=off` desactiva SSH. Sin `SSH_MODE`, los
archivos antiguos conservan su comportamiento: clave indicada = SSH por clave,
si no, SSH desactivado. Tras la instalación, `setup-loco` → **Acceso SSH** permite
cambiar el modo; la contraseña sigue siendo la de la cuenta root.

## Configuración y actualizaciones

En el portal, **Administración → Configuración** ajusta los accesos, el proxy
de salida, las funciones, los DNS de la caché de Windows Update, el tamaño de las cachés,
los envíos y la conservación de las copias de seguridad. Las redes del portal deben
incluir la dirección del navegador para no perder el acceso.

**Administración → Actualizaciones** muestra las versiones disponibles, los paquetes
de Alpine, la necesidad de reinicio y el registro de las operaciones. El agente comprueba
las versiones cada día. El portal se ejecuta como `loco`; un servicio OpenRC
separado, `loco-agent`, vuelve a validar las peticiones antes de aplicarlas como root.

Las mismas operaciones están disponibles en la consola root:

```sh
setup-loco                       # asistente de red, nombre, contraseñas y SSH
loco-config show
loco-config set 'ALLOW=192.168.43.0/24' WU_CACHE_SIZE=50g BACKUP_KEEP=5
loco-update status
loco-update check
loco-update loco                  # última release estable, archivo comprobado por SHA-256
loco-update system                # apk upgrade, en la rama de Alpine instalada
loco-update all
loco-update reboot
loco-backup create
```

Cambiar la dirección o el nombre se hace con `setup-loco` en la consola: la
conexión de red puede cortarse y los equipos deben usar la nueva
dirección. El certificado y los enlaces se regeneran conservando la autoridad.

Si una instalación de loco o un ajuste falla, el agente restaura la
configuración y la base de datos, y luego relanza las fuentes anteriores. Las fuentes de las
dos versiones se conservan en `/usr/local/src/`. Esta vuelta atrás cubre
la aplicación; `apk upgrade` sigue las transacciones de Alpine y no restaura
automáticamente los paquetes del sistema anteriores. Pasar a otra rama de
Alpine sigue siendo una operación de administración que preparar con una copia de seguridad.

Solo puede ejecutarse una operación privilegiada a la vez (portal y CLI).
Las actualizaciones requieren acceso a los repositorios oficiales de Alpine y a GitHub,
directamente o a través del proxy configurado.

Registros: `/var/log/loco/`, `/var/lib/loco-agent/jobs/` y, en el primer arranque,
`/var/log/loco-firstboot.log`. Si este falla, se retomará en el siguiente
reinicio; su archivo de argumentos sigue reservado a root.

## Construir la ISO

En Alpine 3.24, instale las herramientas de construcción y luego use una cuenta
no root miembro del grupo `abuild`, autorizada a instalar su clave pública APK:

```sh
# como root:
apk add git xorriso squashfs-tools fakeroot abuild alpine-base mkinitfs \
    syslinux grub-bios grub-efi mtools dosfstools python3
addgroup mi-cuenta abuild
# volver a conectarse como mi-cuenta, y luego:
sh linux/iso/build.sh --outdir out
# opciones: --answers archivo-privado --aports clon-aports --source-archive loco-VERSION.tar.gz
```

La construcción usa los scripts oficiales `mkimage` de la rama `3.24-stable`
de aports y los repositorios oficiales v3.24. `abuild-keygen` crea una clave de firma del modloop
si no se proporciona `PACKAGER_PRIVKEY`; esta clave
nunca se incluye en la ISO. La verificación del medio distribuido se apoya
en las huellas de la release descargadas en HTTPS. Secure Boot no está
soportado.

Las fuentes incluidas provienen únicamente de los archivos seguidos por Git
(añada los archivos nuevos con `git add` antes de construir). Los archivos
locales no seguidos, en particular las respuestas privadas, se excluyen del archivo
de fuentes. Desde una extracción sin Git, proporcione `--source-archive` con
el archivo oficial correspondiente.

La salida es `out/loco-v3.24-x86_64.iso` y su `.sha256`; la publicación
renombra la imagen con la versión de loco. El runtime completo figura en
[packages](packages), usado también por el perfil y el overlay. La caché
APT usa nginx, como en Windows; OpenRC lanza supervisord, el portal,
nginx, las tareas periódicas y el agente de administración.

## Validación y publicación

```sh
tests/vm-alpine-server-e2e.sh       # servidor Alpine nativo: paquetes/firmas/caché, agente, rollback, retirada
tests/vm-alpine-iso-build.sh        # fabrica la ISO, instala en BIOS y luego en UEFI
tests/vm-alpine-iso-e2e.sh out/loco-v3.24-x86_64.iso
```

Herramientas del anfitrión: QEMU, `genisoimage`, `xorriso`, OVMF, SSH, Python y curl. KVM se
usa si está disponible; si no, QEMU usa TCG. Los discos son desechables.
La prueba de la ISO expulsa el medio, arranca desde el disco, comprueba el portal con su
CA, los comandos, las copias de seguridad y un segundo arranque.

El workflow **Release**, lanzado con una etiqueta existente, exige una CI correcta
para ese commit. Retoma exactamente la ISO probada en el artefacto `loco-iso`,
crea el archivo de fuentes con el prefijo `loco-VERSION/`, calcula `SHA256SUMS` y publica
los tres archivos. No se reconstruye la ISO en la publicación.

---

[← Volver a la presentación](../../README.es.md) · [Guía del servidor Linux](../../docs/es/linux.md)
