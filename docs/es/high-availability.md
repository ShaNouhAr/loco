[English](../high-availability.md) · [Français](../fr/high-availability.md) · **Español**

# Alta disponibilidad

Dos servidores loco, una sola **IP virtual**: el **principal** sirve el portal, los repositorios y los agentes; el **de reserva** lo replica todo de forma continua y toma la IP automáticamente si el principal cae (keepalived / VRRP). Los equipos, el portal y los repositorios solo conocen la IP virtual, que no cambia durante una conmutación. En **Administración → Servidor → Alta disponibilidad**:

- **Puesta en marcha**: en el primer servidor, **Activar** pide una IP virtual libre y muestra un **código de emparejamiento** de un solo uso; en un segundo servidor nuevo (misma versión de loco), **Unirse** con la dirección del principal y ese código copia toda la configuración y los repositorios, y luego arma la conmutación. loco ajusta él mismo su dirección a la IP virtual y amplía su certificado a la IP virtual y a los dos servidores.
- **Replicación**: la configuración, la base de datos y los repositorios pasan **cifrados** (AES‑256‑GCM) de un servidor al otro, autenticados por un secreto de clúster del que el portal solo guarda la huella. Las cachés (Windows Update, APT…) no se replican: se reconstruyen. Durante una actualización, el control de salud no provoca ninguna conmutación.
- **Conmutación y supervisión**: la página muestra el papel de cada servidor, cuál lleva la IP virtual, la frescura de la replicación, el estado de keepalived y los últimos eventos. **Conmutar al de reserva** hace una conmutación voluntaria (mantenimiento programado); el de reserva permanece pasivo (en lectura) mientras no lleve la IP virtual.

Reservado al permiso **Configurar el servidor**. No disponible con Docker; `keepalived` va incluido en la appliance y se instala a petición en Debian/Ubuntu y Red Hat.

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
