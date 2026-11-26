[English](../proxmox.md) · [Français](../fr/proxmox.md) · **Español**

# Proxmox VE: sistema, actualizaciones, terminal

Un servidor Proxmox vinculado a loco para sus [copias de seguridad](backups.md#proxmox-ve) también puede vincularse para su
**sistema**: aparece entonces en **Mi parque**, como un Home Assistant, con sus actualizaciones, sus recursos
y un botón *Actualizar*, y tiene su propia página (el nombre del servidor en Mi parque, o *Sistema y actualizaciones*
en su tarjeta de la página Copias de seguridad).

## Por qué un vínculo SSH además del token

La API de Proxmox sabe decir qué actualizaciones esperan, pero **no instalarlas**: el botón *Upgrade* de su
interfaz abre un shell reservado a un `root@pam` conectado, rechazado a cualquier token de API. loco entra por tanto por **SSH**,
con una clave propia — el mismo planteamiento que para Home Assistant. El token sigue siendo el de las copias de seguridad;
también da los recursos del servidor (procesador, memoria, disco del sistema) que se muestran en Mi parque.

## Vincular el sistema

En la página del servidor, *Gestionar también el sistema de este Proxmox* (la pestaña **Proxmox VE** de **Mi parque › Inscripción**
lleva a ella, *Vincular por SSH*, para cada servidor vinculado por su token pero aún no por SSH):

1. **Autorizar la clave de loco**: abra el shell del nodo (interfaz de Proxmox › el nodo › *Shell*) y lance el
   comando propuesto, que añade la clave pública de loco a `/root/.ssh/authorized_keys`. La clave privada nunca sale
   del servidor loco; es distinta de la de Home Assistant.
2. **Leer la clave de host**: normalmente la misma dirección que la API, el puerto 22 y `root`. loco muestra la huella
   de la clave de host de Proxmox; compárela con la que da, en el shell del nodo,
   `for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done`, y luego confirme. No se intercambia nada antes.

*Retirar el vínculo SSH* (menú *Más*) detiene la gestión del sistema sin tocar las copias de seguridad; retire también la
clave de loco de `authorized_keys`.

## Lo que muestra la página

- la versión de Proxmox VE, el nodo, el **núcleo** en marcha — y *reinicio necesario* cuando ya hay
  instalado un núcleo más reciente;
- las **actualizaciones** disponibles, las de seguridad primero, los paquetes nuevos y los que una actualización
  retiraría, según la simulación de `apt-get dist-upgrade` (no se cambia nada para leerla);
- las máquinas, el procesador, la memoria, el disco del sistema y el tiempo encendido;
- la **actividad**: cada tarea con su registro, en directo durante una actualización.

Si el propio loco funciona en este Proxmox, la página lo dice (se reconoce por sus direcciones MAC en la configuración
de las máquinas).

## Actualizar

*Actualizar* vuelve a leer los repositorios y luego lo instala todo con `apt-get dist-upgrade` — lo que hace el botón *Upgrade*
de Proxmox; nunca `apt-get upgrade`, que Proxmox desaconseja. Los archivos de configuración modificados en el
servidor se conservan. La actualización se ejecuta **en el propio servidor** (unidad `systemd-run`, registro en
`/var/lib/loco-upgrade/`): llega hasta el final aunque el vínculo caiga o loco se reinicie, y loco retoma su
seguimiento. Las máquinas virtuales siguen funcionando; la interfaz web de Proxmox puede recargarse.

Marque *Reiniciar el servidor después* para encadenar un reinicio — solo si la actualización ha tenido éxito.
loco se niega a lanzar una actualización que retiraría el paquete `proxmox-ve` (repositorios mal configurados).

*Buscar actualizaciones* solo vuelve a leer los repositorios (`apt-get update`); Proxmox también lo hace cada día.

En Mi parque, el botón de la fila, la selección, *Actualizar todo* y la programación de todo el parque lanzan
la misma actualización. Un Proxmox **también se reinicia desde Mi parque** — selección, *Actualizar y luego reiniciar si
es necesario* (solo si la actualización instala un núcleo que cargar), botón *Reiniciar* de su fila cuando hace falta un
reinicio —, como un equipo, con una diferencia: loco espera primero a que terminen las actualizaciones en
curso en los equipos del parque, una hora como máximo, para no cortar ninguna instalación dentro de una máquina virtual. La
tarea dice lo que espera («Esperando el fin de las actualizaciones del parque: 2 equipos en curso.»). El botón
*Reiniciar* de su página, en cambio, reinicia de inmediato.

## Reiniciar

*Reiniciar* detiene todas las máquinas virtuales y los contenedores del servidor durante el reinicio; los
configurados para arrancar con él vuelven después. loco espera a que el servidor responda de nuevo, reiniciado (otro
identificador de arranque). Si loco funciona en este Proxmox, también se detiene unos minutos y retoma el seguimiento a
su vuelta.

## Terminal

*Terminal* abre un shell **root** en el nodo, mantenido por loco como el de Home Assistant (sesión registrada en el diario).

## Permisos

*Consultar Proxmox*, *Actualizar Proxmox* (buscar e instalar), *Reiniciar Proxmox*, *Abrir un terminal
de Proxmox*, *Vincular servidores Proxmox* (también el vínculo SSH). Ningún perfil incluido los concede, salvo
Administrador.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
