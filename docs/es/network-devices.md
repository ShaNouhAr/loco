[English](../network-devices.md) · [Français](../fr/network-devices.md) · **Español**

# Dispositivos de red: switches y cortafuegos

> **Función experimental**, desactivada por defecto: un administrador la activa en *Administración ›
> Funciones* (sección *Experimental*). Aparece entonces en *Sistemas › Red › Dispositivos de red*.

loco gestiona sin agente sus **switches Cisco y Extreme** y sus **cortafuegos Stormshield**, con una cuenta del
dispositivo, como lo haría un administrador:

| Familia | Vínculo | Lectura y configuración | Firmware |
|---|---|---|---|
| Cisco IOS / IOS-XE (Catalyst, routers) | SSH | línea de comandos | copia scp, MD5 comprobado, `boot system` — o `install add … activate commit` en modo INSTALL |
| Cisco Small Business (CBS, SG) | SSH | línea de comandos | señalado; instalación desde la interfaz web (TFTP solo en línea de comandos) |
| Extreme EXOS / Switch Engine | SSH | línea de comandos | copia scp, `install image` en la partición de reserva |
| Extreme VOSS / Fabric Engine | SSH | línea de comandos | copia SFTP en `/intflash`, `software add` / `activate`, `software commit` tras el reinicio |
| Stormshield SNS | API HTTPS (SSH para el terminal) | API (`SYSTEM PROPERTY`, `CONFIG BACKUP`) | `SYSTEM UPDATE UPLOAD` y luego `ACTIVATE` |

## Lo que necesita el dispositivo

- **Cisco IOS / IOS-XE**: SSH activado (`ip ssh version 2`) y una cuenta de nivel 15 — o una cuenta de nivel 1 con
  la contraseña *enable* guardada en loco. Para el firmware: `ip scp server enable`.
- **Cisco Small Business**: SSH activado (`ip ssh server`) y una cuenta de nivel 15. Tanto si el switch pide la cuenta
  por SSH como si la pide él mismo una vez conectado (`ip ssh password-auth` ausente), loco responde.
- **Extreme EXOS**: SSH activado (`enable ssh2`) y una cuenta de administración.
- **Extreme VOSS**: SSH activado (`boot config flags sshd`) y una cuenta `rwa`; SFTP para el firmware.
- **Stormshield SNS**: una cuenta de administración **sin segundo factor** (TOTP), y el acceso a la interfaz
  de administración desde loco. SSH es opcional: solo sirve para el terminal.

Los dispositivos antiguos que solo hablan los algoritmos SSH de antes de 2015 (clave de host `ssh-rsa`, Diffie-Hellman
grupo 14 con SHA-1, cifrado CBC) se alcanzan: loco los añade como última opción, cuando su cliente SSH aún los
conoce.

## Vincular un dispositivo

*Añadir un dispositivo*: nombre, familia, dirección, puerto SSH (y puerto HTTPS para Stormshield), cuenta y contraseña.
La contraseña se **cifra** en la base de datos de loco; ya no se muestra, y se entrega a `ssh` mediante
`SSH_ASKPASS` — nunca en los argumentos de un comando ni en claro en el disco. loco muestra después la **huella
de la clave de host** SSH (y la del **certificado** del cortafuegos): compárela y luego confirme. No se intercambia
nada más antes, y loco rechaza después cualquier dispositivo que presente otra clave. Desde un equipo que ya se
ha conectado a él, `ssh-keygen -lF DIRECCIÓN` muestra la huella que conoce.

## Lo que muestran Mi parque y la página del dispositivo

Modelo, número de serie, versión (e imagen de arranque), puertos activos sobre el total, procesador, memoria,
temperatura y tiempo encendido — leídos cada quince minutos, y al abrir la página. Un comando que
el dispositivo no conoce simplemente deja su casilla vacía. Inaccesible, vínculo fallido, copia de seguridad fallida o
firmware más reciente disponible se ven en Mi parque y en *Por tratar* de la vista general.

## Copia de seguridad de la configuración

Cada día por defecto (cada seis horas, cada semana, o solo a petición), loco lee la configuración
(`show running-config`, `show configuration` para EXOS, la copia completa `.na` para Stormshield) y la
guarda **solo si ha cambiado**: las líneas que cambian solas («Last configuration change at…», marca de tiempo de
VOSS) no cuentan. La página muestra el historial, el número de líneas añadidas y retiradas, las **diferencias**
entre dos versiones, y permite descargar cada una. La conservación (de 10 a 365 versiones) se ajusta por dispositivo.

Las configuraciones suelen contener secretos del dispositivo: se **cifran** en el estado de loco
(`/var/lib/loco/network/configs`), y por tanto se incluyen en la copia de seguridad del propio loco, y cada consulta o
descarga queda inscrita en el diario. El permiso *Hacer copia de seguridad y leer las configuraciones* es distinto del de
consultar los dispositivos.

## Firmware

1. **Deposite** el archivo descargado del fabricante en la *Biblioteca de firmwares* (página Dispositivos
   de red): loco lee su versión en el nombre (corregible) y muestra su huella SHA-256 — compárela con la
   publicada por el fabricante. *Modelos afectados* restringe el archivo a algunos modelos (`C9300-*, X440G2*`).
2. loco **señala** los dispositivos que tienen una versión más antigua. Para Cisco, solo los de la **misma
   plataforma** que la imagen (una imagen de Catalyst 9300 nunca se propone a un 2960X); EXOS, VOSS y Stormshield
   rechazan por sí mismos una imagen de otra plataforma.
3. *Instalar el firmware*: la configuración se guarda justo antes, y luego la imagen se envía y se activa
   (vea la tabla del principio). Con *Reiniciar después*, loco sigue el reinicio y la vuelta del dispositivo y
   comprueba la nueva versión. Los Catalyst en modo INSTALL y los cortafuegos Stormshield se reinician solos al
   activarse. En VOSS, loco valida la versión (`software commit`) en cuanto vuelve a alcanzar el switch: sin ello, este
   volvería solo a la anterior.

Compruebe primero en las notas de versión del fabricante que la versión conviene al modelo y que la actualización
es posible desde la que está en servicio: loco no sabe nada de ello.

## Terminal y reinicio

*Terminal* abre la línea de comandos del dispositivo en el navegador (sesión mantenida por el servidor loco y
registrada en el diario), con la cuenta guardada; el modo privilegiado (`enable`) se escribe en el terminal. *Reiniciar*
reinicia el dispositivo y sigue su vuelta: el tráfico que lo atraviesa se corta durante el reinicio, y las
modificaciones de configuración no guardadas se pierden — loco no guarda nada en su lugar.

## Permisos

*Consultar los dispositivos de red*, *Hacer copia de seguridad y leer las configuraciones*, *Instalar un firmware*, *Reiniciar
un dispositivo de red*, *Abrir un terminal en un dispositivo*, *Vincular dispositivos de red* (añadir, retirar,
cuenta y contraseña, huellas): a marcar en los perfiles de acceso.

## Lo que se ha comprobado

Cada familia se ha probado con el verdadero cliente SSH de loco contra **simuladores** fieles a las salidas
documentadas de estos sistemas (indicador, paginación, `enable`, credenciales pedidas por el dispositivo, preguntas de
confirmación, reinicio), incluido un IOS antiguo limitado a `ssh-rsa` y Diffie-Hellman grupo 14 con SHA-1, y
contra una API Stormshield simulada a partir del cliente oficial. **Todavía no en dispositivos reales**: las lecturas
(puertos, procesador, temperatura) dependen del modelo y de la versión, y algunas casillas pueden quedar vacías.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
