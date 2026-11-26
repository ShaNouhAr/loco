[English](../wazuh.md) · [Français](../fr/wazuh.md) · **Español**

# Agente Wazuh

**Opcional** — que activar en *Administración › Funciones › Opcional*.

Tiene un manager [Wazuh](https://wazuh.com) y un parque ya inscrito en loco: loco instala el agente Wazuh en los
equipos que usted elige, configurado para su manager, y le dice en qué punto está. Cuando retira el agente de un equipo,
loco también puede borrar su entrada en el manager. No sustituye a Wazuh y no configura su manager.

**Índice** —
[Ajustar](#ajustar) ·
[Instalar](#instalar-en-los-equipos) ·
[Seguir](#seguir-el-estado) ·
[Retirar](#retirar) ·
[Borrar la entrada del manager](#borrar-la-entrada-del-manager) ·
[Lo que pasa en el equipo](#lo-que-pasa-en-el-equipo) ·
[Permisos](#permisos) ·
[Si no se conecta](#si-no-se-conecta)

---

## Ajustar

*Mi parque › Wazuh*, bloque **Ajustes**:

| Ajuste | Qué es |
|---|---|
| **Dirección del manager** | el nombre o la dirección IP de su manager Wazuh, sin `https://` ni puerto |
| **Versión del agente** | a elegir en la lista de versiones publicadas por Wazuh. **No debe superar la del manager**: un manager 4.14.9 rechaza un agente 4.14.10 |
| **Grupo de agentes** *(opcional)* | el grupo Wazuh en el que colocar los equipos (`servers`, `docker`…). Debe existir ya en el manager; varios grupos se separan con comas |
| **Contraseña de inscripción** *(opcional)* | la del manager, si exige una (`<use_password>yes</use_password>`, archivo `authd.pass`) |

La contraseña se guarda cifrada. loco nunca la vuelve a mostrar ni la escribe en el diario: solo figura en él el hecho de que
ha cambiado.

Sin contraseña en loco (casilla *dejar de enviarla*), una reinstalación retira del equipo el archivo `authd.pass`
colocado antes: un manager que no espera contraseña rechaza el agente que envía una.

## Instalar en los equipos

Dos caminos:

- **Un equipo** — en su ficha, *Más › Instalar el agente Wazuh*; o el botón *Instalar* de su fila, página Wazuh.
- **Varios** — página Wazuh: marque los equipos, ajuste si hace falta el grupo de agentes, y luego
  *Instalar en los equipos marcados*.

La instalación sale en el próximo contacto del equipo — en unos segundos si está encendido, a su vuelta si no.
La primera vez, loco descarga antes el paquete: el comando espera a que esté listo.

Sistemas compatibles: **Windows**, **Debian y Ubuntu**, **RHEL, Rocky Linux y AlmaLinux** (x86-64; ARM de 64 bits
en Linux). Android no está afectado.

> Los equipos no necesitan Internet. loco descarga una vez el paquete oficial (`deb`, `rpm` o `msi`) de
> `packages.wazuh.com`, lo comprueba con la huella SHA-512 que publica Wazuh, y luego lo entrega a los equipos a través de su agente
> loco, que a su vez comprueba su huella.

*Volver a aplicar* (equipo ya equipado) reinstala el agente con los ajustes actuales: útil tras un cambio de manager
o de contraseña. *Actualizar* aparece cuando la versión del equipo es más antigua que la ajustada.

## Seguir el estado

La página Wazuh y la ficha de cada equipo (pestaña *Detalles*) muestran lo que el equipo informa en cada contacto:

| Estado | Lo que significa |
|---|---|
| **Conectado** | el agente funciona y se comunica con el manager |
| **Esperando al manager** | instalado y arrancado, aún no aceptado — vea [más abajo](#si-no-se-conecta) |
| **Desconectado** | el manager ya no responde a este equipo |
| **Detenido** | el servicio del agente Wazuh no funciona |
| **No instalado** | ningún agente Wazuh en este equipo |
| **Manager Wazuh** | este equipo es el propio manager: loco no instala ni retira agente en él, y no lo cuenta entre los equipos por equipar |
| **Desconocido** | el agente loco del equipo es anterior a la 2.166.0: se actualiza solo, el estado sigue |

Se añaden la versión instalada, el número y el nombre del agente en Wazuh, y una precisión cuando el equipo está
vinculado a otro manager distinto del ajustado.

## Retirar

Ficha del equipo, *Más › Retirar el agente Wazuh…*, o *Retirar* en su fila. El agente se desinstala del equipo.

Si loco tiene acceso al manager (más abajo), borra después la entrada del equipo en el manager, y el informe de la
retirada, en el historial del equipo, lo dice: «Entrada 003 retirada del manager Wazuh.» Si no, la entrada se queda en el
manager: bórrela allí si el equipo ya no debe figurar.

## Borrar la entrada del manager

*Mi parque › Wazuh*, bloque **Retirada de las entradas del manager** — opcional, que ajustar una vez.

loco se conecta al manager por SSH, con una clave propia, y solo lanza allí un comando:
`manage_agents -r <número>`. Tres pasos:

1. **Autorizar la clave de loco** — loco muestra una línea que añadir al archivo `~/.ssh/authorized_keys` de la cuenta
   elegida en el manager (`root` en general, ya que `manage_agents` requiere permisos de administración):

   ```
   restrict,command="/var/ossec/bin/manage_agents -r $(printf %s \"$SSH_ORIGINAL_COMMAND\" | tr -cd 0-9)" ssh-ed25519 AAAA… loco@…
   ```

   Con ella, la clave solo puede lanzar `manage_agents -r`, con solo las **cifras** de lo que envía: ni
   shell, ni redirección, ni otro comando (`restrict` también corta el terminal y los túneles). La clave privada se queda en el
   servidor loco.
2. **Leer la clave de host** — indique la cuenta y el puerto SSH; loco lee la clave que presenta el manager y
   muestra su huella. Compárela con la del manager (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`), y luego
   confirme: loco solo se comunicará con esa clave.
3. **Probar** — *Probar el acceso* pide al manager que retire un número que ningún agente lleva: no se retira nada,
   pero loco sabe que el acceso funciona. Avisa si la clave abre un shell completo (línea sin `command=`).

Después, en cada retirada correcta, loco borra la entrada del equipo. Por prudencia, solo lo hace si:

- el equipo estaba vinculado al manager ajustado (no a otro);
- el equipo indica que el agente se ha desinstalado bien;
- se conoce el número del agente — nunca `000`, el propio manager.

Cambiar la dirección del manager en los ajustes olvida la clave de host: se vuelve a confirmar. *Retirar el acceso*
corta al instante; retire también la línea de `authorized_keys` en el manager.

## Lo que pasa en el equipo

El agente loco, que funciona como root (SYSTEM en Windows):

1. recibe el paquete de loco y comprueba su huella SHA-256;
2. lo instala con el gestor del sistema (`apt-get`, `dnf` o `msiexec`), dando al paquete la dirección del
   manager y el grupo — las variables de despliegue previstas por Wazuh;
3. escribe la contraseña de inscripción, si la hay, en el archivo que lee el agente Wazuh:
   `/var/ossec/etc/authd.pass` (`root:wazuh`, modo 640) en Linux, `authd.pass` en la carpeta del agente en
   Windows (solo SYSTEM y administradores);
4. activa y arranca el servicio (`wazuh-agent`, `WazuhSvc` en Windows);
5. espera una treintena de segundos el primer intercambio con el manager, y luego informa.

La contraseña nunca se pasa como argumento de un comando ni se escribe en una salida: viaja en el comando
entregado al agente loco, por su canal HTTPS, y loco no la guarda con el comando.

En un equipo ya equipado, la actualización del paquete conserva `ossec.conf`: loco solo sustituye en él la dirección del manager
si ha cambiado.

## Permisos

Tres permisos, en los perfiles de acceso:

| Permiso | Permite |
|---|---|
| **Consultar Wazuh** | ver la página y el estado de los equipos |
| **Instalar y retirar el agente Wazuh** | instalar el agente, actualizarlo, retirarlo — en los equipos del parque de su perfil |
| **Ajustar Wazuh** | dirección del manager, contraseña de inscripción, grupo y versión, acceso de loco al manager |

El perfil Administrador los tiene todos; los demás perfiles deben recibirlos. Los permisos se vuelven a comprobar en el momento en que
el comando sale hacia el equipo.

## Si no se conecta

El informe de la instalación (historial del equipo) lo dice: «instalado y arrancado; aún no conectado».

| Qué comprobar | Cómo |
|---|---|
| ¿Alcanza el equipo al manager? | puertos **1514** (intercambios) y **1515** (inscripción) abiertos del equipo hacia el manager |
| La versión | el agente no debe ser más reciente que el manager |
| `Invalid password` | la contraseña no corresponde a `/var/ossec/etc/authd.pass` del manager (espacio o salto de línea de más incluidos) |
| `Invalid request for new agent` | el equipo envía una contraseña cuando el manager no la espera (`<use_password>no</use_password>`): marque *dejar de enviarla*, y luego *Volver a aplicar* |
| El grupo | debe existir en el manager (`/var/ossec/bin/agent_groups -l`) |
| Un agente antiguo con el mismo nombre | una entrada con el mismo nombre en el manager puede bloquear la inscripción: retírela, y luego *Volver a aplicar* |

En el equipo, el registro del agente Wazuh dice por qué: `/var/ossec/logs/ossec.log` (Linux),
`C:\Program Files (x86)\ossec-agent\ossec.log` (Windows).

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
