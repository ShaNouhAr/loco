[English](CHANGELOG.md) · [Français](CHANGELOG.fr.md) · **Español**

# Registro de cambios

## 2.206.0 — 2026-11-26

- **Enlazar Proxmox y Home Assistant se hace en Mi parque › Inscripción.** Dos pestañas, *Proxmox VE* y *Home Assistant*, junto a Windows, Linux y Android, dan los pasos del enlace y la confirmación de la huella (certificado de Proxmox, clave de host de Home Assistant). Para Proxmox, un último paso lista los servidores enlazados por su token pero todavía no por SSH (*Enlazar por SSH*), que es lo que hace falta para verlos en Mi parque. *Copias de seguridad* y la página *Home Assistant* conservan lo ya enlazado y remiten a la inscripción para enlazar uno nuevo; la renovación de un certificado o de una clave de host se sigue confirmando en el sitio.
- **Mi parque: un Proxmox que debe reiniciarse se presenta como un equipo.** Sin actualización que instalar, está *al día* (indicador verde), con *reinicio necesario* debajo, en lugar de «reinicio requerido» en el lugar del estado; la vista general lo cuenta igual.
- **Administración › Configuración, reordenada.** La página se organiza por pregunta: *Quién puede llegar a loco* (equipos del parque, ordenadores que abren el portal, HTTP sin cifrar), *Salida a Internet* (proxy de empresa, DNS para llegar a Microsoft), *Cachés servidas a sus equipos* — una fila por caché, con su interruptor, su tamaño máximo y el espacio que ocupa —, y luego *Envíos al portal*. Cada ajuste dice para qué sirve, en francés, inglés y español (las etiquetas seguían en francés). El número de copias de seguridad conservadas, ya ajustable en *Copias de seguridad*, ya no está duplicado allí; *Ver también* lleva a los ajustes vecinos (Copias de seguridad, Funciones, Sistema). Un solo *Aplicar*, que reinicia los servicios una vez.

## 2.205.0 — 2026-11-26

- **«Servidor MCP» pasa a ser «MCP y API», con claves.** La página Administración › Acceso se llama ahora *MCP y API*: las mismas claves sirven a un LLM, a través de su cliente MCP (Claude Code, Codex, Cursor…), y a sus scripts o herramientas de supervisión, a través de la API REST. En todas partes se habla de *claves* en lugar de *tokens*: página, instaladores de Claude y Codex, autorización de una aplicación, permiso *Crear claves MCP y API*, diario, Funciones y documentación.
- **Cada cuenta ve sus claves.** La página muestra primero *Sus claves*. Las de las demás cuentas, que un administrador puede revocar sin cambiar sus herramientas, están en una tabla aparte, con la cuenta que las tiene. Una cuenta que ya no tiene permiso para crearlas ve y revoca igualmente las suyas: antes, seguían funcionando sin que pudiera verlas.
- **Corregido: tras una actualización del sistema, la página de actualizaciones se quedaba en el estado anterior.** La página se recargaba en cuanto terminaba la instalación, antes de que loco volviera a leer lo que quedaba por actualizar: seguía mostrando los paquetes recién instalados y ya no se refrescaba. La operación solo se da ahora por terminada tras esa relectura — última etapa de la barra: *Comprobación de lo que queda por actualizar* —, y la página también se recarga en cuanto termina una comprobación.

## 2.204.0 — 2026-11-26

- **Mi parque: reservarse un equipo.** En la ficha de un dispositivo, *Más › Reservar este equipo…* lo reserva con una contraseña: las demás cuentas — incluso con el perfil Administrador — deben introducirla para abrir su terminal, su escritorio remoto (consola incluida) y sus archivos, para reiniciarlo (reinicio, actualización seguida de un reinicio, actualización a una nueva versión del sistema), y en Android para su terminal adb, su pantalla y su desbloqueo. Una vez introducida, abre el acceso media hora (*Cerrar* lo cierra antes); cinco intentos fallidos bloquean un cuarto de hora. Todo lo demás les sigue abierto según sus permisos: inventario, actualizaciones sin reinicio, despliegues, scripts, retirada del agente, olvido del equipo. Los reinicios agrupados de Mi parque dejan de lado un equipo reservado por otra cuenta y las actualizaciones agrupadas lo actualizan sin reiniciarlo (salvo contraseña ya introducida); una actualización por oleadas o una programación solo lo reinicia si su autor es el propietario. La ficha muestra *Reservado por …*, Mi parque un candado junto al nombre; solo el propietario cambia la contraseña o libera el equipo. Reservar o cambiar la contraseña cierra las sesiones ya abiertas por los demás; reservar también anula los reinicios que habían pedido y que todavía no habían salido hacia el equipo. Las herramientas MCP y la API de las demás cuentas se rechazan en un equipo reservado (solo una persona puede introducir la contraseña). Contraseña guardada con hash, nunca mostrada; si se pierde, el equipo se olvida y luego se vuelve a inscribir. Nuevo permiso *Reservarse un equipo*; todo queda inscrito en el diario.
- **Corregido: el mensaje de una acción pedida en un equipo.** En francés concordaba todo en femenino («Redémarrage demandée», «Inventaire demandée»); ahora dice «Reinicio en espera en …: lanzamiento en el próximo contacto».

## 2.203.0 — 2026-11-25

- **Recursos compartidos: abrir un recurso compartido en loco, con un verdadero gestor de archivos.** *Abrir*, en la tarjeta de un recurso compartido, lo muestra en el navegador: explorar sus carpetas, subir archivos o carpetas enteras (botón, o arrastrar y soltar desde el ordenador), descargar un archivo, o una carpeta y una selección en archivo ZIP, vista previa de fotos, PDF, sonido y vídeos (con los vecinos de la carpeta, ← →), editor para los archivos de texto, nueva carpeta o archivo, renombrar, copiar, cortar y pegar — en el recurso compartido o hacia otro —, eliminar, propiedades (tamaño de una carpeta y de su contenido). Selección de varios elementos (casillas, Mayús y Ctrl + clic, barra inferior), orden por nombre, tamaño o fecha, filtro de la carpeta y búsqueda en las subcarpetas (sin mayúsculas ni acentos), clic derecho, atajos de teclado (Supr, F2, Ctrl+C/X/V, Ctrl+A, Intro, Retroceso…), carpetas anterior y siguiente, espacio en disco al estilo TreeSize. Un nombre ya ocupado se plantea antes de cualquier envío o pegado: sustituir, conservar ambos, omitir, de una vez por todas si se desea. Se conserva la fecha de modificación original de los archivos enviados; un archivo modificado mientras tanto por otra persona no se sobrescribe desde el editor. Se muestra el espacio ocupado sobre el tamaño límite: un envío que lo superaría se rechaza de antemano, y un recurso compartido lleno ya solo puede disminuir (eliminar, renombrar, mover).
- **Quién abre qué: un nuevo permiso, «Abrir los recursos compartidos en el navegador».** Con *Gestionar los recursos compartidos*, abre todos los recursos compartidos en lectura y escritura; si no, los del usuario de Samba vinculado a la cuenta loco, con sus permisos en la red (lectura, o lectura y escritura) — un usuario de Samba desactivado ya no abre nada. Una cuenta que solo tiene este permiso ve, en *Recursos compartidos*, la lista de sus recursos compartidos (espacio, dirección en la red, *Abrir*) y nada de su gestión. Los envíos, creaciones, renombrados, copias, desplazamientos y eliminaciones van al diario.
- **Seguridad de los archivos de los recursos compartidos.** El portal nunca toca por sí mismo las carpetas de los recursos compartidos: un servicio aparte, lanzado por el agente root con la cuenta propietaria de los recursos compartidos (sin ningún permiso de root), hace cada operación en la carpeta del recurso compartido — allí una ruta se sigue carpeta a carpeta, sin atravesar nunca un enlace simbólico — y pasa al portal el archivo ya abierto para un envío o una descarga. Los archivos creados desde loco tienen el mismo propietario y los mismos permisos que los colocados por Samba. Se rechazan los nombres que un equipo Windows no podría abrir (`\ / : * ? " < > |`, punto o espacio final, `CON`, `NUL`…). Una página HTML o una imagen SVG de un recurso compartido se descarga, nunca se ejecuta en el navegador.
- **Un solo gestor de archivos, también para los equipos.** La pestaña *Archivos* de un dispositivo recibe el mismo: selección múltiple, orden, filtro, vista previa de fotos, PDF, sonido y vídeo (leídos desde el equipo), clic derecho, atajos, arrastrar y soltar carpetas enteras, desplazamiento arrastrando hacia una carpeta, conflictos de nombres preguntados antes del envío. Los agentes no cambian.
- **Los colores del tema se aplican a los recursos.** Los indicadores de procesador, memoria y disco de *Mi parque*, de la ficha de un equipo (discos), de Home Assistant, de Proxmox y de los dispositivos de red, y la curva del procesador de la supervisión, toman los tonos del color de acento elegido — suavizados hacia el gris del tema, gris con el acento neutro; un acento cálido (naranja, ámbar, rosa) se suaviza más, para que un indicador normal nunca parezca una alerta. El naranja (casi lleno) y el rojo (lleno) siguen siendo los de las alertas; la curva de la memoria conserva otro tono.
- **Corregido: el espacio en disco de un equipo Linux.** El botón *Espacio en disco: ver lo que más ocupa* del gestor de archivos nunca aparecía en los equipos Linux (solo en Windows).
- **Corregido: las graduaciones del gráfico en directo.** En la ficha de un equipo, las marcas 100 %, 50 % y 0 % del eje del gráfico *En directo* se quedaban arriba a la izquierda (estilo rechazado por la política de seguridad del portal).

## 2.202.1 — 2026-11-25

- **Agente Windows: un corte de corriente durante su actualización ya no lo hace desaparecer.** El instalador retiraba la versión anterior antes de colocar la nueva: un PC apagado de golpe entre ambas se quedaba sin agente — carpeta vacía, servicio inexistente — y salía del parque hasta una reinstalación a mano (le ocurrió a un equipo el 25/11). La nueva versión se coloca ahora completa antes de retirar la anterior. Verificado en Windows 11 cortando la máquina en cuatro momentos de la actualización: el agente vuelve cada vez, con la versión anterior o la nueva, y la actualización siguiente pasa. El orden anterior, cortado en el mismo punto, sí dejaba el equipo sin agente.
- **Agente Windows: sus archivos de ajustes resisten los cortes.** Se escriben hasta el disco antes de sustituir a los anteriores (la identidad del equipo depende de ellos), y el registro de la actualización se escribe línea a línea, legible incluso tras un corte.
- **Agente Windows: los instaladores antiguos ya no se acumulan.** Cada actualización guardaba una copia, decenas a lo largo de las versiones: solo queda el de la versión en marcha.

## 2.202.0 — 2026-11-25

- **Mi parque: Home Assistant y Proxmox se comportan como los demás dispositivos.** *Actualizar todo* y la programación de todo el parque también los actualizan (Home Assistant con una copia de seguridad antes), y el número del botón los cuenta. Sus filas siguen los filtros (*Estado*, *Sistema* — con *Home Assistant* y *Proxmox VE* —, *Situación*), la búsqueda y la actualización en directo; cuentan en los números de la página y en el anillo de la vista general, sus tareas se muestran en el desarrollo de las *Actualizaciones* y en la insignia de navegación, y Ctrl K los encuentra. Home Assistant muestra los recursos de su anfitrión (disco; memoria y procesador en Home Assistant OS) y se marca con los permisos de sus propios botones (actualizar, reiniciar) en lugar de «gestionar». No son miembros de ningún grupo: *Actualizar este grupo* no los toca.
- **Proxmox se reinicia desde Mi parque, después de las actualizaciones del parque.** Con una selección, *Actualizar y luego reiniciar si es necesario* (solo si la actualización instala un núcleo que cargar) o el nuevo botón *Reiniciar* de su fila cuando hace falta, como un equipo — salvo que loco espera primero a que terminen las actualizaciones en curso en los equipos del parque, una hora como máximo: el hipervisor detiene todas sus máquinas con él, loco incluido, y no debe cortar ninguna instalación. La tarea dice lo que espera.
- **SSO: la foto de perfil viene del proveedor.** Al iniciar sesión con OpenID Connect (Authentik…), loco toma la foto de la cuenta del proveedor (claim `picture`): vuelta a leer si cambia, como mucho una vez al día, retirada si el proveedor ya no da ninguna (avatar con iniciales). Una imagen elegida en *Mi cuenta* tiene prioridad, y *Volver a usar la foto de mi cuenta …* vuelve a la del proveedor. La imagen solo se pide al propio proveedor (misma confianza que el inicio de sesión) o a una dirección pública (Gravatar) — nunca a una dirección interna ni a través de una redirección; PNG, JPEG, GIF o WebP, 2 MB como máximo. Verificado con un Authentik 2026.8.6 real: imagen servida por Authentik (certificado fijado), Gravatar, vuelta a las iniciales.
- **Mi cuenta: cambiar de tema ya no quita la foto de perfil.** Guardar la apariencia (tema, color, fondo, logo) reescribía todos sus ajustes: la foto de perfil desaparecía, junto con los avisos ocultados («no volver a mostrar»).

## 2.201.1 — 2026-11-25

- **Android: cerrar la sesión de pantalla bloquea de verdad la tableta o el teléfono.** El bloqueo esperaba cinco segundos tras el final de la sesión, y una sesión reabierta mientras tanto lo anulaba: cerrar la sesión y volver a abrirla en seguida para comprobarlo dejaba el dispositivo abierto. Ahora se hace en cuanto termina la última sesión de pantalla; una sesión abierta después encuentra el dispositivo bloqueado (*Desbloquear*, en la sesión). Nunca mientras loco siga controlando la pantalla por adb. El resultado queda anotado en el registro de Android (etiqueta `loco`).

## 2.201.0 — 2026-11-25

- **Recursos compartidos: el puerto SMB se abre de oficio en el cortafuegos del servidor.** Samba solo necesita el puerto 445/TCP (NetBIOS está desactivado): loco lo abre él mismo en el cortafuegos activo del servidor — ufw (Debian, Ubuntu, la appliance) o firewalld — para las redes de los equipos, mientras se sirvan los recursos compartidos, y lo vuelve a cerrar cuando se desactivan. Sus reglas llevan su marca (`loco-samba`) y las del administrador nunca se tocan; un cortafuegos encendido después recibe la regla en diez minutos. La página de los recursos compartidos indica la situación (*Cortafuegos del servidor*).
- **Recursos compartidos: la dirección para cada sistema.** El servidor y cada recurso compartido muestran, para copiar, `\\servidor\recurso` para Windows y `smb://servidor/recurso` para Linux y macOS.
- **Recursos compartidos: cortar una conexión.** La pestaña *Conexiones* muestra una fila por conexión de un equipo — usuario, recursos compartidos abiertos, equipo, desde cuándo — y *Cortar* la termina: el proceso de Samba que la sirve, el que designa el propio Samba (loco nunca detiene otro), se cierra limpiamente. El equipo se vuelve a conectar solo mientras el usuario conserve sus permisos; la casilla *Desactivar también* lo impide. El corte queda inscrito en el diario.
- **Recursos compartidos: lo que se retira vale también para las conexiones ya abiertas.** Samba solo aplica su configuración a las conexiones que se abren: un usuario desactivado o retirado, o al que se le quitaba el permiso sobre un recurso compartido, conservaba su conexión abierta — y podía seguir escribiendo — mientras su equipo siguiera conectado; un recurso compartido lleno seguía siendo escribible para los equipos ya conectados, un recurso compartido retirado seguía abierto. Ahora, en cada cambio, loco cierra en el acto lo que ya no está permitido: las conexiones de un usuario desactivado o retirado, y el recurso compartido en las conexiones cuyo permiso ha cambiado (retirado, pasado a solo lectura, o devuelto) — el equipo que todavía tiene derecho lo vuelve a abrir solo, con sus nuevos permisos. Verificado con el Samba 4.23 real de Alpine.
- **Recursos compartidos: botones en lugar de menús «⋮».** Modificar, desactivar y retirar se hacen directamente desde la tarjeta del recurso compartido y la fila del usuario, sin menú que abrir.
- **Menús «⋮» de las tablas (gestor de archivos, grupos, directorios, Wazuh): nunca más recortados ni fuera de la pantalla.** En la última fila de una tabla, la tabla recortaba el menú y ganaba una barra de desplazamiento; en un teléfono, salía de la pantalla por la izquierda. Ahora se abre por encima de la página, debajo de su botón o encima si falta sitio, siempre entero, y sigue a la página cuando se desplaza.

## 2.200.0 — 2026-11-25

- **Android: siempre accesible, con la pantalla apagada y el dispositivo bloqueado.** Android duerme el procesador cuando ya nada lo retiene: los contactos de la aplicación se detenían y loco perdía el dispositivo al cabo de una hora de reposo aproximadamente. Ahora, enchufada, la aplicación mantiene el procesador despierto (la pantalla sigue apagada) y el Wi-Fi fuera de su ahorro de energía: contactos, canal persistente y depuración inalámbrica continúan como con la pantalla encendida. Con batería, Android la despierta cada cinco minutos, incluso en reposo profundo, para un contacto y para reabrir el canal. El ajuste *Siempre accesible* de la aplicación la mantiene también despierta con batería, a costa de un consumo mayor.
- **Android: se desactiva la optimización de la batería.** La aplicación la pide por sí misma, una vez por versión, mientras no esté desactivada — si no, Android corta también su red en reposo. En un dispositivo emparejado por adb, loco la desactiva él mismo en cuanto la aplicación indica que ha vuelto (lista blanca del reposo, actividad en segundo plano). La ficha del dispositivo muestra *Reposo no desactivado* mientras no lo esté.
- **MCP en Windows: `terminal_job` sigue una tarea en curso.** El lanzador separado de una tarea en segundo plano mantiene su salida (`out.txt`) abierta en escritura: la lectura, que pedía exclusividad, fallaba («el proceso no tiene acceso al archivo…») en lugar de devolver un estado, durante la tarea y justo después. Salida, código de salida y número de proceso se leen ahora en modo compartido, y un archivo momentáneamente ilegible ya no corta el seguimiento: la tarea conserva siempre un estado (en curso con su salida parcial, terminada…). Verificado en Windows 11.

## 2.199.0 — 2026-11-25

- **MCP: un comando que supera su plazo se detiene de verdad.** Hasta ahora, `terminal_run` devolvía el control al cumplirse el plazo y enviaba un Ctrl+C, que algunos programas ignoran (`cmd /c … .bat` pregunta entonces «¿Desea terminar el trabajo por lotes (S/N)?»): el shell seguía ocupado y todos los comandos siguientes caducaban. Ahora, al cumplirse el plazo, el shell y todos los procesos que el comando ha lanzado se cierran (Windows, Linux, Android), y la llamada siguiente abre un shell nuevo. Un shell que sigue ocupado se detecta en lugar de hacer caducar el comando siguiente: se cierra, el comando sale en un shell nuevo y la respuesta lo indica. `fresh` pide un shell nuevo; `reset` de `terminal_interact` sigue siendo la vía de socorro del terminal interactivo.
- **MCP: comandos en segundo plano.** `terminal_run` con `background` lanza un comando separado del terminal — sobrevive al cierre de la sesión — y devuelve un número de tarea; la nueva herramienta `terminal_job` da su estado, su código de salida y su salida, o la detiene con todo lo que ha lanzado. Para las compilaciones e instalaciones largas.
- **MCP: salida limitada, nunca un eco del comando.** `terminal_run`, `adb_shell` y `terminal_job` devuelven como máximo `max_output` caracteres (24 000 por defecto, por debajo del límite de los clientes MCP) conservando el principio y el final. Un script largo ya no sale en una sola línea de terminal: en Linux y en el shell adb, el eco y la edición de línea del shell reservado al asistente se desactivan y el script sale en líneas cortas — un comando adb cargado con 15 KB de base64 ya no vuelve reproducido en una respuesta de más de 60 KB.
- **MCP: archivos hacia y desde un dispositivo Android, por adb.** Nuevas herramientas `push_file` y `pull_file`, como `adb push` / `adb pull`, con los permisos del usuario *shell* de Android (`/data/local/tmp`…): el contenido va en el cuerpo de la petición (nunca en una línea de comandos) o va directamente de un equipo del parque al dispositivo, y a la inversa — un `.dex` construido en un PC Windows sale hacia la tableta sin rodeos —, y luego se comprueba en el dispositivo (tamaño y SHA-256). Donde la aplicación loco no tiene acceso, `write_file` y `read_file` pasan solos por adb.
- **MCP: `write_file` escribe en UTF-8 sin BOM, en todas partes, y lo dice.** Opciones `charset` (utf-8 por defecto, utf-8-bom, ascii estricto, utf-16le-bom) y `newline` (lf, crlf); la documentación de las herramientas recomienda `write_file` en lugar del terminal para depositar código (`Set-Content -Encoding UTF8` de PowerShell 5.1 añade un BOM que hace fallar javac).
- **MCP en Windows: `exit 3` ya no cierra la sesión.** El código de salida vuelve, la consola sigue abierta; un error de PowerShell da siempre el código 1.
- **Tokens MCP existentes**: los que tenían `terminal_run` reciben `terminal_job`, los que tenían `adb_shell` reciben `push_file` y `pull_file` (migración 61).
- **Android: cerrar la sesión de pantalla bloquea el teléfono o la tableta.** Unos segundos después del final de la última sesión de pantalla, el dispositivo se bloquea como con el botón de encendido (Android 9 y posterior; no los televisores). Una sesión reabierta en seguida lo encuentra tal cual.
- **Android: el terminal de la aplicación detiene también lo que un comando ha lanzado** (Ctrl+C, plazo superado, fin de sesión), no solo su `sh`.

## 2.198.1 — 2026-11-24

- **Desbloquear: el patrón se traza en diagonal.** En el panel del portal, cada punto ocupaba toda su casilla: un trazo en diagonal enganchaba los puntos vecinos al pasar. El panel se comporta ahora como la cuadrícula de Android: un punto solo se toma si el puntero pasa cerca de su centro, un gesto rápido no se salta ningún punto atravesado, el trazo sigue al puntero, y un punto saltado en una línea recta (1 → 3, 1 → 9…) se toma al pasar, como hace Android. El mismo panel en la sesión de pantalla y en *Controlar la pantalla*.

## 2.198.0 — 2026-11-24

- **Android: «Desbloquear» pasa a la sesión de pantalla.** Un botón de la barra de herramientas abre un panel donde trazar el patrón (o hacer clic en sus puntos en orden), o campos para el PIN y la contraseña; la aplicación loco despierta el dispositivo, hace aparecer la introducción del código y escribe allí el código. El código ni se guarda ni se registra, y la aplicación nunca lo escribe en otro lugar que la introducción del código: en un dispositivo ya desbloqueado, simplemente se descarta. El desbloqueo por adb deja la ficha del dispositivo por la barra de *Controlar la pantalla*.
- **Recordar el código de desbloqueo, solo para uno mismo.** Una casilla guarda el patrón (o el PIN, la contraseña) para las próximas sesiones en este dispositivo: *Desbloquear* desbloquea entonces con un clic. El código se sella en el servidor y queda vinculado a la cuenta y al dispositivo; nadie más, administrador incluido, puede usarlo, nunca se muestra, y el diario solo anota su registro, su uso y su olvido (migración 60).
- **OnePlus, Oppo, Realme (OxygenOS, ColorOS): se reconoce el patrón.** La cuadrícula de estas capas lleva otro nombre que la de Android de serie: la aplicación no la encontraba y el código escrito no tenía efecto. Ahora la encuentra, como las de las capas que la nombran de forma parecida, y hace aparecer la introducción del código mediante la petición oficial a Android (el deslizamiento hacia arriba iba a las notificaciones de la pantalla de bloqueo). Verificado en una OnePlus Pad 3 (OxygenOS 16).
- **Desbloqueo por adb: la cuadrícula se localiza en la pantalla.** Una vez abierta la introducción del código, loco lee la posición exacta de la cuadrícula en la descripción de la pantalla que da Android, en lugar de deducirla de la marca: el patrón pasa a la primera en una tableta OnePlus, en cualquier orientación. El cálculo por marca y la calibración siguen ahí para los dispositivos que no la indican.
- **OxygenOS / ColorOS: se acabó la pantalla negra tras el desbloqueo.** Estas capas ocultan las aplicaciones en la compartición de pantalla concedida de una vez por todas por el emparejamiento adb; solo seguían visibles las barras del sistema. La aplicación lo detecta, detiene esa compartición y muestra la pantalla por accesibilidad (unas tres imágenes por segundo; *Controlar la pantalla*, por adb, sigue siendo fluido).
- **adb: «failed to connect» reparado.** Otro programa de la cuenta loco que tomaba prestado el servidor adb de loco podía arrancarlo primero, al arrancar el servidor, con otra clave: los dispositivos emparejados rechazaban entonces a loco. loco detecta ese servidor y relanza el suyo con su clave. Un dispositivo que de verdad ya no reconoce a loco se indica tal cual («vuelva a emparejarlo») en lugar de un mensaje de adb.

## 2.197.1 — 2026-11-24

- **Funciones: la confirmación dice lo que cambia.** Al activar una función, la ventana de confirmación anunciaba que «los usuarios pierden el acceso a las funciones desactivadas», incluso cuando no se desactivaba nada. Ahora dice lo que se activa, lo que se desactiva — y lo que se desactiva con ello, por dependencia (desactivar *Mi parque* desactiva también el acceso remoto, los scripts…) —, solo habla de los servicios de red si uno de ellos cambia, y ya no aparece cuando nada cambia.

## 2.197.0 — 2026-11-24

- **Recursos compartidos (opcional): el servidor loco comparte carpetas en la red, por SMB, como un NAS.** loco instala Samba desde los repositorios del sistema y lo configura él mismo: solo cuentas (ni invitado ni anónimo), SMB 2 y posteriores en el puerto 445, sin NetBIOS ni impresoras, solo las redes de los equipos de loco. Cada recurso compartido vive en una **partición ya en servicio**, elegida con un clic — loco no formatea ni monta ninguna —, en una carpeta que crea él mismo a partir del nombre del recurso compartido: ninguna ruta que escribir. Cada partición se muestra como una barra (sistema y otros datos, archivos de los recursos compartidos, espacio reservado, espacio libre) y el tamaño límite se elige con el cursor, dibujado sobre esa barra; cada recurso compartido tiene su anillo de ocupación. En la appliance, Debian y Ubuntu; que activar en *Funciones*.
- **Tamaño límite por recurso compartido**: los equipos Windows ven el recurso compartido como un disco de ese tamaño; más allá, pasa **a solo lectura** para todos, la alerta aparece en *Por tratar*, y la escritura vuelve sola en cuanto se ha liberado espacio.
- **Usuarios de Samba, todos configurados en la página de los recursos compartidos**: una cuenta loco elegida en la lista (su identificador; desactivado y retirado con su cuenta), o un usuario solo de Samba, sin acceso a loco (puesto de digitalización, colega, impresora). El administrador elige su contraseña y sus permisos recurso por recurso (lectura, o lectura y escritura), desde el usuario o desde el recurso compartido; se puede desactivar sin perder su contraseña. Las contraseñas van a Samba por su entrada estándar, nunca en los argumentos, la base de datos ni el diario de loco.
- **Dispositivos de red: lecturas de fondo y copias de seguridad programadas, de nuevo en marcha.** Desde la 2.193.0, el bucle de fondo se detenía con un error silencioso: los dispositivos solo se leían al abrir su página, y las copias de seguridad de configuración programadas (cada día por defecto) nunca se hacían.
- **Servidor Debian o Ubuntu: la purga y el análisis de las cachés desde el portal funcionan.** El portal, confinado por systemd, no podía dejar estas peticiones para el agente de administración (la appliance no estaba afectada).

## 2.196.0 — 2026-11-24

- **Vista general, Mi parque y página Sistema más rápidas.** En un parque de 300 equipos, la vista general se muestra en 25 ms en lugar de 190 (33 consultas a la base de datos en lugar de 334), el estado del servidor consultado por las páginas en directo en 4 ms en lugar de 130, la página *Sistema* en 15 ms en lugar de 93, y Mi parque con su actualización en directo hace 18 consultas en lugar de 318. La carga del procesador se mide en segundo plano (cada visualización esperaba 120 ms para medirla); el certificado del portal, la lista de zonas horarias y las versiones de los servicios instalados ya no se releen en cada página; el último resultado de cada equipo se lee de una vez; la vista general ya solo calcula el color de los equipos, sin el detalle de sus motivos. Nuevo índice para las actualizaciones fallidas (migración 58).
- **Hojas de estilo y scripts comprimidos** por nginx en la appliance: la hoja de estilo pasa de 210 KB a 45 KB transferidos, el script del portal de 122 a 35 KB — páginas más ágiles en un enlace lento (VPN, teléfono).
- **Código aligerado**: cifrado de los secretos, sesiones de terminal mantenidas por el servidor (Home Assistant, Proxmox, dispositivos de red), claves de host SSH conocidas, envío de paquetes a los repositorios Linux y lectura de las búsquedas en los catálogos, reunidos cada uno en un solo lugar; código muerto retirado, así como 30 clases de estilo y 209 textos traducidos que ya no se usaban.

## 2.195.0 — 2026-11-24

- **Inscripción: el nombre DNS, luego la IP.** Cuando loco tiene un nombre DNS — la dirección del portal o, en su defecto, el nombre completo de su certificado de empresa —, el comando de instalación (Linux y Windows), el script de Linux, los paquetes .deb y .rpm, el MSI y el APK preconfigurados prueban primero ese nombre, y luego la IP del servidor. Un equipo que no reconoce el certificado del nombre (autoridad de empresa ausente del equipo o del conjunto de autoridades de los agentes) se inscribe por la IP, con el certificado de loco, en lugar de fallar. El agente (Linux, Windows, Android) guarda la otra dirección como respaldo. La página *Inscripción* muestra las direcciones y su orden; `SERVER` del MSI y el valor `Server` del registro (GPO) aceptan varias direcciones separadas por comas.

## 2.194.0 — 2026-11-23

- **Nueva fuente: Outfit**, en lugar de Bricolage Grotesque, en todo el portal y en la aplicación Android. Formas redondas y geométricas, diseño limpio, y compacta: las columnas de Mi parque y de las tablas siguen siendo legibles. Incluida con loco (ninguna llamada a un sitio externo), licencia SIL OFL; IBM Plex Mono sigue siendo la fuente de las direcciones, versiones y comandos.

## 2.193.0 — 2026-11-23

- **Dispositivos de red (experimental): switches Cisco y Extreme, cortafuegos Stormshield.** loco se enlaza sin agente con sus dispositivos, con una cuenta del dispositivo: Cisco IOS / IOS-XE (Catalyst, routers), Cisco Small Business (CBS, SG), Extreme EXOS / Switch Engine, Extreme VOSS / Fabric Engine — por SSH y por la línea de comandos —, y Stormshield SNS por su API HTTPS (SSH para el terminal). La contraseña se cifra en la base de datos y se entrega a ssh sin pasar por sus argumentos; la clave de host SSH y el certificado del cortafuegos solo se aceptan una vez confirmada su huella. También se llega a los dispositivos antiguos que solo hablan los algoritmos SSH de antes de 2015 (ssh-rsa, Diffie-Hellman grupo 14 con SHA-1, CBC). Una función experimental, que activar en *Funciones*.
- **Estado en Mi parque y en su página**: modelo, número de serie, versión, puertos activos, procesador, memoria, temperatura, tiempo encendido — leídos cada quince minutos; inaccesible, enlace fallido o firmware más reciente disponible se ven de un vistazo y en *Por tratar*.
- **Copia de seguridad de la configuración**, cada día por defecto (o cada seis horas, cada semana, bajo demanda), guardada **solo cuando cambia** — las marcas de tiempo que se mueven solas no cuentan — con el historial, las **diferencias** línea a línea entre dos versiones y la descarga. Archivos cifrados en el estado de loco: forman parte de su propia copia de seguridad. El cortafuegos Stormshield da su copia de seguridad completa (.na). Una nueva pestaña *Red* en *Copias de seguridad* las reúne.
- **Firmware**: deposite en la biblioteca de loco el archivo del fabricante (versión leída en su nombre, huella SHA-256 mostrada); loco señala los dispositivos de la misma plataforma que tienen una versión más antigua y la instala, con la configuración respaldada justo antes: Cisco IOS (copia scp, MD5 comprobado por el switch, arranque ajustado en él; modo INSTALL de los Catalyst 9000), EXOS (partición de respaldo), VOSS (añadir, activar y luego validar tras el reinicio), Stormshield (envío y activación por la API). Reinicio y vuelta seguidos, nueva versión constatada. Un Cisco Small Business solo carga un firmware por TFTP: loco lo señala, la instalación se hace desde su interfaz web.
- **Terminal** del dispositivo en el navegador (sesión registrada, contraseña entregada por loco) y **reinicio** seguido. Permisos aparte: consultar, respaldar y leer las configuraciones, instalar un firmware, reiniciar, terminal, enlazar.
- **Un solo indicador de estado** en las filas Proxmox y Home Assistant de Mi parque: la etiqueta («reinicio requerido», «enlace fallido»…) añadía un segundo. Una tarea en curso se señala en la columna de las acciones, como para un equipo.
- **Proxmox: núcleo fijado respetado.** Un núcleo fijado con `proxmox-boot-tool kernel pin` es aquel con el que el servidor se reinicia: si ya está en marcha, loco ya no pide reinicio (lo pedía sin fin en cuanto se instalaba un núcleo más reciente). La página del servidor señala la fijación.

## 2.192.0 — 2026-11-22

- **La tarjeta gráfica en las características del equipo** (pestaña *Detalles*), en Windows y en Linux: nombre, memoria de vídeo y versión del controlador, para cada tarjeta presente. En Windows, la memoria de vídeo se lee en el registro — WMI la limita a 4 GB — y no se cuentan los adaptadores de pantalla virtuales (escritorio remoto, pantallas virtuales); en Linux, las tarjetas son las del bus PCI, nombradas por nvidia-smi, el controlador de AMD o lspci.
- **Actualizar el controlador NVIDIA desde loco (Windows).** loco compara el controlador de cada tarjeta NVIDIA con el último controlador *Game Ready* publicado por NVIDIA para esa tarjeta y esa versión de Windows (servicio público de NVIDIA, leído en segundo plano): la ficha lo muestra en la pestaña *Actualizaciones* (sección *Controlador gráfico*) y en *Características*, con un botón *Actualizar el controlador*. El agente descarga el instalador oficial de download.nvidia.com, rechaza cualquier archivo que no esté firmado por NVIDIA, lo instala sin ventana ni reinicio, y luego comprueba la versión realmente instalada. Nunca lanzado por *Actualizar* ni *Actualizar todo*: solo bajo demanda. En Linux, el controlador NVIDIA es un paquete del sistema, actualizado con él.

## 2.191.0 — 2026-11-22

- **Los detalles de hardware también para Windows.** La ficha de un equipo Windows muestra ahora la sección *Características* (pestaña *Detalles*), como en Linux: procesador, memoria, disco del sistema, versión de Windows, virtualización, arranque, y la configuración de hardware — máquina y número de serie, placa base, BIOS, módulos de memoria (ranura, tamaño, tipo, velocidad, fabricante, referencia) y discos físicos (modelo, tamaño, SSD o disco mecánico). Lo lee el agente Windows (lectura de WMI) en el próximo inventario, una vez actualizado el agente.
- **Núcleos físicos y procesadores lógicos diferenciados**: «20 núcleos · 28 hilos» para un procesador con hyperthreading, en lugar de contar los procesadores lógicos como núcleos (Linux y Windows).
- **Tipo de disco desconocido** (disco de una máquina virtual en Windows) mostrado como «—» en lugar de «disco mecánico».
- El servidor MCP (`get_device`) da también estas características y la configuración de hardware, para Linux y Windows.

## 2.190.0 — 2026-11-22

- **Proxmox en Mi parque, como Home Assistant: actualizaciones, reinicio y terminal.** Un servidor Proxmox enlazado para sus copias de seguridad puede enlazarse también por SSH, con una clave propia de loco (clave de host confirmada por su huella) — la API de Proxmox no sabe instalar actualizaciones. Aparece entonces en Mi parque con sus actualizaciones, sus recursos y un botón *Actualizar*, y tiene su página: versión, núcleo (*reinicio requerido* cuando hay uno más reciente instalado), paquetes que actualizar (seguridad primero), máquinas, procesador, memoria, disco, actividad con registro en directo, terminal root.
- **Actualización segura del hipervisor.** `apt-get dist-upgrade` como el botón *Upgrade* de Proxmox (nunca `apt-get upgrade`), lanzado en el propio servidor: la actualización llega hasta el final aunque el enlace caiga o loco se reinicie — incluso cuando loco se ejecuta en ese Proxmox, lo que la página señala. Reinicio después como opción, solo si la actualización ha salido bien; rechazo de una actualización que retiraría `proxmox-ve`. Un Proxmox nunca se reinicia mediante una selección de Mi parque.
- **Mi parque muestra Home Assistant y Proxmox incluso sin ningún equipo inscrito** (seguían ocultos detrás de «Todavía no hay ningún equipo»).

## 2.189.0 — 2026-11-20

- **Salidas largas completamente legibles para los asistentes (MCP).** Cuando un comando, un script o el terminal remoto produce una salida más larga que el límite, el asistente recibe ahora el **principio** *y* el final del texto (con, en medio, la cuenta de los caracteres omitidos) en lugar de solo el final: el contexto y los errores, a menudo al principio, ya no se pierden. Se aplica al terminal (`terminal_run`), a los comandos y scripts (`get_script_run`, acciones recientes de un equipo). La salida sigue señalada como recortada.

## 2.188.0 — 2026-11-20

- **Más detalles de hardware en un equipo Linux.** La sección *Características* (pestaña *Detalles*) muestra ahora la marca, el modelo y el número de serie de la máquina, la placa base, la BIOS, los zócalos e hilos de ejecución del procesador, los módulos de memoria (ranura, tamaño, tipo, velocidad, fabricante) y los discos (modelo, tamaño, SSD o disco mecánico). *(Los equipos Windows vendrán después.)*
- **Icono de desinstalación más claro.** El botón para desinstalar un paquete o una aplicación usa ahora una papelera.

## 2.187.0 — 2026-11-19

- **Control fácil de un Android TV / Google TV.** En *Controlar la pantalla* de un dispositivo Android, una cruceta (↑ ← OK → ↓) permite navegar como con el mando, y las flechas del teclado (con Intro = OK) hacen lo mismo — indispensable en un televisor, que se controla con la cruceta y no al tacto.
- **Desinstalar un paquete o un programa (Linux).** En un equipo Linux, la pestaña *Paquetes instalados* permite desinstalar un paquete instalado voluntariamente, y la pestaña *Flatpak* una aplicación Flatpak, con confirmación. (Las dependencias del sistema no se ofrecen para desinstalar.)

## 2.186.1 — 2026-11-19

- **Corrección: página Wazuh.** La página *Wazuh* ya no se mostraba (error interno) en cuanto un equipo tenía el agente Wazuh instalado. Está corregido.

## 2.186.0 — 2026-11-19

- **Análisis del espacio en disco (tipo TreeSize), bajo demanda.** En la pestaña *Archivos* de un equipo, un botón *Espacio en disco* cambia la vista para mostrar, en la carpeta mostrada, cada elemento con su **tamaño real (contenido incluido)** y una barra proporcional, del más grande al más pequeño. Se hace clic en una carpeta para bajar a ella, exactamente como al navegar. (Equipos Windows y Linux, una vez actualizado el agente.)
- **Menús de acciones más limpios.** El botón de «tres puntos» de los archivos pasa a ser un pequeño menú discreto y redondo; en la página *Wazuh*, las acciones *Volver a aplicar* y *Retirar* de un equipo están ahora en ese mismo menú en lugar de mostrarse como enlaces pegados.
- **Acabados.** La pantalla «Ninguna programación» ya solo muestra un icono.

## 2.185.0 — 2026-11-19

- **Actualizaciones de HACS: funcionan de nuevo con Home Assistant 2026.11.** Desde la versión 2026.11, Home Assistant ha sustituido HACS por un *Marketplace* integrado que bloquea las actualizaciones mientras no se haya aceptado una advertencia — una aceptación registrada por separado para cada usuario. Como loco actúa con su propio acceso, sus actualizaciones fallaban («error 500»). loco acepta ahora esa advertencia para sí mismo antes de instalar, y la actualización llega a término. Ante cualquier otro problema, se muestra ahora el mensaje exacto de Home Assistant en lugar de un «error 500» opaco.
- **Botón «Actualizar» en la fila de Home Assistant de Mi parque.** Cuando una instancia de Home Assistant tiene actualizaciones disponibles, aparece un botón *Actualizar* directamente en su fila en *Mi parque*, como para los demás equipos.

## 2.184.0 — 2026-11-19

- **Actualizaciones de HACS (Home Assistant Community Store).** En la página de una instancia de Home Assistant, loco lista ahora los repositorios HACS instalados (tarjetas, integraciones y temas de la comunidad), señala aquellos para los que hay una actualización disponible, y permite actualizarlos — uno a uno, desde la selección de *Mi parque*, o con *Actualizar todo* — en el mismo lugar que el núcleo y los complementos.

## 2.183.1 — 2026-11-19

- **Corrección: ficha de un equipo con tarjeta gráfica.** La página de un equipo cuya tarjeta gráfica no comunica su temperatura (o su ocupación) ya no se abría (error interno). Está corregido.
- **Botón «Refrescar» mejor colocado.** El botón que llama al agente para rehacer el inventario de inmediato ha pasado a la barra de acciones, arriba de la ficha (estaba mal colocado y aparecía duplicado en la tarjeta Inventario).

## 2.183.0 — 2026-11-19

- **Transferencias de archivos más claras, y cancelables.** En la pestaña *Archivos* de un equipo, cada envío o descarga muestra el tamaño transferido (MB / GB), el porcentaje y la velocidad, y una cruz permite cancelarlo por el camino.

## 2.182.0 — 2026-11-19

- **Tarjeta gráfica en la supervisión.** Los equipos con una tarjeta gráfica (NVIDIA, y AMD en Linux) muestran su ocupación, su memoria y su temperatura en la pestaña *Supervisión*, junto al procesador y la memoria.
- **Home Assistant en la selección de Mi parque.** Las instancias de Home Assistant se pueden marcar como los demás equipos y lanzar sus actualizaciones disponibles, o reiniciarlas, desde la barra de selección.
- **Refrescar un equipo bajo demanda.** Un botón en la ficha de un equipo llama al agente para rehacer el inventario de inmediato, sin esperar al próximo contacto.
- **Foto de perfil.** Cada cuenta puede añadir una foto (en *Mi cuenta › Apariencia*), mostrada en la barra superior en lugar de su inicial.
- **«No volver a mostrar» en los recuadros de información.** Los recuadros de información naranjas tienen ahora una cruz para no volver a verlos; esa elección se guarda para su cuenta.
- **Barra de navegación: «Sistemas».** El menú que reúne Linux, Windows, Android y Home Assistant se llama ahora *Sistemas*.

## 2.181.0 — 2026-11-19

- **Recursos de sus Home Assistant OS.** En la página de una instancia de Home Assistant OS, loco muestra ahora la memoria y el procesador además del almacenamiento, leídos en directo.
- **Batería de los dispositivos Android.** La ficha de un teléfono o de una tableta muestra su nivel de batería (y si se está cargando); nada para un dispositivo sin batería (decodificador de TV).
- **Menús «Sistema» y «Administración» más limpios.** Se muestran en una sola columna con un desplazamiento discreto cuando son largos, en lugar de un bloque que desbordaba.
- **Icono de Home Assistant a juego con el resto.** En *Mi parque*, el icono de una instancia retoma el color neutro de los demás iconos de dispositivo.
- **Corrección: actualización de un programa en la sesión del usuario.** Cuando un instalador debe ejecutarse en la sesión abierta (como iCUE), loco informa ahora fielmente del resultado real en lugar de un mensaje engañoso. *(Nota: el instalador de Corsair iCUE termina a veces con error por su propia cuenta durante una actualización; es un defecto de ese instalador, no de loco.)*

## 2.180.0 — 2026-11-18

- **Windows: los programas rebeldes por fin se actualizan (alternativa a winget).** Tres casos en los que winget fallaba en un parque real se tratan ahora, cada uno con el método adecuado, desde las fuentes oficiales de los editores:
  - **HWiNFO**: el manifiesto público de winget seguía fijado en una versión cuyo instalador el editor había retirado (error 404). loco encuentra la versión oficial actual y coloca el instalador del editor tras **comprobar su firma digital**.
  - **Corsair iCUE**: su instalador terminaba bruscamente cuando se lanzaba en segundo plano. loco lo relanza en la sesión del usuario conectado, donde llega a término.
  - **Google Play Games**: se actualiza solo (Google gestiona sus versiones) y sin embargo aparecía sin fin como «por actualizar». loco aplica la actualización y luego deja de volver a proponerla.
- Nada de esto añade nada al catálogo de loco: estos programas simplemente se siguen.
- **Barra de navegación más equilibrada.** Las entradas están centradas (se acabó la impresión de vacío a la derecha) y *Vista general* vuelve a mostrar su nombre junto al icono. Los menús desplegables se abren y se cierran con un breve retardo, más agradables de recorrer.
- **Home Assistant en Mi parque, depurado.** El verdadero icono de «casa» de Home Assistant (a juego con el estilo de los demás iconos), solo la dirección de la instancia, y una fila que se comporta como los demás equipos.
- **Pequeños acabados.** Las barras de almacenamiento de la vista general se animan al mostrarse como el resto del panel; el texto de bienvenida del terminal de Home Assistant ya no se estira.

## 2.179.0 — 2026-11-18

- **Home Assistant: una página por instancia.** Cada instancia tiene ahora su propia página (versiones, actualizaciones, complementos, copias de seguridad, registro, terminal y acciones); la página *Home Assistant* se ha convertido en una lista clara de instancias que llevan a ellas.
- **Sus Home Assistant en Mi parque.** Las instancias de Home Assistant aparecen como filas de *Mi parque*, junto a los equipos Windows, Linux y Android — con su estado y las actualizaciones disponibles; un clic abre su página.
- **Menú «Sistema» revisado.** Linux, Windows, Android y Home Assistant se reúnen en un solo menú *Sistema*, más ancho y equilibrado (secciones Linux, Windows, Android, Home Assistant).
- **Ficha de un equipo: más aire.** La etiqueta *Actualizaciones · Al día* ya no está pegada al bloque de las tarjetas.

## 2.178.0 — 2026-11-18

- **Windows: los programas instalados «para todos» por fin se actualizan.** Algunos programas (PdfCraft, PhotoCraft, WordCraft…) se instalan para todos los usuarios; su instalador rechazaba entonces la actualización lanzada por loco («already installed for everyone, pass ALLUSERS=1», error MSI 1603 / `0x8A150049`). El agente vuelve a intentarlo ahora pasando `ALLUSERS=1` al instalador, lo que actualiza efectivamente la copia común — sin nadie conectado.
- **Barra de navegación: un solo menú «Sistema».** Linux, Windows y Android ya no son tres entradas separadas arriba: se reúnen en un menú **Sistema** (secciones Linux, Windows, Android), como el menú Administración. La barra es más clara.

## 2.177.0 — 2026-11-18

- **Windows: los programas de terceros se actualizan aunque estén abiertos, y un fallo ya no bloquea la actualización.** Cuando winget no consigue actualizar un programa fuera del catálogo porque está en uso (archivos bloqueados, instalador MSI fallido `0x8A150049`), el agente **cierra ese programa — sin confirmación — y vuelve a intentarlo**. Si aun así falla, la actualización del equipo ya no se da por fallida por ello: Windows Update y los programas del catálogo deciden solos el resultado; el programa simplemente se señala y **sigue propuesto**, reintentado en la siguiente actualización. Como el agente funciona como servicio (cuenta SYSTEM), una actualización programada fuera de sesión actualiza estos programas sin nadie conectado.
- **Mi parque: los contadores de actualizaciones pasan a ser insignias.** «N sistema», «N programas» y «N de seguridad» se muestran como insignias a juego con el estilo (icono de pantalla, paquete, escudo), en lugar de un simple texto.

## 2.176.0 — 2026-11-18

- **API REST (HTTP)**: los tokens del *Servidor MCP* abren ahora una **API REST** — cada herramienta se convierte en una ruta HTTP corriente, para un script, una supervisión (Zabbix, Grafana, un cron) u otro programa, sin cliente MCP. Base `https://<servidor>:8443/api/v1`, cabecera `Authorization: Bearer …`; mismos tokens, mismos permisos, mismo parque y mismos rastros en el diario («· API «nombre del token»») que el servidor MCP. Las lecturas son `GET` (equipos, grupos, programas instalados, catálogo, scripts y sus ejecuciones, vista general, estado del servidor, diario), las acciones `POST` o `DELETE` (inventario, actualización, reinicio, encendido, instalación, cancelación, lanzar un script), puestas en cola como para el servidor MCP. La descripción **OpenAPI 3.1** se sirve sin token en `/api/v1/openapi.json`, y `GET /api/v1` lista las rutas que el token puede llamar.
- **Los recursos de las máquinas desde la API**: `GET /api/v1/devices/{id}/resources` devuelve la última lectura de un equipo — procesador (porcentaje, núcleos, carga), memoria y swap **en bytes** (total, usado, disponible) y en porcentaje, cada disco (punto de montaje, bytes, porcentaje), caudal de red y tiempo desde el arranque; `?history=1h|24h|7d` añade la curva. `GET /api/v1/resources?sort=memory` (o `cpu`, `disk`) clasifica todo el parque por uso, para localizar de una vez las máquinas cargadas.

## 2.175.0 — 2026-11-18

- **Windows: actualizaciones de los programas fuera del catálogo**: los programas instalados que winget sabe seguir (7-Zip, Notepad++, VLC, Remote Desktop Manager…) aparecen en la pestaña *Actualizaciones* del equipo, junto a las actualizaciones de Windows, con la versión instalada y la nueva — sin añadirlos al catálogo de loco. Se marcan uno a uno o *Actualizar* los toma todos (equipo, todo el parque, programaciones); el agente los actualiza por winget, en silencio, con la cuenta SYSTEM. Cuentan en el indicador y en Mi parque, y se bloquean como una actualización de Windows, por su nombre o su identificador winget. La lectura se hace con `winget export` en el equipo y la comparación con el índice winget de loco, renovado cada día. Sin winget en el equipo, la ficha lo indica. No se ven los programas instalados para un solo usuario.
- **En un Windows en francés, los programas publicados solo en inglés también se actualizan** (Remote Desktop Manager, por ejemplo): winget los consideraba «no aplicables» por falta de instalador en francés; el agente vuelve entonces a intentarlo aceptando el instalador en inglés. Lo que winget no sabe actualizar en un equipo se deja aparte, con su motivo, mientras su versión no cambie, y los componentes que Windows o su editor mantienen al día por sí mismos (App Installer, Edge, WebView2, Teams, OneDrive, Terminal, bibliotecas de la Store) nunca se proponen.
- **Actualizar la selección** ya no consulta Windows Update cuando solo hay programas marcados.

## 2.174.0 — 2026-11-18

- **Apariencia libre para los administradores**: una cuenta con el perfil *Administrador* ya no está sujeta al marco de la empresa (*Administración › Apariencia*) — su ventana *Apariencia* ofrece todos los temas y preajustes, todos los colores y todos los fondos, incluso cuando la empresa los limita o los impone. Las demás cuentas y la pantalla de inicio de sesión siguen dentro del marco.
- **Su propio logo**: en la misma ventana, un administrador elige el logo que ve — el de la empresa, el de loco, o una imagen propia (PNG, JPEG, GIF, WebP o SVG, 2 MB como máximo), en la cabecera y en el icono de la pestaña. Esa elección solo vale para él. Cambiar de logo recarga la página, con la ventana reabierta, sin perder el tema que se acababa de probar.

## 2.173.1 — 2026-11-18

- **Copias de seguridad en un mismo lugar**: la pestaña *Copias de seguridad* de la página *Sistema* duplicaba la nueva página *Copias de seguridad*; se une a ella. Descargar, restaurar o eliminar una copia de seguridad de loco, ajustar su número y restaurar desde un archivo se hacen ahora en *Administración › Copias de seguridad*, pestaña *loco*; la copia externa es allí la pestaña *Destino*. La tarjeta «Última copia de seguridad» de *Sistema*, las alertas de la vista general (copia de seguridad atrasada, copia externa fallida) y las páginas de la documentación llevan a la nueva página. En un servidor Windows, la pestaña *loco* muestra las copias de seguridad y los comandos, como hacía *Sistema*.
- **Copias de seguridad de loco en el teléfono**: cada fila aplastaba el nombre del archivo (una letra por línea) y elegir un archivo que restaurar ensanchaba la página en una pantalla pequeña; el nombre pasa encima de los botones y el campo se estrecha.

## 2.173.0 — 2026-11-18

- **Página Copias de seguridad** (*Administración › Servidor › Copias de seguridad*): lo que se respalda — el propio loco, sus Home Assistant, sus máquinas Proxmox —, dónde y cuándo, en un mismo lugar. El destino externo (NAS montado, S3, SFTP) también se configura allí, y se elige lo que va a él: las copias de seguridad de loco (casilla *Copiar también las copias de seguridad de loco*, marcada por defecto), y los Home Assistant uno a uno.
- **Proxmox VE** (función opcional): un servidor Proxmox se enlaza mediante un **token de API** creado para loco, con permisos limitados a la copia de seguridad (se dan los comandos `pveum`). El secreto se guarda cifrado y nunca vuelve a mostrarse; el certificado de Proxmox nunca se acepta de oficio: se muestra su huella SHA-256, que comparar con la de Proxmox antes de confirmar, y el token solo va hacia ese certificado (un certificado renovado lo detiene todo hasta una nueva confirmación). La página lista las máquinas virtuales y los contenedores con su última copia de seguridad; se marcan, se elige el almacenamiento de Proxmox (disco, NAS declarado en Proxmox, Proxmox Backup Server) y el modo (instantánea, suspensión, parada), y luego *Respaldar ahora* o *Programar…* (una vez, cada día, cada semana, cada mes; las máquinas marcadas, o todas incluidas las futuras). Cada copia de seguridad se sigue hasta el final — incluso tras un reinicio de loco, ya que Proxmox continúa la tarea —, con su registro, y puede cancelarse. La conservación de una programación solo elimina sus propias copias de seguridad, nunca las demás ni las copias protegidas. *Declarar un NAS…* añade un recurso NFS o SMB como almacenamiento de Proxmox (la contraseña se entrega a Proxmox, loco no la guarda). Permisos: consultar, respaldar, enlazar.
- **Home Assistant: copia de las copias de seguridad al destino externo**: *Copiar ahora* (con, si se desea, una copia de seguridad completa justo antes) o programada; la copia de seguridad más reciente guardada en Home Assistant se descarga por la API del Supervisor — el token pasa por la entrada de curl, nunca como argumento —, se comprueba hasta el tamaño exacto, se cifra con la frase de contraseña del destino si la hay, y luego se deposita; solo se conservan las últimas copias de cada instancia (3 por defecto). Una copia de seguridad ya copiada no vuelve a salir.
- **Copia externa**: un envío S3 se hace ahora en flujo (una copia de seguridad de varios gigabytes ya no pasa por la memoria), un envío SFTP ya no se corta al cabo de tres minutos, y las copias de Home Assistant y las de loco nunca se eliminan unas a otras.

## 2.172.3 — 2026-11-17

- **Teléfono: la barra superior ya no se sale**: con Mi parque, Linux, Windows, Home Assistant o Android y Administración, desbordaba la pantalla en la mayoría de los teléfonos (la página se ensanchaba y se desplazaba). Ahora se estrecha según el espacio real — flechas, y luego etiquetas de los menús no abiertos — y cada entrada conserva su icono; Administración recupera el suyo cuando su etiqueta ya no cabe.
- **Ya no se sale nada en un teléfono pequeño (de 320 a 360 px)**: los nombres e identificadores largos sin espacio (aplicaciones del catálogo, paquetes Android, enlaces en una descripción, comando dentro de un texto) se cortan en lugar de ensanchar la página; el estado de Windows Update, la casilla «reinstalar» de un despliegue y los interruptores de las alertas pasan a la línea siguiente.

## 2.172.2 — 2026-11-17

- **Inscripción de Windows sin importar la autoridad de loco**: el comando de la página *Inscripción* fallaba en un equipo nuevo («No se puede establecer una relación de confianza para el canal seguro SSL/TLS»), ya que el certificado de loco todavía no se reconocía. Ahora reconoce ese certificado por su huella SHA-256, tanto para el script como para la descarga del MSI: funciona a la primera, y cualquier otro certificado sigue rechazándose. Un certificado que el equipo ya reconoce (autoridad de empresa) pasa como antes. Ejecútelo en Windows PowerShell (no PowerShell 7).
- **Home Assistant: enlace que fallaba con algunos complementos SSH** («sh: /dev/fd/64: line 14: syntax error: unexpected "(" (expecting "}")»): loco lanzaba sus comandos en un shell de inicio de sesión, que lee primero los perfiles del complemento; un perfil escrito para bash o zsh detenía `sh` antes de cualquier comando. Los comandos pasan ahora por un shell simple, y el token del supervisor, que necesita el comando `ha`, se toma del entorno del complemento (si no, de su perfil, leído sin ejecutarse). Nunca aparece en un comando.

## 2.172.1 — 2026-11-17

- **Páginas seguidas en directo, nunca más bajo sus dedos**: la ficha de un equipo se actualiza ahora en el sitio (cabecera, acciones en curso, pestañas *Actualizaciones*, *Historial* y *Detalles*) en lugar de recargarse entera cada minuto; una ventana o un menú abiertos, un texto seleccionado, una escritura, unas casillas marcadas o un registro desplegado ya no se pierden — la actualización espera, y un registro abierto sigue abierto. La misma regla para el seguimiento de las actualizaciones, el desarrollo de un script y el detalle de un despliegue, que solo se recargan cuando nadie está haciendo nada en ellos.
- **Wazuh en directo**: la página *Wazuh* sigue las instalaciones, retiradas y estados que envían los agentes, sin recargar.
- **Se acabaron las páginas de error por el camino**: cuando loco se reinicia (una actualización) o una petición falla, Mi parque y las demás páginas seguidas ya no se recargan en una página de error; continúan en la siguiente lectura. Una petición sin respuesta ya no detiene el seguimiento, y volver a la pestaña lanza una lectura de inmediato.
- Un menú que abre una ventana se cierra.

## 2.172.0 — 2026-11-17

- **Barra de los equipos marcados más clara** (Mi parque): «2 equipos seleccionados» en lugar de «Selección: 2», y luego *Despertar*, *Reiniciar* y *Actualizar*. *Reiniciar* es nuevo: los equipos marcados se reinician un minuto después de su próximo contacto (permiso de reiniciar; los que ya se están reiniciando, los que cambian de versión del sistema o los dispositivos Android se dejan aparte y se cuentan). La casilla aislada «Reiniciar si es necesario» pasa a ser *Actualizar y luego reiniciar si es necesario* en el menú *Más*, con *Activar el encendido remoto*.
- **Aprobación antes del escritorio remoto, por grupo**: *Exigir la aprobación* y *Dejar de exigir la aprobación* salen de la barra de selección; ahora se configuran para todos los equipos de un grupo, desde el menú del grupo (*Grupos de equipos*), y siempre equipo por equipo en su ficha.

## 2.171.0 — 2026-11-17

- **Actualizar a una nueva versión del sistema**: cuando se publica la siguiente versión principal (Debian 13, Ubuntu 26.04 LTS, Fedora, nueva versión de Windows propuesta por Windows Update), loco la muestra — «Actualizar a Debian 13 (trixie)» en la ficha del equipo y en Mi parque, filtro *Situación › Actualización a una nueva versión* — y la instala bajo demanda, reinicio incluido. Debian: versión actual actualizada, rechazo si quedan paquetes retenidos, repositorios pasados a la nueva versión con copia de los archivos anteriores (un repositorio de terceros que todavía no la publica se desactiva, no se elimina), espacio en disco comprobado, repositorios dejados como antes si falta algo, y luego `upgrade` y `full-upgrade` sin preguntas. Ubuntu: la herramienta oficial `do-release-upgrade`, tras un reinicio si la versión actual lo pide, y luego se vuelve a poner el repositorio de loco para la nueva versión. Fedora: `dnf system-upgrade`, instalación sin conexión en el reinicio, versión comprobada a la vuelta. En Linux, la actualización se ejecuta fuera del agente, con su avance en directo y su registro en el equipo. Permisos: actualizar y reiniciar.
- **Windows: se acabaron las nuevas versiones de Windows por sorpresa**: una nueva versión propuesta por Windows Update (categoría «Upgrades») ya no sale con *Actualizar* ni *Actualizar todo*; se muestra aparte y solo se instala con su botón.
- **Reiniciar, junto a Actualizar**: en la ficha de un equipo, *Reiniciar* sale del menú *Más* y se coloca junto a *Actualizar*, con su propio botón de calendario.
- **Reinicios programados**: el calendario abre *Programar un reinicio* — una vez, cada día, cada semana o cada mes, a la hora del servidor. La página *Programaciones* propone también *Reiniciar* para grupos o todo el parque. Cada equipo se reinicia un minuto después de recibir la orden, y un mensaje avisa a la persona conectada. Permisos: reiniciar los equipos, y para un grupo o todo el parque, además el permiso «todo el parque». Los dispositivos Android no están afectados.

## 2.170.0 — 2026-11-17

- **Certificado de empresa sin dirección IP**: la solicitud de certificado (*Sistema › Certificado*) ya solo contiene los nombres DNS del servidor. Cuando los agentes llegan a loco por una dirección IP, conservan el certificado de la autoridad de loco, y el de su autoridad se presenta a quien llega por nombre (SNI) — navegadores, Claude Code, Codex. Antes, la IP de los agentes figuraba en él: una autoridad que restringe las direcciones IP (restricciones de nombres, por ejemplo una autoridad intermedia limitada a `lab.local` y a rangos de IP) hacía entonces que los clientes BoringSSL, entre ellos Claude Code, rechazaran toda la cadena («unsupported name constraint type»), aunque OpenSSL la validara.
- **Se señala el caso**: si el certificado de empresa en servicio contiene una dirección IP mientras una autoridad de la cadena restringe las IP, la página Sistema lo dice e indica los pasos que seguir (nueva solicitud, firma, importación). El certificado de empresa servido por nombre también lo vigila la alerta de caducidad.
- No cambia nada para un certificado que ya cubre la dirección de los agentes, ni cuando llegan a loco por un nombre.

## 2.169.0 — 2026-11-17

- **Claude y Codex: un instalador en lugar del mensaje precompletado**: las tarjetas Claude y Codex de la página *Servidor MCP* abren una ventana — nombre, duración y herramientas del token, sistema del ordenador — y luego descargan un instalador (Windows `.cmd`, macOS y Linux que lanzar con `sh`). Al abrirse, escribe la configuración MCP de la aplicación: `~/.claude.json` para Claude Code (comando `claude` y pestaña *Code* de la aplicación de escritorio), `~/.codex/config.toml` para Codex (aplicación de escritorio y comando `codex`), conservando el resto del archivo y una copia del anterior. Después se borra. Ningún enlace permite añadir un servidor MCP a estas aplicaciones, de ahí el instalador.
- **Certificado de loco**: si el equipo no lo reconoce y la autoridad de loco lo ha firmado, el instalador muestra su huella y propone confiar en él (almacén del usuario en Windows, llavero en macOS, ajuste de Claude Code o almacén del sistema en Linux).
- **Codex por dirección IP**: con el instalador, Codex también se conecta a un loco al que se llega por una dirección IP. La conexión a través de loco (OAuth), siempre posible desde un terminal, sigue pidiendo un nombre para Codex.

## 2.168.0 — 2026-11-16

- **Añadir loco a Claude o a Codex con un clic**: la página *Servidor MCP* propone dos botones, con los colores y logos de Claude y de Codex. Cada uno abre la aplicación de escritorio del ordenador con la petición de añadirlo ya escrita: una vez enviada, añade loco a la configuración **local** de la aplicación, y luego abre loco en el navegador para autorizar el acceso. Ningún token que copiar. Los comandos equivalentes para un terminal (`claude mcp add … && claude mcp login loco`, `codex mcp add … && codex mcp login loco`) se dan debajo.
- **Conexión a través de loco (OAuth 2.1)**: un cliente MCP puede ahora conectarse sin token preparado. Descubre loco, se registra solo, y la persona autoriza en loco, en la página *Autorizar Claude Code* (o Codex): nombre, duración y herramientas del token — la consulta viene marcada de oficio, nunca el acceso remoto. El token obtenido es un token MCP corriente, dentro de los límites de la cuenta que lo ha autorizado, visible en la lista («obtenido por …») y revocable. PKCE obligatorio, código devuelto solo a una aplicación del ordenador (bucle local), token válido solo para el servidor MCP de loco. Cerrar sesión en Claude Code también lo retira de loco.
- **Codex pide un nombre**: se niega a conectarse a través de loco si se llega a loco por una dirección IP privada. La página lo señala; declare un nombre en *Sistema › Red › Dirección del portal*.
- Los tokens creados a mano y su cabecera `Authorization` funcionan como antes. Vea [docs/es/mcp.md](docs/es/mcp.md).

## 2.167.0 — 2026-11-16

- **Wazuh: retirar el agente borra también su entrada en el manager**: cuando el agente Wazuh se desinstala de un equipo, loco retira su entrada del manager (`manage_agents -r`), y luego lo escribe en el informe de la retirada. Necesita un acceso SSH al manager, que configurar una vez en la página Wazuh (bloque *Retirada de las entradas del manager*): loco da una línea que añadir a `authorized_keys`, que solo autoriza a su clave ese único comando, con solo las cifras recibidas — ni shell, ni otro comando; la clave de host del manager se muestra y se confirma antes de cualquier intercambio, y un botón *Probar el acceso* lo comprueba todo sin retirar nada. Sin este acceso, nada cambia: la entrada sigue en el manager, y el informe lo dice.
- **Prudencia**: loco solo retira una entrada si el equipo estaba enlazado con el manager configurado y si indica que el agente está bien desinstalado; nunca la entrada `000` (el propio manager).
- **Se reconoce el manager Wazuh inscrito como equipo**: aparece como «Manager Wazuh» (en lugar de «Detenido — el servicio del agente Wazuh no se ejecuta»), no se cuenta entre los equipos que equipar, y loco no instala ni retira ningún agente en él. Llega con la actualización del agente loco del equipo (2.167.0).

## 2.166.1 — 2026-11-16

- **Wazuh: inscripción sin contraseña después de haber tenido una**: cuando se retira la contraseña de inscripción de los ajustes (*dejar de enviarla*), *Volver a aplicar* retira también del equipo el archivo `authd.pass` colocado antes. Sin ello, el agente Wazuh seguía enviando la contraseña anterior, y un manager que no espera ninguna la rechazaba en bucle («Invalid request for new agent»).

## 2.166.0 — 2026-11-16

- **Agente Wazuh en los equipos (opcional)**: que activar en *Funciones › Opcional*. Una página *Mi parque › Wazuh* configura la dirección de su manager, la versión del agente (elegida entre las publicadas por Wazuh), el grupo de agentes y, si hace falta, la contraseña de inscripción; y luego instala el agente Wazuh en los equipos marcados, o en un equipo desde su ficha (*Más › Instalar el agente Wazuh*). Windows, Debian, Ubuntu y familia Red Hat.
- **El paquete oficial, verificado, sin Internet en los equipos**: loco descarga una vez el paquete de Wazuh (deb, rpm o msi), lo comprueba con la huella SHA-512 publicada por Wazuh, y luego lo entrega a los equipos a través de su agente loco, que comprueba a su vez su huella antes de instalarlo.
- **El estado de cada equipo**: conectado, esperando al manager, desconectado, detenido o no instalado, con la versión, el número y el nombre del agente en Wazuh — en la página Wazuh y en la ficha del equipo. Cuando el agente no se conecta, el informe retoma el error de su registro (contraseña rechazada, nombre ya ocupado en el manager…). *Volver a aplicar*, *Actualizar* y *Retirar* completan el conjunto.
- **La contraseña de inscripción sigue siendo secreta**: guardada cifrada, nunca vuelve a mostrarse ni se escribe en el diario; en el equipo, va al archivo que lee el agente Wazuh (`authd.pass`, legible solo por los administradores), nunca como argumento de un comando.
- **Tres permisos** en los perfiles de acceso: consultar Wazuh, instalar y retirar el agente, ajustar Wazuh. Una cuenta limitada a una parte del parque solo actúa sobre ella.
- Los agentes loco se actualizan solos en la 2.166.0 para enviar el estado del agente Wazuh; una instalación pedida antes espera a su actualización. Vea [docs/es/wazuh.md](docs/es/wazuh.md).

## 2.165.0 — 2026-11-15

- **Docker sin clonar el proyecto**: se publica una imagen lista para usar, `ghcr.io/shanouhar/loco`, con cada versión. Basta con descargar `linux/docker/docker-compose.yml` en una carpeta y lanzar `docker compose up -d`; los ajustes van en un archivo `.env` al lado (modelo: `.env.example`). Actualización: `docker compose pull && docker compose up -d`. La variable `LOCO_IMAGE` elige la imagen, por ejemplo una versión concreta (`ghcr.io/shanouhar/loco:2.165.0`).
- **Desde las fuentes, nada cambia**: `sudo ./up.sh` sigue construyendo la imagen en el sitio. La construcción se describe en un archivo aparte, `docker-compose.build.yml`, que `up.sh` añade a `docker-compose.yml`. Quien lanzaba `docker compose up -d --build` directamente usa ahora `./up.sh`, o `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.

## 2.164.0 — 2026-11-15

- **Retirada la recuperación de las instalaciones anteriores a la 2.0**: loco ya no recupera una instalación, una copia de seguridad ni la configuración de un equipo anteriores a la versión 2.0, con el antiguo nombre del proyecto. Los instaladores de Linux, Windows y Docker ya no migran una instalación así; `loco-backup restore` y la página *Sistema* solo aceptan las copias de seguridad de loco; los scripts de los equipos (APT, RPM, Flatpak) ya no retiran los archivos colocados por esas versiones antiguas, y la antigua dirección del repositorio Flatpak ya no se sirve. **Para venir de una instalación tan antigua**, instale primero la 2.163.1, que hace la recuperación, y luego actualice.
- **Un repositorio APT ya publicado conserva su identidad**, sea cual sea (`Origin` y `Label` de su archivo `Release`): los equipos ya conectados siguen actualizándose sin confirmar nada.
- **Docker**: `up.sh` construye la imagen, arranca loco y espera a que esté sano, sin otro paso.
- **Documentación rehecha**: README con imágenes (recorrido del portal, animación de las actualizaciones en directo, esquema de funcionamiento), capturas nuevas tomadas en una instancia de demostración, una guía por tema en `docs/` y una nueva guía de explotación, [docs/es/operations.md](docs/es/operations.md): rutina, alertas, métricas, copias de seguridad, actualizaciones, certificados, accesos perdidos, incidentes.

## 2.163.1 — 2026-11-13

- **Tamaños más legibles**: un tamaño pasa a la unidad superior a partir de mil — 999,9 MB como máximo, luego 0,98 GB, 1,00 GB… Una caché APT de 1021,9 MB se lee ahora «1,00 GB». En todas partes donde loco muestra un tamaño: tabla Almacenamiento de la vista general, cachés, repositorios, copias de seguridad, Home Assistant, y lo que el navegador actualiza en directo.
- En español, los tamaños actualizados en directo por el navegador se muestran en «MB», como el resto de la página (aparecían en unidades francesas).

## 2.163.0 — 2026-11-13

- **Escritorio remoto: elegir la pantalla en KDE Plasma (Wayland)**: la lista *Pantalla* aparece también en un equipo KDE Plasma con varias pantallas — *Todas las pantallas*, o una sola. Plasma comparte entonces su «Espacio de trabajo completo» (primera tarjeta de su ventana *Compartir pantalla*, con el consentimiento pedido una vez en el equipo): loco recorta en él la pantalla elegida. Se tiene en cuenta una pantalla ajustada a otra escala; la elección se recuerda por equipo, como en otros lugares.
- **KDE Plasma: el ratón cae donde se hace clic de una pantalla a otra**: Plasma retiene el puntero un centenar de píxeles cuando pasa de una pantalla a la vecina; un clic hecho justo después de cambiar de pantalla caía al lado. El puntero se coloca ahora en el lugar correcto desde el primer movimiento.
- **KDE Plasma: una sola pantalla compartida entre varias**: cuando el consentimiento dado en el equipo cubría una sola pantalla mientras el equipo tiene varias, el ratón actuaba en otro lugar del que se hacía clic. loco reconoce ahora la pantalla compartida y coloca en ella el puntero. En este caso, no hay lista (solo se ve una pantalla); para compartir todo el espacio de trabajo, elimine `~/.config/loco/portal-token` en el equipo: la siguiente sesión vuelve a pedir el consentimiento.
- GNOME en Wayland sigue mostrando su pantalla principal. Estos cambios llegan con la actualización del agente del equipo (2.163.0).

## 2.162.0 — 2026-11-13

- **Escritorio remoto: elegir la pantalla de un equipo que tiene varias**: una lista *Pantalla* aparece junto a *Resolución* en cuanto el equipo tiene más de una pantalla — *Todas las pantallas* (una al lado de otra, como hasta ahora) o una sola, designada por su número, su tamaño y la mención *principal*. La imagen toma el tamaño de la pantalla elegida; el ratón y el teclado siguen siendo precisos. En pantalla completa, la lista sigue a los demás ajustes en el panel de mandos.
- **La elección se recuerda**: por equipo, en su navegador. La siguiente sesión en este equipo se vuelve a abrir en la misma pantalla; una pantalla desconectada durante la sesión devuelve la vista a todas las pantallas.
- **Windows**: los números son los de la configuración de pantalla de Windows. Una pantalla mostrada sola se lee como en un equipo con una sola pantalla (duplicación DXGI, solo cuando la imagen cambia), en lugar de la captura más lenta que imponían varias pantallas. El escritorio remoto cuenta ahora en píxeles reales en cada pantalla, incluso ajustadas a escalas diferentes.
- **Linux**: en un escritorio X11. GNOME en Wayland sigue mostrando su pantalla principal, KDE Plasma en Wayland la que se concedió en el equipo.
- La lista llega con la actualización del agente del equipo (2.162.0); un agente más antiguo muestra todas las pantallas, como antes.

## 2.161.0 — 2026-11-13

- **Funciones opcionales**: la página *Funciones* gana una sección *Opcional*, después de *Experimental*. Reúne lo que está terminado pero no afecta a todo el mundo; como lo experimental, está desactivado por defecto y cualquier cuenta con el perfil Administrador lo activa.
- **Home Assistant** (opcional): una página *Home Assistant* sigue y mantiene su instalación. loco entra por el complemento SSH de Home Assistant («Advanced SSH & Web Terminal» o «Terminal & SSH»), con una clave propia: no se pide ni se guarda ningún token de Home Assistant, y la clave privada no sale del servidor. La página da los tres pasos del enlace y la clave pública que copiar.
- **La clave de host se confirma**: antes de cualquier intercambio, loco muestra la huella de la clave que presenta la dirección indicada; no se guarda nada mientras no la haya confirmado. Si cambia después (complemento reinstalado), el enlace se detiene y vuelve a pedir una confirmación.
- **Lo que muestra la página**: las versiones de Home Assistant, del sistema y del Supervisor con la que está en espera, los complementos y su estado, el almacenamiento, las copias de seguridad, lo que señala el Supervisor (sin copia de seguridad reciente, disco casi lleno, instalación degradada…) y, bajo demanda, las 200 últimas líneas del registro. La lectura se rehace sola mientras la página está mostrada.
- **Actualizaciones**: una a una, o *Actualizar todo*, que las encadena en el orden aconsejado — el Supervisor, los complementos, Home Assistant y luego el sistema. La ventana de confirmación propone crear una copia de seguridad antes (marcada de oficio). Una actualización lanzada se muestra «en curso» y no se vuelve a lanzar; la actividad conserva las doce últimas tareas con su salida.
- **Copia de seguridad y reinicios**: *Respaldar* lanza una copia de seguridad completa, guardada en Home Assistant; *Reiniciar* reinicia Home Assistant o toda la máquina. Cuando el sistema se reinicia, loco espera su vuelta: la tarea solo sale bien si vuelve a responder.
- **Terminal**: el shell del complemento SSH se abre en la página de sesión de loco (pantalla completa, nueva pestaña), sesión registrada. Requiere la función *Acceso remoto* y un servidor loco en Linux.
- **Permisos y vista general**: un grupo *Home Assistant* en los perfiles de acceso separa consultar, actualizar, respaldar, reiniciar, abrir un terminal y enlazar instancias; solo el perfil Administrador los recibe de oficio. La vista general señala en «Por tratar» un enlace fallido, una instalación degradada y las actualizaciones disponibles.
- **En el servidor**: el enlace usa el cliente SSH del servidor — ya presente en la appliance, añadido por la instalación en los demás casos (sin bloquearla nunca). Las sesiones remotas abiertas en el momento de la actualización se cierran. Vea [docs/es/homeassistant.md](docs/es/homeassistant.md).

## 2.160.0 — 2026-11-13

- **Android: la pantalla en reposo se vuelve a encender cuando se toma el control**: al abrir una sesión de pantalla, la aplicación vuelve a encender la pantalla del dispositivo; si se vuelve a dormir durante la sesión, el primer clic o la primera tecla la vuelve a encender, como un dedo en un teléfono en reposo. Así se llega a la pantalla de bloqueo en lugar de a una pantalla apagada.
- **Android: el código del dispositivo se escribe con el teclado**: con el dispositivo bloqueado, escriba su código en su teclado y luego Intro — PIN o contraseña tal cual; para un patrón, los números de sus puntos, contados como en el teclado de un teléfono (1 arriba a la izquierda, 9 abajo a la derecha). Escrita desde la pantalla de bloqueo, la primera tecla hace aparecer la introducción del código. No se elude nada: hay que conocer el código, y los intentos fallidos cuentan como en el dispositivo.
- Android oculta la pantalla de introducción del código en la imagen transmitida: se muestra en negro, la escritura pasa igualmente. Una nota lo recuerda bajo la sesión, y la descripción del servicio de accesibilidad de la aplicación dice lo que hace ahora.
- Estas novedades llegan con la actualización de la aplicación Android (2.160.0).

## 2.159.0 — 2026-11-12

- **Mi parque: filtros en listas desplegables**: las dos filas de pastillas dejan paso a una barra de listas — *Estado*, *Sistema*, *Situación* (desconectado, reinicio necesario) y *Grupo*. Cada botón dice el filtro y el valor elegido, y se rellena cuando se aplica un filtro; cada fila de una lista da su número de equipos. Aparece un enlace *Borrar los filtros* en cuanto hay uno. La búsqueda y las páginas conservan los filtros elegidos.
- **Filtrar por distribución**: la lista *Sistema* ofrece la familia — Windows, Linux, Android — y luego, con sangría, sus distribuciones presentes en el parque, sin número de versión: Windows 10, Windows 11, Windows Server 2025, Debian, Ubuntu, Rocky Linux… Debian 12 y Debian 13 cuentan juntos. Los dispositivos Android tienen ahora su propia fila: ya no se cuentan con Linux.
- **Appliance y Docker: la parada del servicio ya no espera a las conexiones de los agentes**: desde las conexiones persistentes (2.158.0), nginx esperaba diez segundos a que los agentes devolvieran la suya antes de ser detenido a la fuerza; la parada del servicio pasaba de tres a doce segundos, y durante una actualización el servicio relanzado mientras tanto fallaba hasta cuatro veces de las cinco que tolera el sistema — más allá, loco se habría quedado detenido. nginx se detiene ahora de inmediato, y la actualización de la appliance espera a que el servicio anterior haya terminado de verdad antes de arrancar el nuevo. Las instalaciones Debian, Ubuntu y Red Hat no estaban afectadas.

## 2.158.1 — 2026-11-12

- **Un reinicio pedido ya no se vuelve a lanzar**: una vez que el equipo había tomado el reinicio, el botón *Reiniciar* volvía durante el minuto o más que precede a la parada, como si no se hubiera pedido nada — se podía lanzar un segundo. El equipo muestra ahora «reinicio en curso», con un indicador que gira, hasta su vuelta (cinco minutos como máximo si no vuelve); en su ficha, la entrada *Reiniciar* del menú Más se sustituye por «Reinicio en curso» y la tarjeta Reinicio también lo indica.
- **Una actualización en curso se ve, y no se vuelve a lanzar**: en Mi parque, el botón de un equipo deja paso a «actualización en espera» mientras el equipo no la ha tomado, y luego a «actualización en curso» con un indicador que gira; en la ficha, el botón *Actualizar* pasa a ser «Actualización en curso» y ya no se puede pulsar, y las casillas de selección de los paquetes desaparecen durante la actualización.
- **«Actualizar todo» ya no vuelve a contar los equipos ya lanzados**: el número entre paréntesis solo se refiere a los equipos restantes, y aparece al lado un botón «Actualizaciones en curso (n)», que lleva al desarrollo. Cuando todos los equipos ya están lanzados, solo queda él.
- El servidor rechaza una segunda petición con «ya en curso», aunque se formule de otra manera (con o sin reinicio, por la selección de equipos, por «Actualizar todo»), y un doble clic durante el envío ya no sale dos veces.
- El encendido de un equipo se muestra «encendido en curso» una vez enviado, en lugar de «encendido en espera».

## 2.158.0 — 2026-11-12

- **Sesiones remotas con conexión persistente**: el agente de un equipo y el navegador mantienen ahora una conexión abierta con loco (WebSocket, en el puerto HTTPS habitual). El servidor les avisa en cuanto se pide una sesión, se pulsa una tecla o el equipo responde, en lugar de esperar a que vengan a preguntar. El terminal, el escritorio remoto y la pestaña Archivos se benefician de ello. Medido en un equipo Debian: de la tecla a su eco, 62 ms de mediana antes, 25 ms ahora; una sesión abierta en reposo ya no produce ninguna petición (una decena por segundo antes); un terminal se abre en menos de 0,2 s, se haya abierto o no la ficha del equipo.
- **Las acciones salen de inmediato**: una actualización, un reinicio o un script pedido desde el portal despierta en seguida al agente conectado, que viene a buscarlo sin esperar a su próximo contacto.
- **Nada que ajustar, y un respaldo permanente**: ningún puerto que abrir en los equipos ni en el cortafuegos. Si la conexión persistente no es posible — agente todavía no actualizado, equipo de red que la corta, navegador que la rechaza —, la sesión pasa por idas y vueltas HTTPS como antes, sin corte: se puede pasar de una a otra durante la sesión. Los permisos, la aprobación del usuario antes del escritorio remoto, los límites de sesiones y el diario no cambian.
- **En el servidor**: el relé se ejecuta en el propio portal, en el puerto local 8090 (vecino del 8089), y nginx le transmite dos direcciones con las mismas reglas de acceso que el resto — `/agent/relay` para los equipos, `/fleet/session/…/ws` para la interfaz. La actualización rehace esta configuración. Si el puerto 8090 ya lo ocupa otro programa, loco se instala igualmente y las sesiones pasan por HTTPS.
- **Una pestaña en segundo plano ya no pierde su sesión**: mientras el navegador siga conectado, loco sabe que está presente, aunque el navegador ralentice las pestañas que no se miran.
- Agentes Linux, Windows y Android: el canal persistente llega con su actualización (automática para Linux y Windows). El agente Windows no se pudo probar en un Windows real antes de esta versión; en caso de problema, vuelve solo a las idas y vueltas HTTPS.

## 2.157.0 — 2026-11-12

- **Un terminal se abre de inmediato**: una sesión pedida esperaba a que el agente del equipo viniera a preguntar, lo que solo hacía cada cinco segundos en reposo — de ahí de dos a cinco segundos de espera al abrir un terminal, un escritorio remoto o la pestaña Archivos. En cuanto se abre la ficha de un equipo, o hay una sesión en marcha o recién cerrada, su agente viene ahora a preguntar varias veces por segundo: la sesión se abre en un cuarto de segundo aproximadamente. Medido en un equipo Debian: 3,9 s de mediana antes, 0,25 s ahora.
- **Incluso sin pasar por la ficha**: un equipo que nadie mira viene a preguntar cada segundo en un parque de 25 equipos como máximo, y luego cada vez con menos frecuencia a medida que crece el parque, hasta los cinco segundos de antes — el servidor nunca recibe más de 25 de estos intercambios por segundo, unos 2 ms cada uno. Un dispositivo Android en reposo mantiene su ritmo de tres segundos, por su batería.
- **Android: el primer texto de una sesión ya no espera**: tras una pausa larga, llegaba con más de un segundo de retraso.
- El seguimiento en directo de la pestaña Supervisión mantiene su lectura cada dos segundos, sea cual sea el ritmo de los intercambios.
- Los agentes Linux y Windows se actualizan solos; hasta entonces, mantienen su ritmo de cinco segundos. La aplicación Android sigue con su propia actualización.

## 2.156.0 — 2026-11-12

- **Mi parque y la vista general se mantienen al día sin recargarse**: cuando cambia el estado del parque, solo se sustituyen las partes afectadas de la página — se acabó la página que parpadea, el desplazamiento se queda donde está. En Mi parque, la fila de un equipo cuyo estado cambia se ilumina un instante; la ventana *Actualizar todo* sigue el número mostrado en su botón. No se sustituye nada mientras hay un menú o una ventana abiertos, ni durante una selección de equipos. Si la página debe cambiar de forma (primer equipo del parque, por ejemplo), se recarga como antes.
- **Vista general más visual**: el parque es un anillo coloreado según el estado de los equipos, con su número en el centro. El procesador y la memoria del servidor tienen, como la red, una curva de los dos últimos minutos, actualizada cada dos segundos; pasa a naranja y luego a rojo al acercarse a la saturación.
- **El color dice el nivel**: los indicadores (disco del servidor, recursos de un equipo) son verdes mientras todo va bien, naranjas y luego rojos en sus umbrales. El color de acento sigue siendo su ajuste.
- **Iconos para orientarse**: en los títulos de los paneles y en las tarjetas de la vista general, de Sistema, de la ficha de un equipo, de los paquetes Linux y de Windows Update.
- **Estados vacíos más legibles**: un icono encima de la frase, en todas partes donde una lista está vacía. Una caché Linux o de Windows Update vacía dice cuándo se llenará, en lugar de una cabecera de tabla encima de nada.
- **Un poco de movimiento**: los paneles entran con suavidad al llegar a una página, las pestañas con fundido, los botones se hunden ligeramente. Ningún movimiento en una recarga, ni cuando el sistema pide reducir las animaciones.

## 2.155.0 — 2026-11-12

- **Android: las actualizaciones de la aplicación sin Play Protect, por adb**: en un dispositivo emparejado por adb y accesible, loco instala él mismo la nueva versión de la aplicación, como desde un ordenador — ni ventana de confirmación, ni análisis propuesto por Play Protect en el dispositivo. Si la instalación por adb falla (dispositivo fuera de la red, depuración inalámbrica apagada), la aplicación retoma el control y se actualiza sola, como antes. Nunca durante una sesión remota abierta; cada instalación queda inscrita en el diario.
- **Preparar el registro de la aplicación ante Google**: Android exige poco a poco que una aplicación instalada fuera de Play Store venga de un desarrollador registrado. El registro se hace una vez, por el titular de la clave de firma de la aplicación, y vale para todas las instancias de loco. La construcción del APK incorpora ahora el token de prueba dado por la Android Developer Console, si se coloca junto a la clave; la documentación de Android describe los pasos que seguir.
- Los permisos configurados por adb tras el emparejamiento ya no se configuran dos veces cuando llegan dos conexiones al mismo tiempo.

## 2.154.0 — 2026-11-11

- **Android: una imagen fluida en cada sesión, que configurar desde el teléfono**: sin emparejamiento adb, Android limita la pantalla remota a unas tres imágenes por segundo. El nuevo panel *Imagen fluida en cada sesión* de la aplicación hace el emparejamiento sin pasar por el portal: **Configurar** abre *Depuración inalámbrica*; actívela, toque *Vincular dispositivo con código de sincronización* y escriba el código de seis cifras en la notificación de loco, sin cerrar esa ventana. La aplicación encuentra sola el puerto y transmite el código a loco. Después, cada sesión se abre con una imagen fluida, sin ventana «Proyectar» (unas treinta imágenes por segundo medidas en un OnePlus 12).
- **Un emparejamiento limitado al propio dispositivo**: loco solo empareja con la dirección de donde viene la petición, declarada por el dispositivo, nunca una dirección elegida por quien llama. El dispositivo debe estar por tanto en la misma red que loco. El código no aparece en ningún comando, y el emparejamiento queda inscrito en el diario como hecho «desde el dispositivo».

## 2.153.0 — 2026-11-11

- **Un icono dice de qué tipo de equipo se trata**: una pequeña pantalla delante del nombre de los ordenadores (Debian, Ubuntu, Red Hat, Windows), y para los dispositivos Android un teléfono, una tableta o un televisor. Se ve en Mi parque, en el título de la ficha de un equipo, en el desarrollo de las actualizaciones, en los grupos de equipos y en la elección de los equipos de un despliegue.
- **Android: teléfono, tableta o televisor, reconocido por la aplicación**: la aplicación anuncia el tipo del dispositivo — televisor con Android TV, tableta cuando la pantalla mide al menos 600 dp de ancho (el umbral de Android), teléfono si no. La ficha del dispositivo lo indica en *Detalles › Tipo de dispositivo*. Un dispositivo cuya aplicación es más antigua se muestra como un teléfono, y su ficha lo precisa, hasta que la aplicación se actualice. Un teléfono plegable abierto se ve como una tableta.

## 2.152.0 — 2026-11-11

- **Mi parque mucho más rápido**: la página consulta al servidor cada dos segundos para mantenerse al día, y cada consulta recalculaba el estado de todos los equipos. Lo más costoso era comparar los programas instalados en cada equipo Windows con el catálogo, rehecho varias veces por equipo. Lo que la lista saca del inventario de un equipo se guarda ahora en memoria y solo se recalcula cuando cambian ese inventario o el catálogo. En un parque simulado con un catálogo de 30 programas: 5,5 ms en lugar de 528 ms para 100 equipos, 34 ms en lugar de 5 segundos para 1000 equipos. La primera consulta tras un reinicio del portal sigue siendo más larga (0,6 segundos para 1000 equipos).
- **La vista general también se beneficia**: ya no vuelve a cargar el inventario completo de cada equipo para contar los indicadores.
- **Un historial largo de acciones ya no ralentiza la lista**: las acciones en curso de todo el parque se leen en una sola consulta, y la última acción terminada de un equipo se encuentra sin releer todo su historial.

## 2.151.0 — 2026-11-11

- **Tras esta actualización, vuelva a iniciar sesión una vez**: las sesiones abiertas antes de ella ya no se reconocen.
- **Cerrar sesión cierra de verdad la sesión**: el servidor conoce ahora cada sesión del portal. Una vez cerrada la sesión, una cookie copiada antes ya no vale nada. Una sesión dura siete días como máximo, aunque se use sin parar; sigue caducando tras ocho horas de inactividad, y cambiar la contraseña cierra las demás sesiones de la cuenta.
- **Autenticación de dos factores: un código solo abre una sesión**: un código ya usado se rechaza («Este código ya se ha usado»), hay que esperar al siguiente. Alguien que haya visto escribir el código ya no puede reutilizarlo en el mismo minuto.
- **La clave de inscripción ya no se queda en los equipos**: los paquetes `.deb` y `.rpm` del agente conservaban su script de instalación, legible por todos los usuarios del equipo, con la clave de inscripción dentro. Ahora solo lo puede leer root, y se sustituye por una simple nota en cuanto el equipo queda inscrito; los equipos ya instalados por un paquete los limpia su agente en su próximo contacto. Durante la inscripción, la clave ya no aparece en la lista de procesos del equipo.
- **La clave de inscripción ya no se escribe en el registro**: el registro de accesos del portal (nginx) ya no guarda los parámetros de las direcciones, por tanto tampoco el `?key=…` de los comandos de inscripción.
- **Asistentes de IA**: un comando escrito por el asistente apunta como máximo a 25 equipos por llamada, para que un error de destino no salga de una vez hacia todo el parque; un script de la biblioteca, escrito y guardado por una persona, conserva el límite de 500. Las instrucciones dadas al asistente precisan que lo que vuelve de un equipo (salida de un comando, texto de una pantalla, contenido de un archivo) es un dato, nunca una instrucción que seguir.
- **Copias de seguridad**: `SECURITY.md` decía erróneamente que estaban en 0600. Están en 0640 `root:loco` para que el portal pueda ofrecerlas en descarga y enviarlas fuera, y contienen las claves privadas del servidor y de la autoridad local: un archivo se protege como el propio servidor.

## 2.150.0 — 2026-11-11

- **Escritorio remoto: el teclado numérico funciona**: en un equipo Windows, las teclas del teclado numérico escribían caracteres sin relación (glifos coreanos). Las cifras, los operadores, Intro y las teclas de navegación del teclado numérico llegan ahora como las teclas esperadas. En Linux, la cifra escrita es la que llega, sea cual sea el estado de Bloq Num en el equipo; en la consola de texto, donde el teclado numérico no escribía nada, también funciona. En Android, el teclado numérico escribe sus caracteres.
- **Android: se acabaron las pulsaciones perdidas**: al escribir con el teclado en un campo de un dispositivo Android, podía desaparecer aproximadamente un carácter de cada dos. Corregido.
- **«Ajustada a la ventana» dice cuándo el equipo no puede seguir**: algunos equipos no ofrecen la resolución pedida — una máquina virtual Windows con la tarjeta gráfica básica a menudo solo propone una. La imagen se ajusta entonces a la ventana, y la sesión lo indica ahora: *El equipo no ofrece esta resolución*, con en la información emergente el tamaño pedido, el que ha conservado el equipo, y el remedio (una tarjeta y un controlador gráficos completos, VirtIO-GPU o QXL).

## 2.149.1 — 2026-11-11

- **Escritorio remoto en pantalla completa: un panel centrado y legible**: el panel de mandos sigue en el borde izquierdo, pero a media altura de la pantalla en lugar de la esquina superior. Ya no se corta nada: el estado de la sesión tiene su propia línea y se muestra entero, y las listas *Resolución* y *Zoom* ocupan todo el ancho.
- **Salir de la pantalla completa se ve**: el botón está escrito con todas sus letras, con un icono de «reducir», en lugar de un icono solo. *Nueva pestaña* y *Cerrar la sesión* siguen como iconos a su derecha.

## 2.149.0 — 2026-11-11

- **Actualizar el agente Android desde la aplicación**: cuando loco propone una versión más reciente, la pantalla de la aplicación muestra *Actualización de la aplicación* con la versión disponible y el botón **Instalar la actualización**. La aplicación se sigue actualizando sola; este botón la instala de inmediato, sin esperar. Abrir la aplicación consulta a loco en el acto.
- **Se sabe en qué punto está el dispositivo**: abajo de la pantalla, la línea de versión indica «actualizada» o la versión disponible. Si falta el permiso para instalar aplicaciones, el botón lleva primero allí; y lo que impide una instalación (loco inaccesible, archivo inesperado, rechazo en el dispositivo) se escribe bajo el botón.
- **Tras la instalación**: Android cierra la aplicación para sustituirla. Vuelve a arrancar sola, una notificación *Aplicación actualizada* lo confirma, y su pantalla se vuelve a abrir cuando Android lo permite.
- Fuera de sus redes de confianza, el dispositivo no contacta con loco: el botón no se ofrece allí.

## 2.148.0 — 2026-11-10

- **Escritorio remoto Android mucho más rápido**: la pantalla de un dispositivo Android llegaba a trompicones, con más de un segundo entre un clic y su efecto. La aplicación rehacía una conexión segura completa en cada uno de sus intercambios con loco (una treintena por segundo): ahora mantiene la conexión abierta. Lo que se mueve en la pantalla sale en JPEG, dos veces más ligero, y luego la imagen vuelve a ser nítida en cuanto la pantalla se detiene; y la aplicación envía las imágenes sin esperar a que el navegador las pida una a una, ajustándose a lo que realmente ha mostrado. En el dispositivo de prueba: unas diez imágenes por segundo en lugar de una y media, y un tercio de segundo entre un clic y la imagen en lugar de un segundo y medio.
- **El dedo sigue al ratón en directo**: arrastrar para desplazar o mover actúa durante el gesto, en lugar de reproducirse una vez soltado el botón; mantener pulsado el botón hace una pulsación larga.
- **Para una imagen fluida, compartir la pantalla**: el control remoto (accesibilidad) solo sigue limitado por Android a unas tres imágenes por segundo. Active la pantalla remota en la aplicación, o empareje el dispositivo una vez por adb para que la retome solo.
- El asistente de IA, que no lee el JPEG, sigue recibiendo la pantalla sin pérdida, como antes.

## 2.147.2 — 2026-11-10

- **Escritorio remoto: una página ordenada**: fuera de la pantalla completa, los ajustes de visualización, los botones de la sesión y los mandos se repartían tres filas sin orden. El título y el estado de la sesión están ahora a la izquierda, **Pantalla completa**, **Nueva pestaña** y **Cerrar la sesión** a la derecha en la misma línea. La pantalla del equipo cabe en un solo marco: encima, la barra de los comandos enviados al equipo (Ctrl+Alt+Supr, atajos, teclas que mantener, texto, portapapeles); debajo, una barra de estado con la resolución, el zoom y la nota de sesión. La página cabe en la ventana, sin barra de desplazamiento.
- **Barra superior del portal**: entre 980 y 1060 píxeles de ancho, desbordaba la ventana cuando se mostraba el menú Android, lo que hacía aparecer una barra de desplazamiento horizontal en todas las páginas. Corregido.

## 2.147.1 — 2026-11-10

- **Escritorio remoto en pantalla completa: mandos que ya no tapan el equipo**: el panel lateral ocupaba toda la altura de la pantalla en una franja ancha y opaca. Ahora es un pequeño panel flotante, cuatro veces menos voluminoso, **translúcido** — se intuye el escritorio debajo, y se vuelve más opaco en cuanto se usa. El estado de la sesión y las acciones (salir de la pantalla completa, nueva pestaña, cerrar la sesión) caben en una línea, como iconos con su nombre en una información emergente; plegado, solo queda un discreto tirador en el borde.

## 2.147.0 — 2026-11-10

- **Redes de confianza en Android**: en la aplicación, un nuevo panel ajusta dónde el dispositivo permanece en contacto con loco — **en todas partes** (como antes, por defecto), **sin datos móviles**, o **solo en las Wi-Fi que usted aprueba por su nombre**. En otros lugares el agente está en pausa: ningún contacto con el servidor, las sesiones se detienen, la depuración inalámbrica no se vuelve a encender; la notificación dice por qué, y se reanuda sola al volver a una red aprobada. Una Wi-Fi se aprueba con un botón cuando el dispositivo está conectado a ella, o por su nombre.
- **Lo que Android exige para el modo por nombre de Wi-Fi**: el permiso de ubicación ajustado en «Permitir todo el tiempo», y la ubicación del dispositivo activada — Android solo da el nombre de una Wi-Fi con esa condición. loco nunca lee la posición; el permiso solo se pide si usted elige este modo, y la aplicación guía paso a paso. Sin él, el agente sigue en pausa en lugar de estar activo en una red que no sabe nombrar.
- **La ficha de un dispositivo Android dice dónde está activo**: «activo solo sin datos móviles», «activo solo en 2 Wi-Fi aprobadas»… Así se entiende por qué aparece desconectado a ratos. El nombre de las Wi-Fi se queda en el dispositivo, y el ajuste solo se cambia en el dispositivo.
- **Actualizar el agente desde la ficha de un equipo**: cuando el agente de un equipo es más antiguo que el servidor, su ficha lo indica («agente por actualizar») y el menú *Más* propone **Actualizar el agente**. Se llama al agente de inmediato en lugar de esperar a su próximo contacto, y la acción se sigue como las demás: correcta cuando anuncia la nueva versión, fallida al cabo de un cuarto de hora con el lugar donde mirar en el equipo. Funciona con los agentes ya instalados (Linux, Windows, Android).

## 2.146.2 — 2026-11-10

- **Una acción ya no se queda «transmitida al agente» sin ejecutarse nunca**: un reinicio y una actualización pedidos seguidos salían en la misma respuesta; el agente reiniciaba el equipo y abandonaba la actualización, que se quedaba «transmitida» seis horas antes de fallar. Ahora, no sale nada más con un reinicio (ni con una actualización que reinicia, ni con la retirada del agente): lo siguiente espera a la vuelta del equipo, y luego se ejecuta.
- **Las acciones perdidas vuelven a salir solas**: cuando un equipo nunca ha recibido o nunca ha iniciado una acción (respuesta perdida, equipo apagado, agente relanzado), loco la vuelve a transmitir en el contacto siguiente — tres veces como máximo — en lugar de darla por fallida tras seis horas. Una acción interrumpida por el camino (equipo apagado durante la acción) se señala de inmediato, con su causa.
- **Caché APT de la appliance y del servidor Windows: salida en HTTPS**: en una red que ya no deja salir el HTTP, la caché ya no llegaba a los repositorios. Ahora llega en HTTPS a los repositorios que se sabe que responden así (Debian, Ubuntu, PPA de Launchpad, Zorin, Raspberry Pi), a los demás en HTTP, y prueba la otra vía cuando la primera no responde; se comprueba el certificado del repositorio.
- **Se acabó el «Release file … is expired» por culpa de la caché**: cuando un repositorio ya no respondía, la caché volvía a servir indefinidamente su índice anterior; una vez pasada la fecha de validez (siete días para las actualizaciones de Debian), todo el inventario del equipo fallaba y sus actualizaciones quedaban bloqueadas. Un índice ya nunca se vuelve a servir caducado: el equipo conserva sus listas y actualiza el resto.
- **Un repositorio defectuoso ya no bloquea a los demás** (agente Linux): un índice caducado o un repositorio sin firmar entre repositorios legibles ya no impide el inventario ni la actualización; se señala aparte. El aviso de la ficha del equipo da ahora la causa de cada repositorio inutilizable, y dice cuándo el error viene de la caché APT de loco y no del equipo.
- **Actualización automática del agente Linux**: tras actualizarse, el agente lanza en el acto la nueva versión. Hasta ahora se volvía a descargar en cada contacto mientras duraban los contactos frecuentes, anunciando siempre la versión anterior.

## 2.146.1 — 2026-11-10

- **Ya no hay nada reservado a la cuenta «admin» de la instalación**: *Administración › Funciones* (activar o desactivar partes enteras de loco, parte experimental incluida) y el **Shell root** del servidor están ahora abiertos a **cualquier cuenta activa con el perfil Administrador**, sea cual sea su nombre o su origen (local, directorio, SSO). Los demás perfiles siguen sin tener acceso.
- **Activación del shell root**: pide la contraseña de la cuenta conectada, y ya no la de la cuenta «admin». Una cuenta de directorio o SSO, que no tiene contraseña local, no puede activarlo ella misma — la página se lo explica — pero lo usa una vez activado por una cuenta local.

## 2.146.0 — 2026-11-10

- **Inicio de sesión con clave de acceso (passkey)**: una cuenta local puede ahora iniciar sesión **sin contraseña**, con su teléfono, su ordenador o una llave de seguridad. La clave se añade en *Mi cuenta › Seguridad › Claves de acceso* (se vuelve a pedir la contraseña actual, diez claves como máximo por cuenta), y luego la página de inicio de sesión propone **Iniciar sesión con una clave de acceso**. El dispositivo verifica a la persona en cada uso (huella, rostro o código): la clave vale como contraseña y segundo factor, y loco solo guarda su parte pública. La contraseña sigue siendo utilizable, nada cambia para quien no añada ninguna clave.
- **Dos condiciones, impuestas por los navegadores**: loco debe abrirse por un **nombre** — el de *Sistema › Red › Dirección del portal*, nunca una dirección IP — y su **certificado** debe ser reconocido por el navegador (autoridad de loco instalada en el dispositivo, o certificado de su autoridad). En otros casos, *Mi cuenta* explica por qué dirección abrir loco y la página de inicio de sesión no ofrece el botón.
- **Dispositivo perdido**: la persona retira su clave en *Mi cuenta*; un administrador puede retirar las de una cuenta desde *Cuentas* (**Quitar las claves de acceso**) o con `loco-user passkeys-reset NOMBRE`, y restablecer una contraseña también retira las claves de la cuenta. Cada adición, retirada e inicio de sesión con clave queda anotado en el diario. Las cuentas de directorio y SSO conservan las reglas de su proveedor.

## 2.145.0 — 2026-11-10

- **Pantalla remota Android sin volver a pasar por la aplicación**: hasta ahora bastaba con reiniciar la aplicación loco, o el dispositivo, para tener que volver a tocar «Activar la pantalla remota». Ahora **basta con una sola autorización**: una vez activado el control remoto (servicio de accesibilidad) en el dispositivo, loco muestra la pantalla en cada sesión — también tras una actualización de la aplicación o un reinicio del dispositivo — sin rehacer nada. Android limita esta lectura a unas tres imágenes por segundo; la compartición de pantalla, más fluida, se sigue ofreciendo. Durante toda sesión de pantalla, el dispositivo muestra la notificación «Control remoto en curso».
- **Autorizarlo todo emparejando el dispositivo una vez**: cuando un dispositivo Android está emparejado con loco por adb (*Más › Depuración adb…*), loco le da él mismo, de una vez por todas, lo que necesita para una pantalla remota fluida y sin gestos: compartición de pantalla sin ventana de consentimiento, control activado, bloqueo de los «ajustes restringidos» levantado, aplicación exenta del reposo. La aplicación también vuelve a encender sola la depuración inalámbrica cuando Android la apaga (reinicio, vuelta del Wi-Fi). El *Diario* anota estas autorizaciones; *Olvidar* en la depuración adb les pone fin.
- **«Acceso denegado» al activar el control**: en Android 13 y posterior, Android bloquea el servicio de accesibilidad de una aplicación instalada desde un archivo. La aplicación indica ahora cómo permitirlo (*Ajustes › Aplicaciones › Agent loco*, menú ⋮, *Permitir ajustes restringidos*).

## 2.144.0 — 2026-11-10

- **Contactos de los agentes mucho más ligeros**: el portal mantiene ahora su base de datos abierta mientras da servicio, en lugar de guardarla en el disco al final de cada petición. Un contacto simple de un agente cuesta al servidor unas diez veces menos, y las sesiones remotas también se benefician. Nada cambia para lo que compromete: inscripciones, inventarios, comandos transmitidos, resultados, ajustes y cuentas se siguen escribiendo en el disco antes de la respuesta; solo la presencia y las medidas de un contacto simple — que el agente vuelve a enviar por sí mismo en el contacto siguiente — se escriben en el segundo siguiente.
- **Una acción pedida sale en unos segundos**: cuando una acción espera a un equipo, loco se lo indica por el canal ya abierto para el acceso remoto, y el agente (Linux y Windows) contacta de inmediato en lugar de esperar a su temporizador de dos minutos. Los contactos en reposo no se multiplican por ello.
- **Una alerta que no sale ya no paraliza el portal**: el correo electrónico o el webhook de una alerta se envía sin retener la base de datos. Un servidor de correo o un webhook que tarda en responder ya no bloquea los contactos de los agentes ni las acciones del portal mientras tanto. Añadir una fuente de aplicaciones Android personalizada también vuelve a leer el repositorio sin retener la base de datos.
- **Un equipo ya no puede recuperarse solo con la clave de inscripción**: un agente que se vuelve a inscribir (reinstalación, cambio de dirección del servidor) demuestra que posee el token del equipo, sin presentarlo, y conserva su ficha como antes. Sin esa prueba, quien presenta el identificador de un equipo ya inscrito — disco restaurado, clon, equipo retirado, o un intento de ocupar su lugar — queda inscrito como un **equipo nuevo**: la ficha y el token del equipo original siguen intactos, y el *Diario* lo anota. Un dispositivo Android retirado desde loco y luego vuelto a inscribir reaparece por tanto como un dispositivo nuevo; tras un simple «Salir» en la aplicación, recupera su ficha.
- **Escritorio remoto KDE y GNOME en Wayland sin paquete que instalar**: en un equipo Debian o Ubuntu con una sesión KDE Plasma o GNOME en Wayland, el agente instala él mismo, una vez, los componentes que necesita el escritorio remoto. El mensaje «Faltan componentes para el escritorio remoto» ya no aparece al tomar el control.
- **Paquetes Linux: los equipos del parque vuelven a contarse**: la página *Paquetes Linux* mostraba siempre 0 equipos Linux en el parque y 0 equipos por versión. Los contadores son correctos, y solo cuentan el parque de la cuenta conectada.
- **Vuelta atrás de una actualización más segura**: si la instalación de una versión falla, el portal se detiene antes de volver a poner la base de datos anterior, en todos los sistemas (ya era así en la appliance).

## 2.143.0 — 2026-11-09

- **Escritorio remoto en KDE Plasma con Wayland**: el escritorio remoto de los equipos KDE con Wayland funciona ahora, sin tener que abrir una sesión X11. El agente captura la pantalla por el portal estándar `xdg-desktop-portal` (ScreenCast, flujo PipeWire) e inyecta la entrada mediante un dispositivo de entrada virtual. La primera apertura pide un consentimiento para compartir la pantalla en el equipo (ventana KDE «Compartir pantalla»); marcando «Permitir la restauración para las sesiones futuras», las aperturas siguientes ya no piden nada. (GNOME y los compositores wlroots como Sway o Hyprland ya estaban soportados; los demás compositores Wayland siguen sin estarlo.)

## 2.142.0 — 2026-11-09

- **Contactos de los agentes más reactivos**: cuando hay una acción en espera en un equipo (o una acaba de ejecutarse), el agente Linux vuelve a contactar con loco cada ~10 segundos en lugar de esperar hasta dos minutos, y luego vuelve a su ritmo de reposo (dos minutos) cuando vuelve la calma. Las acciones y sus encadenamientos (actualización y luego reinicio, acciones sucesivas) salen por tanto casi de inmediato cuando trabaja en un equipo, sin cargar los contactos en reposo. El agente Android, que ya seguía el ritmo anunciado por loco, también se beneficia.
- **Inventario de un equipo Linux mostrado desde la inscripción**: un equipo Debian, Ubuntu o RHEL que se inscribe muestra **de inmediato** sus programas instalados y sus características (procesador, memoria, disco…), sin esperar al final del primer `apt-get update`; el número de actualizaciones se muestra justo después. Antes, la ficha seguía en «inventario en curso» hasta el final del análisis de red.

## 2.141.0 — 2026-11-09

- **Nombre personalizado de un equipo**: en *Mi parque*, puede dar a un dispositivo un nombre más claro que su nombre de host. En la ficha del equipo, *Más › Nombre personalizado…* abre una pequeña ventana; el nombre introducido se muestra en lugar del nombre de host en la lista, la ficha y la búsqueda, **manteniendo visible el nombre de host** justo debajo. Dejar el campo vacío vuelve al nombre de host. Nuevo permiso **Nombrar los dispositivos**, concedido de oficio al Administrador y al Operador.

## 2.140.0 — 2026-11-09

- **Escritorio remoto Wayland instalable sin conexión**: el paquete `.deb` del agente Linux **incluye ahora `grim`** (la captura de pantalla de los escritorios Wayland wlroots — Sway, Hyprland, Wayfire, river). En un equipo **sin acceso a un repositorio** que proporcione «grim», el agente se instala igualmente y el escritorio remoto de estos compositores funciona. `grim` ya no es una dependencia del paquete: el agente usa el del sistema si está presente; si no, el binario incluido que corresponde a su arquitectura (amd64 o arm64).
- **APK del agente Android siempre ofrecido en la inscripción**: en *Mi parque › Inscripción*, pestaña *Android*, el botón de descarga del APK preconfigurado **se muestra siempre**. Si el APK de la versión todavía no está en el servidor — justo después de una actualización —, **se descarga solo en segundo plano** en cuanto se abre la página, y el mensaje de espera desaparece en cuanto está disponible.

## 2.139.0 — 2026-11-06

- **Aplicaciones Android** (función experimental *Dispositivos Android*): un catálogo de aplicaciones que instalar en sus dispositivos desde loco, con un nuevo menú *Android* (*Catálogo*, *Repositorios F-Droid*, *Fuentes*).
  - **Repositorios F-Droid verificados**: F-Droid e IzzyOnDroid se ofrecen de entrada. Otros nueve repositorios de editores se añaden con un clic: Guardian Project, Bitwarden, Brave, Molly, Session, Threema Libre, KDE, NewPipe y microG. Su clave se ha comprobado: la que firma el índice es la misma que publica el editor.
  - **Otro repositorio F-Droid** puede añadirse a mano: loco lee su clave y pide confirmar su huella antes de añadirlo.
  - **Cada índice está firmado**: loco comprueba la firma con la clave de la fuente y rechaza un índice más antiguo que el último verificado. Los índices se vuelven a leer cada día.
  - **Búsqueda** por nombre, selección *Por descubrir* y categorías. La ficha de una aplicación indica la versión, el Android mínimo, los procesadores, el tamaño, la licencia, la clave de firma, y las advertencias de F-Droid (publicidad, rastreo…).
  - **Google Play**: Google no permite a ninguna herramienta de terceros instalar sus aplicaciones. Una aplicación de Play se añade al catálogo por su enlace o su nombre de paquete; en el despliegue, el dispositivo abre su ficha en Play Store y usted toca *Instalar*. Google Play se encarga después de las actualizaciones.
  - **Despliegue** desde el catálogo, la ficha de un dispositivo (*Más › Instalar aplicaciones…*) o *Mi parque › Despliegues* (nuevo tipo *Aplicaciones Android*), con grupos de equipos y oleadas como para los demás despliegues.
  - **Instalación**: cada dispositivo recibe la versión compatible con su Android y su procesador. loco descarga el APK desde la fuente y comprueba su huella, inscrita en el índice firmado; el dispositivo también la comprueba, con la clave de firma, antes de confiárselo a Android.
  - **Confirmación**: una primera instalación se confirma en el dispositivo, en la ventana *Instalar* de Android, abierta por la aplicación loco o por su notificación. En Android 12 o posterior, las actualizaciones de las aplicaciones que loco ha instalado se hacen sin pedir nada.
  - **Seguimiento**: la ficha de un dispositivo muestra, para cada aplicación del catálogo, la versión instalada, la tienda que la instaló y la versión disponible. El catálogo indica los dispositivos que actualizar.
  - **Nuevos permisos**: *Consultar las aplicaciones Android* y *Gestionar las aplicaciones y las fuentes Android*. Instalar sigue siendo el permiso *Desplegar un programa*.
- **APK listo para inscribirse**, en *Mi parque › Inscripción*, pestaña *Android*: como en el MSI de Windows, la dirección del servidor, la clave de inscripción y la huella de la autoridad están **grabadas en el APK**. Abierta en el dispositivo, la aplicación se inscribe **sola**, sin introducir ni confirmar nada. Con un grupo de equipos, el dispositivo se une a él.
  - La configuración se coloca en el *bloque de firma* del APK: **su firma sigue siendo válida** (sin volver a firmar), y la aplicación puede actualizarse como antes. Si la huella recibida no coincide con la grabada, la inscripción automática se rechaza y la aplicación vuelve a la introducción manual.
  - El **APK genérico** (dirección y clave que introducir a mano) se sigue ofreciendo, plegado bajo *APK genérico e introducción manual*.
- **Aplicación Android 2.139.0**: nueva tarjeta *Instalaciones*, para autorizar a loco a instalar aplicaciones; la aplicación se llama ahora **«Agente loco»** («Agent loco», «loco Agent»), para distinguirla como el agente de los demás sistemas.
  - **Nueva presentación**, a imagen del portal: mismos colores, misma fuente (Bricolage Grotesque, e IBM Plex Mono para las direcciones, huellas y versiones), mismos paneles, botones e insignias de estado. La apariencia por defecto es *Bruma*; en modo oscuro, la aplicación pasa al tema «oscuro suave». Se han rehecho la pantalla de inscripción y la lista de accesos en el dispositivo.
  - **Se actualiza sola** desde loco en las versiones siguientes, fuera de una sesión remota en curso; tras una actualización, hay que volver a activar la pantalla remota.
  - **Vuelve a arrancar sola** tras un reinicio del dispositivo o una actualización.
  - **Que instalar una vez a mano** desde la inscripción, encima de la 2.138.0: la 2.138.0 no sabía actualizarse.
- **Retirar el agente** desde la ficha de un dispositivo Android lo retira ahora de loco; la aplicación sigue instalada.
- **Terminal adb** para los dispositivos Android: un verdadero terminal `adb shell`, con los permisos del usuario «shell» de Android (`pm`, `am`, `settings`, `input`, `logcat`, `dumpsys`…), mucho más amplios que los del terminal de la aplicación.
  - **Abrir**: botón *Terminal adb* en la ficha del dispositivo, o *Más › Depuración adb…*.
  - **Conexión**: loco se conecta él mismo al dispositivo, en la red local, como un ordenador de desarrollo.
  - **Depuración inalámbrica** (Android 11 y posterior): se introducen en loco el puerto y el código de vinculación que muestra el dispositivo, una sola vez. La aplicación loco indica después a loco el puerto de conexión, que cambia en cada activación.
  - **Puerto adb clásico**: conexión directa para un dispositivo que ya escucha (5555).
  - **Asistentes de IA**: nueva herramienta MCP `adb_shell`.
  - **Permisos y seguridad**: nuevo permiso *Abrir un terminal adb*. Las sesiones, emparejamientos y comandos se registran. loco guarda su propia clave adb y su servidor adb no escucha en ningún puerto de red.
  - **adb en el servidor**: incluido en la appliance, instalado por loco cuando la distribución lo proporciona (paquete `adb`, o `android-tools` en Rocky con EPEL). Sin terminal adb en un servidor Windows.
- **Desbloquear un dispositivo Android a distancia** (botón *Desbloquear* en la ficha de un dispositivo emparejado por adb, y herramienta para asistentes de IA): loco despierta la pantalla y reproduce el **patrón**, el **PIN** o la **contraseña** que usted introduce — por adb, sin tocar el dispositivo.
  - **Patrón**: lo traza en un panel de 3×3 en el portal. Como la pantalla de bloqueo no puede mostrarse (superficie segura de Android), los 9 puntos se calculan a partir de la resolución; un **perfil por fabricante** (Pixel/AOSP, Samsung, Xiaomi, OnePlus…) sirve de punto de partida, y una **calibración** por dispositivo lo ajusta si el patrón no pasa. El **PIN** y la **contraseña** no piden ninguna calibración.
  - **Asistentes de IA**: nueva herramienta MCP `adb_unlock` (patrón, PIN o contraseña).
  - **Seguridad**: se requiere el permiso *Abrir un terminal adb* (el acceso adb ya permitía inyectar esos gestos). El secreto introducido nunca se registra. Se apoya en la depuración inalámbrica ya autorizada en el dispositivo.
- **Controlar la pantalla de un dispositivo Android por adb** (botón *Controlar la pantalla*, una vez desbloqueado el dispositivo): loco difunde la pantalla e inyecta los gestos — hacer clic para tocar, arrastrar para deslizar, teclas *Atrás*, *Inicio*, *Recientes*, *Encendido/reposo*, y escritura de texto. **Sin diálogo de MediaProjection.** La pantalla de bloqueo, superficie segura, no puede mostrarse (de ahí el desbloqueo a ciegas de arriba).
  - **Flujo de baja latencia**: loco lanza scrcpy-server (solo vídeo) en el dispositivo y retransmite el **H.264 en bruto**; el navegador lo decodifica con **WebCodecs** (decodificación por hardware, nada que transcodificar en el servidor). **Respaldo automático** a un flujo MJPEG (`screenrecord` + ffmpeg) para los navegadores sin WebCodecs o si scrcpy falla. scrcpy-server (Apache 2.0) va incluido; ffmpeg solo es necesario para el respaldo MJPEG.
  - **Salvaguarda visible**: mientras loco controla la pantalla, el dispositivo muestra una notificación **«Control remoto en curso»**, y contacta con loco más a menudo para hacerla aparecer y desaparecer rápido.
- **El paquete `.deb` del agente Linux instala grim**: grim solo estaba recomendado, pasa a ser una dependencia. El escritorio remoto de un compositor Wayland como Sway o Hyprland funciona por tanto desde la instalación, incluso con `--no-install-recommends`. Para un agente ya instalado, instale grim a mano si hace falta.

## 2.138.0 — 2026-11-06

- **Dispositivos Android** (experimental, desactivado por defecto): gestionar sus propios teléfonos y tabletas Android desde loco, **sin código QR** en la instalación.
  - **Activar** en *Administración › Funciones*, sección *Experimental*: *Dispositivos Android*. Reservado a la cuenta `admin` creada en la instalación.
  - **Instalar y conectar**: pestaña *Android* de la inscripción, descargar la aplicación (`loco-agent.apk`), introducir la dirección del servidor y la clave de inscripción, y luego confirmar la huella de la autoridad. Ninguna cuenta de Google.
  - **Pantalla, terminal, archivos** como para un equipo: ver y controlar la pantalla (tacto, teclado, ratón; botones *Atrás*, *Inicio*, *Aplicaciones recientes*, *Notificaciones*), un terminal con los permisos de la aplicación (sin root), y el gestor de archivos. Los asistentes de IA (MCP) disponen de las mismas acciones.
  - **Con su consentimiento, en el dispositivo**: la aplicación sigue visible y cada capacidad (pantalla, control, archivos, segundo plano) se activa a mano. No se captura nada fuera de una sesión abierta desde el portal.
  - **Sin actualizaciones del sistema ni catálogo de programas** en Android. Detalles: [docs/es/android.md](docs/es/android.md).

## 2.137.1 — 2026-11-05

- **Appliance: el proxy de Windows Update vuelve a arrancar tras un reinicio.** Su carpeta en `/run` solo se creaba en la instalación: en cada reinicio, el servicio quedaba fallido y el puerto 3128 cerrado. La caché por DNS y el resto del portal no estaban afectados. Basta con la actualización.
- **Instalación de la appliance**: la prueba completa comprueba ahora todos los servicios en el segundo arranque, y ya no solo el portal.

## 2.137.0 — 2026-11-04

- **Bloquear una actualización**: nueva pestaña *Bloqueos* en Mi parque, o *Bloquear* en la fila de una actualización en la ficha de un equipo. Un paquete (Linux) o un KB o un título (Windows), con «*» como comodín, en todo el parque o en un grupo de equipos, con un motivo.
  - **Respetado en todas partes**: ficha de un equipo, todo el parque, oleadas, programaciones, asistentes de IA. Un bloqueo puesto después de una petición se aplica mientras el equipo no la haya recibido. Un agente demasiado antiguo para respetarlo recibe la orden tras su actualización automática.
  - **En el equipo**: Debian y Ubuntu retienen el paquete (`apt-mark hold`), lo que vale también para sus actualizaciones automáticas; una retención puesta a mano nunca se toca. Windows oculta la actualización, salvo las que impone. Con DNF, solo se ven afectadas las actualizaciones lanzadas por loco.
  - **Siempre visible**: una actualización bloqueada ya no se cuenta como pendiente, pero sigue señalada en la ficha, y la página indica los equipos donde espera. Permiso *Bloquear actualizaciones*, reservado por defecto a los administradores y a los perfiles que cubren todo el parque.
  - Flatpak y los programas del catálogo no están afectados.
- **Aprobación del usuario antes del escritorio remoto**, desactivada por defecto. La persona que está delante del equipo ve una ventana *Aceptar / Rechazar* antes de que se capture nada.
  - **Exigida en un equipo**: ficha del equipo, pestaña *Detalles*, o selección en Mi parque. Permiso aparte, *Configurar la aprobación antes del escritorio remoto*: un técnico que abre el escritorio no puede retirarla. Se aplica también a los asistentes de IA, que la ventana nombra como tales.
  - **Para una sola sesión**: menú *Más* de la ficha, *Escritorio remoto, con la aprobación del usuario*.
  - **Sin respuesta en un minuto, o con la pantalla bloqueada, es un rechazo**, explicado al administrador. Si no hay nadie conectado en el equipo, el escritorio se abre sin pregunta, y el diario lo indica. El terminal y los archivos no están afectados.
  - **Un agente demasiado antiguo no se abre sin aprobación**: la sesión se rechaza hasta su actualización automática.
  - **En Linux**, la ventana usa zenity o kdialog, presentes en los escritorios habituales. Sin ninguno de los dos, el escritorio se rechaza con esta explicación.

## 2.136.0 — 2026-11-03

- **Nueva apariencia por defecto**: tema *Oscuro suave*, sin color de acento, con una foto de crestas en la bruma (preajuste *Bruma*). *Medianoche* y *Alpes* siguen disponibles.
  - **En un servidor existente**, se aplica a la pantalla de inicio de sesión y a las cuentas sin apariencia guardada, salvo si se ha elegido una apariencia de instancia. Las cuentas que guardaron la suya la conservan.
- **La pantalla de inicio de sesión puede tener su propia apariencia**: en *Apariencia de la instancia*, sigue la apariencia por defecto o recibe la suya. Desde la ventana *Apariencia*, *Aplicar al inicio de sesión* le da la que está probando, sin tocar la de las cuentas por defecto. Reservado al permiso de configurar el servidor.
- **Nuevo preajuste *Bosque*** con un nuevo tema *Abeto*.
- **Correcciones**: en la ventana *Apariencia*, la cruz de cierre y la miniatura *Sin fondo* estaban vacías, y las etiquetas de los preajustes podían carecer de contraste.

## 2.135.0 — 2026-11-03

- **El agente puede activar el encendido remoto**: botón *Activar la activación remota* en la ficha de un equipo y para una selección en Mi parque, en sus tarjetas Ethernet.
  - **En Linux**, el agente lo vuelve a aplicar en cada contacto: la mayoría de las tarjetas vuelven a «desactivado» en cada reinicio. La configuración de red del equipo no se modifica, y no hace falta el comando `ethtool`.
  - **En Windows**, el ajuste persiste. La tarjeta puede reiniciarse en el momento del cambio: unos segundos de corte, un solo intento al día en caso de fallo.
  - **En caso de rechazo** del controlador o del sistema, la ficha muestra el error.
  - *Dejar de activarlo* desactiva el encendido remoto y deja de mantenerlo. La BIOS sigue configurándose a mano: el agente no tiene acceso a ella. No se activa nada sin petición.

## 2.134.0 — 2026-11-03

- **Encendido remoto (Wake-on-LAN)**: botón *Despertar* en un equipo apagado, en Mi parque y en su ficha, para un equipo o una selección. También disponible por el servidor MCP (`wake_device`).
  - **Solo se ofrece cuando tiene posibilidades de funcionar**: el equipo tenía una tarjeta Ethernet conectada en su último contacto, el encendido remoto no está desactivado en ella, y algo puede emitir en su red. Nunca por Wi-Fi, donde un equipo apagado ya no escucha.
  - **Emitido por el servidor** si está en la misma red; si no, por un equipo encendido de esa red en su próximo contacto, ya que un paquete de activación no atraviesa los routers. Un relé que vuelve demasiado tarde ya no despierta a nadie de improviso.
  - **Resultado seguido** en el historial del equipo: vuelto a estar en línea, o no vuelto tras 10 minutos, con lo que hay que comprobar.
- **Tarjetas de red en la ficha de un equipo**: tipo (Ethernet, Wi-Fi), conectada o no, dirección MAC, red y estado del encendido remoto. La ficha también dice por qué el encendido remoto no está disponible. Hace falta un agente 2.134.0 o posterior.
- **Nuevo permiso *Despertar los dispositivos***, concedido al perfil Operador.

## 2.133.0 — 2026-11-03

- **Solicitud de certificado (CSR)**: la página pide el nombre completo (FQDN), muestra los SAN solicitados y avisa de que la autoridad debe conservar la IP si los agentes la usan. Un nombre corto como `loco` ya no se descarta sin decirlo.
- **Importación de un certificado de empresa**: se acepta solo el certificado final, con el intermedio como complemento. Los tres errores son distintos y dicen qué corregir: intermedio ausente, SAN ausente (con la lista de lo que cubre el certificado) y autoridad equivocada.
- **Vincular una cuenta de administrador a su identidad SSO**: *Mi cuenta › Inicio de sesión SSO*. El vínculo se hace mediante un paso real por el proveedor, desde una cuenta ya conectada. No se empareja nada automáticamente por nombre o correo electrónico.
  - La contraseña y los permisos no cambian, y se sigue exigiendo la autenticación de dos factores de la cuenta.
  - Eliminar o desactivar el proveedor no corta la cuenta local.
  - Se rechaza una identidad ya vinculada a otra cuenta activa. Se sustituye una solicitud de acceso todavía pendiente para esa identidad.
- **Perfil propuesto a partir de un grupo**: para cada proveedor OpenID Connect, líneas `grupo = perfil`. El perfil se preselecciona al aprobar una solicitud: no se concede nada sin aprobación.

## 2.132.0 — 2026-11-03

- **Configurar Authentik desde loco**: botón en *Directorios y SSO*.
  - **Con un token de API temporal**: loco crea o actualiza el proveedor y la aplicación, sin duplicados: cliente confidencial, solo `authorization_code`, URL de retorno estricta, ámbitos `openid profile email`, firma RS256. Luego obtiene el client ID, el secreto y el emisor, y lanza la comprobación.
  - **El token** no se almacena, ni se registra, ni se pasa como argumento de un comando. Entre dos pasos, se queda en el navegador, cifrado y limitado a diez minutos. Elimínelo en Authentik al terminar.
  - **El certificado primero**: el token solo se envía una vez reconocido el certificado de Authentik, o confirmada su autoridad por su huella. Si el servidor no envía la raíz, la página pide pegarla.
  - **Aplicación existente**: los cambios se muestran antes de aplicarse. El client ID y el secreto no cambian. Una URL de retorno solo se elimina si la marca. Si Authentik ha cambiado mientras tanto, no se aplica nada.
  - **Sin token**: un blueprint listo para pegar, más los valores que introducir. El secreto lo sigue generando Authentik.

## 2.131.0 — 2026-11-03

- **Nombres internos gestionados desde el portal**: *Sistema › Red › Nombres internos*. Cuando el DNS del servidor no conoce un nombre, como la dirección de Authentik en una red interna, indique su dirección IP, sin abrir un shell.
  - **Propuesto por el diagnóstico**: cuando «Comprobar la configuración» falla en el DNS, un campo permite añadir el nombre en el sitio, y luego la comprobación vuelve a empezar.
  - **Conservado**: la lista está en `/etc/loco`, por tanto respaldada y replicada en alta disponibilidad. El bloque dedicado de `/etc/hosts` se rehace si el archivo se reescribe, por una actualización o por `setup-loco`.
  - **Nada más cambia**: `RESOLVERS` y `resolv.conf`, usados por la caché de Windows Update, no se tocan. Con Docker y en un servidor Windows, la página indica cómo hacerlo: `extra_hosts`, o el archivo `hosts`.

## 2.130.0 — 2026-11-03

- **Botón «Comprobar la configuración» para los proveedores OpenID Connect**, en *Directorios y SSO*. También se lanza solo al guardar. Sin que inicie sesión ningún usuario y sin crear ninguna cuenta, comprueba siete etapas desde el servidor y da para cada una su estado y su corrección: resolución del nombre, conexión, certificado, descubrimiento, claves de firma, cliente y secreto, petición de inicio de sesión. Ya no hace falta leer los registros de Authentik.
  - **Certificado**: el mensaje nombra el intermedio o la raíz que falta y da la huella que comprobar antes de pegarla. Un nombre no cubierto, un certificado caducado o un certificado fijado que ha cambiado se señalan por separado.
  - **URL de retorno no declarada**: se muestra la URL que declarar, lista para copiar.
  - **Flujo «code» no autorizado**: `invalid_request`, «The request is otherwise malformed», por fin se explica.
  - **Secreto erróneo**: se detecta con un código falso, sin obtener ningún token. Como Authentik responde `invalid_client` también cuando la URL de retorno no está declarada, en ese caso no se culpa al secreto.
  - **Otros proveedores**: una respuesta desconocida da «no concluyente», nunca «fallo». No se muestra ni se registra ningún secreto.
- **Inicio de sesión rechazado**: la página indica a los administradores dónde encontrar la causa. El detalle sigue reservado a quienes tienen permiso para gestionar el SSO.

## 2.129.0 — 2026-11-03

- **Dirección del portal separada de la de los agentes.** Nuevo ajuste en *Sistema › Red › Dirección del portal*: abrir loco por su nombre (`https://loco.lab.local:8443`) ya no obliga a volver a lanzar el instalador con `--host`, que también cambiaba la dirección de inscripción. Los agentes y la inscripción se quedan en su dirección. Sin ajuste, nada cambia.
  - **Modificable sin reinstalar ni reiniciar**, desde el portal o con `loco-config portal-url`, presente ahora en todo servidor Linux. El ajuste sobrevive a las actualizaciones y el agente root lo vuelve a validar. Con Docker: `LOCO_PORTAL_URL`. En un servidor Windows: `install.ps1 -PortalUrl`.
  - **El certificado debe cubrir la dirección.** Si no, el cambio se rechaza. Para un certificado de loco, una casilla añade el nombre al certificado. Para un certificado de empresa o proporcionado en la instalación, el mensaje indica qué pedir.
  - **Inicio de sesión SSO**: el retorno se hace siempre a la dirección del portal. Desde otra dirección, como la IP, el navegador se redirige allí antes de colocar la cookie. Desaparece el falso mensaje «inicio de sesión caducado», y un retorno a una dirección equivocada dice qué corregir. No se construye ninguna URL a partir de la cabecera Host.
  - **URL de retorno que volver a declarar**: la página las lista para cada proveedor y pide confirmación antes del cambio.
- **Alta disponibilidad: renovar el certificado conserva los dos servidores.** La renovación desde el portal retiraba del certificado las direcciones de los dos servidores del clúster, para conservar solo la IP virtual.

## 2.128.0 — 2026-11-03

- **Despliegue por oleadas.** Una actualización del parque o de un grupo, un despliegue de programas o de paquetes pueden salir por oleadas en lugar de tocar todos los equipos de una vez. Se elige el número de equipos por oleada, la espera entre dos oleadas, el número de fallos que lo pone todo en pausa, y opcionalmente un grupo piloto para empezar.
  - **Una oleada solo sale con pruebas**: al menos un éxito en la anterior y menos fallos que el umbral. Si no, todo se detiene: los equipos siguientes no reciben nada, la vista general lo señala, se envía una notificación, y le toca a usted decidir lanzar la oleada siguiente o cancelar el resto.
  - **Un equipo apagado no bloquea**: su orden le espera y seguirá a su vuelta. Los equipos en línea pasan primero, porque responden rápido. Si ningún equipo de una oleada ha respondido, no sale nada solo.
  - **Al salir cada oleada**, se aparta un equipo retirado del parque, o que ya no tiene nada que actualizar.

## 2.127.3 — 2026-11-03

- **Órdenes sin respuesta**: una orden enviada a un equipo que nunca vuelve se da ahora por fallida al cabo de 6 h. Se quedaba mostrada indefinidamente, ya que solo un contacto del equipo podía cerrarla.
- **Mi parque: filtros *Desconectado* y *Reinicio necesario*.** Aparecen cuando afectan al menos a un equipo, y esas dos líneas de la vista general llevan a ellos, con el mismo recuento.
- **Plurales**: se acabaron los «equipo(s)» y «actualización(es)» en el portal, el diario, los agentes y la consola, en francés, inglés y español. Los resultados ya guardados siguen siendo legibles.
- **Información emergente del indicador**: ya no repite «al día: al día» ni «inventario incompleto: inventario incompleto».
- **Vista general**: el procesador muestra una media entre dos actualizaciones en lugar de una medida de 120 ms, muy inestable, una caché vacía ya no aparece en *Almacenamiento* («Caché APT 2 B»), y la carga se escribe con el mismo decimal desde la carga de la página.

## 2.127.2 — 2026-11-03

- **Repositorios averiados detectados por fin en los equipos Debian y Ubuntu.** `apt-get update` responde «éxito» incluso cuando no responde ningún repositorio, así que el agente no veía nada y una actualización podía terminar con éxito sin instalar nada.
  - **Ningún repositorio legible**: el equipo pasa a *Estado desconocido* con la causa, y una actualización se detiene con fallo en lugar de afirmar que ha salido bien.
  - **Un repositorio averiado entre otros** (repositorio de terceros abandonado, por ejemplo): el indicador no cambia y las actualizaciones continúan, pero la ficha del equipo señala que las actualizaciones de ese repositorio no se cuentan.

## 2.127.1 — 2026-11-03

- **Corregido: tras un cambio de IP del servidor, todos los repositorios de un equipo Linux caían.** El agente llegaba al nuevo servidor, pero el proxy APT (`01loco-proxy`) y las fuentes HTTP conservaban la dirección anterior: el equipo ya no leía ningún repositorio, repositorios oficiales incluidos. Los agentes actualizan ahora el proxy APT, las fuentes APT y RPM y los repositorios Flatpak, en HTTP como en HTTPS, cuando adoptan una nueva dirección. La nota de la 2.125.0 sobre el seguimiento de una nueva IP estaba por tanto incompleta.
- **Quedan por retomar a mano**: los equipos que ya habían cambiado de dirección antes de esta versión, y los configurados con `setup.sh` sin agente: vuelva a lanzar `setup.sh` una vez.

## 2.127.0 — 2026-11-03

- **Mi parque está paginado**, de 50 en 50 equipos, los más urgentes primero. La navegación solo aparece a partir de 50 equipos y conserva los filtros.
- **La búsqueda de equipos abarca todo el parque**, y ya no solo la página mostrada: nombre, sistema, cualquier dirección del equipo, grupo, sin tener en cuenta mayúsculas ni acentos. Sobrevive a la actualización y se comparte por su dirección.
- **La selección de equipos sigue el cambio de página.** La barra indica cuántos equipos seleccionados están fuera de la página; «Seleccionar todo» abarca la página mostrada.
- **Corregido: «Actualizar todo» anunciaba un número erróneo cuando había un filtro activo.** El contador seguía el filtro mientras que la acción abarca todo el grupo o el parque: con el filtro Windows, también se actualizaban equipos Linux. El contador indica ahora lo que realmente se hará.

## 2.126.1 — 2026-11-03

- **Dirección equivocada mostrada para un equipo con Docker.** Mi parque podía mostrar la dirección del puente de Docker (`172.17.0.1`) en lugar de la de la tarjeta de red. El portal muestra ahora primero la dirección por la que ve el equipo, y los agentes Linux y Windows colocan en cabeza la tarjeta de la ruta por defecto, las interfaces virtuales al final (Docker, máquinas virtuales, Hyper-V, WSL) y IPv4 antes que IPv6. Todas las direcciones siguen listadas en la ficha del equipo.

## 2.126.0 — 2026-11-02

- **Vista general rehecha para responder primero a «¿va todo bien, y qué debo hacer?».**
  - **Estado general arriba**: «Nada por tratar» con lo que se ha comprobado, o la lista de lo que requiere una acción, lo más grave primero, con un enlace a la página donde actuar. Servicios detenidos, certificado, copias de seguridad, syslog, disco, actualizaciones de seguridad, actualizaciones fallidas: todo aparece ahí, aunque la notificación correspondiente esté desactivada. La lista se actualiza sola y sigue los permisos y el parque de cada cuenta. Un equipo simplemente apagado no figura en ella.
  - **El parque justo debajo**: cada estado abre la lista filtrada, los estados a cero quedan discretos, y se indican las actualizaciones en curso, los reinicios necesarios y los equipos desconectados.
  - **El servidor en una sola franja**: procesador, memoria, disco y red. La cifra del disco y su barra por fin dicen lo mismo, y la curva de red sobrevive a las recargas y a las idas y vueltas por el portal.
  - **El almacenamiento en una sola tabla**, ordenada por tamaño, con un enlace a cada caché. Sustituye a las tarjetas de caché y a «Disco de los datos», que se solapaban.
  - **Retirados de la página**: las 8 tarjetas, la tabla de los servicios (el detalle sigue en *Sistema*, un servicio detenido aparece en el estado general) y el segundo porcentaje de Windows Update, que podía contradecir al del gráfico.
- **Alertas**: los mensajes ya no escriben «actualización(es)» ni «día(s)» para las actualizaciones de seguridad, el certificado y los inicios de sesión fallidos.

## 2.125.0 — 2026-11-02

- **Cambiar el puerto de la interfaz web** desde *Sistema › Red*. El nuevo puerto se abre junto al anterior: ni el administrador ni los equipos se ven cortados. Los agentes aprenden el nuevo puerto en su próximo contacto, la página cuenta los que lo conocen, y el puerto anterior solo se cierra desde el nuevo, lo que demuestra que es accesible, cortafuegos incluido. Los valores inutilizables se rechazan antes de cualquier modificación: puerto ya ocupado, puerto reservado a loco, puerto que bloquean los navegadores. Un fallo restablece la configuración anterior. Docker y servidor Windows: el puerto sigue configurándose en la instalación, la página lo explica.
- **Los agentes Linux siguen la dirección del servidor hasta en sus repositorios.** Cuando un agente adopta una nueva dirección, nueva IP incluida, actualiza sus fuentes APT y RPM y sus repositorios Flatpak instalados en HTTPS. Hasta ahora se quedaban en la dirección anterior.
- **Alta disponibilidad: ya no se supone que el puerto es el 8443.** La comprobación de salud y los intercambios entre servidores usan el puerto configurado, y un servidor de respaldo puede unirse a un principal en otro puerto (`dirección:puerto`). El puerto se cambia fuera de la alta disponibilidad.

## 2.124.2 — 2026-11-02

- **Retirar un programa del catálogo local, directamente desde la lista.** Hasta ahora, solo era posible desde el menú *Más* de su ficha. Aparece un botón *Retirar* en cada fila y junto a *Reintentar* cuando la descarga ha fallado, por ejemplo si un cortafuegos bloquea el sitio de origen. Marque varios programas para retirarlos de una vez, incluidos los que tienen error, que hasta ahora no se podían marcar; *Desplegar*, *Crear un grupo* y el comando de PowerShell siguen limitados a los programas listos. No se desinstala nada de los equipos.
- **Barra de selección del catálogo corregida sobre fondo de foto**: seguía en parte visible sin selección y se ajustaba al final de la página en lugar del de la pantalla, fuera de la vista en un catálogo largo.

## 2.124.1 — 2026-11-02

- **Vista general: las últimas incorporaciones se pueden pulsar.** Un programa Windows abre su ficha; un paquete APT, que no tiene página propia, abre su repositorio ya filtrado por ese paquete. Toda la fila se puede pulsar, como en Mi parque.

## 2.124.0 — 2026-11-02

- **Envío de los registros a un servidor syslog o a un SIEM** (*Administración › Envío syslog*). A elegir: el **diario de loco**, los **registros de los equipos**, o ambos. Formato RFC 5424, en TLS (recomendado, con su autoridad de certificación), TCP o UDP, y un botón para enviar un mensaje de prueba.
  - **Equipos**: journald en Linux, registros de eventos en Windows, al nivel mínimo elegido, con los eventos de seguridad si lo desea, en todo el parque o en grupos de equipos. Los equipos pasan por loco: ninguna apertura de red hacia el servidor syslog. Agentes 2.124.0 y posteriores (actualización automática).
  - **Servidor syslog ausente**: el envío se reanuda donde se había detenido, sin almacenar nada en el servidor loco. Un evento puede llegar duplicado tras una avería.
  - **Seguimiento**: último envío, último fallo y estado de cada equipo en la página; nueva regla de alerta *Envío syslog con fallos* tras 5 minutos.

## 2.123.1 — 2026-11-02

- **Página *Mi parque › Actualizaciones* rehecha**: una sola lista en columnas alineadas (equipo, acción, estado, fecha, autor), agrupada en *En curso*, *En espera* y *Terminadas*. En el teléfono, cada fila se apila.
- **Los fallos aparecen por fin en esta página**: las actualizaciones fallidas nunca figuraban en ella (estado erróneo en la consulta). Ahora pasan a la cabeza de las terminadas, con los paquetes afectados o la línea de error, en lugar de la última línea del registro.
- **La lista de paquetes se muestra de verdad en directo**: no aparecía si la página se había abierto antes del primer paquete (ficha del equipo y desarrollo). Las listas desplegadas o plegadas se quedan en ese estado cuando la página se recarga.
- **«En espera» dice la verdad**: un equipo apagado ya no muestra «menos de 2 min» sino *Equipo desconectado, visto el …, saldrá a su vuelta*; una orden en curso o transmitida a un equipo mudo muestra *Sin respuesta del equipo*. Botón *Cancelar* directamente en la fila, y la insignia de la pestaña ya no cuenta los equipos desconectados ni las programaciones futuras.
- **Duraciones visibles en la página**: una acción en espera no caduca; las terminadas se quedan 24 horas (en lugar de 30 minutos, demasiado poco para seguir una oleada), y luego en el historial del equipo.

## 2.123.0 — 2026-11-02

- **Avance de las actualizaciones paquete a paquete, en directo.** Durante una actualización, se ve ahora pasar cada paquete (o cada actualización de Windows) con su resultado: **instalado** (✓), **en curso**, o **fallido** (✗), más un resumen «X instalados · Y fallidos · Z en curso». Visible en la ficha del equipo y en el desarrollo *Mi parque › Actualizaciones*, actualizado cada 2 s sin recargar.
  - **APT**: desempaquetado → en curso, *Setting up* → instalado, error de dpkg → fallido. **DNF/YUM** y **Windows Update**: una entrada por actualización con su resultado. **Flatpak**: una entrada global.
  - La lista sigue consultable después en el historial del equipo (junto al registro), para volver a ver lo que ha salido bien o mal.

## 2.122.0 — 2026-11-01

- **Desarrollo global de las actualizaciones del parque.** Nueva página *Mi parque › Actualizaciones*: el seguimiento, en un solo lugar, de todas las actualizaciones del parque — **en curso** (avance en directo, barra por equipo, actualizada cada 2 s sin recargar), **en espera** del próximo contacto, y **terminadas recientemente** (éxito o fallo con el motivo). Cada fila lleva a la ficha del equipo.
  - **Adaptado a los permisos**: cada uno solo ve los equipos de su parque (mismo alcance que Mi parque); una pestaña con una **insignia** indica el número de actualizaciones en curso o en espera.
  - *Actualizar todo el parque* lleva directamente a este desarrollo para seguir la oleada lanzada.

## 2.121.1 — 2026-11-01

- **Reiniciar desde Mi parque se queda en la lista.** El botón *Reiniciar* de un equipo ya no lleva a su ficha: un mensaje confirma que se ha pedido el reinicio y la lista sigue mostrada. Mientras el reinicio está en espera, la insignia pasa de «reinicio necesario» a «reinicio en curso».

## 2.121.0 — 2026-11-01

- **Alta disponibilidad integrada.** Dos servidores loco, una sola *IP virtual*: el principal sirve el portal, los repositorios y los agentes; el de respaldo lo replica todo de forma continua y toma la IP automáticamente si el principal cae (keepalived / VRRP). *Administración › Servidor › Alta disponibilidad*.
  - **Puesta en marcha guiada**: en el primer servidor, «Activar» pide una IP virtual libre y muestra un **código de emparejamiento** de un solo uso; en un segundo servidor nuevo (misma versión), «Unirse» con la dirección del principal y ese código copia toda la configuración y los repositorios, y luego arma la conmutación. loco ajusta él mismo su dirección a la IP virtual y extiende su certificado a la IP virtual y a los dos servidores.
  - **Replicación segura**: la configuración, la base de datos y los repositorios se transfieren cifrados (AES-256-GCM) entre los servidores, autenticados por un secreto de clúster que el portal nunca ve (solo guarda su huella). Las cachés no se replican (se reconstruyen). Durante una actualización, la comprobación de salud no provoca conmutación.
  - **Supervisión y conmutación**: la página muestra el papel de cada servidor, quién tiene la IP virtual, lo reciente que es la replicación, el estado de keepalived y los últimos eventos. *Conmutar al respaldo* permite una conmutación voluntaria (mantenimiento planificado); el respaldo es pasivo (en lectura) mientras no tiene la IP.
  - No disponible en un contenedor Docker; `keepalived` viene incluido en la appliance y se instala bajo demanda en Debian/Ubuntu y Red Hat.

## 2.120.2 — 2026-11-01

- **Icono de reinicio**: flecha circular (la de «reinicio necesario») en lugar del símbolo de encendido, que evocaba un apagado — botón *Reiniciar* de Mi parque, botón y banda de reinicio del servidor.

## 2.120.1 — 2026-10-31

- **Mi parque sin barra de desplazamiento horizontal**: la tabla cabe en el ancho de la página, incluso con nombres de sistema largos. Sistema mostrado en versión corta («Windows Server 2025 Standard», «Debian 13 (trixie)», nombre completo en información emergente), grupos bajo el nombre del equipo, último contacto bajo la dirección, botón *Reiniciar* como icono; en una pantalla estrecha, las barras de recursos se estrechan y los botones pasan uno debajo de otro.

## 2.120.0 — 2026-10-31

- **Actualizaciones de un equipo una a una, con avance en directo.** En la ficha de un equipo, la pestaña *Actualizaciones* ofrece una casilla por actualización del sistema (paquete APT o RPM, actualización de Windows) y una línea *Aplicaciones Flatpak*; *Actualizar la selección* solo aplica esas, con reinicio opcional. *Actualizar* sigue haciéndolo todo.
  - **En directo**: la línea de la acción muestra la etapa (preparación, descarga, instalación), el paquete o la actualización en curso y una barra de progreso, releídos cada 2 s sin recargar la página; en la tabla, los elementos terminados aparecen marcados y el que está en curso gira. La ficha se recarga al final con el inventario al día, y no mientras se marca.
  - **Agente Linux**: salida de apt (líneas de estado `APT::Status-Fd`) y de dnf leída sobre la marcha; una selección pasa por `apt-get install` (nuevos paquetes de una actualización incluidos) conservando su marca «instalado automáticamente», o `dnf upgrade <paquetes>`.
  - **Agente Windows**: cada actualización envía su identificador de Windows Update; instalación una a una (pila de mantenimiento primero), con la etapa enviada, y una actualización fallida ya no impide las siguientes.
  - Elección una a una reservada a los agentes 2.120.0 y posteriores (un agente antiguo lo haría todo); selección limitada a las actualizaciones anunciadas por el inventario.
  - **MCP**: `update_device` acepta `updates` (nombres de paquetes, títulos, números KB o identificadores) y `flatpak`; `get_device` da los identificadores y el avance de las acciones en curso.

## 2.119.2 — 2026-10-31

- **Ficha de un equipo**: una acción en espera (actualización, inventario, reinicio…) se muestra en una línea discreta — punto de color, acción, «en el próximo contacto», enlace *Cancelar* — en lugar de un gran recuadro de alerta.

## 2.119.1 — 2026-10-31

- **Terminales más sobrios**: bajo el shell root y los terminales de los equipos, los atajos (copiar, pegar, buscar, tamaño del texto…) y la observación sobre el cierre de la sesión caben en un bloque «Atajos y ayuda» plegado por defecto.
- **Shell root**: el servicio escucha en su socket en cuanto se crea; una conexión que llega justo al arrancar ya no se rechaza.

## 2.119.0 — 2026-10-30

- **Apariencia de la instancia** (*Administración → Apariencia*, permiso *Configurar el servidor*): una empresa adapta loco a su imagen sin retirar los temas incluidos. En la instalación, todo se sigue ofreciendo como antes.
  - **Identidad**: logo (trasladado aquí desde la ventana Apariencia personal), **color de la empresa** — propuesto como color de acento, con el texto encima en negro o en blanco según su claridad — y **fondo de la empresa**, imagen propuesta entre los fondos y servida también en la pantalla de inicio de sesión.
  - **Apariencia por defecto**: tema, color, fondo y opacidad de las cuentas nuevas, de la pantalla de inicio de sesión y de *Restablecer* (hasta ahora fijada en «Alpes»).
  - **Elecciones dejadas a cada uno**: temas propuestos (por ejemplo uno claro y uno oscuro), color de acento y fondo **a elegir o impuestos**. La ventana Apariencia de cada cuenta ya solo muestra lo autorizado; un ajuste fuera del marco se sustituye al mostrarse por la apariencia por defecto, sin borrarse. *Ajustes de origen* restablece todas las elecciones. Diario: categoría Servidor.

## 2.118.0 — 2026-10-30

- **Inscribir un equipo directamente en un grupo.** Arriba de **Inscripción**, *Inscribir los equipos en el grupo* cambia el MSI, los paquetes .deb/.rpm, los comandos y el comando de despliegue masivo (GPO, Intune) a la **clave del grupo**: el equipo inscrito con ellos se une a ese grupo (un equipo ya inscrito también se une, sin dejar los suyos). Ningún agente que actualizar — la clave de grupo (`g<id>-…`) ocupa el lugar de la del servidor.
  - Por defecto, ningún grupo; *Inscribir equipos en este grupo* en el menú ⋯ de cada grupo abre la página en él.
  - Una cuenta limitada a una parte del parque solo inscribe en los grupos asignados a su perfil, nunca sin grupo (el equipo saldría de su parque): su primer grupo viene preseleccionado, y un perfil limitado sin grupo ya no recibe la clave del servidor.
  - *Cambiar la clave de este grupo* (permiso *Configurar el parque*) invalida sus archivos y comandos ya distribuidos; eliminar el grupo también. Diario: inscripción con su grupo, «Nueva clave de inscripción de un grupo».

## 2.117.0 — 2026-10-30

- **Perfiles limitados a una parte del parque.** En **Perfiles de acceso**, un perfil cubre **todo el parque** (por defecto, y siempre para los perfiles integrados) o **grupos de equipos y equipos elegidos** — por ejemplo «Técnicos edificio B». Los equipos añadidos más tarde a esos grupos entran solos; la lista de perfiles muestra el parque de cada uno.
  - Las cuentas del perfil solo ven y apuntan a esos equipos: Mi parque, ficha (si no, no se encuentra), búsqueda, vista general, grupos reducidos a sus equipos, actualizaciones agrupadas, despliegues, scripts e historial, programaciones, sesiones remotas — y los **tokens MCP**, que siguen el parque de la cuenta que los creó.
  - Una programación aplica en cada ocurrencia el parque de su autor; un perfil limitado no puede apuntar a «Todo el parque».
  - Gestionar los grupos de equipos y configurar el parque siguen reservados a los perfiles que cubren todo el parque (los grupos definen el parque de los demás); un gestor limitado solo crea o asigna perfiles incluidos en su propio parque.
  - Cambiar el parque de un perfil vuelve a conectar sus cuentas; una sesión remota cuyo equipo sale del parque se cierra. Diario: «profils.parc».

## 2.116.0 — 2026-10-30

- **Servidor MCP: página rehecha y vocabulario preciso.** «Asistentes de IA» pasa a ser **Servidor MCP** (pestaña, función, permiso **Crear tokens MCP**, diario), y un «acceso» pasa a ser un **token**: la página dice lo que es — un LLM controla loco por MCP desde su cliente (Claude Code, Cursor, VS Code…), con un token que solo abre las herramientas activadas.
  - **Tokens** primero: número de herramientas y de acciones, reparto por ámbito («Parque 5 · Acceso remoto 3»), última llamada con el cliente y la dirección, caducidad con el año.
  - **Conectar un cliente MCP**: dirección, cabecera de autenticación y comando de Claude Code, con un marcador `<token>` — o el token real justo después de su creación; el archivo JSON (Cursor, VS Code) está plegado.
  - **Ventana del token** en dos columnas, la mitad de alta: cada herramienta muestra su nombre MCP (`list_devices`, `terminal_run`…), la marca **acción** o **sensible**, y el motivo si está en gris.

## 2.115.0 — 2026-10-30

- **Asistentes de IA: acceso remoto más rápido y más completo.**
  - **Apertura de las sesiones más rápida**: un equipo al que apunta el asistente (ficha, acción…) se precalienta un minuto y luego abre su sesión en menos de 2 s en lugar de 5; una sesión de PowerShell nueva está lista en ~2 s (sonda enviada a intervalo corto); las sesiones del asistente siguen abiertas 5 minutos entre dos llamadas (2 para el escritorio), así que las llamadas siguientes salen de inmediato.
  - **Capturas de pantalla ligeras**: la imagen se guarda en memoria y, tras la primera captura, solo transitan las zonas modificadas — una acción que abre un menú solo devuelve ese menú (Zorin GNOME: de 0,2 a 0,5 s por captura siguiente). Los equipos Windows envían ahora su pantalla comprimida sin pérdida (en lugar de píxeles en bruto) y responden de inmediato cuando nada se ha movido, en lugar de dejar al servidor esperar casi un segundo, una vez actualizado su agente.
  - **Escritura fiel en Linux**: el agente escribe cada carácter con la tecla y el nivel de la distribución de teclado real del equipo (leída en la sesión GNOME, el servidor X o el sistema): `<`, `>`, `@`, `€`, las mayúsculas y las letras acentuadas con tecla muerta (`ê`) pasan en AZERTY en GNOME Wayland, X11 y wlroots. En Windows, la escritura de texto sale en un bloque.
  - **Terminal interactivo** (nueva autorización): el asistente escribe, pulsa teclas (`{enter}`, `{ctrl+c}`, `{esc}`, flechas…) y lee la pantalla del terminal (120 × 40) — vim, top, preguntas sí/no, PowerShell; la sesión conserva su estado de una llamada a otra.
  - **Scripts más largos** en el terminal: hasta 64 KB, plazo de hasta 5 minutos.
  - **Una persona pasa antes que el asistente**: cuando se alcanza el límite de sesiones de un equipo (dos por tipo, cuatro para un equipo Windows), abrir una sesión desde la interfaz cierra primero las sesiones inactivas del asistente. Si su terminal interactivo se cerró entre dos llamadas, su siguiente entrada no se escribe en el shell nuevo: se le avisa.
- **Consola de texto de los equipos Linux sin pantalla**: abrir vim ya no la cierra (una secuencia de terminal privada, `ESC [ ? 4 m`, hacía caer el emulador de pantalla).
- **Agentes**: una sesión remota que permanece en silencio espacia sus lecturas (25 ms → 0,1 s → 0,25 s), en lugar de consultar al servidor decenas de veces por segundo; el agente Windows reserva el JPEG a los navegadores (noVNC) y envía Tight sin pérdida a los demás clientes.

## 2.114.0 — 2026-10-30

- **Asistentes de IA: el terminal, los archivos y el escritorio de los equipos, en directo.** Once nuevas autorizaciones en **Asistentes de IA**, grupo «Acceso remoto», cada una que marcar por separado:
  - **Comandos en el terminal**: el asistente lanza un comando en el bash root (Linux) o el PowerShell SYSTEM (Windows) del equipo y lee su salida y su código de salida en unos segundos, sin esperar al próximo contacto del agente; superado el plazo elegido, el comando se interrumpe.
  - **Archivos**: explorar, leer (texto o base64), escribir (sustitución atómica), organizar (carpeta, copia, desplazamiento, renombrado, permisos) y eliminar, con una autorización distinta para la lectura, la escritura, la organización y la eliminación.
  - **Escritorio**: captura de pantalla devuelta como imagen al asistente, que ve la pantalla (escritorio de Windows, GNOME en Wayland, pantalla de bloqueo, consola de texto de un equipo sin pantalla, zoom sobre una zona), y luego clic, escritura, teclas y atajos (Ctrl+Alt+Supr incluido) y rueda, y cada acción devuelve una nueva captura.
- Mismas sesiones que la interfaz (relé del agente, permisos «Abrir un terminal remoto», «Gestionar los archivos del equipo», «Abrir el escritorio noVNC» de la cuenta que lo creó): una sesión por acceso y por equipo, reutilizada de una llamada a otra y cerrada tras un minuto sin llamada, o en cuanto se revoca o modifica el acceso. Los comandos, escrituras, eliminaciones y acciones en el escritorio entran en el diario (sin el texto escrito).
- Validado en el laboratorio con Claude Code: captura y descripción del escritorio GNOME Wayland de Zorin OS, PowerShell en Windows Server 2025 y Windows 11, archivos en Windows y Linux, Ctrl+Alt+Supr en la pantalla de bloqueo de Windows.

## 2.113.0 — 2026-10-30

- **Asistentes de IA (MCP): conecte Claude Code, Cursor, VS Code… a loco.** Nueva página **Administración → Asistentes de IA**: cree un **acceso** cuyas autorizaciones se marcan **una a una** — listar y consultar los equipos, buscar un programa instalado, pedir un inventario, actualizar uno o varios equipos, reiniciar, instalar un programa del catálogo, cancelar una acción en espera, leer y lanzar los scripts de la biblioteca, hacer ejecutar comandos escritos por el asistente, estado del servidor, diario. El asistente controla entonces loco en lenguaje natural («¿qué equipos tienen actualizaciones de seguridad?», «lanza uptime en este servidor»).
- **Nunca más que su creador.** Un acceso está vinculado a la cuenta que lo crea: en cada llamada, solo puede hacer lo que está marcado **y** sigue estando permitido por el perfil de esa cuenta. Cuenta desactivada, eliminada o por finalizar, acceso caducado o revocado: rechazo inmediato. Las acciones siguen el mismo camino que la interfaz y entran en el diario con el nombre del acceso; un comando escrito por el asistente se conserva tal cual en el historial de los scripts.
- **Sencillo de conectar.** El token solo se muestra una vez (loco solo guarda una huella), con el comando `claude mcp add` y la configuración JSON listos para copiar; caducidad a elegir; último uso visible (fecha, dirección, cliente). Servidor MCP en `https://<servidor>:8443/mcp` (HTTP «Streamable HTTP»), compatible con las dos generaciones del protocolo: saludo `initialize` (de 2025-03-26 a 2025-11-25) y 2026-07-28. Validado con los SDK oficiales de Python 1.x y 2.x y con Claude Code en la demo.
- Nuevo permiso **Conectar asistentes de IA** (administradores por defecto); función que se puede desactivar en **Funciones**.

## 2.112.0 — 2026-10-30

- **Inicio de sesión con Authentik (y cualquier proveedor OpenID Connect).** En **Directorios y SSO**, dos tipos nuevos: **Authentik** y **Otro OpenID Connect** (Keycloak, Zitadel, Authelia, Microsoft Entra ID…). La página de inicio de sesión muestra entonces un botón **«Iniciar sesión con Authentik»**: la contraseña, la autenticación de dos factores y las reglas de acceso las sigue gestionando Authentik. Como para LDAP y RADIUS, el primer inicio de sesión de una persona crea una **solicitud de acceso** que aprobar en **Cuentas**; no se empareja ninguna cuenta local por su nombre o su correo electrónico, y desactivar el proveedor cierra en el acto las sesiones de sus cuentas.
- **Puesta en marcha guiada.** El formulario muestra la **URL de redirección** que declarar en Authentik (botón *Copiar*) y acepta el emisor igual que la dirección de configuración completa. El **certificado autofirmado de Authentik** se pega tal cual: loco lo **fija** (su nombre «*» no permite comprobar la dirección); una autoridad de su red se pega del mismo modo. **Probar** hace un verdadero inicio de sesión en el proveedor y muestra la identidad recibida (identificador propuesto, nombre, correo electrónico, grupos) sin crear nada, incluso antes de la activación; en caso de rechazo, se muestran el detalle devuelto por el proveedor y los ajustes que comprobar.
- **Seguridad.** Flujo «code» con **PKCE** (S256), `state` y `nonce` vinculados al navegador mediante una cookie cifrada; token de identidad comprobado (firma RSA, ECDSA o EdDSA con las claves publicadas, o **HS256 con el secreto del cliente** cuando el proveedor Authentik no tiene clave de firma; emisor, audiencia, caducidad); control del emisor a la vuelta (RFC 9207); ningún token conservado; secreto del cliente cifrado como los demás secretos de directorio. Validado de extremo a extremo con **Authentik 2026.8** (clave RS256 y HS256, certificado autofirmado fijado).
- Vista general: la parte «Sistema y otros» del disco se traduce ahora al inglés y al español.

## 2.111.1 — 2026-10-30

- **Escritorio remoto GNOME Wayland en un equipo bloqueado.** GNOME rechaza el escritorio remoto mientras la sesión está bloqueada («Session creation inhibited») — y es el caso habitual cuando no hay nadie delante del equipo. El agente **desbloquea la sesión durante la toma de control, y luego la vuelve a bloquear automáticamente al final** (pestaña cerrada, sesión cerrada): un equipo bloqueado nunca se deja abierto, y un equipo no bloqueado no se bloquea. Durante la sesión, la pantalla local muestra el escritorio con el indicador de compartición de GNOME. Si la pantalla se bloquea por el camino (inactividad, bloqueo local), la sesión remota se cierra con un mensaje claro; basta con volver a abrirla.

## 2.111.0 — 2026-10-30

- **Escritorio remoto GNOME en Wayland, de verdad (Zorin OS, Ubuntu…).** La captura por la API de «captura de pantalla» de GNOME Shell (2.110) la rechaza GNOME 46 («Screenshot is not allowed»). El agente pasa ahora por **la API del escritorio remoto integrado de GNOME** (sesiones RemoteDesktop + ScreenCast de Mutter): imagen en directo por PipeWire (~25 i/s), teclado y ratón inyectados por la misma sesión — **ninguna configuración, ninguna ventana de consentimiento, ningún reinicio**. Validado en un **Zorin OS 18.1 recién instalado** (GNOME 46, Wayland): todo lo necesario viene de serie. Durante la sesión, GNOME muestra su indicador de compartición de pantalla; la persona que está delante del equipo puede detenerla con un clic, y la sesión remota se cierra entonces con un mensaje. Para esta sesión, el agente deja root y actúa como el usuario conectado. Solo la pantalla principal.
- **Teclado fiel en los escritorios Wayland (AZERTY, teclas muertas, AltGr).** El servidor de escritorio remoto admite la extensión de «teclas físicas» de noVNC, como la consola de Proxmox: la distribución del equipo se aplica tal cual — `<` `>`, `ê` con la tecla muerta, `@` `€` con AltGr… Las sesiones X11 conservan su comportamiento.
- Compositores *wlroots* (Sway, Hyprland…): captura con grim sin cambios, ahora con las teclas físicas. Se retira la vía de «captura de GNOME Shell» de la 2.110.

## 2.110.0 — 2026-10-30

- **Escritorio remoto en GNOME Wayland (Zorin OS, Ubuntu…).** Un escritorio GNOME en sesión Wayland no expone la captura de pantalla libre (*wlr-screencopy*) que usa grim: el escritorio remoto fallaba con «el compositor no admite wlr-screencopy». El agente cambia ahora a **la API de captura de pantalla de GNOME Shell** (por D-Bus, en nombre del usuario de la sesión) — el escritorio remoto funciona por tanto en GNOME Wayland **sin cambiar de sesión ni reiniciar el equipo**. El teclado y el ratón ya pasaban por un dispositivo de entrada virtual independiente del compositor. Es más lento que una sesión X11 o que un compositor *wlroots* (unas pocas imágenes por segundo) y depende de una versión de GNOME que siga exponiendo esta API; si no, el mensaje invita a abrir una sesión X11 (Xorg).

## 2.109.0 — 2026-10-30

- **Ignorar un certificado HTTPS rechazado en el sitio de origen de un programa.** Cuando la descarga de un instalador falla con «Certificado HTTPS no válido en el sitio de origen» (certificado caducado o mal configurado por parte del editor), la ficha del programa propone ahora **ignorarlo y volver a intentarlo**, y el panel *Instalador* conserva un ajuste «Certificado del sitio de origen: comprobado / ignorado» que se puede restablecer en cualquier momento. La opción solo aparece cuando loco conoce la **huella SHA-256** del archivo (catálogo WinGet): la integridad sigue comprobándose — se rechaza cualquier archivo alterado —, solo se levanta la validación del certificado de transporte.

## 2.108.0 — 2026-10-30

- **Inventario de los equipos Debian/Ubuntu más robusto.** Cuando otra herramienta APT ocupaba el equipo en el momento del inventario — actualizaciones automáticas, tarea `apt-daily`, o el final mismo de la instalación del agente —, `apt-get update` fallaba con «E: Unable to lock directory /var/lib/apt/lists/» y el equipo seguía mostrado como **«inventario incompleto»** durante horas. El agente **espera ahora a que se libere el bloqueo** (y rehace el inventario en el próximo contacto si hace falta) en lugar de señalar un fallo.
- **Distribuciones derivadas de Ubuntu/Debian reconocidas automáticamente.** La configuración del repositorio de loco detecta ahora las derivadas (Zorin OS, Linux Mint, Pop!_OS, elementary…) a través de su base (`ID_LIKE`) y conserva el nombre de versión de origen (p. ej. Zorin OS 18 → Ubuntu *noble*), en lugar de mostrar «Distribución no reconocida» y recurrir solo a la caché APT.

## 2.107.0 — 2026-10-30

- **Escritorio remoto: soporte de Wayland.** La toma de control remota de un equipo Linux solo funcionaba en una sesión **X11** (mensaje «Escritorio Wayland no soportado; hace falta una sesión X11.»). Ahora también admite **Wayland** en los compositores de tipo *wlroots* (Sway, Hyprland, Wayfire, river…): la pantalla se captura con **grim** y el teclado y el ratón se reproducen mediante un **dispositivo de entrada virtual** (`/dev/uinput`), independiente del compositor. En Debian/Ubuntu, `grim` se propone automáticamente al instalar el agente (dependencia recomendada).
- **Mensaje claro en GNOME/KDE.** Estos escritorios Wayland no permiten la captura de pantalla sin portal de consentimiento: el agente lo indica ahora explícitamente e invita a abrir una sesión X11, en lugar de un rechazo genérico.

## 2.106.0 — 2026-10-29

- **Centro de software más cuidado.** Las tarjetas de programas se rehacen al estilo del resto de la interfaz: icono más grande, nombre destacado, versión legible, sombra suave y efecto al pasar el ratón — se acabó el identificador técnico de WinGet mostrado sin más en la tarjeta (sigue en la ficha del programa).
- **Iconos más rápidos y más listos.** La caché del navegador de los iconos pasa a una semana (las visitas y cambios de página siguientes son instantáneos), y al abrir una página del centro, loco **precarga en segundo plano** los iconos visibles para que estén listos en lugar de descargarse bajo demanda.
- **Corrección:** la tarjeta «Certificado HTTPS» de la página Sistema abre bien la pestaña **Certificado** (abría la pestaña Red).

## 2.105.0 — 2026-10-29

- **Windows Update: inventario más robusto (agente).** Cuando un equipo rechaza la búsqueda de controladores por Windows Update — error `0x80240032` (criterio no válido), visto en algunas compilaciones de Windows 11 o cuando los controladores están desactivados por directiva — el agente **vuelve automáticamente a solo las actualizaciones de software** en lugar de señalar «inventario incompleto». El software se inventaría y se instala normalmente; los controladores siguen detectándose cuando el equipo lo permite.

## 2.104.0 — 2026-10-29

- **Dos correcciones de estilo.** El selector de sistema de la página *Paquetes Linux* (Debian/Ubuntu ↔ Rocky/RHEL) ya no se extiende a todo el ancho medio vacío: vuelve a ser un botón compacto de dos segmentos (una regla de la página *Directorios* lo aplastaba por error; se conserva el selector de cuatro opciones de los directorios). Y la flecha de las listas desplegables (`select`) pasa a ser un **chevron** limpio en lugar de un pequeño triángulo.

## 2.103.0 — 2026-10-29

- **Aplicación web instalable (PWA).** loco puede ahora **instalarse** como una aplicación (en el escritorio o en la pantalla de inicio del móvil): manifiesto de aplicación, iconos (incluido un icono «maskable»), apertura en ventana independiente. Un **service worker** guarda en caché el armazón de la interfaz (cargas más rápidas) y muestra una cuidada **página «sin conexión»** si no se puede llegar al servidor. La instalación requiere un acceso HTTPS de confianza (certificado de loco aceptado).

## 2.102.0 — 2026-10-29

- **Fondo de pantalla: el contenido se coloca sobre una «hoja».** Con un fondo de pantalla, el contenido de la página (títulos, descripciones, estadísticas, tarjetas) ya no flota sin más sobre la foto: descansa sobre una superficie, y el fondo de pantalla pasa a ser un **marco** limpio alrededor y detrás de la barra de navegación. Mucho más nítido y legible. Los temas **sin fondo** (claro, oscuro liso…) mantienen el contenido directamente sobre el fondo liso, depurado. La página de inicio de sesión conserva su tarjeta central.

## 2.101.0 — 2026-10-29

- **Tarjetas y superficies depuradas (estilo más «pro»).** Inspirado en un diseño de panel cuidado: sombras más suaves, esquinas algo más redondeadas, más aire en las tarjetas y entre ellas, jerarquía más clara. Las sombras se **armonizan en todos los temas** para que un cambio de tema siga siendo agradable en todas partes (claro, OLED, oscuro suave, medianoche, sepia). El fondo de pantalla sigue siendo el predeterminado; la interfaz simplemente está más tranquila y mejor asentada.

## 2.100.0 — 2026-10-29

- **Interfaz más «asentada» sobre el fondo de pantalla.** Los paneles se hacen algo más opacos y la foto de fondo se difumina más: la interfaz destaca claramente en lugar de dar una impresión de «cristal que flota», sin perder el ambiente del fondo. La transparencia por defecto pasa del 80 al 90 % (siempre ajustable en *Apariencia*).

## 2.99.0 — 2026-10-29

- **Pantalla de inicio de sesión coherente con la apariencia por defecto.** La página de inicio de sesión (y las demás pantallas fuera de sesión) toman ahora el **tema, el color, la transparencia y el fondo** por defecto de la instancia — tarjeta de **cristal esmerilado** incluida. Antes, un ajuste antiguo memorizado en el navegador podía mostrarlas en un tema totalmente distinto (p. ej. negro + acento verde, tarjeta opaca) sobre el fondo. Se retira el pequeño selector de tema fuera de sesión, que ya no servía (la apariencia se ajusta en la cuenta).

## 2.98.0 — 2026-10-29

- **Cabecera «flotante».** La barra de navegación ya no se extiende por todo el ancho arriba: sus elementos se reúnen en un bloque **redondeado**, separado de los bordes, colocado encima del contenido (y del fondo de pantalla). Más ligera, más moderna.
- **Legibilidad sobre los fondos de foto.** El texto colocado directamente sobre el fondo (cabeceras de página, estadísticas, pie del inicio de sesión) recibe un ligero halo del color del tema para seguir nítido sobre una foto — en todos los temas, claros y oscuros. La página de inicio de sesión también muestra el fondo de pantalla, con su tarjeta central opaca.
- **Corrección:** elegir «Sin fondo» en *Apariencia* quita bien el fondo de pantalla (volvía por error al fondo por defecto desde la 2.97.0).

## 2.97.0 — 2026-10-29

- **Apariencia por defecto más acogedora.** En la instalación, loco adopta el preajuste **«Alpes»** (tema azul noche, acento azul, foto de montaña de fondo, paneles de cristal esmerilado) en lugar del negro liso. Cada usuario sigue siendo libre de cambiar el tema, el color, la transparencia y el fondo, o de volver a un tema sobrio, desde *Apariencia*.
- Capturas de pantalla del repositorio rehechas con este nuevo estilo.

## 2.96.0 — 2026-10-29

- **Logo personalizable (global).** En *Apariencia*, una sección **Logo** reservada a los administradores permite sustituir el logo de loco por el de su organización — **para todo el mundo**: cabecera, página de inicio de sesión y favicon. Imagen (PNG, JPEG, GIF, WebP) o **SVG**, 2 MB como máximo; vuelta al logo de loco con un clic. Cuando hay un logo definido, la palabra «loco» desaparece para dejar sitio a la marca elegida.
- A diferencia del tema, el color y el fondo (propios de cada cuenta), el logo es **global** a la instancia. El SVG se sirve como imagen (no puede ejecutarse ningún script) y su envío está reservado al permiso «Configurar el servidor».

## 2.95.0 — 2026-10-29

- **Fondos de pantalla fotográficos (naturaleza) en 4K.** La ventana *Apariencia* ofrece ocho verdaderos fondos fotográficos de alta definición — **Montaña, Bosque, Desierto, Dunas, Océano, Aurora, Noche estrellada, Invierno** — además de los lisos y los degradados, ahora ordenados en una sección aparte. Tres preajustes fotográficos (Alpes, Boreal, Desierto) los ponen en valor.
- **Legibilidad garantizada en todos los temas.** Un velo adaptado al color del tema y unos paneles de cristal esmerilado hacen que cualquier combinación siga siendo legible: tema **claro** sobre una montaña, tema oscuro sobre un cielo estrellado, sepia sobre un paisaje de invierno — el texto siempre destaca. Elegir una foto baja automáticamente la transparencia si los paneles eran opacos, para que el fondo se vea de inmediato.
- Fotos con licencia de Unsplash (uso libre) — créditos en `static/wallpapers/CREDITS.txt`.

## 2.94.0 — 2026-10-29

- **Apariencia personalizable.** Nueva ventana *Apariencia* (menú de la cuenta → «Apariencia…») para componerse un portal a su gusto, **guardado en la cuenta** (se recupera el ajuste en cualquier navegador):
  - **5 temas**: OLED, Oscuro suave, Claro, y dos nuevos — **Medianoche** (azul noche) y **Sepia** (papel cálido).
  - **8 colores de acento** (se añaden **verde azulado** y **ámbar**).
  - **Transparencia de los paneles** ajustable: las tarjetas se vuelven translúcidas y dejan ver el fondo.
  - **Fondos de pantalla**: seis degradados incluidos (Aurora, Océano, Bosque, Crepúsculo, Rosa, Pizarra) o **su propia imagen** que importar (PNG, JPEG, GIF, WebP, 8 MB como máximo).
  - **6 preajustes** listos para usar (OLED, Claro, Océano, Bosque, Crepúsculo, Rosa) que ajustan el tema, el acento, la transparencia y el fondo con un clic.
  - Todo se aplica **en directo** y **sin parpadeo** al cargar (el tema se genera en el servidor). Fuera de sesión, la elección queda memorizada en el navegador.

## 2.93.0 — 2026-10-29

- **Windows Update: sección «Dirigir los equipos» despejada.** Los dos métodos (DNS, proxy a través del agente) caben en dos columnas cortas y equilibradas; el detalle de las condiciones y el comando manual de PowerShell pasan a un desplegable «Ajuste manual y solución de problemas del proxy» — se acabó el bloque de texto y el comando que desbordaba.

## 2.92.0 — 2026-10-29

- **Ajustes del parque y Configuración, más acabados.** *Ajustes del parque*: la dirección actual del servidor pasa a un recuadro con su estado justo al lado, y el campo de la nueva dirección está claramente etiquetado. *Configuración* (appliance): los menús «activado / desactivado» pasan a ser **interruptores** (Acceso HTTP, Caché de Windows Update, Red Hat, Flatpak), cada uno con una breve descripción; y cada límite de caché muestra su **uso real** debajo.

## 2.91.0 — 2026-10-29

- **Compositor de inscripción más sobrio.** Las casillas del compositor pasan a ser **interruptores** (como la página Funciones): una lista enmarcada, una etiqueta y su descripción a la izquierda, el interruptor a la derecha, la fila en gris cuando está apagada. La opción «primera instalación» queda aparte. El comando generado no cambia.

## 2.90.0 — 2026-10-29

- **Compositor de inscripción.** La página *Inscripción* pasa a ser un selector: se **marca lo que recibe el equipo** — **Gestión (agente)**, **Repositorio loco**, **Caché de las actualizaciones** (y, en Windows, **Redirigir Windows Update**), con una casilla **Todo** — y el **comando que lanzar se genera en directo**, por sistema (Windows, Debian/Ubuntu, Red Hat). La descarga del paquete para un equipo sin conexión y el despliegue masivo (GPO, Intune) siguen en «Otros métodos».
- **Repositorios Debian/Ubuntu por defecto.** loco crea ahora de oficio los repositorios **Debian 12 y 13, Ubuntu 22.04 y 24.04** (como el8/el9/el10 en Red Hat): ya no hace falta crearlos a mano antes de conectar un equipo Debian. Se revisa la sección *Sus repositorios* — el botón «Añadir paquetes» pasa a la cabecera del repositorio elegido.
- **Caché: nombres más claros desde la actualización, plegado más práctico.** Las etiquetas «Actualización de Windows», «Metadatos del repositorio»… aparecen ahora justo después de una actualización de loco (el agente reconstruye el índice de la caché al arrancar), sin esperar a un análisis manual. Y cuando la lista completa está desplegada, mantiene una altura limitada: ya no hace falta bajar hasta el final para plegarla.

## 2.89.0 — 2026-10-28

- **Lista de la caché más legible.** En las páginas **Windows Update** y **Paquetes Linux**, la lista de archivos en caché ya solo muestra los **5 más recientes** — el resto se despliega (y se pliega) con un clic — y cada fila indica **a qué corresponde el archivo** («Actualización de Windows», «Definiciones de Microsoft Defender», «Microsoft Edge», «Metadatos del repositorio»…), con el nombre del archivo justo debajo. Los paquetes .deb y .rpm conservan su nombre tal cual, ya claro.

## 2.88.0 — 2026-10-28

- **Ver y vaciar las cachés.** Las páginas **Windows Update** y **Paquetes Linux** listan ahora lo que está realmente en caché — cada archivo (actualización de Windows, paquete Debian o Ubuntu, réplica Red Hat o EPEL) con su origen, su tamaño y su fecha de entrada en caché — y permiten **eliminar un elemento** o **vaciar toda la caché** con un clic. Los trozos de un mismo archivo (Windows Update) se agrupan en un solo objeto; un archivo eliminado simplemente vuelve a salir de su servidor de origen en la siguiente petición de un equipo, no se pierde nada.
  - La lista se filtra, se actualiza en directo y se limita a los objetos más recientes en una caché grande. El análisis y la purga pasan por el agente (el contenido de las cachés solo puede leerlo él); en APT, se admiten tanto la caché nginx de la appliance como el apt-cacher-ng de un servidor Debian.
- **Páginas revisadas.** Presentación más sobria: un resumen objetivo (tamaño de la caché, parte servida localmente, últimos 14 días) sustituye a las grandes tarjetas, y el contenido de la caché ocupa el lugar central.

## 2.87.0 — 2026-10-28

- **Mi parque: reiniciar desde la lista.** Cuando un equipo espera un reinicio, aparece un botón **Reiniciar** en la lista, en el mismo lugar que *Actualizar*. Si el equipo también tiene actualizaciones pendientes, los dos botones conviven uno al lado del otro. El reinicio se confirma antes del envío y avisa a la persona conectada.

## 2.86.0 — 2026-10-28

- **Pantalla de inicio de sesión rediseñada.** Una barra ligera (loco, el servidor, el idioma y el tema) y una tarjeta centrada, más aireada: título y frase de bienvenida, campos más cómodos, y una mención **SSO** clara cuando hay directorios de empresa activos. Los errores (contraseña incorrecta, demasiados intentos) se muestran en la tarjeta, una sola vez. La misma presentación para la autenticación de dos factores (con un enlace para cambiar de cuenta) y la elección de la contraseña en el primer inicio de sesión.

## 2.85.0 — 2026-10-28

- **Directorios y SSO, más sencillos.** La página (renombrada *Directorios y SSO*) lista los directorios en una tabla: tipo, servidor, número de cuentas, **interruptor Activo / Inactivo** (desactivar cierra las sesiones de sus cuentas), Probar y Modificar, orden de prueba en el menú. Las solicitudes de acceso pendientes se señalan con un enlace a Cuentas.
  - Alta guiada por tipo: **Active Directory** (basta con el dominio; controlador y cuenta de servicio opcionales), **FreeIPA** (dominio y servidor; cuentas bajo `cn=users,cn=accounts`, autoridad FreeIPA indicada), otro directorio LDAP o RADIUS. Los ajustes técnicos pasan a «Opciones avanzadas». El servidor acepta un puerto (`dc1:3269`, catálogo global en SSL).
  - La pantalla de inicio de sesión habla de inicio de sesión SSO con la cuenta de la empresa.

## 2.84.0 — 2026-10-28

- **Autoridad de certificación a elegir.** Nueva pestaña *Sistema › Certificado* con un interruptor **Autoridad loco / Autoridad de empresa**: el certificado del portal puede firmarlo la autoridad de su empresa (AD CS, PKI interna…), en la que ya confían los equipos del dominio, y volver a la autoridad loco en cualquier momento.
  - Puesta en marcha guiada en cuatro pasos: **importar la autoridad** (.cer, .crt, .pem o cadena .p7b); **obtener el certificado** del servidor, ya sea mediante una **solicitud (CSR) generada por loco** — la clave privada nunca sale del servidor, se proporciona el comando `certreq` para AD CS — o con un certificado y su clave (PEM o PFX); **difundir la confianza** a los equipos, con el número de equipos listos; **cambiar**.
  - **Sin corte para el parque**: los agentes (2.84 y posteriores) reciben un conjunto «autoridad loco + autoridad de empresa» por su canal ya seguro y lo confirman; el cambio avisa si algunos equipos todavía no lo tienen. La autoridad loco sigue reconocida: la vuelta atrás siempre es segura.
  - Cada certificado se comprueba antes de servirse (clave que corresponde, dirección de los agentes cubierta, cadena firmada por su autoridad); nginx se recarga, o se restablece el certificado anterior. Una actualización de loco conserva el certificado de empresa mientras sea válido.
- **Actualizaciones: listas más cortas.** El registro de cambios y la lista de operaciones ya solo muestran los **5 más recientes**; el resto se despliega (y se pliega) con un clic. Solo está abierta la versión más reciente; una operación elegida más antigua despliega sola su lista.

## 2.82.0 — 2026-10-28

- **Página Actualizaciones rediseñada.** Todo lo que importa se ve de un vistazo, arriba de la página:
  - Una tarjeta **loco** (versión instalada → disponible, fecha de publicación, versiones de diferencia, «En el programa» con las novedades, botón **Instalar**) y una tarjeta **Sistema** (paquetes, parches de seguridad, núcleo, recuadro **Corregir con prioridad** ordenado por gravedad, botones **Instalar los parches** y **Actualizar todo**).
  - Una **banda de reinicio** compacta con su botón, cuando un nuevo núcleo o unos servicios lo exigen.
  - Las **novedades de la versión disponible** se leen antes de instalar: en cada comprobación, loco obtiene el registro de cambios publicado con ella (misma API de GitHub o GitLab, ningún flujo saliente nuevo).
  - **Registro de cambios en cronología**: novedades y versión instalada señaladas, una línea de resumen por versión, el historial antiguo plegado.
  - **Operaciones**: la lista y el registro de la operación elegida uno al lado del otro, sin códigos de color parásitos.
  - El **origen de las actualizaciones**, ajuste poco frecuente, pasa al final de la página, plegado.

## 2.81.0 — 2026-10-28

- **Scripts básicos listos para usar.** Dos nuevos scripts de Windows llegan a la biblioteca, junto al del controlador NVIDIA: **Reparar Windows (DISM + SFC)** (repara la imagen con `DISM /RestoreHealth` y luego comprueba los archivos del sistema con `SFC /scannow`, salida limpia) y **Limpiar los archivos temporales** (carpetas Temp, caché de Windows Update y papelera, con el espacio liberado). Modificables y eliminables como cualquier script.

## 2.80.0 — 2026-10-28

- **Registro de cambios en el portal.** La página *Administración → Actualizaciones* muestra ahora un panel **Registro de cambios**: lo que cambia con cada versión de loco, sin conexión, sin pasar por GitHub. Se señalan la versión instalada y la disponible.

## 2.79.0 — 2026-10-28

- **Controlador NVIDIA con un clic.** Un script listo para usar llega a la biblioteca *Scripts*: detecta la GPU NVIDIA del equipo, obtiene el último controlador **Game Ready (DCH)** directamente de NVIDIA, lo compara con la versión instalada y lo instala en silencio. Para lanzar en los equipos Windows, ahora o en una programación. Modificable y eliminable como cualquier script.

## 2.78.0 — 2026-10-28

- **Detección de los controladores en Windows Update.** El inventario solo miraba las actualizaciones de software: los controladores (tarjeta gráfica NVIDIA/AMD, chipset…) distribuidos por Windows Update nunca aparecían. Ahora se detectan, se muestran con una etiqueta **controlador** en la ficha del equipo, y se instalan junto con las demás actualizaciones.

## 2.77.0 — 2026-10-28

- **Scripts de Python.** Además de PowerShell (Windows) y shell (Linux), la biblioteca acepta scripts **Python compatibles con cualquier sistema**: el agente los ejecuta con el intérprete de Python del equipo (tanto en Windows como en Linux), como SYSTEM o root. Nueva pastilla de filtro y etiqueta propia.

## 2.76.0 — 2026-10-28

- **Congelar Windows Update en todo un grupo.** El menú «⋯» de un grupo de equipos propone **bloquear** o **devolver** las actualizaciones de Windows para todos sus miembros Windows de una vez.

## 2.75.0 — 2026-10-28

- **Congelar las actualizaciones de Windows por equipo.** En la página de un equipo Windows, un interruptor (desmarcado por defecto) impide que Windows instale sus actualizaciones por sí solo: es usted quien las lanza desde loco. El agente aplica la consigna en el próximo contacto y la retira si la desmarca.

## 2.74.1 — 2026-10-27

- **Mi cuenta, más legible.** Cabecera en etiquetas compactas (perfil, tipo de cuenta, último inicio de sesión, creación) con un solo formato de fecha. Pestaña Seguridad: campos de un ancho razonable y reglas de la contraseña mostradas al lado, como lista de comprobación; recuadro «Bueno saber» más discreto; autenticación de dos factores con su explicación y su botón en una misma línea.

## 2.74.0 — 2026-10-27

- **Creación de cuentas rediseñada.** La página Cuentas muestra la lista a todo el ancho y un botón **Nueva cuenta** arriba (desaparece el duplicado «Perfiles de acceso», la pestaña se queda). La creación se hace en una ventana en tres partes: el identificador con su regla mostrada; el **perfil de acceso en tarjetas**, cada una con lo que permite (solo se ofrecen los perfiles que usted puede asignar); la contraseña (escrita, generada con el dado o dejada vacía) y la opción de hacerla cambiar en el primer inicio de sesión. Si la creación se rechaza (identificador ocupado, contraseña demasiado débil), la ventana se vuelve a abrir con lo introducido.

## 2.73.3 — 2026-10-27

- **Mi parque: «reinicio necesario» en su sitio.** La información ya no es una insignia que pasaba a la línea siguiente según el espacio disponible, desplazada bajo el recuento: ahora es una discreta línea de estado (icono y texto anaranjados), siempre bajo el número de actualizaciones, alineada con él. El recuento y la insignia «seguridad» ya no se cortan.

## 2.73.2 — 2026-10-27

- **Mi parque: cabecera aligerada.** La casilla «Reiniciar si es necesario» ya no flota entre los botones: «Actualizar todo» (o «Actualizar este grupo») abre una ventana que resume la operación y ofrece el interruptor **Reiniciar los equipos si es necesario**, solo para aquellos en los que una actualización lo exige.

## 2.73.1 — 2026-10-27

- **Grupos de equipos y de programas: renombrar y eliminar desde la lista.** Cada fila tiene un menú «⋯» con *Modificar los miembros*, *Renombrar…* (solo cambia el nombre: se conservan los miembros, la descripción y los despliegues; se rechaza un nombre ya ocupado) y *Eliminar…* (con confirmación; los equipos, los programas y los despliegues se quedan). Reservado a las cuentas que pueden gestionar los grupos.

## 2.73.0 — 2026-10-27

- **Nueva fuente: Bricolage Grotesque** en lugar de IBM Plex Sans. Más carácter en los títulos y en las cifras grandes, sin dejar de ser sobria y legible en tamaño pequeño gracias a su tamaño óptico variable. Cifras de ancho fijo para las tablas, y cobertura completa del francés y del español (incluidos «—» y «’», que antes se mostraban en otra fuente). IBM Plex Mono se queda para las direcciones y el código.
- **Barra de avance de las operaciones del servidor** (*Administración → Actualizaciones*): durante una operación, una tarjeta arriba de la página muestra la etapa en curso, el porcentaje y el tiempo transcurrido.
  - **Actualización de loco**: descarga, comprobación de la huella, y luego cada una de las etapas del instalador (puertos, paquetes, copia del portal, certificado, nginx, servicios…).
  - **Actualizaciones del sistema**: «paquete n de N» según apk, dnf o APT.
  - Otras operaciones: barra animada.
  - Cuando el portal se reinicia al final de la instalación, la tarjeta lo indica y la página se vuelve a conectar sola.

## 2.72.0 — 2026-10-27

- **Shells más agradables, como un verdadero terminal** (shell de un equipo y shell root del servidor):
  - **Portapapeles**: seleccionar copia; el clic derecho copia la selección o pega si no la hay; **Ctrl+Mayús+C / Ctrl+Mayús+V** y Mayús+Insert; **Ctrl+C con una selección copia** (sin interrumpir el comando), sin selección va al shell como de costumbre.
  - **Enlaces pulsables**: Ctrl+clic (⌘+clic) abre la dirección en una nueva pestaña.
  - **Búsqueda en el historial**: Ctrl+Mayús+F, Intro / Mayús+Intro para navegar, resultados resaltados, Esc para cerrar.
  - **Tamaño del texto con el teclado**: Ctrl + / Ctrl − / Ctrl 0, además de los botones A− / A+.
  - **Pantalla completa**: el teclado se bloquea (navegadores Chromium) para que Ctrl+W, Ctrl+T… vayan al shell en lugar de cerrar la pestaña.
  - **Representación**: visualización WebGL (texto nítido, desplazamiento inmediato, marcos y tablas continuos), fuente **JetBrains Mono** incluida (flechas, guiones, símbolos y marcos en la misma fuente, cargada antes de la apertura para columnas exactas), ancho Unicode 11 (emojis y símbolos anchos alineados), contraste mínimo garantizado, historial de 10 000 líneas. Vuelta automática a la representación anterior si el navegador no tiene WebGL.
  - Un recordatorio de los atajos se muestra bajo el terminal.

## 2.71.1 — 2026-10-27

- **Actualización automática de los agentes Windows restablecida**: desde la 2.39, el servidor anunciaba a los equipos la huella del MSI original mientras les servía el MSI «genérico» (casillas de inscripción vaciadas). El agente Windows rechazaba por tanto cada actualización («huella del MSI incorrecta» en su registro, cada 2 minutos) y se quedaba en su versión de instalación. La huella anunciada es ahora la del archivo realmente servido: los equipos Windows se actualizan solos y reciben la supervisión de la 2.71.

## 2.71.0 — 2026-10-27

*Las versiones 2.70.0 y 2.70.1 no se publicaron por separado (una prueba fallaba en Rocky 8): su contenido, descrito más abajo, se entrega con la 2.71.0.*

- **Supervisión de los equipos**: cada agente (Linux y Windows) adjunta a su contacto, cada 2 minutos, una lectura: procesador y caudal de red de media desde el contacto anterior, memoria, swap o archivo de paginación, cada disco local con su punto de montaje, carga (Linux) y tiempo desde el arranque. loco conserva la última lectura y 7 días de historial.
  - **Ficha del equipo → pestaña Supervisión**: valores actuales (umbrales señalados), curvas *Procesador y memoria* y *Red* en 24 h o 7 días (interrumpidas cuando el equipo estaba apagado, información emergente al pasar el ratón), tabla de los discos con su ocupación. Un equipo sin contacto conserva su última lectura, en gris.
  - **Mi parque → columna Recursos**: mini indicadores de procesador, memoria y el disco más lleno, en color de estado más allá de los umbrales.
  - **Alertas**: tres nuevas reglas ajustables para los equipos: disco casi lleno, memoria saturada y procesador saturado (media en 15 minutos, nunca por un simple pico).
  - **En directo**: mientras la pestaña Supervisión está abierta y visible, el agente envía una lectura **cada 2 segundos** (procesador, memoria, red) por el canal que ya mantiene abierto con el servidor. Desfila una curva de los 3 últimos minutos, los valores se actualizan y se enciende un indicador «en directo». En cuanto se abandona la pestaña o se oculta la ventana, ya no se pide nada: ninguna carga adicional el resto del tiempo.
  - Los agentes se actualizan automáticamente; la supervisión aparece desde su primer contacto en la 2.71.

## 2.70.1 — 2026-10-27

- **NTP: servidor introducido que no responde, señalado con claridad**: cuando no se usa ninguno de los servidores introducidos, *Sistema → Hora y hardware* lo muestra («El servidor introducido no responde: 192.168.100.23») y precisa de dónde viene la hora (servidores de respaldo de la distribución, con su dirección), en lugar de un simple «sincronizada». La insignia pasa a «sincronizada por los servidores de respaldo». Una indicación da la pista en el servidor NTP: aceptar los clientes de la red (chrony: `allow`) y permitir UDP 123 de entrada. La tabla de las fuentes muestra también el nombre introducido (por ejemplo pool.ntp.org) bajo la dirección.
- **«Usar solo estos servidores»**: nueva opción para que los servidores introducidos sean las únicas fuentes. Con chrony, loco comenta él mismo las fuentes de la distribución en chrony.conf (pool, server, sourcedir), de forma reversible: desmarcar las restablece de forma idéntica. Con timesyncd, se desactivan los servidores de respaldo integrados. Sin servidor introducido, la opción se ignora, para no dejar nunca al servidor sin fuente.

## 2.70.0 — 2026-10-27

- **Certificado HTTPS: renovar e importar desde el portal** (*Sistema → Red y certificados*).
  - **Renovar**: nuevo certificado firmado por la autoridad loco (825 días, dirección y nombres actuales del servidor). nginx se prueba antes de recargarse, y el certificado anterior se restablece en caso de fallo. Los equipos que confían en la autoridad no notan ninguna diferencia.
  - **Importar un certificado para un nombre DNS** (autoridad de empresa, Let's Encrypt…): cadena PEM y clave, comprobadas (clave correspondiente, validez, nombres DNS). Solo se sirve a los navegadores que abren el portal por uno de sus nombres (SNI); los agentes del parque, que fijan la autoridad loco, conservan el certificado de loco en la dirección del servidor. Se rechaza un certificado que cubriera esa dirección, ya que los agentes perderían la conexión. El certificado importado puede sustituirse o retirarse, y su bloque nginx se regenera en cada actualización.

## 2.69.3 — 2026-10-27

- **Creación de cuenta: contraseña a elegir**: el formulario *Nueva cuenta* ofrece un campo **Contraseña**. Se puede escribir una, hacer clic en el **dado** para generar una aleatoria (legible, sin caracteres ambiguos, mostrada en claro y seleccionada; cada clic propone otra), o dejarlo vacío para que se genere al crearla. Una línea de ayuda comprueba en directo la regla (12 caracteres como mínimo, sin el nombre de la cuenta). Un interruptor **«Que cambiar en el primer inicio de sesión»** (activado por defecto) deja elegir si se impone o no el cambio. Tras la creación, el recuadro **Credenciales que transmitir** muestra la cuenta y su contraseña con un botón Copiar. Una llamada con script que no envía el interruptor mantiene obligatorio el cambio.

## 2.69.2 — 2026-10-27

- **Descargas rechazadas con «Certificado HTTPS no válido» (GIMP, SumatraPDF…)**: la verdadera causa era el reloj del servidor atrasado. Un certificado emitido después de la fecha del servidor parece «todavía no válido»: con un reloj en el 3 de octubre, 7-Zip pasa pero SumatraPDF y GIMP, cuyos certificados son más recientes, fallan. El mensaje señala ahora el reloj, con la fecha del servidor, y remite a *Sistema → Hora y hardware*.
- **NTP en la appliance Alpine 3.24**: su chrony.conf lee la carpeta `conf.d` al principio y luego desactiva el puerto de órdenes (`cmdport 0`) más abajo; el ajuste de loco depositado en `conf.d` quedaba por tanto anulado y el estado de la hora seguía ilegible. loco se incluye ahora al final de chrony.conf (y retira el archivo anterior), lo que restablece la lectura del estado y la desviación del reloj en el portal.

## 2.69.1 — 2026-10-27

- **Grupos de equipos y de programas**: tras guardar un grupo (creación o modificación), se vuelve a la lista de grupos, situada en el grupo guardado y resaltado, en lugar de quedarse en el editor.

## 2.69.0 — 2026-10-27

- **Flujos salientes que autorizar en el cortafuegos**: *Sistema → Red y certificados* lista ahora, bajo los puertos de entrada, los destinos de Internet a los que el servidor debe llegar **de salida**, con puertos y protocolos: resolutores DNS, servidores NTP configurados, repositorios del sistema del servidor, origen de las actualizaciones de loco (GitHub o GitLab), réplicas Linux realmente vistas en la caché de apt-cacher-ng, repositorios RPM y Flathub, dominios de Windows Update, winget y hosts de los instaladores del catálogo de Windows, y, si están configurados, servidor SMTP, webhook de alertas y copia de seguridad externa (S3 o SFTP). Todo se saca de la configuración y de las funciones activas; un botón copia la lista en formato «un destino por línea» para el equipo de red.

## 2.68.0 — 2026-10-27

- **Hora del servidor, renovación de NTP**: el estado de la hora mostrado solía ser falso. Ahora lo mide el agente root (el portal, sin privilegios, no puede consultar chrony) y se ha rediseñado el bloque *Sistema → Hora y hardware*: insignia de estado clara (sincronizada / en curso / desactivada / gestionada por el anfitrión), resumen (hora local, zona horaria, **fuente en uso, estrato, desviación**) y tabla por servidor (en uso / accesible / inaccesible…). La sincronización NTP acepta **varios servidores** (IP v4/v6 o nombres, separados por espacios), prioritarios sobre los de la distribución, y el ajuste de red pasa antes que la zona horaria. En la appliance (chrony con `cmdport 0`), el puerto de órdenes local se vuelve a activar automáticamente para que el estado vuelva a ser legible; se admiten timesyncd (Debian/Ubuntu) y chrony (Red Hat, Alpine), con «include» para las distribuciones sin *confdir* (Rocky, AlmaLinux) y persistencia de la elección tras el reinicio en la appliance (OpenRC).

## 2.67.0 — 2026-10-27

- **Shell root: el terminal directamente**: una vez activado el shell (y reiniciado el servidor), *Administración → Shell root*, el icono de la barra y Ctrl+K abren directamente el terminal root, sin página intermedia. Un botón **«Desactivar el shell root»** bien visible en el terminal cierra todos los shells y corta el acceso (se pide confirmación); se vuelve entonces a la puesta en servicio. La página de pasos solo sirve mientras el shell no está activo. El shell solo arranca en el primer intercambio del terminal (protegido contra el CSRF), nunca al simple mostrar la página.

## 2.66.0 — 2026-10-26

Esta versión publica también el contenido de la 2.64.0 y la 2.65.0 (página Shell root dedicada, pasada de ergonomía y de diseño), no publicadas por separado.

- **Integración continua más rápida y más sobria, sencilla de retomar**: cada push ya solo lanza las comprobaciones rápidas (lint, pruebas unitarias, sintaxis de PowerShell — ~3 min, sin máquina virtual). Las pruebas de integración completas (VM Linux, Docker, ISO, servidores Windows) se ejecutan cuando un push toca los instaladores, los agentes o la ISO, y siempre antes de una publicación: empujar una etiqueta `vX.Y.Z` comprueba, prueba y publica en una sola ejecución (la ISO y el agente Windows publicados = los probados). Se acabó la limitación del paralelismo, QEMU se instala sin paquetes gráficos (43 paquetes en lugar de 298), se acabó la ejecución semanal automática. Un fork funciona sin secreto ni servidor: vea *Integración continua y publicación* en el README.

## 2.65.0 — 2026-10-26

Pasada de ergonomía y de diseño por todo el portal, para orientarse más rápido.

- **Barra superior despejada**: cinco entradas (Vista general, Mi parque, Linux, Windows, Administración). El idioma, la apariencia, la cuenta y el cierre de sesión se reúnen en un **menú de cuenta** (avatar). Ya no se desborda nada, ni siquiera a 1280 px.
- **Administración ordenada por secciones**: el menú presenta *Acceso* (cuentas, perfiles, directorios), *Servidor* (sistema, actualizaciones, configuración, funciones, shell root) y *Seguimiento* (diario, alertas). Las pestañas ya solo muestran las de la sección en curso.
- **Inscripción se une a Mi parque**, como pestaña junto a los equipos, los grupos y los despliegues.
- **Sistema en pestañas**: *Estado* (servicios, ubicaciones, versiones), *Copias de seguridad*, *Red y certificados*, *Hora y hardware*. Los indicadores de arriba llevan a la pestaña correspondiente, y las acciones devuelven a la pestaña correcta. El panel QEMU solo aparece si se aplica.
- **Vista general**: los indicadores de las cachés y los del servidor forman dos filas distintas.
- **Diario y operaciones legibles**: etiquetas para la autenticación de dos factores, las programaciones, las copias de seguridad, el shell root y las alertas, en lugar de los códigos técnicos; resumen legible de los parámetros de las operaciones del servidor.
- **Correcciones**: la barra de selección de *Mi parque* seguía mostrada sin selección; espaciados en *Mi cuenta*; espacios que faltaban en *Inscripción* y *Windows Update*; opción «Reiniciar si es necesario» aligerada.

## 2.64.0 — 2026-10-26

- **Shell root mejor integrado**: tiene ahora su propia página en el menú *Administración* (y por tanto en la búsqueda Ctrl+K), con su estado como subtítulo (desactivado, reinicio requerido, activo). La página guía la puesta en servicio en tres pasos — activar, reiniciar, abrir — y el terminal se abre en la misma página. Cuando el shell está activo, un icono de terminal en la barra superior da acceso a él con un clic. El panel ha dejado la página *Sistema*.

## 2.63.0 — 2026-10-26

- **Shell root del servidor en el navegador** (*Sistema → Shell root del servidor*): un terminal root en el propio servidor loco (instalación Linux o appliance), reservado a la cuenta «admin» de la instalación. **Desactivado por defecto**; la activación pide la contraseña de la cuenta admin y **solo surte efecto tras un reinicio del servidor** (se ofrece el botón de reinicio). La desactivación es inmediata y cierra los shells abiertos. Cada activación, apertura y cierre queda registrado. El terminal lo sirve el agente root mediante un socket local reservado al portal.

## 2.62.0 — 2026-10-26

- **`ufw` presente también en las appliances existentes**: en la 2.59 el paquete `ufw` solo se añadía a la imagen ISO (instalaciones nuevas). Ahora también está en la lista de paquetes que aplica `install.sh`, así que una appliance ya instalada recibe `ufw` en su próxima actualización de loco. Siempre instalado sin activarse ni configurarse.
- **Mensaje de bienvenida (MOTD) de la appliance**: la conexión a la appliance (consola y SSH) muestra ahora un cuidado banner de loco — logo, versión, sistema, dirección del portal y recordatorio de los comandos de consola — regenerado en cada arranque.

## 2.61.0 — 2026-10-26

- **Estado NTP correcto en la appliance Alpine**: *Sistema → hora del servidor* solo leía el estado de sincronización mediante `timedatectl`, ausente en la appliance Alpine — la banda mostraba por tanto siempre «sincronización NTP desactivada», incluso cuando chrony estaba activo y sincronizado. El estado (activado / sincronizado / en curso) se lee ahora de chrony cuando `timedatectl` no existe. Para usar con la corrección de la 2.60 (recarga de chrony con `rc-service`).

## 2.60.0 — 2026-10-26

- **Sincronización NTP corregida en la appliance Alpine**: el ajuste del servidor NTP desde *Sistema → hora del servidor* recargaba el servicio con `systemctl`, ausente en la appliance Alpine (OpenRC) — el nuevo servidor solo se tenía en cuenta en el reinicio. El servicio se recarga ahora con `rc-service` cuando `systemctl` no existe. Validado de extremo a extremo contra un servidor NTP en contenedor. Cubierto por pruebas (Debian → `systemctl`, Alpine → `rc-service`, contenedor → se omite).

## 2.59.0 — 2026-10-26

- **`ufw` presente en la appliance (ISO)**: el cortafuegos `ufw` se instala ahora en la imagen Alpine, pero **ni se activa ni se configura** — no aplica ninguna regla y no filtra nada por defecto. Simplemente está disponible si desea poner reglas usted mismo más adelante (`ufw enable`, `ufw allow …`).

## 2.58.0 — 2026-10-26

- **Puertos de red en Sistema**: una nueva sección *Administración → Sistema* resume los puertos que abrir **de entrada** en el cortafuegos para que la aplicación funcione — interfaz y agentes en HTTPS (8443), arranque HTTP de los equipos, caché de paquetes Linux (3142), caché de Windows Update (80/443) y proxy de Windows Update (3128). La lista se adapta a la configuración y a las funciones activadas, precisa el público afectado (administración, equipos Linux, equipos Windows) y recuerda que el puerto interno de la aplicación se queda en local.

## 2.57.0 — 2026-10-26

- **QR de autenticación de dos factores fiable en todas partes**: el código QR de alta se muestra ahora incluso sin el paquete del sistema `python3-qrcode` (appliance, instalaciones mínimas) gracias a un generador de QR incluido sin dependencias. La página de alta lista también las **aplicaciones compatibles** (Microsoft Authenticator, Google Authenticator, Authy, 1Password, Bitwarden — sirve cualquier aplicación TOTP).

## 2.56.0 — 2026-10-25

- **Despliegue de paquetes Linux**: la sección *Despliegues* ofrece ahora, junto a los programas Windows, una tienda de los paquetes `.deb` y `.rpm` publicados en loco. Se eligen paquetes, se apunta a equipos o grupos Linux, se previsualiza y luego se confirma; cada equipo solo instala los paquetes compatibles con su sistema (`.deb` → Debian/Ubuntu, `.rpm` → Red Hat), desde el repositorio loco ya configurado. El avance se sigue equipo por equipo como para Windows. Nueva acción de agente `install-package` (permiso *Desplegar un programa*).

## 2.55.0 — 2026-10-25

- **Insignia «reinicio necesario»** en *Mi parque*: el antiguo texto de advertencia pasa a ser una insignia ámbar con icono, bien alineada con los contadores de actualizaciones y la insignia de seguridad.

## 2.54.0 — 2026-10-25

- **Autenticación de dos factores (TOTP)** para las cuentas locales: desde *Mi cuenta → Seguridad*, se activa escaneando un QR (o introduciendo la clave) con una aplicación de autenticación, y luego se pide un código de 6 cifras en cada inicio de sesión. Unos **códigos de respaldo** de un solo uso permiten iniciar sesión si se pierde el teléfono. Un administrador puede quitar la 2FA de una cuenta (permiso *Restablecer las contraseñas*), y `loco-user mfa-reset NOMBRE` la quita desde la consola como último recurso. El secreto se cifra en reposo y los códigos de respaldo se guardan con hash.

## 2.53.0 — 2026-10-24

- **Tareas en segundo plano más robustas**: las evaluaciones periódicas (alertas, copia externa) solo actúan en un único proceso a la vez gracias a un arrendamiento en la base de datos, para evitar cualquier duplicado si el servidor funciona con varios workers. Recuperación automática si el proceso líder se detiene.
- **CI más rápida**: las pruebas unitarias del portal se ejecutan ahora fuera de una máquina virtual, en unos minutos, y condicionan las pruebas en VM, más largas.

## 2.52.0 — 2026-10-24

- **Selección de equipos y actualización agrupada** (*Mi parque*): marcar equipos en la lista (casilla «seleccionar todo» en la cabecera) hace aparecer una barra discreta arriba de la tabla con el número seleccionado y un botón **Actualizar seleccionados** (opción *Reiniciar si es necesario*). La actualización automática se pone en pausa durante la selección. Reservado al permiso *Actualizar un equipo*.

## 2.51.0 — 2026-10-24

- **Copia de seguridad externa** (*Sistema → Copias de seguridad*): copia automática de la última copia de seguridad diaria a un almacenamiento externo — **compatible con S3** (nube AWS/Backblaze/Wasabi/OVH/Scaleway/R2 o MinIO local), **SFTP** (clave SSH), o **carpeta / punto de montaje** (2.º disco, soporte externo, recurso SMB/NFS montado). Cifrado AES-256 opcional antes del envío, botón **Probar**, secretos cifrados en reposo. La retención sigue el ajuste «Copias de seguridad conservadas». Una alerta «Copia externa fallida» avisa en caso de problema.
- **Unidades en los umbrales de alerta** (*Alertas → Reglas*): cada umbral muestra su unidad (días, %, fallos / 24 h, actualizaciones, equipos).
- **Panel Copias de seguridad revisado**: lista más regular (acciones siempre alineadas a la derecha) y copia externa integrada en el mismo panel.

## 2.50.0 — 2026-10-23

- **Alertas y notificaciones** (*Administración → Alertas*): recibir aviso por **correo electrónico (SMTP)** o **webhook** (Slack, Teams, Discord o un relé propio, con firma HMAC) cuando el estado del servidor o del parque lo requiere. Reglas activables con umbrales — servicio detenido, certificado HTTPS que caduca, copia de seguridad atrasada, disco lleno, inicios de sesión fallidos, actualizaciones de seguridad pendientes, equipos sin contacto — y una gravedad mínima por canal. Botón **Probar**, secretos cifrados en reposo, evaluación en el servidor cada minuto sin repetición, con reenvío en caso de fallo. Nuevo permiso *Configurar alertas* e interruptor de función dedicado.

## 2.49.0 — 2026-10-23

- **Copias de seguridad rediseñadas** (página *Sistema*): lista más compacta (fecha y tamaño en una línea, nombre del archivo debajo), acciones agrupadas con un icono de eliminación, y una zona de ajustes clara para la retención y la restauración desde un archivo.
- **Mensaje de Windows Update corregido**: la advertencia «ningún equipo ha pasado todavía por loco» ya no se muestra en cuanto no ha habido peticiones recientes; solo aparece cuando la caché realmente no ha servido nada (ningún tráfico en 14 días y caché vacía).
- **README retocado**: cabecera, insignias, índice y secciones detalladas plegables, para una lectura más agradable.

## 2.48.0 — 2026-10-23

- **Eliminar una copia de seguridad** (página *Sistema › Copias de seguridad*): un botón *Eliminar* en cada copia de seguridad, con una confirmación antes de la eliminación definitiva. Lo hace el agente root. Reservado al permiso *Configurar el servidor*.
- **Mensaje de Windows Update al día**: cuando ningún equipo ha pasado todavía por la caché, el mensaje explica ahora que los equipos Windows inscritos se dirigen automáticamente a la caché (por proxy, activado por defecto) y remite a la sección «Dirigir los equipos a la caché» para los equipos no gestionados, en lugar de hablar solo de la redirección DNS.

## 2.47.0 — 2026-10-23

- **Restaurar desde un archivo subido** (página *Sistema › Copias de seguridad*): se puede enviar un archivo `.tar.gz` (una copia de seguridad guardada en otro lugar, o procedente de otro servidor) para restaurarlo. El archivo se **comprueba antes de cualquier escritura** — que sea de verdad una copia de seguridad loco/local-repo (presencia de la base de datos y de la configuración, rutas seguras, sin `..` ni enlace simbólico, `.tar.gz` legible) — en el portal y luego una segunda vez por el agente root antes de aplicarla.
- **Retención de las copias de seguridad configurable**: un campo *Copias de seguridad conservadas* (de 1 a 60) escribe `BACKUP_KEEP` y elimina en el acto las más antiguas más allá del límite. Reservado al permiso *Configurar el servidor*.

## 2.46.0 — 2026-10-23

- **Descargar y restaurar las copias de seguridad** (página *Sistema*): cada copia de seguridad tiene un botón **Descargar** (para guardarla en otro lugar) y un botón **Restaurar** con un clic. La restauración pasa por el agente root — parada del portal, vuelta a poner la base de datos, las cuentas, las claves y los repositorios, regeneración de los servicios, y luego reinicio; se conserva la configuración propia del servidor (dirección, puertos, redes), y un respaldo automático restablece el estado anterior en caso de fallo. El comando sigue disponible para restaurar en otro servidor. Reservado al permiso *Configurar el servidor*.
- **Logos de los programas en «Últimas incorporaciones»** (Vista general): cada programa añadido recientemente muestra su logo (icono real para las aplicaciones WinGet, si no una insignia con la inicial), como en el Centro de software.
- **Aviso del Diario que se puede cerrar**: la banda «N inicio(s) de sesión fallido(s) en las últimas 24 horas» tiene una cruz para cerrarla; sigue oculta mientras no se produzca ningún fallo nuevo.

## 2.45.0 — 2026-10-23

- **Encontrar sus máquinas en la búsqueda (Ctrl+K)**: la paleta de búsqueda de la barra de navegación lista ahora los **equipos del parque** además de las páginas. Se escribe un nombre de máquina, una dirección IP o un sistema, y se abre su ficha con *Intro*. Reservado a los perfiles que tienen acceso al parque (permiso *Ver el parque*).

## 2.44.0 — 2026-10-23

- **Conectar un anfitrión Proxmox VE a la caché**: nueva sección en la página *Paquetes*, junto a «Conectar un equipo Linux». Un solo comando en el anfitrión Proxmox conecta sus repositorios a loco, a elegir **sin suscripción** (gratuito, en caché) — recomendado — o **empresarial** (requiere suscripción, en directo por HTTPS). Debian y el repositorio sin suscripción pasan por la caché; `--trust-ca` para la primera conexión, `--remove` para volver atrás.
- **Los repositorios de Proxmox se sustituyen, nunca se duplican**: el script localiza el repositorio de Proxmox VE esté donde esté — archivos `.sources` (deb822, Proxmox 9) como los antiguos `.list` en `sources.list.d` y `sources.list` — lo retira y escribe un único `loco-proxmox.sources` con la variante elegida. Los repositorios de Debian y Ceph no se tocan.

## 2.43.0 — 2026-10-23

- **Tarjeta «Agente invitado QEMU» visible en la appliance**: la appliance funciona con Alpine (sin systemd), donde la detección de VM fallaba, así que el botón de instalación seguía oculto. loco reconoce ahora una VM QEMU/KVM por su fabricante DMI. La tarjeta (página *Sistema*) está ahora siempre presente, con un mensaje claro según el servidor: instalar en una VM QEMU, «reloj/virtualización gestionados por el anfitrión» en un contenedor, «no aplicable» en un servidor físico.

## 2.42.0 — 2026-10-23

- **Hora del servidor, NTP en los contenedores**: en un servidor en contenedor (LXC), la tarjeta *Sistema › Hora del servidor* indica ahora que el reloj viene del anfitrión (la sincronización NTP se configura en el anfitrión) en lugar de un interruptor sin efecto; en una VM real o un servidor físico, el ajuste del servidor NTP de origen funciona normalmente.
- **Agente invitado QEMU preinstalado en la appliance**: la ISO instala y activa ahora `qemu-guest-agent` de oficio (la appliance siempre funciona como VM Proxmox/QEMU) — el apagado limpio, las direcciones IP en Proxmox y las copias de seguridad coherentes funcionan desde la instalación. En los demás servidores, la tarjeta *Sistema › Agente invitado QEMU* sigue permitiendo instalarlo con un clic.

## 2.41.0 — 2026-10-23

- **Hora del servidor** (página *Sistema*): elegir la **zona horaria** del servidor y, si se desea, **sincronizar el reloj con un servidor NTP** de origen (el servidor pasa a ser cliente NTP, no aloja ninguno). Lo aplica el agente — `timedatectl` para la zona horaria, `systemd-timesyncd` (Debian/Ubuntu) o `chrony` (Red Hat) para el NTP.
- **Agente invitado QEMU** (página *Sistema*): cuando loco se ejecuta en una VM Proxmox/QEMU, una tarjeta muestra el estado de `qemu-guest-agent` y permite **instalarlo con un clic** (apagado limpio, direcciones IP en Proxmox, congelación de los archivos para copias de seguridad coherentes). Oculta en los contenedores y en el hardware físico.

## 2.40.0 — 2026-10-22

- **Página *Ajustes del parque* más clara**: la tarjeta de la dirección del servidor muestra la dirección actual con una insignia de confirmación («3/3 agentes la han confirmado») y pliega las instrucciones en un «¿Cómo cambiar la dirección sin perder los equipos?» en tres pasos; una dirección anunciada a la espera del cambio tiene su propio recuadro. El umbral de las insignias gana una leyenda de colores (al día · de 1 a N · N o más), y la clave de inscripción muestra una vista previa enmascarada.

## 2.39.0 — 2026-10-22

- **MSI de Windows listo para usar**: «Descargar el MSI» desde la página *Inscripción* da ahora un instalador listo — **un doble clic instala el servicio Y inscribe el equipo**, sin ningún comando, como el paquete `.deb` en Linux. El portal graba la dirección de loco, la clave de inscripción y la huella de la autoridad en el MSI al descargarlo. El comando de PowerShell y el despliegue masivo (GPO) siguen disponibles en «Otros métodos» (MSI genérico, sin inscripción).
- **Actualizaciones desde GitLab**: loco puede obtener sus versiones de un **proyecto GitLab** (incluido un GitLab interno), no solo de GitHub. En *Actualizaciones › Origen de las actualizaciones*, indique `owner/repo` (GitHub) o la URL de un proyecto GitLab. Sección revisada, más legible.
- **Contador de actualizaciones de seguridad** del parque: en lugar de un «de ellas N de seguridad» en rojo, una discreta **insignia de escudo**, en la lista del parque y en la ficha de cada equipo.

## 2.38.0 — 2026-10-22

- **Vista general más legible**: las tarjetas (cachés, Procesador, Memoria, Disco) pasan a la línea siguiente en lugar de amontonarse en una sola línea, así que se acabaron los valores cortados. La tarjeta **Memoria** muestra ahora un **porcentaje** de uso (como el Procesador) con «usada sobre total» debajo, en lugar de un número de bytes truncado.
- **El terminal de los equipos Linux como un verdadero terminal**: la sesión abre por defecto un **símbolo de sistema en color** (`usuario@equipo: ruta`) cuando el equipo no tiene ninguno, para un verdadero aspecto de terminal desde la apertura. El `.bashrc` del equipo tiene prioridad: si ya define un símbolo de sistema en color (o un `PROMPT_COMMAND`), se muestra el suyo.

## 2.37.0 — 2026-10-22

- **Procesador y memoria del servidor en la vista general**: dos tarjetas, junto a «Disco», muestran la carga del servidor en el que funciona loco — **Procesador** (porcentaje de uso, carga en 1 min y número de núcleos) y **Memoria** (usada sobre total), con un indicador (naranja y luego rojo cuando se llena). Los valores se actualizan en directo como el resto de la página. Leídos en el servidor (`/proc`); ocultos si no están disponibles.

## 2.36.0 — 2026-10-22

- **Terminal de los equipos rediseñado** (tanto el shell de Linux como PowerShell de Windows, misma visualización): una verdadera paleta ANSI de 16 colores legible, a juego con el tema elegido (OLED, oscuro, claro — fondo blanco y colores adaptados en el tema claro); el cursor y la selección toman su color de acento y se actualizan en directo cuando cambia de tema o de acento. Fuente monoespaciada moderna, interlineado más aireado y desplazamiento suave para las salidas largas, fondo a juego con la superficie (se acabó el borde alrededor del margen) y una barra de desplazamiento fina y discreta. Los botones **A− / A+** ajustan el tamaño del texto, memorizado en el navegador.

## 2.35.0 — 2026-10-22

- **Página Actualizaciones totalmente traducida**: el agente de administración (`locoadmin`) devuelve ahora un estado estructurado (códigos) que el portal pone en palabras en el idioma de cada persona — resumen, motivos de reinicio, errores. Los registros de operación (comprobación, actualización, reinicio) siguen el idioma del servidor (francés, español o inglés según su configuración regional), en inglés por defecto.

- **Direcciones (URL) de las páginas en inglés**, idénticas sea cual sea el idioma elegido, para una navegación uniforme: `/fleet`, `/accounts`, `/log`, `/system/updates`, `/enrollment`… (antes `/parc`, `/comptes`, `/journal`, `/systeme/mises-a-jour`, `/enrolement`). Los enlaces internos se adaptan automáticamente; las direcciones servidas a los equipos (repositorios, agentes, scripts de instalación) no cambian.

- **Actualización de loco más sencilla**, desde la página *Actualizaciones*: el botón de instalación de la versión publicada vale ahora para **cualquier servidor Linux** (systemd), y no solo para la appliance Alpine — el archivo se comprueba (SHA-256) y luego se instala, con vuelta automática a la versión anterior en caso de fallo. Una sección **Repositorio GitHub** permite apuntar las actualizaciones a otro repositorio «owner/repo» (por ejemplo su fork) y volver a comprobar; la elección se guarda en `loco.conf` (y se conserva de una actualización a otra). También se puede ajustar en la instalación con `--github-repo owner/repo`.

- **Selector de idioma dedicado**: un icono de globo, arriba a la derecha y en la pantalla de inicio de sesión, abre un menú *Français / English / Español* accesible desde cualquier página. La elección del idioma sale del menú *Apariencia* (paleta) para pasar a ser una acción de un solo clic.

- **Actualización desde una versión antigua**: los motivos de reinicio y los errores ya en caché siguen siendo legibles en la página Actualizaciones y en consola, hasta la siguiente comprobación del agente.


## 2.34.0 — 2026-10-21

- **Portal multilingüe (francés, inglés, español)**: cada persona elige **Français**, **English** o **Español** en el menú *Apariencia* (icono de paleta, arriba a la derecha, también en la pantalla de inicio de sesión); la elección se guarda en su cuenta. Sin elección, el portal sigue el idioma del navegador. Todo está traducido: páginas, ventanas, confirmaciones, mensajes tras una acción y errores, etiquetas (permisos, diario, estados, recurrencias, perfiles integrados), gestor de archivos, consola remota, búsqueda Ctrl+K. Las fechas, los tamaños (Go/GB, coma o punto decimal) y los números siguen el idioma; las recurrencias de las programaciones se redactan en el orden de cada idioma («Every Tuesday and Thursday at 02:00», «Cada martes y jueves a las 02:00»).
- **Scripts de los equipos multilingües**: los comandos servidos a los equipos (configuración APT `setup.sh`, RPM `rpm-setup.sh`, Flatpak, instalación de programas Windows `install.ps1`, zonas DNS, agentes Linux y Windows) escriben sus mensajes en el idioma del equipo — francés, español o inglés según el sistema (`LANG` en Linux, idioma de visualización de Windows). El servidor coloca las tres versiones en el script; el comando mostrado en el portal no cambia.
- **Agentes multilingües**: el agente Linux (`loco-client`) y el agente Windows (servicio, información emergente y ventana del acompañante de escritorio, consola y archivos remotos) se expresan en el idioma del equipo (francés, español o inglés): estado mostrado, registro, mensajes devueltos al portal (resultados de las acciones), mensajes de reinicio.
- **Servidor en tres idiomas**: el instalador (`install.sh`, `remove.sh`), la consola de la appliance (`setup-loco`, instalador de la ISO, banner de conexión) y las herramientas de administración (`loco-user`, `loco-fleet`, `loco-backup`…) siguen el idioma del servidor (`LANG`).
- El francés sigue siendo el idioma de referencia: los textos del código sirven de claves, el inglés está en `portal/i18n/en.json` y el español en `portal/i18n/es.json`. Una prueba rechaza cualquier texto mostrado sin su traducción al inglés **y** al español, con los mismos valores que insertar.

## 2.33.0 — 2026-10-21

- **Paquetes Linux más claros**: la página tiene dos partes. **Conectar un equipo Linux**, en tres pasos válidos para todas las familias: el sistema del equipo (Debian, Ubuntu o Rocky, AlmaLinux, RHEL), lo que loco le proporciona (caché y sus repositorios, solo sus repositorios, solo caché, o retirar loco), con las opciones EPEL y «el equipo todavía no tiene la autoridad de loco» como casillas que marcar; el comando que copiar se compone solo. Luego **Sus repositorios**: la barra de las versiones, la adición de paquetes al repositorio mostrado y la lista. Arriba, los tamaños de las dos cachés, sus paquetes y los equipos Linux del parque. Los casos particulares (distribuciones derivadas, `wget`, claves, réplicas) se reúnen.
- **Scripts**: contadores (biblioteca, equipos en espera, ejecuciones con fallos); biblioteca filtrable (búsqueda, Windows / Linux); historial de las 100 últimas ejecuciones con filtros (en curso, con fallos, correctas) y estado de cada ejecución. **Vaciar la cola** cancela todos los envíos todavía no recibidos por los equipos (programados incluidos), **Vaciarlo todo** elimina el historial de las ejecuciones terminadas (las que están en curso se conservan), y cada ejecución terminada se retira del historial (también desde su página, que propone además «Volver a lanzar…»). El Diario guarda constancia de estas limpiezas.

## 2.32.0 — 2026-10-21

- **Añadir / modificar un programa Windows**: ventana reorganizada en pasos (instalador, y luego nombre y versión), el tipo se **detecta** a partir del enlace o del archivo, y los ajustes poco útiles (tipo, argumentos silenciosos, nombre en Windows) se pliegan en «Opciones de instalación», con su resumen. Para un `.exe` sin argumentos silenciosos, las opciones se abren solas con una advertencia (si no, la instalación espera un clic en cada equipo).
- **Todas las ventanas**: el título y los botones siguen visibles, solo se desplaza el contenido; cierre con una cruz; un solo anillo de foco en los campos (se acabó el doble contorno).
- **Paquetes Linux: APT y RPM en una sola página** (*Linux › Paquetes*, que sustituye a «Paquetes APT» y «Paquetes RPM»): una barra reúne todos los repositorios, agrupados por familia (Debian, Ubuntu · Red Hat), con su número de paquetes; un clic cambia de versión en lugar de una lista y un botón «Mostrar». El resto sigue a la familia elegida: caché (APT o réplicas de Red Hat), comando de conexión (`apt` o `dnf`, opción EPEL), adición de `.deb` o de `.rpm`. «+ Nuevo repositorio» crea un repositorio Debian, Ubuntu o Red Hat desde la misma ventana. Las direcciones `/linux` y `/rpm`, los scripts de los equipos y los permisos separados APT / RPM no cambian.
- Lista de los paquetes a todo el ancho, con la **descripción** de cada paquete, su fecha de adición y acciones como iconos (descargar, copiar a otro repositorio, eliminar); número de **equipos del parque** en la versión mostrada; estado vacío explicativo; opciones de conexión y réplicas reunidas.

## 2.31.0 — 2026-10-21

Una interfaz más moderna y más rápida de recorrer, en los tres temas:

- **Profundidad**: la página tiene un fondo ligeramente teñido y el contenido descansa sobre tarjetas blancas (o antracita) con esquinas más redondeadas y una sombra suave; los indicadores pasan a ser tarjetas separadas. Títulos, márgenes y tablas más aireados.
- **Navegación**: el elemento activo de la cabecera es una pastilla (su icono toma el color de acento); los submenús (Mi parque, Administración, Programas) forman una barra donde la página en curso destaca como una tarjeta; pestañas de las fichas en pastillas; filtros en pastillas redondeadas, el filtro activo relleno.
- **Ir a una página (Ctrl+K)**: botón de lupa en la cabecera; una ventana de búsqueda lista todas las páginas accesibles (incluidas Grupos, Despliegues, Scripts, Programaciones, Centro de software…) y se abre escribiendo unas letras, flechas e Intro. **«/»** coloca el cursor en la búsqueda de la página (parque, catálogo, diario…).
- **Filas pulsables**: en las listas del parque, de los scripts, de los despliegues y de los grupos, un clic en cualquier parte de la fila abre el elemento (Ctrl+clic: nueva pestaña); los botones, casillas y enlaces de la fila conservan su acción.
- Botones, campos y menús algo más grandes y redondeados, ventanas de diálogo con fondo difuminado, pantalla de inicio de sesión sobre un degradado discreto.

## 2.30.0 — 2026-10-21

- **Actualizaciones del sistema del servidor desde el portal**, en todas las instalaciones Linux (Debian, Ubuntu, Rocky Linux, y la appliance Alpine, que ya lo tenía): *Administración → Actualizaciones* lista los paquetes pendientes (núcleo, OpenSSL, Squid, nginx, Python, glibc…), con los **parches de seguridad primero** (repositorio `-security` de Debian/Ubuntu, avisos de seguridad DNF con su gravedad en Rocky) y los paquetes importantes señalados («núcleo», «servicio loco», «base»). Filtros y búsqueda en la lista.
- **Instalar solo los parches de seguridad** o **actualizarlo todo**; los archivos de configuración colocados por loco se conservan, y los servicios de loco que todavía usan bibliotecas antiguas (portal, nginx, Squid, apt-cacher-ng) se reinician automáticamente. El registro de cada operación se muestra en directo.
- **Reinicio**: la página indica por qué es necesario (nuevo núcleo instalado, petición de un paquete, bibliotecas sustituidas que el sistema todavía tiene cargadas) y los servicios que usan bibliotecas antiguas; botón *Reiniciar el servidor*. En un contenedor LXC, el núcleo se señala como gestionado por el anfitrión.
- Comprobación automática cada día y en el reinicio. En consola: `loco-update status|check|security|system|reboot`. Las operaciones pasan por un agente root separado (`loco-agent`, servicio systemd instalado ahora en todas partes) que vuelve a validar cada petición: el portal sigue sin privilegios.

## 2.29.2 — 2026-10-21

Pasada de seguridad y de fiabilidad:

- **Proxy de Windows Update nunca enviado sin Squid**: en una instalación `--no-wu` o en un servidor loco con Windows (sin proxy-caché), la función «Proxy de Windows Update», activa por defecto desde la 2.26, seguía enviando el proxy a los equipos Windows, cuyas actualizaciones fallaban. El portal sabe ahora si Squid está instalado; si no, la función se desactiva, la página *Windows Update* lo indica y ya no se propone el comando manual.
- **Agente Windows: el proxy se retira de verdad cuando loco ya no responde** (anunciado en la 2.25 pero ausente): tras tres contactos fallidos seguidos (unos 6 min), o si loco rechaza el equipo, se retira el proxy WinHTTP puesto por loco y Windows Update vuelve al acceso directo; se vuelve a poner en el primer contacto correcto.
- **Squid reforzado**: el proxy se niega a retransmitir hacia el propio servidor (127.0.0.0/8, ::1) y hacia el enlace local (169.254.0.0/16, fe80::/10), para que un equipo no pueda llegar de rebote a los servicios internos de loco.
- **Gestor de archivos**: los campos devueltos por el agente (tipo, permisos, fechas) se validan y se escapan antes de mostrarse, para que un equipo comprometido no pueda inyectar nada en la página (ya lo bloqueaba la política de seguridad del contenido, ahora también en el origen).
- **Programaciones**: la persona que modifica o reanuda una programación pasa a ser su responsable (sus permisos se vuelven a comprobar al enviarla a los equipos, su nombre figura en el diario), en lugar del autor original.

## 2.29.1 — 2026-10-21

- Gestor de archivos: la ruta de navegación ya no duplica la «/» de la raíz («/ root» en lugar de «/ / root»), y una carpeta que solo contiene archivos ocultos ya no se anuncia como «vacía»: se indica el número de elementos ocultos, con un enlace para mostrarlos.

## 2.29.0 — 2026-10-21

- **Programaciones rediseñadas, y recurrentes**: nueva pestaña *Mi parque → Programaciones* (lista con acción, destinos, recurrencia en lenguaje claro, próxima y última ejecución, estado; modificar, poner en pausa, reanudar, eliminar). Las actualizaciones y los scripts se programan **una vez, cada día, cada semana** (días a elegir) **o cada mes** (día fijo o último día del mes), en todo el parque, en grupos (miembros resueltos en cada ejecución) o en equipos. Un componente «Cuándo» común (elección segmentada, días en pastillas, resumen en lenguaje claro con la próxima ejecución) sustituye a los campos de fecha/hora. En contexto: botón de calendario junto a *Actualizar* en la ficha de un equipo y *Programar…* junto a *Actualizar todo* (ventana de programación), opción «Ahora / Programar» al ejecutar un script; la ficha de un equipo muestra las programaciones que le afectan.
- Una programación vencida se convierte en una acción en el próximo contacto de los agentes; las ocurrencias perdidas no se acumulan. Un script modificado desde su programación la pone en pausa (se reanuda con la versión actual, a petición). Los permisos de la acción programada se exigen al crearla y se vuelven a comprobar al enviarla. Las programaciones puntuales de la 2.24 (campo de fecha/hora) se sustituyen por este sistema.

## 2.28.0 — 2026-10-20

- **Iconos de ojo / ojo tachado** para mostrar u ocultar: en todos los campos de contraseña (inicio de sesión, primera contraseña, cuenta, directorios), y en lugar de la casilla «Archivos ocultos» del gestor de archivos.
- **Windows Update, últimas peticiones**: solo se nombran los equipos Windows (ya no aparece un equipo Debian que comparta la dirección). Detrás de un NAT, cuando varios equipos Windows tienen la misma dirección vista por loco, se nombran todos («pc-a o pc-b») con la mención «dirección compartida (NAT)», en lugar de elegir uno al azar.
- Gestor de archivos: la barra de herramientas ya no toca los bordes del panel.

## 2.27.0 — 2026-10-20

- **Nuevo logo de la aplicación** (edificio, mano y ratón): vectorial (SVG), fondo transparente, toma el color del tema (claro, oscuro suave, OLED) y sigue el color de acento elegido. Sustituye al cuadrado en la barra de navegación y aparece en grande en las pantallas de inicio de sesión y de primera contraseña. El icono de la pestaña del navegador usa el mismo logo y se adapta al modo claro u oscuro del navegador.

## 2.26.0 — 2026-10-20

- **Proxy de Windows Update activado por defecto**, y ajustable en *Administración › Funciones* («Proxy de Windows Update (sin DNS)»). Los equipos Windows gestionados se dirigen a la caché a través de su proxy WinHTTP desde la instalación, sin ninguna redirección DNS; desactivar la función hace que los agentes retiren el proxy. La página *Windows Update* muestra el estado en lugar de un interruptor dedicado.
- **Gestor de archivos**: corrección del botón vacío («Pegar») que aparecía en la barra, y arranque en la **raíz del disco del sistema (`C:\`)** en Windows (siempre `/root` en Linux, ya que el agente actúa como root/SYSTEM con todos los permisos).

## 2.25.0 — 2026-10-20

- **Caché de Windows Update por proxy, sin DNS**: loco incluye un proxy-caché (Squid) al que se llega mediante el ajuste de **proxy WinHTTP** de los equipos Windows. Nueva alternativa a la redirección DNS: en la página *Windows Update*, un interruptor «Dirigir los equipos gestionados» hace que el agente loco ajuste el proxy WinHTTP de cada equipo Windows hacia loco (por equipo, reversible, el agente lo retira si ya no consigue llegar a loco o al retirar el agente); ninguna zona DNS que editar. El proxy guarda en caché los mismos archivos inmutables (HTTP) y retransmite el HTTPS sin descifrarlo — la misma cobertura que la vía DNS. El comando manual (`netsh winhttp set proxy …`) también se propone en las páginas *Windows Update* e *Inscripción*. El servicio se instala con la caché de Windows Update (opción `--wu-proxy-port`, 3128 por defecto) en Debian/Ubuntu, Rocky/AlmaLinux, Alpine, la ISO y el contenedor, y aparece en la página *Sistema*. Validado en real: el Windows Update de un equipo pasa por el proxy (búsqueda y descarga), archivo WU guardado en caché (MISS y luego HIT), e instalación completa de loco con Squid activo.

## 2.24.0 — 2026-10-20

- **Programación puntual de las actualizaciones y de los scripts**: elegir una fecha y una hora (hora del servidor) en lugar de «en el próximo contacto». La orden sigue en espera y solo se transmite al agente a partir del momento elegido. Disponible para *Actualizar* (un equipo desde su ficha, mediante «Programar una actualización»), *Actualizar todo* (parque o grupo) y la ejecución de un script (página de ejecución). Una acción programada en espera puede **cancelarse** antes de transmitirse (botón *Cancelar* en la ficha del equipo, *Cancelar los equipos en espera* para un script). La ficha del equipo y el seguimiento de la ejecución muestran el momento programado. Campo dejado vacío = comportamiento actual (desde el próximo contacto).

## 2.23.0 — 2026-10-20

- **Gestor de archivos del equipo** (pestaña *Archivos* de la ficha de un dispositivo): explorar el árbol, subir (botón o arrastrar y soltar), descargar, crear una carpeta, renombrar, copiar, mover, eliminar, modificar un archivo de texto en el editor integrado, y ajustar los permisos (octal) en Linux. Ruta de navegación, atajos a la raíz y a la carpeta personal, visualización opcional de los archivos ocultos y avance por transferencia. Como los terminales y el escritorio, todo pasa por el relé HTTPS — el agente ejecuta cada operación como root (SYSTEM en Windows) y el servidor nunca se conecta al equipo; ninguna dirección ni ningún token de equipo llega al navegador. Nuevo permiso *Gestionar los archivos del equipo* (reservado al administrador por defecto), agente 2.23 como mínimo; la actualización automática de la ficha se suspende mientras la pestaña está abierta, para no interrumpir una transferencia o una edición.

## 2.22.0 — 2026-10-20

- **Paquete «agente y repositorios»** (página Inscripción, Debian/Ubuntu y Rocky/AlmaLinux/RHEL): el mismo paquete `loco-client` confía la autoridad de loco al sistema, conecta el equipo a la caché y al repositorio local de su versión — solo la caché si ese repositorio todavía no existe — y luego instala el agente. Es ahora la opción recomendada; «solo agente» se sigue ofreciendo. Su versión está un nivel por encima (`+depots`, `1.depots`): pasar del agente solo al paquete completo es una actualización que conserva la inscripción del equipo, y desinstalar el paquete completo retira también la configuración de los repositorios (repositorios del sistema restablecidos en Red Hat). Los scripts de repositorios ganan la opción `--from-package` (ninguna llamada a APT ni a DNF, ocupados con la instalación). Validado con instalaciones, actualizaciones y desinstalaciones reales en Debian 13 y Rocky Linux 9.
- **Paquetes del agente servidos también en HTTP** (puerto de arranque), como el script: los comandos «autoridad todavía no importada» de la página Inscripción funcionan.
- **Agente Red Hat**: el inventario (`dnf check-update`, `updateinfo`) acepta importar la clave de un repositorio firmado añadido después; sin terminal, dnf se negaba y señalaba erróneamente una «firma incorrecta» hasta que alguien lanzaba dnf a mano.
- **Cuentas: origen del último inicio de sesión** — bajo la fecha, la dirección desde la que la cuenta inició sesión por última vez (vista por loco), y un enlace al historial de sus inicios de sesión en el Diario. La dirección se registra en cada inicio de sesión; para las cuentas existentes, se toma del diario (`loco-user list` también la muestra). La lista de cuentas pasa a la línea siguiente en lugar de recortar las acciones.
- **Página Sistema rediseñada**: balance arriba (servicios activos, días de validez del certificado, disco, última copia de seguridad con alerta si falta la copia diaria, versión), servicio detenido y certificado ausente señalados arriba de la página; paneles emparejados (servicios y copias de seguridad, certificado y autoridad, ubicaciones y versiones); botón de descarga de la autoridad y comandos de importación por sistema, Rocky / AlmaLinux / RHEL incluidos.

## 2.21.0 — 2026-10-20

- **Página Inscripción** (barra de navegación): todo lo que vincula un equipo a loco, una pestaña por sistema (Windows; Debian, Ubuntu; Rocky, AlmaLinux, RHEL), en pasos numerados — instalar el agente, configurar los repositorios y las cachés (APT, DNF, Flatpak), Windows Update y los programas del catálogo en Windows. Sustituye al bloque «Conectar un equipo» de la vista general y a la ventana «Añadir un equipo» de Mi parque (cuyo botón lleva ahora a la página). Cada sección sigue los permisos y las funciones de la cuenta; la clave de inscripción sigue reservada al permiso de inscripción.
- **Paquetes .deb y .rpm del agente**, producidos por loco para este servidor (dirección, autoridad fijada y clave de inscripción incluidas): `sudo apt install ./loco-client_<version>_all.deb` o `sudo dnf install ./loco-client-<version>-1.noarch.rpm` inscribe el equipo de inmediato, y desinstalar el paquete retira el agente. El paquete solo contiene el script de instalación (después el agente se actualiza solo). Validados con dpkg-deb, rpm 4.14 y 4.16 (huellas de cabecera y de carga útil), y luego con una instalación y una desinstalación reales en Debian 13 y Rocky Linux 9.

## 2.20.1 — 2026-10-20

- **Disponibilidad y actualizaciones diferenciadas en Mi parque**: junto al nombre del equipo, un indicador verde muestra un contacto del agente en los seis últimos minutos (tres ciclos habituales) y uno rojo la ausencia de contacto reciente. El indicador del volumen de actualizaciones está ahora en la columna *Actualizaciones*. La ficha retoma la disponibilidad junto al nombre y el balance de las actualizaciones junto a los contadores. El paso a desconectado lo tiene en cuenta la actualización automática, incluso sin nueva actividad del equipo.
- Los intercambios del relé remoto y los resultados de acciones también mantienen el equipo en línea durante una operación larga. La marca de tiempo se registra como mucho una vez por ciclo de contacto para evitar una escritura en cada imagen noVNC.

## 2.20.0 — 2026-10-20

- **La red en directo en la vista general**: caudales de entrada y de salida del servidor, leídos cada 2 segundos en la interfaz de la ruta por defecto (en su defecto, las interfaces físicas), con el pico y la curva de los 2 últimos minutos (escala común para los dos sentidos, valor de un instante al pasar el ratón). El caudal se calcula en el navegador entre dos lecturas de los contadores: no se mide ni se conserva nada cuando la página no está mostrada. Linux (`/proc/net/dev`) y servidor Windows nativo (contadores de 64 bits de la API `GetIfEntry2`, comprobados en la CI contra `Get-NetAdapterStatistics`).
- **Iconos de vuelta en la barra de navegación**.
- Capturas del README actualizadas.

## 2.19.0 — 2026-10-20

- **Interfaz rediseñada, sobria y coherente**: un verdadero sistema de diseño sustituye a los añadidos sucesivos — escala tipográfica más ajustada (de 12 a 24 px), espaciados en una cuadrícula de 4 px, radios de 6 px (mandos) y 8 px (paneles), una sola sombra para los elementos flotantes. Fuente monoespaciada reservada al código (comandos, rutas, direcciones, versiones, huellas): los nombres de equipos, las fechas, las cuentas, los contadores y los valores de los indicadores pasan a la fuente normal. Etiquetas de los indicadores en minúsculas en lugar de mayúsculas espaciadas, enlaces no subrayados fuera del texto corriente, navegación principal sin iconos en pantalla grande, cabecera translúcida.
- **Vista general**: estado del parque en barra segmentada con la lista de los estados (en lugar del anillo), ocupación del disco en filas con barras de un solo tono (en lugar de la barra arcoíris), gráfico de Windows Update en dos tonos de un mismo azul (paleta comprobada para el contraste y el daltonismo). «Actividad reciente», que en realidad listaba las incorporaciones al catálogo, pasa a ser «Últimas incorporaciones».
- **Estados nombrados igual en todas partes**: Al día, Actualizaciones en espera, Por tratar, Estado desconocido — en la vista general, Mi parque, la ficha de un equipo y los grupos.
- **Ergonomía**: lista del parque en tarjetas en el móvil (se acabó el desplazamiento horizontal); opción «Reiniciar si es necesario» presentada como un botón; filtros y búsqueda de los paquetes en una sola barra; zonas de depósito de archivos en francés con un botón «Examinar…» en lugar del campo nativo; chevrones discretos en lugar de los triángulos nativos; confirmación como botón principal, en rojo solo para las acciones destructivas (eliminar, retirar, reiniciar…); filtros seleccionados marcados sin relleno de acento; insignias de estado teñidas; cabeceras de columnas numéricas alineadas.

## 2.18.0 — 2026-10-20

- **Paquetes instalados de los equipos Linux** (*Mi parque › ficha del equipo*): el agente Linux envía ahora los paquetes instalados (nombre, versión, arquitectura, tamaño), las aplicaciones Flatpak y las características del equipo (procesador, memoria, disco, núcleo, arquitectura, virtualización, arranque). Una pestaña *Paquetes instalados* los lista, con una búsqueda y filtros (todos, instalados voluntariamente, por actualizar — se muestra entonces la versión candidata enfrente); una pestaña *Flatpak* y un bloque *Características* en *Detalles* completan la ficha. El inventario detallado se guarda en una tabla aparte, para que la lista del parque siga siendo ligera. Debian/Ubuntu (`dpkg-query`/`apt-mark`) y Red Hat (`rpm`/`dnf repoquery`); leído al ritmo habitual del inventario y rehecho una vez tras la actualización del agente.
- **Los despliegues viven en Mi parque**: un despliegue es una acción sobre los equipos (destinos, avance equipo por equipo), como los scripts. La pestaña *Despliegues* de Mi parque se queda ahora en Mi parque (submenú del parque, entrada «Mi parque» activa) en lugar de pasar a Windows. *Windows › Programas* conserva el catálogo local, el centro de software y los grupos de programas; sus botones *Desplegar* abren la preparación en Mi parque. Nuevas direcciones `/parc/deploiements…`; las antiguas (`/windows-apps/deploiements…`, `/windows-apps/deployer`) redirigen, parámetros incluidos.
- La entrada «Mi parque» también está activa en las páginas Scripts.

## 2.17.1 — 2026-10-20

- **Resolución del equipo siempre restablecida al cerrar la consola** (agente Windows): en la 2.17.0, cuando el navegador cerraba la sesión mientras una petición de imagen esperaba un cambio de pantalla, el acompañante de escritorio no veía el cierre del canal; se le detenía a la fuerza al cabo de 5 segundos, sin restablecer la resolución original (el equipo se quedaba por ejemplo en 848 × 480). El canal cerrado se detecta ahora en el acto: el acompañante termina limpiamente y devuelve la resolución inicial. Comprobado en una serie de sesiones con cambios de resolución, pantalla completa y zoom.

## 2.17.0 — 2026-10-20

- **Consola noVNC más reactiva** (agente Windows 2.17): la pantalla se lee mediante **duplicación DXGI** — solo cuando cambia, con las zonas modificadas — en lugar de una captura GDI completa en cada imagen (≈ 180 ms en una máquina virtual). El acompañante retiene la petición de imagen mientras nada se mueve y responde en cuanto hay un cambio, sin retrasar la entrada; se acabaron los intercambios y los cálculos en un escritorio inmóvil (≈ 1 % de un núcleo frente a ≈ 35 %). GDI toma el relevo en el escritorio seguro, con varias pantallas o con un controlador sin duplicación, y la duplicación se vuelve a abrir en seguida tras un cambio de modo. Búferes reutilizados, como máximo ~30 imágenes por segundo. Medido en la VM de demostración: entrada → imagen modificada de 257 a ~135 ms (peor caso 641 → ~250 ms), imagen completa de 310 a ~85 ms.
- **Relé más corto**: el agente vuelve a salir en cuanto una imagen está lista (en un solo intercambio) y, con la sesión abierta, vuelve a consultar cada 12 ms (ida y vuelta pura del relé 48 → 33 ms); las transacciones del relé, efímeras, ya no fuerzan una escritura en disco en cada petición (coste por petición 5,4 → 2,1 ms), mientras el resto de loco conserva toda su durabilidad; decodificación base64 más rápida en el navegador, consultas espaciadas tras un segundo de calma.
- **Se acabaron las barras de desplazamiento en resolución adaptada** (incluso al salir de la pantalla completa): la imagen siempre se ajusta a la ventana, y el zoom agranda en ella la interfaz del equipo sin desplazamiento. En la resolución del equipo, el zoom agranda la imagen, con barras de desplazamiento finas. Ajustes de visualización memorizados por navegador; «Ajustar» por defecto.
- **Un solo cursor, nítido**: en las máquinas cuyo controlador no tiene cursor por hardware (VM, adaptador básico), Windows dibuja el cursor en la imagen; el agente lo reconoce y el navegador ya solo muestra un punto de referencia en lugar de una segunda flecha desplazada. El cursor sigue la escala del escritorio mostrado (zoom, ajuste).
- **Consola rediseñada**: insignia de estado con la latencia hacia el servidor (repetida en el carril en pantalla completa), superposición de conexión y luego de fin de sesión con «Nueva sesión» en un clic, botones con iconos, teclas mantenidas al estilo de teclas de teclado, etiquetas reducidas a iconos en una ventana estrecha. En pantalla completa, un clic en el escritorio pliega el carril de mandos.
- **Portapapeles**: opción de envío automático del portapapeles local al equipo en cada clic en el escritorio (permiso del navegador pedido una vez).
- Una parada anómala del acompañante de escritorio queda registrada en el registro del agente; una captura fallida vuelve a GDI en lugar de cerrar la sesión.

## 2.16.0 — 2026-10-19

- **Consola noVNC — pantalla completa rediseñada (escritorio)**: la imagen del equipo ocupa **toda la pantalla**, sin cabecera. Los mandos (resolución, zoom, Ctrl+Alt+Supr, atajos, teclas mantenidas, envío de texto, portapapeles, nueva pestaña, cierre) pasan a un **carril lateral izquierdo replegable**; un tirador en el borde de la pantalla lo despliega o lo pliega.
- **Cursor del equipo corregido en pantalla completa**: el cursor de software de respaldo de noVNC (usado sobre todo en los equipos táctiles) se dibujaba fuera del elemento de pantalla completa — por tanto **invisible en pantalla completa** y **desplazado al volver**. Ahora se coloca dentro de la consola mientras dura la pantalla completa, y luego se restablece: cursor visible y alineado en los dos casos.
- La pantalla completa la controla una clase colocada al cambiar de estado (independiente del motor); la pantalla completa del terminal no cambia.

## 2.15.0 — 2026-10-19

- **Consola noVNC — visualización rediseñada**: dos menús independientes al estilo RDP. *Resolución* (Ajustada a la ventana, que cambia la resolución de Windows para seguir a la ventana en píxeles físicos, o Resolución del equipo, que conserva y restablece la original) y *Zoom* (Ajustar, o 50/75/100/125/150/200 % con desplazamiento; el puntero sigue siendo preciso con cualquier zoom porque se controla la escala propia de noVNC). La resolución y el porcentaje obtenidos siguen mostrados. El escritorio se centra, se desplaza en los dos sentidos y llena limpiamente la ventana sin bandas negras inútiles.
- **Compresión JPEG adaptativa del escritorio** (agente Windows 2.15): las zonas complejas (fotos, degradados, fondos de pantalla) se envían en JPEG (subcodificación Tight estándar, decodificada por noVNC), mientras que el texto, los colores planos y los iconos siguen **sin pérdida** (Tight zlib). Mucho menos ancho de banda y un escritorio más fluido, sin pérdida visible en la interfaz. Vuelta automática al zlib sin pérdida si el codificador no está disponible.
- **Portapapeles de texto compartido** (agente Windows 2.15): panel *Portapapeles* de la consola. El texto copiado en el equipo se muestra en el navegador (y, si este lo permite, se copia en el portapapeles local); *Enviar al equipo* coloca un texto en el portapapeles del equipo para pegarlo allí (Ctrl+V). Acentos conservados (Latin-1), finales de línea normalizados, tamaño limitado. Solo en el escritorio del usuario, nunca en el escritorio seguro; loco no conserva nada.

## 2.14.0 — 2026-10-19

- **Directorios LDAP y RADIUS en lugar de OpenID Connect** (*Administración › Directorios*): inicio de sesión desde el formulario habitual de loco con una cuenta **LDAP** (Active Directory, OpenLDAP, 389 DS, FreeIPA; LDAPS o StartTLS obligatorio, autoridad PEM opcional, búsqueda mediante cuenta de servicio o vinculación directa, filtros escapados según la RFC 4515, contraseña vacía rechazada) o **RADIUS** (PAP, secreto compartido, atributo Message-Authenticator enviado y exigido en las respuestas contra BlastRADIUS). Directorios ordenados, botón **Probar** sin crear ninguna cuenta, secretos cifrados y nunca vueltos a mostrar. Cada identidad nueva espera una **aprobación manual** en *Cuentas*; ningún emparejamiento con una cuenta local. Modificar o desactivar un directorio cierra las sesiones de sus cuentas, eliminarlo las desactiva. Función que se puede desactivar. Las cuentas locales siguen siempre disponibles.
- **OpenID Connect retirado**: los proveedores existentes se desactivan en la actualización y sus transacciones se borran; las cuentas afectadas se conservan sin acceso y se señalan en *Cuentas*, donde **Contraseña local** las convierte en cuentas locales. Se suprime la biblioteca PyJWT incluida. Permisos renombrados: «Gestionar los directorios», «Aprobar las cuentas de directorio».
- **Centro de software**: la «Tienda WinGet» pasa a llamarse «Centro de software» en toda la interfaz (direcciones y permisos sin cambios).
- **Perfil «Operador»**: el perfil integrado «Técnico del parque» cambia de nombre, también en las instalaciones existentes (salvo si un perfil personalizado ya tiene ese nombre).
- **Apariencia**: menú en la cabecera y pestaña de *Mi cuenta*; nuevo tema **Oscuro suave** (antracita) junto a OLED y Claro, y **color de acento** a elegir (neutro, azul, verde, violeta, naranja, rosa) para los botones principales, las selecciones, las pestañas, el foco y la navegación activa. Textos secundarios con más contraste (WCAG AA) en claro y en OLED.
- **Scripts** (*Mi parque › Scripts*): biblioteca de scripts PowerShell (Windows) y shell (Linux) con descripción, versiones (una nueva en cada cambio de contenido) y autor; ejecución en equipos o grupos de equipos (solo se conservan los equipos compatibles y activos), contenido fijado y comprobado por su huella SHA-256 en el equipo, ejecución como SYSTEM o root con un plazo de 30 minutos; seguimiento por ejecución (correctos, en espera, fallos, salida estándar y errores, código de retorno) y cancelación de los equipos en espera. Permisos distintos: consultar, gestionar, ejecutar (sensible, reservado por defecto a los administradores y vuelto a comprobar antes del envío al agente). La versión revisada debe ser la ejecutada. Función que se puede desactivar. Se requieren agentes Windows y Linux 2.14.0.
- **Avance de los despliegues en directo**: el agente Windows envía la etapa en curso de cada instalación (descarga del instalador con porcentaje, comprobación de la huella, instalación, programa n/total; actualizaciones de Windows para la acción Actualizar), como mucho una vez por segundo. La página del despliegue muestra para cada equipo una barra de progreso y la etapa («Descarga de 7-Zip · 45 % · programa 1/3»), actualizadas cada 3 segundos; la ficha del equipo retoma la etapa en sus acciones en espera. Las etapas no válidas se ignoran; los agentes antiguos conservan la visualización por estados.
- **Funciones activables** (*Administración › Funciones*, reservado a la cuenta local «admin» de la instalación, incluso para los demás administradores): Paquetes APT, Paquetes RPM, Flatpak, Caché de Windows Update, Mi parque y agentes, Programas Windows, Acceso remoto, Scripts, Directorios. Una función desactivada desaparece de la navegación, de los botones y de la vista general; sus páginas y su API se rechazan con un mensaje explícito, sus permisos dejan de tener efecto, los agentes reciben «vuelva a intentarlo más tarde» (503) cuando el parque está desactivado y las instalaciones esperan a que se reactiven los programas Windows. Se aplican las dependencias (el acceso remoto sigue al parque). En la appliance, las cachés de Windows Update, RPM y Flatpak también se detienen o se vuelven a lanzar; en los demás casos, los servicios de red siguen arrancados (indicado en la página). Cada cambio queda registrado y los datos se conservan.
- **Panel de mandos de la consola noVNC**: Ctrl+Alt+Supr, atajos (menú Inicio, Ejecutar, Explorador, Mostrar el escritorio, Bloquear, Administrador de tareas, Alt+Tab, Alt+F4, Esc, Impr Pant, menú contextual), teclas Ctrl/Alt/Mayús/Win mantenidas hasta el siguiente clic, y **Enviar texto** escrito carácter a carácter sea cual sea la distribución del teclado del equipo (contraseñas, comandos; no se conserva nada).
- **Ctrl+Alt+Supr real en Windows**: Windows solo acepta la secuencia segura desde un servicio. El acompañante de escritorio se la señala al servicio loco-client mediante un evento reservado a SYSTEM; el servicio la envía (SendSAS) solo si hay una sesión de escritorio remoto abierta, autorizando la directiva SoftwareSASGeneration durante la llamada y restableciendo luego su valor. La pantalla «Seguridad de Windows» se muestra y se controla en la consola. Se añaden al agente Windows las teclas Impr Pant, Menú y Pausa.
- **Mi cuenta rediseñada**: cabecera con inicial, perfil, tipo de cuenta y fechas; pestañas Seguridad (reglas de la contraseña comprobadas en directo, botón Mostrar), Apariencia (vista previa de los temas), Mis permisos (agrupados por ámbito con su número) y Actividad reciente (las diez últimas acciones de la cuenta, enlace a su diario). Un enlace directo a una pestaña (`/compte#apparence`) también funciona sin recargar la página.

## 2.13.4 — 2026-10-19

- Enlace directo del MSI del agente: si el servidor todavía no puede proporcionar el MSI de su versión (GitHub momentáneamente inaccesible…), responde 503 con el motivo y «Vuelva a intentarlo en un momento», y lo anota en su registro, en lugar de un 404 sin explicación.
- El MSI obtenido de la publicación se escribe con un nombre temporal único antes de colocarse: dos descargas simultáneas ya no pueden estorbarse.

## 2.13.3 — 2026-10-19

- **MSI del agente Windows en descarga directa** en la ventana *Añadir un equipo*, justo después de los comandos: botón «Descargar el MSI» (versión y huella SHA-256 mostradas), enlaces directos que abrir desde el navegador del equipo (HTTPS y, si está activado, HTTP sin aviso de certificado), y luego el comando `msiexec` listo para copiar, que instala e inscribe el equipo con la dirección de loco, la clave de inscripción y la huella de la autoridad. Un simple doble clic en el MSI instala el servicio sin inscribirlo: se indica.

## 2.13.2 — 2026-10-19

- **El escritorio remoto llena la ventana**: en modo automático, el agente Windows elige entre las resoluciones del controlador la que mejor llena la zona de visualización (relación de aspecto conservada), y luego la más cercana al 100 %. Una ventana muy ancha obtiene por ejemplo 2560 × 1080 (21:9) en lugar de 1280 × 720 con grandes bandas negras.
- El tamaño pedido se calcula en píxeles de la pantalla y no en píxeles CSS: con una visualización a escala (150 % en un portátil, por ejemplo), el escritorio ya no se reduce en la misma proporción, y un píxel del escritorio corresponde a un píxel mostrado. El porcentaje indicado junto a la resolución sigue la misma regla (100 % = nítido).

## 2.13.1 — 2026-10-19

- **Resolución dinámica de Windows corregida**: el modo automático cambia realmente la resolución del escritorio de Windows a medida que se agranda o se reduce la ventana, incluso en pantalla completa. El agente usa las resoluciones que acepta el controlador gráfico; la resolución inicial se restablece al cerrar. Las peticiones durante el UAC se aplican al volver al escritorio normal. Los equipos con varios monitores y las sesiones concurrentes conservan un ajuste de la visualización sin reconfigurar las pantallas.
- El tamaño pedido sigue toda la superficie disponible, sin quedar limitado a la resolución o a la relación de aspecto de partida. Si el controlador impone un tamaño diferente, el navegador ajusta todo el escritorio a la ventana, conservando su relación de aspecto.
- **Máquinas virtuales**: el controlador de pantalla básico de Windows (adaptador VGA estándar de una VM sin controlador gráfico, incluida Proxmox por defecto) solo ofrece una resolución y rechaza cualquier cambio; la imagen la ajusta entonces el navegador a la ventana. Para un verdadero cambio de tamaño, use un adaptador VirtIO-GPU con su controlador (virtio-win) o el controlador de la GPU física.
- El servicio deja hasta cinco segundos al acompañante de escritorio para restablecer la resolución original al cerrarse la sesión.

## 2.13.0 — 2026-10-19

- **Consola noVNC adaptativa**: resolución del flujo ajustada a la ventana, a la pantalla completa y a la pestaña dedicada, conservando la relación de aspecto y con limitación en el origen para el escritorio de Windows, y vuelta al flujo nativo. Protocolo RFB ExtendedDesktopSize acotado; vuelta al ajuste local con los agentes antiguos y los escritorios X11. La pantalla física del equipo no se reconfigura.
- Las peticiones de imagen consecutivas se agrupan para evitar una cola de actualizaciones durante los cambios de tamaño y las transiciones de UAC; una petición de actualización completa sigue siendo prioritaria.
- **Consola de texto Linux**: cambio de tamaño real del PTY, del número de columnas/filas y de la representación, incluso para dimensiones que no son múltiplos del tamaño de los glifos.
- **UAC de Windows**: acompañante gráfico SYSTEM en la sesión interactiva y seguimiento del escritorio de entrada, incluido el escritorio seguro. Las transiciones de captura y las entradas momentáneamente bloqueadas ya no hacen caer la conexión. Canal local restringido a SYSTEM y comprobación del PID conservada; el icono de la bandeja sigue sin privilegios.
- **Editor de perfiles de acceso**: categorías como navegación, búsqueda sin acentos, filtros de los permisos concedidos/acciones sensibles, contadores por ámbito, selección de los permisos visibles con estado parcial, indicación de las dependencias ajustadas y barra de guardado persistente. Se conservan los perfiles protegidos y los límites de delegación.

## 2.12.1 — 2026-10-19

- **Agentes Windows actualizados después del servidor**: el servidor solo proponía la nueva versión del agente Windows si su MSI ya se había descargado desde el portal (botón de instalación o enlace de despliegue). Sin esa acción, los equipos Windows se quedaban en su versión anterior. Ahora, en cuanto se presenta un agente Windows más antiguo que el servidor, el servidor obtiene el MSI de su versión en GitHub (comprobado con SHA256SUMS) en segundo plano, como mucho un intento cada diez minutos, y lo propone en el contacto siguiente.
- Prueba del diario hecha independiente del segundo en el que se ejecuta.

## 2.12.0 — 2026-10-19

- **Diario rediseñado**: eventos agrupados por día (hoy, ayer, y luego la fecha), solo la hora, acciones en lenguaje claro con su categoría en lugar de los códigos técnicos (el código sigue en una información emergente y en la exportación). Fallos señalados con un indicador rojo y resumidos arriba de la página para las últimas 24 horas.
- **Filtros en el servidor** sobre todo el diario (y ya no solo los 500 últimos eventos): periodo (hoy, 7 días, 30 días, todo o fechas a elegir), categoría con su número de eventos, cuenta, solo fallos, búsqueda; páginas de 100 eventos («Más antiguos»). Un clic en una cuenta filtra sus eventos. En el móvil, el detalle pasa bajo la acción.
- **Exportación y conservación en ventanas** abiertas desde la cabecera: la exportación retoma la vista filtrada (o un periodo, o todo) y añade la etiqueta de cada acción y los filtros aplicados al archivo JSON.
- **Windows Update rediseñado**: indicadores en 14 días (servido por la caché y parte del tráfico, obtenido de Internet), tamaño de la caché y estado del servicio, última petición y número de equipos; gráfico diario de los 14 últimos días; alerta si ningún equipo ha pasado todavía por loco o si la caché está detenida.
- Dominios redirigidos en una lista compacta, modificables en una ventana; explicación del funcionamiento plegada.
- Últimas peticiones: nombre del equipo del parque en lugar de su dirección (permiso *Consultar los equipos*), origen «caché» o «Internet».
- **Catálogo local rediseñado**: insignias de los puntos por tratar con su número (actualizaciones que desplegar en el parque, argumentos silenciosos que faltan, errores, descargas en curso); columnas reducidas a lo esencial (programa, versión, equipos equipados y atrasados con un enlace «desplegar», tamaño, estado). El tipo y el origen pasan bajo el nombre; «Reintentar» en una descarga fallida e «Indicar» en un instalador sin argumentos, directamente en la lista.
- **Ficha de un programa**: cabecera con Modificar, Desplegar y un menú Más (ficha de la tienda, volver a descargar, eliminar); indicadores (estado del instalador, equipos equipados, equipos que actualizar, grupos); lista de los equipos con su versión y «Desplegar la actualización». Tras una modificación desde la ficha, se vuelve a ella.
- **Tienda WinGet rediseñada**: tarjetas compactas (cuatro por fila en pantalla grande) totalmente pulsables hacia la ficha; botón **Añadir** directamente en cada tarjeta, que se queda en la búsqueda o la categoría en curso para añadir otros; estado «en loco», «descarga» o «error» para los programas ya añadidos. Búsqueda y categorías en pastillas reunidas arriba; se retiran los enlaces duplicados hacia el catálogo local.
- **Ficha de la tienda**: editor, versión e identificador bajo el nombre; acciones reunidas en la cabecera (Añadir a loco o Ver en el catálogo local, Desplegar); descripción y detalles de instalación (instalador, argumentos, huella) uno al lado del otro; advertencia cuando la tienda ofrece una versión más reciente que la del catálogo local.
- **Grupos de programas y de equipos rediseñados**: lista compacta con la descripción, una vista previa de los miembros (iconos de los programas, nombres de los equipos) y su estado resumido (programas listos o con error; equipos al día, en espera, muchas actualizaciones); «Desplegar» y, para los equipos, «Ver en Mi parque» en cada fila.
- **Editor de grupo**: miembros mostrados primero con un contador en directo, filtro «solo miembros» y búsqueda; icono, versión y estado de cada programa, sistema e indicador de cada equipo; barra Guardar siempre visible abajo; eliminación del grupo en el menú Más. En solo lectura, solo se listan los miembros.
- Los campos uno al lado del otro (grupos, perfiles, configuración de la appliance) vuelven a estar alineados.
- **Despliegues rediseñados**: lista con insignias por estado (en curso, por confirmar, con errores, terminados), programas con nombre y barra de avance por despliegue (correctos, en espera, fallos). El seguimiento de un despliegue muestra cuatro indicadores, la barra de avance y los equipos primero, con el registro de un fallo desplegado de oficio; las huellas pasan a una información emergente. Confirmar, Abandonar y Cancelar están en la cabecera, y un despliegue que no se puede confirmar dice por qué (otra conexión, instalador no listo, permiso que falta).
- **Preparación de un despliegue**: selecciones mostradas primero (con los iconos de los programas), contadores en directo, equipos compatibles antes que los demás, y barra «Comprobar antes de desplegar» siempre visible con el nombre y la opción de reinstalación.
- Las listas actualizadas en directo también escapan las comillas de los nombres de archivos pedidos por los equipos.

## 2.11.0 — 2026-10-19

- **Mi parque rediseñado**: equipos ordenados del más urgente al más al día; dirección real del equipo (y «a través de» la dirección vista por loco cuando un NAT las separa); actualizaciones en compacto (sistema, programas, de ellas de seguridad); reinicio señalado; grupos en etiquetas pulsables; último contacto en lenguaje claro; acción en espera mostrada en lugar del botón.
- **Filtros inmediatos**: pastillas de estado con su número (todos, muchas actualizaciones, en espera, estado desconocido, al día), Windows / Linux, grupo aplicado en cuanto se elige, búsqueda; «Actualizar todo» y «Desplegar» siguen al grupo mostrado.
- **Añadir un equipo** en una ventana (comandos Windows y Linux, despliegue por GPO) en lugar de un panel al final de la página.
- **Pestaña Ajustes** del parque: dirección del servidor anunciada a los agentes, umbral de las insignias y clave de inscripción.

## 2.10.1 — 2026-10-19

- **Ficha de un equipo rediseñada**: cabecera con el nombre, una insignia de estado corta (al día, actualizaciones en espera, muchas actualizaciones, sin contacto reciente…), la explicación detallada, el sistema, la dirección del equipo, el último contacto en lenguaje claro («hace 2 min») y la versión del agente; grupos en etiquetas.
- Acciones reunidas a la derecha: terminal y escritorio remoto, **Actualizar** como botón principal, y un menú **Más** (actualización con reinicio, inventario, despliegue, reinicio, y luego la retirada del agente, aparte y en rojo).
- Cuatro indicadores (actualizaciones del sistema, de ellas de seguridad, programas o Flatpak, reinicio, inventario) y **pestañas** Actualizaciones, Programas instalados (con filtro), Historial y Detalles; la pestaña elegida se conserva durante la actualización automática.
- Detalles técnicos separados: direcciones del equipo, dirección vista por loco (NAT señalado), direcciones del servidor conocidas por el agente, identificador y grupos.

## 2.10.0 — 2026-10-18

- **Gráficos en la vista general**, trazados en SVG por el servidor (ninguna biblioteca ni script, temas OLED y claro, información emergente al pasar el ratón):
  - **Windows Update en 14 días**: volumen servido por la caché y volumen descargado de Internet, día a día, con la parte ahorrada. Historial reconstruido a partir de los registros rotativos de nginx (`.1`, `.2.gz`, `.AAAAMMDD`), registro actual leído de forma incremental;
  - **Estado del parque**: anillo verde, naranja, rojo, gris (permiso *Consultar los equipos*);
  - **Disco de los datos**: parte de cada caché (APT, Windows Update, RPM, Flathub), de los repositorios y programas locales, del resto y del espacio libre.

## 2.9.0 — 2026-10-18

- **Cambio de dirección del servidor sin perder los agentes**: la futura dirección (IP o nombre DNS) se anuncia en *Mi parque › Dirección del servidor para los agentes* o con `loco-fleet announce https://host:puerto`. Cada agente la recibe en su contacto siguiente; la página y `loco-fleet servers` indican cuántos equipos están avisados.
- Tras el cambio, un agente que ya no llega a la dirección anterior prueba las que conoce y adopta la primera que responde con un certificado de la autoridad loco fijada: una dirección anunciada no puede desviar los equipos hacia otro servidor. Las respuestas HTTP del servidor (token rechazado…) nunca provocan un cambio.
- `setup-loco` (appliance) anuncia la nueva dirección fija y espera a que los equipos estén avisados antes de aplicar la red; con DHCP, avisa de que los agentes no podrán seguir.
- Agentes Windows y Linux 2.9.0: lista de direcciones memorizada y declarada al servidor, mostrada por `loco-client status`.

## 2.8.0 — 2026-10-18

- **Gestión de los programas unificada**: catálogo local, tienda WinGet, grupos de programas y seguimiento de los despliegues en las mismas pestañas. Despliegue desde una ficha de la tienda, del catálogo o de un equipo; importación WinGet compartida y huella verificada conservada en los nuevos intentos.
- **Grupos de programas y de equipos**: pertenencias múltiples, creación y modificación de los miembros; filtro del parque y actualización limitada al grupo mostrado. Eliminación de un grupo sin eliminar sus programas, equipos ni despliegues.
- **Despliegues preparados y confirmados**: combinación de programas, lotes, equipos y grupos, deduplicación, exclusiones Windows/Linux explícitas, selección y versiones fijadas. Conservación de los instaladores confirmados incluso tras la modificación o la eliminación del catálogo; copia de seguridad compatible con esas versiones.
- **Seguimiento por equipo**: órdenes en espera para los agentes desconectados, registros y resultados, cancelación solo de las órdenes no transmitidas. Se omiten las versiones ya instaladas y las instalaciones idénticas ya en espera; reinstalación explícita posible.
- **Permisos distintos**: gestión de los dos tipos de grupos, consulta de los despliegues y despliegue en varios equipos. El despliegue colectivo exige un permiso reservado por defecto a los administradores, vuelto a comprobar antes de la transmisión a los agentes.

## 2.7.0 — 2026-10-18

- **Navegación**: Mi parque va justo después de Vista general.
- **Linux sin escritorio**: la consola noVNC muestra un verdadero terminal PTY y sus colores ANSI en el flujo RFB del agente, sin X11 ni conexión a Proxmox. La alternativa también exige el permiso Terminal remoto; un escritorio Wayland no desencadena un shell implícito. La consola dedicada, el tamaño real y la pantalla completa siguen disponibles.
- **Inicios de sesión SSO**: varios proveedores, plantillas de Microsoft Entra ID, Google, GitHub, GitLab, Keycloak, Authentik, Okta, Auth0, Authelia, ZITADEL, Dex, Cognito, AD FS y OIDC genérico. GitHub usa OAuth 2; los demás usan OpenID Connect.
- **Aprobación obligatoria**: una cuenta SSO nueva no tiene ningún permiso ni sesión antes de la validación administrativa y la asignación explícita de un perfil. Las identidades nunca se emparejan automáticamente por correo electrónico. Los secretos se cifran; modificar, desactivar o eliminar un proveedor revoca sus sesiones. Se conserva el inicio de sesión local.

## 2.6.1 — 2026-10-18

- **Verdadero cursor en el escritorio remoto**: los agentes Windows y Linux envían la forma del cursor del equipo (flecha, texto, mano, espera, cambio de tamaño…) con su transparencia y su punto activo; el navegador lo muestra como un cursor nativo, sin desplazamiento ni latencia. Cursores claros o de inversión con borde para seguir legibles sobre todos los fondos; el punto ya solo sirve de respaldo.
- **Diario**: exportación JSON de un periodo o del diario completo, y la exportación queda a su vez registrada; conservación configurable (de 7 días a 2 años o sin límite), **30 días por defecto**, con rotación automática. Nuevos permisos *Exportar el diario* (perfil Auditor) y *Configurar la conservación del diario*; los perfiles integrados siguen los nuevos permisos.
- Ficha de un equipo: botones de acceso remoto alineados e icono de «nueva pestaña» centrado; la misma corrección para las demás filas de botones.

## 2.6.0 — 2026-10-17

- **Consola en una pestaña dedicada**: terminal y noVNC sin la navegación del portal, barra de herramientas compacta y la superficie disponible para el equipo; pantalla completa, vuelta a su ficha y cierre explícito.
- **Escritorio nativo y nítido**: captura de Windows sin reducción impuesta a 1920 × 1080, compresión Tight sin pérdida en Windows y X11, visualización ajustada sin ampliación artificial o tamaño real 1:1 con desplazamiento e indicación de la resolución.
- **Fluidez**: rectángulos modificados en lugar de imágenes completas, agrupación de los paquetes, envío inmediato de las entradas y de los flujos en espera, conexión HTTPS persistente en Linux, supresión de las esperas TCP inútiles en Windows. Colas acotadas y acuses conservados, reanudación sin repetición cuando el receptor se ralentiza.

## 2.5.0 — 2026-10-16

- **Perfiles de acceso granulares**: 46 permisos por acción, perfiles personalizados reutilizables y modelos Administrador, Solo lectura, Técnico del parque, Gestor de los repositorios y Auditor. Duplicación de los modelos, asignación por cuenta y consulta de los propios permisos.
- Permisos distintos para inventario, actualizaciones individuales o globales, instalación, reinicio, retirada del agente, terminal y noVNC. Consulta y gestión separadas de los repositorios, la tienda, el servidor, las cuentas, los perfiles y el diario.
- Control en el servidor de cada ruta privada y navegación adaptada. Delegación limitada a los permisos que se tienen, perfiles integrados y acceso propio protegidos, último administrador activo preservado ante modificaciones concurrentes.
- Revocación de las conexiones y de las sesiones remotas tras un cambio de permisos; cancelación de las órdenes todavía en espera y comprobación de los permisos antes de su transmisión al agente. Auditoría detallada de los permisos concedidos y retirados.
- Migración que conserva las cuentas y contraseñas existentes. CLI `loco-user roles` y `loco-user set-role`. Formulario de inicio de sesión utilizable de inmediato tras la revocación de una sesión; corrección del envío de las acciones del parque desde el navegador.

## 2.4.0 — 2026-10-16

- **Acceso remoto a través del agente**: terminal interactivo PTY en Linux y PowerShell ConPTY nativo en Windows, con cambio de tamaño, parada al cerrar y relé HTTPS saliente. Ningún acceso a las consolas de Proxmox.
- **Escritorio noVNC**: teclado y ratón en una sesión de Windows abierta o en un escritorio X11 de Linux, sin servidor VNC que instalar ni puerto de entrada que abrir. Acompañante de Windows incluido en el MSI; bibliotecas del navegador servidas localmente.
- **Ventanas de sesión**: pantalla completa con barra de herramientas y salida explícita, y apertura en una nueva pestaña desde la ficha del equipo o una sesión en curso.
- Acceso reservado a los administradores, sesiones vinculadas a su conexión, revocación y plazos de caducidad, flujos con acuse sin repetición, colas temporales excluidas de las copias de seguridad. Auditoría de las aperturas y cierres sin el contenido de las sesiones.

## 2.3.0 — 2026-10-16

- Tienda de programas Windows alimentada por el catálogo comunitario Microsoft WinGet: categorías, búsqueda paginada, fichas, logos verificados y en caché, importación de instaladores verificados en loco; consulta del último índice válido sin conexión.
- Agente Windows: icono de notificación por sesión, indicador verde / naranja / rojo y detalles en solo lectura. El usuario no puede lanzar ninguna actualización desde el icono; los tokens siguen siendo privados y el servicio solo publica el estado del equipo.
- El mismo recuento en Mi parque y en Windows: verde sin actualizaciones, naranja de 1 a 9, rojo a partir de 10; umbral rojo configurable. Un estado no disponible sigue en gris. La instalación, el arranque de sesión y la retirada del acompañante los gestiona el MSI.

## 2.2.0 — 2026-10-16

- **Mi parque**: agente `loco-client` para Windows (servicio .NET Framework, instalador MSI desplegable por GPO) y Linux
  (Debian, Ubuntu, Rocky Linux, AlmaLinux, RHEL; systemd). Indicador verde / naranja / rojo por equipo, dirección IP,
  actualizaciones pendientes (de ellas de seguridad), programas instalados e historial de las acciones.
- Actualización de un equipo o de todo el parque con un clic (Windows Update, APT, DNF, Flatpak, programas del catálogo),
  reinicio opcional, instalación de un programa del catálogo, inventario, reinicio y retirada del agente.
- Identidad del equipo independiente de su dirección IP: un cambio de dirección no lo desvincula; solo la retirada
  enviada desde loco desinstala el agente. Autoridad HTTPS fijada, tokens con hash, lista de acciones cerrada.
- Programas Windows: detección de las versiones instaladas en el parque; búsqueda e importación desde el catálogo winget
  (instalador oficial comprobado por SHA-256, opciones silenciosas y nombre de detección), y nuevas versiones de winget.
- Actualización automática de los agentes; MSI construido en Linux (Mono, msitools) por la CI y publicado con la versión.

## 2.1.3 — 2026-10-16

- Instalación ISO y `setup-loco`: elección entre SSH con la contraseña de root, SSH por clave pública, o SSH desactivado. El modo contraseña no pide ninguna clave pública.
- Respuestas automáticas: `SSH_MODE=password`, `key` u `off`; los archivos antiguos que solo usan `SSH_KEY` siguen siendo compatibles.
- Configuración SSH común a la instalación y a la consola; contraseñas vacías rechazadas, modo por clave sin autenticación por contraseña, pruebas de conexiones SSH reales.

## 2.1.2 — 2026-10-16

- Portal: una petición HTTP recibida en el puerto HTTPS redirige automáticamente a HTTPS en lugar de mostrar «400 Bad Request»; se conservan el host, el puerto, la ruta y los parámetros.
- Configuración común a Linux, Alpine/ISO, Docker y Windows; redirección comprobada mediante peticiones GET, HEAD y POST reales en las pruebas de instalación.

## 2.1.1 — 2026-10-16

- ISO: consola de inicio de sesión fijada en tty1 para impedir que el getty robe el teclado y la pantalla del asistente en tty7.
- Esc e Inicio vuelven al menú sin abrir un shell; la consola de solución de problemas es explícita y `exit` devuelve al asistente.
- Se respeta la cancelación de la selección de las funciones; prueba del teclado, de las cancelaciones y de la consola en una VM.

## 2.1.0 — 2026-10-15

- Appliance ISO Alpine 3.24 / BusyBox: asistente visual, instalación automática con respuestas, BIOS y UEFI, paquetes incluidos y primer arranque configurado.
- Administración gráfica y en línea de comandos: configuración de los accesos, las funciones y las cachés, actualizaciones de loco y del sistema, registros en directo; `setup-loco`, `loco-config` y `loco-update`.
- Agente OpenRC separado del portal sin privilegios; peticiones vueltas a validar, operaciones serializadas y vuelta atrás de loco / configuración / base de datos en caso de fallo.
- Alpine se convierte en una plataforma del instalador Linux común, con caché APT nginx compartida con Windows, firmas y lectores de paquetes en Python, copias de seguridad y desinstalación.
- CI Alpine nativa e ISO: archivos verificados, vuelta atrás provocada, instalaciones BIOS / UEFI y reinicios sin medio; publicación de las fuentes, ISO validada y SHA256SUMS.

## 2.0.0 — 2026-10-15

- Cambio de nombre completo a **loco**: repositorio GitHub, portal, módulos Python, servicios, cuenta del sistema, comandos, rutas y configuraciones de los equipos.
- Migración de los servidores Linux y Windows existentes al volver a lanzar el instalador, con copia de seguridad previa y conservación de las cuentas, los UID de Linux, los certificados, las claves, los repositorios y las cachés.
- Los repositorios APT existentes conservan su Origin/Label firmado para que los equipos antiguos sigan actualizándose sin confirmación manual; los repositorios nuevos llevan loco.
- Actualización de los equipos APT/RPM en el siguiente setup, migración de los orígenes Flatpak; las copias de seguridad antiguas de Linux y Windows se pueden restaurar.
- `linux/docker/up.sh` retoma las variables LR_ y copia los volúmenes históricos al proyecto loco; los volúmenes originales se conservan y el contenedor antiguo se reinicia si la migración falla.
- CI: migraciones desde local-repo 1.9.0 en Debian, Ubuntu, Rocky, Docker y Windows Server 2022/2025.

## 1.9.0 — 2026-10-15

- **Organización del proyecto por sistema**, en una sola rama: `linux/` (instalador, administración,
  Docker, contenedor LXC de Proxmox), `windows/` (instalador y servicio de Windows), `portal/` y `conf/` comunes,
  `docs/linux.md` y `docs/windows.md`. `sudo ./install.sh` sigue siendo válido (atajo a `linux/install.sh`),
  así como las actualizaciones de las instalaciones existentes; el árbol instalado no cambia.
- Docker: `cd linux/docker && docker compose up -d --build`; nombre de proyecto fijo, volúmenes conservados.
- Contenedor LXC: `linux/lxc/proxmox-lxc.sh`.
- README: elección de la instalación, y luego una parte Servidor Linux y una parte Servidor Windows.

## 1.8.0 — 2026-10-15

- Servidor nativo Windows 11 Pro / Enterprise y Windows Server 2022 / 2025: servicio de Windows, Python incluido, nginx y Waitress, sin Linux ni Hyper-V. Firmas OpenPGP de los repositorios
  (clave RSA 4096) producidas en Python: ningún GnuPG que instalar, ninguna modificación del `PATH`.
- Instalador de PowerShell que se puede volver a lanzar, componentes comprobados por SHA-256, cuenta de servicio virtual, ACL y reglas del cortafuegos limitadas a las redes autorizadas.
- Repositorios APT y RPM indexados y firmados en Windows con lectores en Python, comparados con las herramientas nativas de Linux; lectura de los metadatos sin cargar todo el paquete en memoria.
- Caché APT nginx, cachés RPM/Flathub/Windows Update y catálogo de Windows; el repositorio Flatpak local sigue reservado a Linux.
- Administración, copias de seguridad SQLite, restauración con el servicio detenido, renovación HTTPS y desinstalación conservando los datos o con purga explícita.
- Pruebas automáticas de los servidores Windows Server 2022 y 2025, importaciones de paquetes gzip/xz/zstd, firmas, caché y ciclo de mantenimiento.

## 1.7.0 — 2026-10-14

- Instalación nativa del servidor en Rocky Linux 8, con el mismo `install.sh` y las mismas funciones que Rocky 9/10.
- Python 3.11 aislado, repositorio PowerTools e indexador APT 2.6.1 compilado a partir de las fuentes de Debian con control SHA-256; ninguna sustitución del Python del sistema.
- Selección del flujo nginx 1.24 de AppStream en Rocky 8 si el nginx presente no proporciona `stream_ssl_preread`, necesario para el relé de Windows Update.
- Copias de seguridad SQLite ejecutadas con el runtime de la aplicación en Rocky 8, compatible con la copia en línea de la base de datos.
- Generación de la autoridad HTTPS compatible con OpenSSL 1.1, sin extensiones duplicadas, y comprobación del certificado del servidor por esta autoridad.
- Desinstalación del indexador privado y restauración del repositorio PowerTools si el instalador lo había activado.
- Prueba completa de Rocky 8 añadida a la CI, con SELinux y firewalld activos.

## 1.6.0 — 2026-10-14

- Instalación nativa del servidor completo en Rocky Linux 9 y 10 con el mismo `install.sh`: DNF, EPEL/CRB, nginx, caché APT, repositorios APT/RPM, Flatpak y Windows Update.
- SELinux sigue activo, servicio firewalld dedicado si el cortafuegos está en marcha, filtrado de la caché APT por nginx con backend en bucle local.
- Rocky 10: caché APT compilada a partir de las fuentes de Debian 3.7.5-1.3 con parches y huellas fijados.
- Desinstalación adaptada a DNF, restauración de los ajustes de SELinux, del cortafuegos y de nginx añadidos por la aplicación.
- Pruebas del servidor en VM Rocky 9/10, añadidas a la CI; instalación compatible con una umask restrictiva.

## 1.5.1 — 2026-10-14

- Vista general, *Conectar un equipo*: una pestaña por tipo de equipo (Debian / Ubuntu; Rocky Linux,
  AlmaLinux, RHEL; Flatpak; Windows), cada una con su comando, la primera conexión y el enlace a
  sus opciones. Windows precisa que Windows Update pasa por el DNS, sin instalar nada en el equipo.
- Pestañas en una sola línea, con desplazamiento en pantalla pequeña.

## 1.5.0 — 2026-10-14

- Contraseña temporal (primer inicio de sesión, restablecimiento): página dedicada, sin la navegación del
  portal, con posibilidad de cerrar sesión; ninguna otra página es accesible antes del cambio.
- Tema: OLED por defecto, cambio al tema claro con un discreto icono (luna) en la barra de
  navegación y en las pantallas de inicio de sesión; elección memorizada por el navegador.

## 1.4.0 — 2026-10-14

- **Contenedor LXC de Proxmox VE**: `lxc/proxmox-lxc.sh`, que lanzar en cualquier anfitrión Proxmox VE 7, 8 o 9,
  crea un contenedor Debian sin privilegios (almacenamiento, puente y plantilla elegidos según el anfitrión, IP fija o DHCP,
  VLAN) y hace en él la instalación nativa; opciones de `install.sh` transmitidas tras `--`; actualización con
  `--update`, versión fijada con `--version`, copia local con `--source`.

## 1.3.0 — 2026-10-14

- **Despliegue con Docker** (`docker compose up -d --build`), además de la instalación nativa, que sigue siendo
  la recomendada: imagen Debian 13, configuración por variables (`.env.example`) aplicada en cada arranque
  por `install.sh --container`, servicios lanzados por `supervisord`, tareas periódicas (estadísticas,
  recarga de nginx, rotación de los registros, copia de seguridad diaria) por `local-repo-cron`.
- Contenedor sin capacidades superfluas, `no-new-privileges`, cuentas de servicio con identificadores fijos,
  comprobación de salud, volúmenes para los datos, la configuración y las copias de seguridad.
- El portal reconoce el modo contenedor: estado de los servicios, comandos de ayuda y restauración adaptados.
- Validación en una VM Debian 13 desechable (`tests/vm-docker-e2e.sh`).

## 1.2.0 — 2026-10-13

- **Familia Red Hat**: caché de las réplicas de Rocky Linux, AlmaLinux y EPEL (paquetes y metadatos con nombre
  único guardados 6 meses, certificados de las réplicas comprobados); repositorios RPM locales por versión principal
  (`el8`, `el9`, `el10`, adición de una versión futura desde el portal), metadatos firmados, rechazo de los
  paquetes de otra versión o de un contenido diferente para un mismo nombre/versión.
- Script `rpm/setup.sh`: detección del equipo, repositorio local, repositorios oficiales y EPEL a través de la caché con sus
  claves GPG, `--epel`, `--trust-ca`, `--no-cache`, `--no-local`, `--remove` (restablecimiento exacto de los repositorios).
- RHEL: repositorio local y EPEL a través de este servidor; repositorios de Red Hat (suscripción) en acceso directo.
- Opciones `--no-rpm` y `--rpm-cache-size`; la copia de seguridad, las estadísticas, `/metrics` y la desinstalación
  cubren los repositorios RPM.
- Pruebas reales (`rpmbuild`, `createrepo_c`, firmas) y validación de equipos Rocky Linux / AlmaLinux 8, 9
  y 10 en VM, añadida a la CI.

## 1.1.0 — 2026-10-13

- Repositorios APT aislados por distribución y nombre de versión, creación desde el portal o `local-repo-apt`.
- Detección automática de los equipos Debian / Ubuntu, fuentes deb822; opciones `--cache-only`,
  `--distribution`, `--suite`, `--legacy`. Una versión ausente no sustituye la configuración del equipo.
- Repositorio histórico conservado, copia explícita de paquetes entre repositorios; publicación sin sustitución
  de un paquete/versión por un contenido diferente; metadatos firmados preparados antes de la publicación.
- Copia de seguridad y restauración de todos los repositorios, migración del registro de las versiones.
- Correcciones de compatibilidad: HTTPS de APT en acceso directo, conexiones salientes de la caché conservadas,
  limpieza de la caché de los equipos al cambiar de repositorio; soporte del canal de control
  de las nuevas versiones de Gunicorn y plazos acotados para comprobar el arranque.
- Pruebas de aislamiento y de firmas, prueba de migración desde la 1.0.0; matriz de VM Debian 12/13 y
  Ubuntu 22.04/24.04/26.04, imágenes oficiales verificadas, comprobaciones semanales activas en la CI.
- Navegación Linux / Windows con iconos y menús al pasar el ratón, con el teclado y al tacto; iconos de la cuenta.

## 1.0.0 — 2026-10-13

Primera versión destinada a un uso en empresa.

### Añadido
- **Flatpak**: caché de Flathub (objetos inmutables guardados 6 meses, firmas de Flathub comprobadas por los equipos)
  y repositorio Flatpak local firmado, alimentado mediante el envío de paquetes `.flatpak`; script `flatpak/setup.sh`.
- **Cuentas con nombre** (administrador, solo lectura), contraseña temporal que cambiar, bloqueo tras fallos,
  caducidad de las sesiones, diario de auditoría, gestión en línea de comandos (`local-repo-user`).
- **HTTPS en todas partes**: equipos cliente en HTTPS por defecto, scripts que siguen el protocolo usado,
  `setup.sh --trust-ca` para el arranque, `--no-http` para cerrar el puerto HTTP.
- **Redes autorizadas** (`--allow`, `--admin-allow`) aplicadas por nginx, apt-cacher-ng y systemd.
- **Proxy de empresa** (`--proxy`) para apt-cacher-ng y las descargas del portal.
- **Copias de seguridad** diarias con rotación, restauración en el mismo servidor o en uno nuevo
  (`local-repo-backup`).
- **Supervisión**: `/metrics` (Prometheus) y `/healthz`.
- Página **Administración** (cuentas, diario, sistema, copias de seguridad).
- Pruebas unitarias, prueba de extremo a extremo (`tests/e2e.sh`) e integración continua en GitHub.
- Guía de explotación (`docs/EXPLOITATION.md`) y política de seguridad (`SECURITY.md`).

### Modificado
- Política de contenido estricta (CSP): ya no hay ningún script ni estilo en línea.
- Unidad systemd del portal confinada; contraseñas de apt-cacher-ng para su mantenimiento.
- Portal reorganizado como paquete Python; base de datos SQLite en WAL con migraciones versionadas.
- Interfaz: navegación agrupada, selector de tema en *Mi cuenta*.

### Seguridad
- La cuenta única de las versiones anteriores pasa a ser la cuenta `admin` (misma contraseña);
  se elimina el archivo `/etc/local-repo/admin-password`.

## 0.3 — 2026-10-13
- Interfaz en HTTPS (autoridad local), rediseño visual sobrio (IBM Plex, temas claro y OLED).

## 0.2 — 2026-10-13
- Caché de Windows Update limitada a los dominios de la lista, solo archivos inmutables en caché,
  correcciones surgidas de las pruebas (Debian 12/13, Ubuntu 24.04, Windows 11).

## 0.1 — 2026-10-12
- Primera versión: apt-cacher-ng y repositorio APT local, caché de Windows Update, catálogo de programas Windows,
  portal web, `install.sh` y `remove.sh`.
