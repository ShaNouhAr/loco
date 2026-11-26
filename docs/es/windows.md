[English](../windows.md) · [Français](../fr/windows.md) · **Español**

# Servidor Windows nativo

## Instalar

Windows 11 Pro / Enterprise o Windows Server 2022 / 2025, x64. Windows Server Core
y Desktop Experience usan el mismo instalador. Prevea una dirección estable, un volumen NTFS local
con espacio suficiente para las cachés y una conexión a Internet para descargar los componentes.
Abra PowerShell 5.1 o posterior **como administrador**, obtenga el proyecto (archivo ZIP o `git clone`)
y lance el instalador de la carpeta `windows`:

```powershell
Invoke-WebRequest https://github.com/ShaNouhAr/loco/archive/refs/heads/main.zip -OutFile loco.zip
Expand-Archive loco.zip -DestinationPath .
cd loco-main
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install.ps1
```

Los archivos de los componentes proceden de Python, nginx, WinSW y PyPI; sus versiones
y SHA-256 están fijados en `windows/components.json` y se comprueban antes de la instalación. No se instala ningún otro
software en el sistema: ni GnuPG, ni modificación del `PATH`. El Python incluido
sigue siendo privado para la aplicación. El servicio `loco` arranca automáticamente con la cuenta virtual
`NT SERVICE\loco`; el portal lo sirven Waitress y nginx. Las tareas de copia de seguridad, de
renovación del certificado y de cálculo de las estadísticas forman parte del servicio.

El script muestra la dirección HTTPS, la huella de la autoridad y la contraseña inicial de `admin`, que hay que cambiar
en el primer inicio de sesión. Despliegue `C:\ProgramData\loco\etc\tls\ca.crt` en los equipos para usar
HTTPS, por ejemplo por GPO. La autoridad y las claves existentes se conservan al volver a lanzarlo.

Ejemplo con carpetas en otro volumen y redes explícitas:

```powershell
.\windows\install.ps1 -HostAddress 10.20.0.15 `
  -Allow 10.20.0.0/16 -AdminAllow 10.20.99.0/24 `
  -DataPath D:\loco-data -BackupPath D:\loco-backups
```

Las carpetas de datos y de copias de seguridad deben ser locales, distintas, no anidadas y sin espacios.
El programa está en `%ProgramFiles%\loco`; la configuración y el estado están en
`%ProgramData%\loco`. Las ACL dan al servicio los permisos necesarios sobre sus datos,
sus registros y sus certificados; la configuración del instalador sigue reservada al administrador.

| Parámetro | Efecto |
|---|---|
| `-HostAddress` | IPv4 o nombre DNS anunciado a los equipos |
| `-Allow`, `-AdminAllow` | redes de los equipos y de la interfaz; redes privadas por defecto |
| `-HttpsPort`, `-HttpPort`, `-NoHttp` | HTTPS 8443, arranque HTTP 8088 o solo HTTPS |
| `-Proxy http://proxy:3128`, `-NoProxy` | proxy de salida o eliminación del proxy configurado |
| `-NoWU`, `-NoRpm`, `-NoFlatpak` | desactivar la caché de Windows Update, las funciones RPM o la caché de Flathub |
| `-WuCacheSize`, `-AptCacheSize`, `-RpmCacheSize`, `-FlatpakCacheSize` | límite de cada caché, por ejemplo `50g` |
| `-Resolvers 1.1.1.1,9.9.9.9` | DNS de salida para Windows Update; no deben devolver el servidor local |
| `-MaxUpload 8g` | tamaño máximo de un envío |
| `-BackupKeep 3` | copias de seguridad diarias conservadas; `0` desactiva la tarea diaria |

Los parámetros omitidos al volver a lanzarlo conservan la configuración existente. Las reglas de cortafuegos
del grupo `loco` se limitan al programa nginx y a las redes configuradas. El portal de la aplicación
8089 sigue en el bucle local, como el puerto vecino 8090 del relé de las sesiones remotas. La caché APT escucha en el 3142; Windows Update usa el 80 y el 443.
El instalador rechaza los puertos ya ocupados, en particular por IIS/HTTP.sys. Libere esos puertos o use
`-NoWU` si la caché de Windows Update no es necesaria.

## Conectar los equipos

Los comandos del portal son comunes a los servidores Windows y Linux. Los repositorios APT se organizan
por distribución y nombre de versión; los repositorios RPM por versión principal `el8`, `el9`, `el10`.
Añadir una versión de Debian/Ubuntu en la interfaz no requiere modificar el programa.
Los paquetes nuevos los indexa y firma el programa: firmas OpenPGP (clave RSA 4096,
`%ProgramData%\loco\state\signing-key.json`, legible solo por el servicio y los administradores),
comprobadas por los equipos con gpgv, sqv (apt de Debian 13) o dnf como para un servidor Linux. Las firmas
de los editores se conservan para los contenidos de las cachés. En Windows, la caché APT la proporciona nginx en lugar de apt-cacher-ng.

Windows Update se conecta mediante los ajustes DNS propuestos en el portal. Las descargas HTTP
se guardan en caché; el HTTPS se retransmite al servidor de Microsoft sin descifrarlo. No se instala ningún rol WSUS.
El catálogo de software de Windows sigue disponible por separado.

La caché de Flathub funciona para los equipos Linux. Publicar un **repositorio Flatpak local** requiere
ostree y solo está disponible cuando el servidor loco funciona en Linux; la interfaz
de Windows muestra este límite y no propone la importación Flatpak local.

## Administrar, actualizar y restaurar

```powershell
$lr = "$env:ProgramFiles\loco\loco.cmd"
& $lr status
& $lr restart
& $lr user list
& $lr user add operador viewer
& $lr user passwd admin
& $lr fingerprint
& $lr backup create
& $lr backup list
& $lr backup restore 'D:\loco-backups\loco-servidor-AAAAMMDD-HHMMSS.tar.gz'
```

Ejecute estos comandos como administrador. La restauración detiene el servicio, restaura los repositorios,
las cuentas y las claves, y luego reinicia el servicio. Las cachés de descarga no se incluyen en la copia de seguridad.
La dirección, las rutas y las redes del servidor actual se conservan. Estas copias están destinadas
a una instalación Windows; no constituyen un formato de migración entre Linux y Windows.
Copie regularmente los archivos en otro almacenamiento, con permisos adecuados: contienen
las claves privadas y las cuentas.

Para actualizar, obtenga las nuevas fuentes y vuelva a lanzar `windows\install.ps1` como administrador.
Antes de una actualización importante, cree una copia de seguridad. Los componentes se descargan y se comprueban
antes de detener el servicio. Consulte los registros en `%ProgramData%\loco\logs` y el estado en
la página *Sistema* del portal.

```powershell
# Retira el servicio y el programa; conserva los datos, las cuentas, las claves y las copias de seguridad.
powershell -ExecutionPolicy Bypass -File "$env:ProgramFiles\loco\windows\remove.ps1"

# Borrado completo, con confirmación.
powershell -ExecutionPolicy Bypass -File "$env:ProgramFiles\loco\windows\remove.ps1" -Purge
```

Windows 11 bloquea por defecto la ejecución de los scripts de PowerShell: `-ExecutionPolicy Bypass` la autoriza
para este único comando, sin cambiar la directiva de la máquina.

Tras una desinstalación simple, volver a lanzar el instalador desde las fuentes recupera los datos.
`-Purge -Yes` permite un borrado sin interacción para los despliegues automatizados.

## Dimensionamiento y validación

nginx para Windows usa aquí un solo worker y como máximo 1000 conexiones; su editor lo describe
como una versión beta, adecuada para una carga limitada. Para un parque grande o con fuertes caudales
concurrentes, es preferible el servidor Linux y medir la carga antes del despliegue.
Vea los [límites oficiales de nginx para Windows](https://nginx.org/en/docs/windows.html).

La CI ejecuta la instalación nativa en Windows Server 2022 y 2025, con paquetes reales construidos
en Linux: importaciones por HTTPS, control de las firmas, caché APT MISS/HIT, copia de seguridad/restauración,
relanzamiento, reinstalación, desinstalación que conserva los datos y purga. Los lectores de paquetes
Python también se comparan con las herramientas Debian/RPM en Linux, y las firmas OpenPGP se comprueban con
gpg, gpgv y sqv. Windows 11 (25H2) se ha comprobado en una máquina de prueba aparte, con equipos reales:
Debian 13 (repositorio local HTTPS comprobado por sqv, paquetes Debian a través de la caché APT) y Rocky Linux 9 (repositorio `el9`
comprobado por dnf, BaseOS a través de la caché RPM), así como una actualización de Windows de 88 MB servida por la caché.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
