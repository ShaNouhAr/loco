**English** · [Français](SECURITY.fr.md) · [Español](SECURITY.es.md)

# Security

## Reporting a vulnerability

Open a private advisory on GitHub (*Security › Report a vulnerability* tab) rather than a public issue.
Please give the version (`loco` at the bottom of the sign-in page), the distribution and the steps
to reproduce.

## Threat model

loco is designed for an **internal enterprise network**. It is not meant to be exposed on the Internet.

| Component | Measures |
|---|---|
| Administration interface | HTTPS only (TLS 1.2+), named accounts with roles, hashed passwords (scrypt, or PBKDF2 with the Werkzeug versions of older distributions), length policy, forced change of temporary passwords, lockout after failures (per account and per address), TOTP two-factor authentication with backup codes (a code only opens one session: it cannot be replayed), WebAuthn passkeys (user verification by the device required, single-use challenges kept by the server, origin and identity tied to the portal's address, signature counter checked, only the public key is kept), sign-in possible through a directory (LDAP, RADIUS) or OpenID Connect SSO, sessions known to the server (only the fingerprint of their identifier is kept): closed by signing out — a cookie copied before is worth nothing any more —, after 8 h of inactivity, after 7 days in any case, and when the password changes, CSRF token, `Secure`/`HttpOnly`/`SameSite=Strict` cookie, strict CSP headers (no inline script or style), `X-Frame-Options: DENY`, `nosniff`, dedicated allowed networks (`--admin-allow`), audit log |
| Local APT repositories | GPG-signed indexes (RSA 4096), key generated on the server; packages and indexes isolated by distribution/version; repository names validated; a given package/version cannot be replaced by a different content |
| Local RPM repositories | `repomd.xml` GPG-signed and checked by dnf (`repo_gpgcheck=1`); it contains the SHA-256 fingerprint of each package |
| RPM cache (Rocky Linux, AlmaLinux, EPEL) | Mirrors' certificates checked; packages checked by the devices with their distribution's GPG keys (`gpgcheck=1`) |
| Local Flatpak repository | GPG-signed commits and summary; Flathub stays verified with its own key |
| Windows software | SHA-256 of each installer checked by the script before running; recommended commands over HTTPS |
| Windows Update cache | Only relays the domains of its list (HTTP and SNI); HTTPS relayed without decryption; only immutable files are cached |
| apt-cacher-ng | Clients filtered (tcpwrappers on Debian/Ubuntu; nginx TCP relay with ACL and loopback backend on Rocky), maintenance pages protected by a password |
| Portal (process) | Dedicated user without a shell, confined systemd unit (read-only system, no capabilities, filtered system calls, `systemd-analyze security` ≈ 1.5); only nginx exposed |
| Secrets | Portal passwords hashed; apt-cacher-ng maintenance credentials in restricted-access files (0600 for the secret, 0640 root/apt-cacher-ng for its configuration); private keys in 0600; backups in 0640 `root:loco`, so that the portal can offer them for download and send them off-site: an archive contains the database, the server's private keys and the local authority's, and must therefore be protected like the server itself; devices' enrollment key: never written to the access log, and removed from the device once it is enrolled (`.deb`/`.rpm` packages, Windows registry) |

## Known limits

- The `.rpm` files of the local repositories are not re-signed one by one: their integrity relies on the
  signed metadata (`repo_gpgcheck=1`), not on a per-package signature (`gpgcheck=0`).
- The RHEL repositories (Red Hat subscription) do not go through the cache.
- Docker deployment: the processes keep their dedicated accounts and the container only has the useful
  capabilities, but the portal's systemd confinement is not available there; the native installation is more
  compartmentalized. The container uses the host's network.

- The HTTP bootstrap port (8088) serves scripts and installers without encryption: an attacker on the network
  could modify a script. Deploy the certificate authority, then use `--no-http`.
- `setup.sh --trust-ca` trusts the authority received at the first contact (compare the fingerprint shown).
- loco's two-factor authentication only applies to sign-in with a local password. A sign-in through a directory or
  through SSO follows the rules of the directory or the identity provider.
- A passkey replaces the password and the second factor: the person is verified by
  the device (fingerprint, face, code), not by loco. No manufacturer attestation is requested: loco does not restrict
  the device models or the password managers that sync passkeys. Adding a passkey requires the
  account's password; whoever knows this password (and the second factor) can therefore add one: the audit log records
  each addition, removal and sign-in with a passkey.
- The devices access the repositories without authentication; the restriction is by network (`--allow`).
- Syslog forwarding: over TCP and UDP, the logs travel in clear text; prefer TLS. The content of the devices' logs stays declarative
  (a compromised device can write what it wants in its own lines): loco enforces the hostname and removes line breaks and
  control characters, so that a device can neither pass itself off as another nor forge a line. No client certificate (mTLS).
- APT isolation avoids mixing repositories; it does not prove that a `.deb` is compatible with
  its destination. The administrator must validate the dependencies and the origin of the packages before importing
  or copying them. The legacy repository and `setup.sh --legacy` stay shared for the transition.
