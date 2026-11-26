**English** · [Français](README.fr.md) · [Español](README.es.md)

# scrcpy-server

`scrcpy-server.jar` is the device component of [scrcpy](https://github.com/Genymobile/scrcpy)
(version 4.0), under the **Apache License 2.0**. loco pushes it to the Android device through adb and
starts it in video-only mode (audio and control disabled) to stream the **unlocked** screen with low
latency: loco reads the raw H.264 stream and relays it to the browser, which decodes it with WebCodecs. The
control (gestures, keys, text) goes through `adb input`, not through scrcpy's control channel.

See `portal/loco/adb.py` (`scrcpy_*` functions) and `portal/static/adb_screen.js`.
