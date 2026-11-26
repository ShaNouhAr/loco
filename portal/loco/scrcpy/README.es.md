[English](README.md) · [Français](README.fr.md) · **Español**

# scrcpy-server

`scrcpy-server.jar` es el componente del dispositivo de [scrcpy](https://github.com/Genymobile/scrcpy)
(versión 4.0), con licencia **Apache License 2.0**. loco lo envía al dispositivo Android por adb y
lo lanza solo en vídeo (audio y control desactivados) para difundir la pantalla **desbloqueada** con baja
latencia: loco lee el flujo H.264 en bruto y lo retransmite al navegador, que lo decodifica con WebCodecs. El
control (gestos, teclas, texto) pasa por `adb input`, no por el canal de control de scrcpy.

Vea `portal/loco/adb.py` (funciones `scrcpy_*`) y `portal/static/adb_screen.js`.
