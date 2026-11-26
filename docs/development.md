**English** · [Français](fr/development.md) · [Español](es/development.md)

# Development

## Repository layout

```text
portal/     web portal and device scripts, shared by Linux and Windows
conf/       shared nginx templates
linux/      Linux installer (install.sh, remove.sh), administration tools, Docker, LXC container,
            Alpine (lib/alpine.sh) and ISO factory (iso/)
windows/    Windows installer (install.ps1, remove.ps1), Windows service, verified components
docs/       documentation (English; French in docs/fr, Spanish in docs/es)
tests/      unit and end-to-end tests (Linux VMs, Windows Server)
install.sh  shortcut to linux/install.sh
```

**Languages**: the interface is in English, French or Spanish, as each account chooses (account menu), otherwise according to the
browser. Translations: `portal/i18n/en.json` and `es.json` (French text -> language), checked by `tests/test_i18n.py`.
The documentation, the README and the changelog are written in English, with French and Spanish versions next to them
(`*.fr.md`, `*.es.md`, `docs/fr/`, `docs/es/`) and links at the top of each page to switch language.

## Tests

```bash
sudo apt-get install python3-flask apt-utils gnupg createrepo-c rpm
python3 -m unittest discover -s tests          # portal, APT and RPM repositories, real signatures
tests/e2e.sh                                   # end to end, on a DISPOSABLE machine only
tests/vm-e2e.sh debian-13                       # disposable VM; needs QEMU, genisoimage, SSH and curl
tests/vm-rocky-server-e2e.sh rocky-8             # native Rocky 8 server, SELinux and firewall on
tests/vm-rpm-e2e.sh rocky-9                     # Debian 13 server + Rocky 9 device, two disposable VMs
tests/vm-docker-e2e.sh                          # Docker image and docker compose in a disposable Debian 13 VM
```

### Continuous integration and releases

Everything runs on GitHub's machines: a fork needs no secret, no server and no setting
(just enable *Actions* on the fork).

| Workflow | When | Contents | Duration |
|---|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | every push and pull request | `shellcheck`, `pyflakes`, unit tests, PowerShell syntax — no VM | ~3 min |
| [`integration.yml`](../.github/workflows/integration.yml) | pushes touching installers, agents, ISO or VM tests; on demand; before each release | one QEMU VM per entry of [`tests/platforms.json`](../tests/platforms.json) (Debian 12/13, Ubuntu 22.04/24.04/26.04), server + device for [`tests/rpm-platforms.json`](../tests/rpm-platforms.json) (Rocky and AlmaLinux 8/9/10), Docker, native Rocky 8/9/10 and Alpine servers, appliance ISO (BIOS and UEFI), Windows 2022/2025 servers and MSI agent | ~20 min |
| [`release.yml`](../.github/workflows/release.yml) | push of a `vX.Y.Z` tag | checks + full integration, then release of the sources, the ISO and the Windows agent tested in that same run | ~25 min |

Releasing a version: update `VERSION` in `portal/loco/config.py` and the [CHANGELOG](../CHANGELOG.md) with its translations
(`CHANGELOG.fr.md`, `CHANGELOG.es.md`: same versions in the same order, checked by the tests), then
`git tag -a v1.2.3 -m v1.2.3 && git push origin v1.2.3`. Nothing is released if a test fails.
VM images are checked against their official fingerprint before booting; each test's logs
are kept for 14 days. The tests cover device detection, package
isolation, Flatpak, a restore and the uninstallation.

History: [CHANGELOG.md](../CHANGELOG.md).

---

[← Back to the overview](../README.md) · [All the documentation](README.md)
