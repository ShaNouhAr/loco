# scrcpy-server

`scrcpy-server.jar` est le composant appareil de [scrcpy](https://github.com/Genymobile/scrcpy)
(version 4.0), sous licence **Apache License 2.0**. loco le pousse sur l'appareil Android par adb et
le lance en vidéo seule (audio et contrôle désactivés) pour diffuser l'écran **déverrouillé** à faible
latence : loco lit le flux H.264 brut et le relaie au navigateur, qui le décode avec WebCodecs. Le
contrôle (gestes, touches, texte) passe par `adb input`, pas par le canal de contrôle de scrcpy.

Voir `portal/loco/adb.py` (fonctions `scrcpy_*`) et `portal/static/adb_screen.js`.
