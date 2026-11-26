**English** · [Français](fr/high-availability.md) · [Español](es/high-availability.md)

# High availability

Two loco servers, a single **virtual IP**: the **primary** serves the portal, the repositories and the agents; the **standby** replicates everything continuously and takes over the IP automatically if the primary goes down (keepalived / VRRP). Devices, the portal and the repositories only know the virtual IP, which does not change during a failover. In **Administration → Server → High availability**:

- **Setting up**: on the first server, **Enable** asks for a free virtual IP and shows a one-time **pairing code**; on a second, fresh server (same loco version), **Join** with the primary's address and that code copies the whole configuration and the repositories, then arms the failover. loco sets its own address to the virtual IP and extends its certificate to the virtual IP and to both servers.
- **Replication**: configuration, database and repositories travel **encrypted** (AES‑256‑GCM) from one server to the other, authenticated by a cluster secret of which the portal only keeps the fingerprint. Caches (Windows Update, APT…) are not replicated: they rebuild themselves. During an update, the health check does not trigger a failover.
- **Failover and monitoring**: the page shows the role of each server, which one holds the virtual IP, how fresh the replication is, the keepalived state and the latest events. **Fail over to the standby** performs a deliberate failover (planned maintenance); the standby stays passive (read-only) as long as it does not hold the virtual IP.

Requires the **Configure the server** right. Not available with Docker; `keepalived` is included in the appliance and installed on demand on Debian/Ubuntu and Red Hat.

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
