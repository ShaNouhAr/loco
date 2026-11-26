[English](SECURITY.md) · [Français](SECURITY.fr.md) · **Español**

# Seguridad

## Informar de una vulnerabilidad

Abra un aviso privado en GitHub (pestaña *Security › Report a vulnerability*) en lugar de una issue pública.
Indique la versión (`loco` abajo de la página de inicio de sesión), la distribución y los pasos
para reproducirla.

## Modelo de amenazas

loco está diseñado para una **red interna de empresa**. No está previsto para exponerse en Internet.

| Elemento | Medidas |
|---|---|
| Interfaz de administración | Solo HTTPS (TLS 1.2+), cuentas con nombre y roles, contraseñas con hash (scrypt, o PBKDF2 con las versiones de Werkzeug de las distribuciones más antiguas), política de longitud, cambio obligatorio de las contraseñas temporales, bloqueo tras fallos (por cuenta y por dirección), autenticación de dos factores TOTP con códigos de respaldo (un código solo abre una sesión: no se puede reutilizar), claves de acceso WebAuthn (verificación de la persona por el dispositivo exigida, desafíos de un solo uso guardados por el servidor, origen e identidad vinculados a la dirección del portal, contador de firmas controlado, solo se conserva la clave pública), inicio de sesión posible por directorio (LDAP, RADIUS) o SSO OpenID Connect, sesiones conocidas por el servidor (solo se guarda la huella de su identificador): cerradas al cerrar sesión — una cookie copiada antes ya no vale nada —, tras 8 h de inactividad, al cabo de 7 días en todo caso, y al cambiar la contraseña, token CSRF, cookie `Secure`/`HttpOnly`/`SameSite=Strict`, cabeceras CSP estrictas (ningún script ni estilo en línea), `X-Frame-Options: DENY`, `nosniff`, redes autorizadas dedicadas (`--admin-allow`), diario de auditoría |
| Repositorios APT locales | Índices firmados con GPG (RSA 4096), clave generada en el servidor; paquetes e índices aislados por distribución/versión; nombres de repositorio validados; un mismo paquete/versión no puede sustituirse por un contenido diferente |
| Repositorios RPM locales | `repomd.xml` firmado con GPG y comprobado por dnf (`repo_gpgcheck=1`); contiene la huella SHA-256 de cada paquete |
| Caché RPM (Rocky Linux, AlmaLinux, EPEL) | Certificados de las réplicas comprobados; paquetes comprobados por los equipos con las claves GPG de su distribución (`gpgcheck=1`) |
| Repositorio Flatpak local | Commits y resumen firmados con GPG; Flathub sigue verificado con su propia clave |
| Programas Windows | SHA-256 de cada instalador comprobado por el script antes de ejecutarlo; comandos recomendados en HTTPS |
| Caché de Windows Update | Solo retransmite los dominios de su lista (HTTP y SNI); HTTPS retransmitido sin descifrar; solo los archivos inmutables se guardan en caché |
| apt-cacher-ng | Clientes filtrados (tcpwrappers en Debian/Ubuntu; relé TCP de nginx con ACL y backend en bucle local en Rocky), páginas de mantenimiento protegidas por contraseña |
| Portal (proceso) | Usuario dedicado sin shell, unidad systemd confinada (sistema de solo lectura, ninguna capacidad, llamadas al sistema filtradas, `systemd-analyze security` ≈ 1,5); solo nginx expuesto |
| Secretos | Contraseñas del portal con hash; credenciales de mantenimiento de apt-cacher-ng en archivos de acceso restringido (0600 para el secreto, 0640 root/apt-cacher-ng para su configuración); claves privadas en 0600; copias de seguridad en 0640 `root:loco`, para que el portal pueda ofrecer su descarga y su envío externo: un archivo contiene la base de datos, las claves privadas del servidor y la de la autoridad local, y por tanto se protege como el propio servidor; clave de inscripción de los equipos: nunca se escribe en el registro de accesos, y se retira del equipo una vez inscrito (paquetes `.deb`/`.rpm`, registro de Windows) |

## Límites conocidos

- Los `.rpm` de los repositorios locales no se vuelven a firmar uno por uno: su integridad se apoya en los
  metadatos firmados (`repo_gpgcheck=1`), y no en una firma por paquete (`gpgcheck=0`).
- Los repositorios de RHEL (suscripción de Red Hat) no pasan por la caché.
- Despliegue con Docker: los procesos conservan sus cuentas dedicadas y el contenedor solo tiene las capacidades
  útiles, pero el confinamiento systemd del portal no está disponible; la instalación nativa está más
  compartimentada. El contenedor usa la red del anfitrión.

- El puerto HTTP de arranque (8088) sirve scripts e instaladores sin cifrado: un atacante en la red
  podría modificar un script. Despliegue la autoridad de certificación y luego use `--no-http`.
- `setup.sh --trust-ca` confía en la autoridad recibida en el primer contacto (compare la huella mostrada).
- La autenticación de dos factores de loco solo se aplica al inicio de sesión con contraseña local. Un inicio de sesión por directorio o
  por SSO sigue las reglas del directorio o del proveedor de identidad.
- Una clave de acceso (passkey) sustituye a la contraseña y al segundo factor: la verificación de la persona la hace
  el dispositivo (huella, rostro, código), no loco. No se pide ninguna atestación del fabricante: loco no restringe
  los modelos de dispositivo ni los gestores de contraseñas que sincronizan las claves. Añadir una clave requiere la
  contraseña de la cuenta; quien conoce esa contraseña (y el segundo factor) puede por tanto añadir una: el diario anota
  cada adición, retirada e inicio de sesión con clave.
- Los equipos acceden a los repositorios sin autenticación; la restricción se hace por red (`--allow`).
- Envío syslog: en TCP y en UDP, los registros circulan en claro; prefiera TLS. El contenido de los registros de los equipos sigue siendo declarativo
  (un equipo comprometido puede escribir lo que quiera en sus propias líneas): loco impone el nombre de host y retira los saltos de línea y
  los caracteres de control, para que un equipo no pueda ni hacerse pasar por otro ni falsificar una línea. Sin certificado de cliente (mTLS).
- El aislamiento APT evita mezclar los repositorios; no demuestra que un `.deb` sea compatible con
  su destino. El administrador debe validar las dependencias y el origen de los paquetes antes de la importación
  o la copia. El repositorio histórico y `setup.sh --legacy` siguen compartidos para la transición.
