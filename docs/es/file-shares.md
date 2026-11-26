[English](../file-shares.md) · [Français](../fr/file-shares.md) · **Español**

# Recursos compartidos (Samba)

> **Función opcional**, desactivada por defecto: un administrador la activa en *Administración ›
> Funciones* (sección *Opcional*). Aparece entonces en *Sistemas › Archivos › Recursos compartidos*.

El servidor loco comparte carpetas en la red, por **SMB**, como un pequeño NAS: los equipos Windows, macOS y
Linux las abren como `\\servidor\recurso` (o `smb://servidor/recurso`). Cada recurso compartido vive en una **partición ya
en uso** del servidor, tiene un **tamaño límite**, y solo se abre a los usuarios Samba elegidos — cuentas loco o
usuarios solo de Samba — en lectura o en lectura y escritura.

Posible en la appliance (ISO) y en un servidor Debian o Ubuntu instalado con `install.sh`: es el agente
de administración del servidor (`loco-agent`, root) el que instala y configura Samba; el propio portal no tiene ningún permiso
root. No en la imagen Docker, ni en un servidor Windows, ni (por ahora) en un servidor Rocky / RHEL.

## Instalar Samba

La página propone **Instalar Samba**: loco lo instala desde los repositorios del sistema (`samba-server` y
`samba-common-tools` en la appliance, `samba` en Debian y Ubuntu), y luego escribe su propia configuración — la
configuración de origen se conserva en `/etc/samba/smb.conf.avant-loco`. Si Samba ya estaba instalado y configurado a
mano, loco no toca nada mientras un administrador no haga clic en *Dejar que loco gestione Samba* (misma copia conservada).

Ajustes de loco:

- **solo cuentas**: ningún acceso de invitado ni anónimo (ni siquiera se pueden listar los recursos compartidos sin cuenta);
- **solo SMB 2 y posteriores**, en el puerto **445**; NetBIOS (puerto 139, `nmbd`) detenido;
- **solo las redes de los equipos de loco** (*Administración › Configuración*, equipos del parque);
- sin impresoras, sin carpetas personales.

**Cortafuegos**: Samba solo necesita el puerto **445/TCP** (NetBIOS, puertos 137 a 139, está desactivado). loco lo abre él mismo
en el cortafuegos activo del servidor — ufw (Debian, Ubuntu, la appliance) o firewalld —, para las redes de los equipos, mientras
se sirvan los recursos compartidos, y lo vuelve a cerrar cuando se desactivan; sus reglas llevan la marca `loco-samba`, las del
administrador nunca se tocan, y un cortafuegos encendido después recibe la regla en diez minutos. La página
dice cómo está (*Cortafuegos del servidor*). Un cortafuegos **entre** los equipos y el servidor debe, por su parte, dejar pasar
el 445/TCP (*Sistema › Red* lo indica).

**Direcciones**: la página da, para copiar, la dirección del servidor y la de cada recurso compartido para cada sistema —
`\\servidor\recurso` para Windows (Explorador, *Conectar a unidad de red*), `smb://servidor/recurso` para Linux
(Archivos, Dolphin…) y macOS (Finder › *Ir* › *Conectarse al servidor*).

## Particiones y gráficos

loco propone las particiones del servidor **montadas en escritura**, con permisos Unix (ext4, XFS, Btrfs, F2FS…), fuera de las
carpetas del sistema; no formatea ni monta ninguna. Para cada una, una barra muestra lo que la ocupa: sistema
y otros datos, archivos de los recursos compartidos, espacio reservado a los recursos compartidos (sus tamaños límite aún no ocupados) y
espacio libre. Los recursos compartidos van a `/srv/partages` en la partición raíz, a `<punto de montaje>/partages` en las
demás.

Para añadir una partición (un segundo disco de la VM, por ejemplo), formatéela y móntela usted mismo en el
servidor, y luego vuelva a abrir la página.

## Crear un recurso compartido

*Añadir un recurso compartido*: un nombre (`Documentos`, `Contabilidad`…), una partición elegida con un clic, un **tamaño límite** —
con el cursor, que dibuja el futuro recurso en la barra de la partición — y los usuarios que acceden a él. Ninguna
ruta que escribir: loco crea la carpeta a partir del nombre (`/srv/partages/contabilidad`, o `contabilidad-2` si ya está
ocupada) y nunca reutiliza una carpeta existente que contenga archivos que no vienen de él.

Los equipos Windows ven el recurso compartido como **un disco del tamaño límite**, con el espacio que le queda.
La ocupación se lee cada dos minutos: **más allá del límite, el recurso pasa a solo lectura** para
todos, aparece una alerta en *Por tratar* de la vista general, y la escritura vuelve sola en cuanto se ha
liberado espacio. Entre dos lecturas, un envío grande puede superar un poco el límite. Los equipos ya conectados pasan
también a solo lectura (vea [Conexiones en curso](#conexiones-en-curso)).

Retirar un recurso compartido lo retira de la red; **sus archivos se quedan en el servidor**, en su carpeta.

## Usuarios Samba

Todo se ajusta en la página de los recursos compartidos, pestaña *Usuarios*: nada en *Mi cuenta*. Un usuario Samba es:

- **una cuenta loco** elegida en la lista: inicia sesión con su identificador de loco, y se desactiva o se retira de
  Samba al mismo tiempo que su cuenta loco;
- o **un usuario solo de Samba**, para quien no tiene cuenta loco (un puesto de digitalización, un compañero, una
  impresora): un identificador propio de los recursos compartidos, sin ningún acceso a loco. No puede tomar el identificador
  de una cuenta loco.

En ambos casos, el administrador elige su **contraseña de Samba** y sus **permisos, recurso por recurso** (ningún
acceso, lectura, o lectura y escritura) — desde el usuario, o desde cada recurso compartido. Se puede **desactivar**
(conserva su contraseña y sus permisos para volver a activarse) o **retirar** de Samba; la cuenta loco vinculada
no se toca. Desactivado o retirado, pierde al instante sus conexiones en curso.

La contraseña se entrega a Samba por la entrada estándar de `smbpasswd`: nunca pasa por los argumentos de un
comando, no se escribe ni en la base de datos de loco ni en su diario, y loco ya no puede mostrarla — transmítasela a
la persona por un medio seguro. Samba la guarda de una forma menos protegida que una contraseña de loco (hash NT,
sin sal): elija una que no se use en ningún otro sitio.

## Conexiones en curso

La pestaña *Conexiones* muestra, leída cada quince segundos, una fila por conexión de un equipo: el usuario,
los recursos compartidos que ha abierto, el equipo y la hora de conexión.

**Cortar** termina la conexión: el proceso de Samba que la sirve — el que Samba designa él mismo, loco nunca
detiene otro — se cierra limpiamente. Un archivo que se estaba guardando puede quedar incompleto. El equipo se
vuelve a conectar solo mientras el usuario conserve sus permisos: para impedirlo, marque *Desactivar también* en la
ventana de confirmación (se vuelve a activar en la pestaña *Usuarios*). Cada corte queda inscrito en el diario.

Samba solo aplica su configuración a las conexiones que se abren. loco cierra por tanto él mismo, en cada cambio, lo
que ya no está permitido en las conexiones ya abiertas: las de un usuario desactivado o retirado, y el recurso compartido
en las conexiones cuyo permiso ha cambiado (retirado, pasado a solo lectura porque el recurso está lleno, o devuelto) o
que ya no se sirve. El equipo que todavía tiene derecho a él lo vuelve a abrir solo, con sus nuevos permisos.

## Abrir un recurso compartido en el navegador

*Abrir*, en la tarjeta de un recurso compartido, lo muestra en loco, con un gestor de archivos completo:

- **recorrer**: ruta de navegación, carpetas anterior y siguiente, ordenación por nombre, tamaño o fecha, archivos ocultos visibles
  o no, filtro de la carpeta y **búsqueda en las subcarpetas** (sin distinguir mayúsculas ni acentos), espacio en disco al estilo
  TreeSize, propiedades (tamaño de una carpeta y de su contenido);
- **subir** archivos o **carpetas enteras** (botón *Subir*, o arrastrar y soltar desde el
  ordenador, en la lista o en una carpeta): el envío se hace por partes, se reanuda tras un corte de red,
  conserva la fecha de modificación de origen; varios archivos salen a la vez, cada uno con su progreso;
- **descargar** un archivo, o una carpeta y toda una selección como **archivo ZIP** compuesto al vuelo;
- **vista previa** de fotos, PDF, sonido y vídeos, con los vecinos de la carpeta (flechas ← →); **editor** para
  los archivos de texto (Ctrl+S guarda) — un archivo modificado mientras tanto por otra persona no se sobrescribe;
- **nueva carpeta** o **archivo de texto**, **renombrar**, **copiar**, **cortar** y **pegar** (en el recurso, o
  en otro recurso abierto después), mover arrastrando filas sobre una carpeta (Ctrl: copiar),
  **eliminar** — tras confirmación, sin papelera;
- **selección** de varios elementos (casillas, Mayús o Ctrl + clic, Ctrl+A) con una barra en la parte inferior de la pantalla
  (descargar, copiar, cortar, eliminar), **clic derecho** y botón ⋮ en cada fila, **atajos de teclado**
  (Intro, Supr, F2, Ctrl+C / X / V, Retroceso, Alt + ← / →, `/` para buscar; lista mediante el botón del teclado).

Un nombre ya usado en el destino se consulta **antes** de cualquier envío o pegado: *Sustituir* (*Fusionar* para una
carpeta enviada), *Conservar ambos* («nombre (2)»), *Omitir*, de una vez por todas si se quiere. El espacio ocupado sobre
el tamaño límite se muestra arriba: un envío que lo superaría se rechaza de antemano, y **un recurso lleno ya solo puede
disminuir** (eliminar, renombrar, mover) mientras no vuelva a estar por debajo de su límite.

**Quién abre qué.** Hace falta el permiso *Abrir los recursos compartidos en el navegador*. Con *Gestionar los recursos
compartidos*, abre **todos** los recursos en lectura y escritura. Si no, abre los recursos del **usuario
Samba vinculado a la cuenta loco**, con sus permisos en la red: en lectura, el gestor no propone ni envío, ni
creación, ni modificación, ni eliminación; un usuario Samba desactivado ya no abre nada. Una cuenta que solo tiene este
permiso ve, en *Recursos compartidos*, la lista de sus recursos (espacio, dirección en la red, *Abrir*) y nada de
su gestión. Los envíos, creaciones, cambios de nombre, copias, desplazamientos y eliminaciones quedan inscritos en el diario.

**Cómo está hecho.** El portal nunca toca él mismo las carpetas de los recursos compartidos. Un servicio aparte, lanzado por
el agente root con la cuenta propietaria de los recursos (`loco-share`, sin ningún permiso root), hace cada operación
en la carpeta del recurso: una ruta se sigue allí carpeta a carpeta, **sin atravesar nunca un enlace
simbólico** (los enlaces se muestran, nunca se siguen). Para un envío o una descarga, entrega al portal el
archivo ya abierto. Los archivos creados desde loco tienen el mismo propietario y los mismos permisos que los colocados por Samba
(`0660`, carpetas `2770`). Se rechazan los nombres que un equipo Windows no podría abrir (`\ / : * ? " < > |`, punto o espacio
final, `CON`, `NUL`…). Una página HTML o una imagen SVG de un recurso compartido se descarga, sin ejecutarse nunca
en el navegador. Los envíos en curso esperan en la carpeta oculta `.loco-envois` del recurso, que no aparece
en loco; los que un reinicio ha interrumpido se borran de ella al cabo de un día.

## Permisos

*Consultar los recursos compartidos* y *Gestionar los recursos compartidos* (instalar Samba, crear y modificar los
recursos, añadir los usuarios Samba, elegir su contraseña y sus permisos, cortar una conexión), y *Abrir
los recursos compartidos en el navegador* (vea más arriba): a marcar en los perfiles de acceso. El perfil Administrador tiene los
tres.

## Copia de seguridad, alta disponibilidad, desactivación

- La descripción de los recursos compartidos, de los usuarios y de sus permisos está en la base de datos de loco, y por tanto en su copia de seguridad.
  **Los archivos de los recursos no están** (haga su copia aparte), ni las contraseñas de Samba: tras una
  restauración en otro servidor, el administrador las vuelve a elegir (la página las señala como «aún no colocadas
  en Samba»).
- En alta disponibilidad, los recursos compartidos no se replican al segundo servidor.
- Desactivar la función detiene Samba; los archivos se quedan en su sitio y todo vuelve a arrancar al reactivarla.

## Lo que se ha comprobado

Con el verdadero Samba de Alpine 3.24 (el de la appliance) y de Debian 13, manejado por el verdadero código de loco: permisos en
lectura y en escritura, rechazo de las contraseñas incorrectas y del acceso de invitado, paso a solo lectura una vez lleno
y vuelta, usuarios desactivados y luego reactivados, tamaño límite visto por un verdadero equipo Windows 11 (SMB 3.1.1, firmado).
En conexiones ya abiertas (Samba 4.23, clientes `smbclient`): corte a petición solo de la conexión
señalada, y pérdida inmediata del acceso para un usuario desactivado, un permiso retirado, un paso a solo lectura
(recurso lleno, y luego vuelta a la escritura) y un recurso retirado.
La instalación desde la página, en un loco Debian 13; todavía no en la propia appliance, ni con un equipo macOS.

El gestor de archivos, en un loco Debian 13 (Samba 4.22 instalado desde la página, portal confinado por systemd) en
Chromium: envío de un archivo de 20 MB por partes y descarga idéntica (misma huella), parte pedida
(lectura de un vídeo), archivo ZIP, vista previa de una foto y de un PDF, nueva carpeta, copia, cambio de nombre, editor,
búsqueda en las subcarpetas, eliminación; servicio lanzado con `loco-share`, relanzado con el agente root; archivos
creados con el mismo propietario y los mismos permisos que con Samba, fecha de origen conservada; releídos y completados por un
usuario Samba con `smbclient`. Los permisos (gestor, lector, redactor, sin acceso), el tamaño límite y el
recurso lleno, los enlaces simbólicos y los nombres rechazados están cubiertos por las pruebas automáticas. Todavía no en
la appliance, ni con Firefox o Safari.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
