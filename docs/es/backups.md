[English](../backups.md) · [Français](../fr/backups.md) · **Español**

# Copias de seguridad

La página **Administración › Servidor › Copias de seguridad** reúne lo que se guarda — el propio loco, sus Home
Assistant, sus máquinas Proxmox —, dónde y cuándo. Tiene una pestaña por origen, y otra para el **destino
externo**: el NAS, el almacenamiento S3 o el servidor SFTP donde loco deposita las copias.

Home Assistant y Proxmox VE son funciones **opcionales**, desactivadas por defecto: actívelas en
**Administración › Funciones**, sección *Opcional*.

## Destino externo

Un almacenamiento fuera del servidor loco, la única copia que sobrevive si este se pierde:

- **Carpeta / punto de montaje** — un 2.º disco, o un recurso compartido SMB/NFS de su NAS montado por el sistema; la
  cuenta `loco` debe poder escribir en él;
- **S3 / Nube** — cualquier almacenamiento compatible con S3 (AWS, MinIO, Backblaze B2, Wasabi…);
- **SFTP** — con clave SSH.

Con una **frase de contraseña**, todo lo que sale se cifra en AES-256 (`openssl enc -aes-256-cbc -pbkdf2`);
guárdela, es necesaria para restaurar. *Probar* deposita y luego borra un archivo pequeño.

Se elige lo que va allí: las copias de seguridad de loco (*Copiar también las copias de seguridad de loco*, marcada por defecto),
y cada Home Assistant por separado (vea más abajo). Las máquinas Proxmox no pasan por allí: Proxmox escribe
él mismo sus copias de seguridad en uno de sus almacenamientos — para que vayan al mismo NAS, declárelo en
Proxmox (*Declarar un NAS…*).

En el destino, los nombres no se mezclan: `loco-<servidor>-<fecha>.tar.gz[.enc]` para loco,
`homeassistant-<n.º>-<fecha>-<copia>.tar[.enc]` para Home Assistant. La conservación de uno nunca toca
los archivos del otro.

## loco

Las copias de seguridad diarias del servidor (configuración, cuentas, claves, repositorios y catálogo, sin las cachés): la
última, el estado de su copia externa, y para cada una *Descargar*, *Restaurar* (el servidor se reinicia en
el estado de la copia) y la eliminación. También se ajusta el número que se conserva — vale también para el destino —
y se restaura desde un archivo. Esta pestaña sustituye a la antigua pestaña Copias de seguridad de la página Sistema.

## Home Assistant

Para cada instancia vinculada (vea [Home Assistant](homeassistant.md)):

- **Copiar ahora** copia su copia de seguridad más reciente — las guardadas en el propio Home Assistant, no en
  un almacenamiento de red de Home Assistant. Marque *crear antes una copia completa* para hacer una justo
  antes;
- **Programar…** hace lo mismo una vez, cada día, cada semana o cada mes, a la hora del servidor
  loco;
- **Copias conservadas** (3 por defecto): más allá, las más antiguas de esta instancia se eliminan del
  destino en la copia siguiente.

La copia se descarga por el vínculo SSH, desde la API del Supervisor (el token del complemento pasa por
la entrada de `curl`, nunca como argumento de un comando; sin `curl`, el archivo se encuentra en `/backup`).
Se comprueba hasta el tamaño, se cifra si se da una frase de contraseña, se deposita y luego se borra del disco
de loco. Una copia ya copiada no vuelve a salir. Una copia cifrada por Home Assistant (clave de emergencia)
se copia tal cual: su clave sigue siendo necesaria para restaurarla.

Permiso: *Hacer copias de seguridad de Home Assistant*.

## Proxmox VE

### Vincular un servidor

En **Mi parque › Inscripción**, pestaña **Proxmox VE**: loco pasa por la API de Proxmox con un **token** creado para
él. En el shell de un nodo:

```sh
pveum role add LocoBackup --privs "VM.Audit VM.Backup Datastore.Audit Datastore.AllocateSpace Sys.Audit"
pveum user add loco@pve --comment loco
pveum acl modify / --users loco@pve --roles LocoBackup
pveum user token add loco@pve loco --privsep 0
```

El último comando muestra el secreto del token, una sola vez. En loco: dirección (`192.168.1.10`, con
`:puerto` si no es 8006), identificador `loco@pve!loco`, secreto. El secreto se guarda cifrado y nunca se
vuelve a mostrar.

loco lee entonces el **certificado** de Proxmox y muestra su huella SHA-256: compárela con la de
Proxmox (el nodo › Sistema › Certificados, o el comando `openssl` propuesto) antes de confirmar. No se envía
nada antes, y el token solo sale después hacia ese certificado: si cambia (renovado), todo se detiene
hasta **Más › Volver a leer el certificado…** y una nueva confirmación.

¿Le falta un permiso al token? La página lo dice.

### Hacer copias de seguridad

La página lista las máquinas virtuales y los contenedores, con su última copia de seguridad. Marque y elija:

- el **almacenamiento de Proxmox** — los que aceptan copias de seguridad (*VZDump backup file*): disco local, NAS
  declarado en Proxmox, Proxmox Backup Server;
- el **modo** — *Instantánea, sin parada* (por defecto), *Suspensión* o *Parada y luego reinicio*.

Y luego **Hacer copia ahora**, o **Programar…**: una vez, cada día, cada semana o cada mes; las
máquinas marcadas, o *todas, incluidas las futuras* (sin las plantillas); y el número de **copias conservadas por
máquina**.

Cada máquina es una tarea de Proxmox (`vzdump`, compresión zstd), lanzada una tras otra y seguida hasta
su final, con su registro; *Anular* la detiene en Proxmox — puede quedar entonces un archivo inacabado en el almacenamiento
(que borrar en Proxmox), y la máquina quedar bloqueada — y apagada en modo *Parada* (`pct unlock` o
`qm unlock`, y luego reiniciarla). Si loco se reinicia
mientras tanto, la tarea continúa en Proxmox y loco retoma su seguimiento.

La **conservación** de una programación solo elimina sus propias copias, reconocidas por su nota
(`loco plan <n.º> <máquina>`): nunca las hechas de otro modo (a mano, por una tarea de Proxmox) ni las
copias protegidas. loco la aplica él mismo, sin pedir a Proxmox el permiso más amplio que exige
`prune-backups`.

### Declarar un NAS

**Declarar un NAS…** añade un recurso compartido **NFS** (servidor, carpeta exportada) o **SMB** (servidor, recurso compartido,
usuario, contraseña, dominio) como almacenamiento de Proxmox reservado a las copias de seguridad. La contraseña se entrega
a Proxmox, que la guarda; loco no la conserva. El token necesita además el permiso `Datastore.Allocate` en
`/storage`.

### Permisos

*Consultar Proxmox*, *Hacer copias de seguridad de las máquinas Proxmox* (lanzar, programar, anular), *Vincular servidores
Proxmox* (token, certificado, NAS).

### Actualizaciones, reinicio y terminal

Vinculado también por SSH, el servidor aparece en **Mi parque**: vea [Proxmox VE: sistema, actualizaciones,
terminal](proxmox.md).

## Programaciones

A la hora del servidor loco. Una ocurrencia perdida (servidor apagado) no se recupera: la siguiente tiene lugar
con normalidad. Una programación se pausa, se reanuda o se elimina; las copias ya hechas se quedan donde
están. En una instalación de dos servidores, solo el servidor principal las lanza.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
