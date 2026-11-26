[English](../development.md) · [Français](../fr/development.md) · **Español**

# Desarrollo

## Organización del repositorio

```text
portal/     portal web y scripts de los equipos, comunes a Linux y Windows
conf/       plantillas nginx comunes
linux/      instalador Linux (install.sh, remove.sh), herramientas de administración, Docker, contenedor LXC,
            Alpine (lib/alpine.sh) y fábrica de la ISO (iso/)
windows/    instalador Windows (install.ps1, remove.ps1), servicio Windows, componentes verificados
docs/       documentación (inglés; francés en docs/fr, español en docs/es)
tests/      pruebas unitarias y de extremo a extremo (VM Linux, Windows Server)
install.sh  atajo hacia linux/install.sh
```

**Idiomas**: interfaz en inglés, francés o español, a elección de cada cuenta (menú de la cuenta), si no según el
navegador. Traducciones: `portal/i18n/en.json` y `es.json` (texto francés -> idioma), verificadas por `tests/test_i18n.py`.
La documentación, el README y el historial de versiones se escriben en inglés, con sus versiones francesa y española
al lado (`*.fr.md`, `*.es.md`, `docs/fr/`, `docs/es/`) y enlaces al principio de cada página para cambiar de idioma.

## Pruebas

```bash
sudo apt-get install python3-flask apt-utils gnupg createrepo-c rpm
python3 -m unittest discover -s tests          # portal, repositorios APT y RPM, firmas reales
tests/e2e.sh                                   # de extremo a extremo, SOLO en una máquina desechable
tests/vm-e2e.sh debian-13                       # VM desechable; necesita QEMU, genisoimage, SSH y curl
tests/vm-rocky-server-e2e.sh rocky-8             # servidor Rocky 8 nativo, SELinux y cortafuegos activos
tests/vm-rpm-e2e.sh rocky-9                     # servidor Debian 13 + equipo Rocky 9, dos VM desechables
tests/vm-docker-e2e.sh                          # imagen Docker y docker compose en una VM Debian 13 desechable
```

### Integración continua y publicación

Todo funciona en las máquinas de GitHub: un fork no necesita ningún secreto, ningún servidor ni ningún ajuste
(basta con activar *Actions* en el fork).

| Workflow | Cuándo | Contenido | Duración |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml) | cada push y pull request | `shellcheck`, `pyflakes`, pruebas unitarias, sintaxis PowerShell — sin VM | ~3 min |
| [`integration.yml`](../../.github/workflows/integration.yml) | push que toca instaladores, agentes, ISO o pruebas de VM; a petición; antes de cada publicación | una VM QEMU por entrada de [`tests/platforms.json`](../../tests/platforms.json) (Debian 12/13, Ubuntu 22.04/24.04/26.04), servidor + equipo para [`tests/rpm-platforms.json`](../../tests/rpm-platforms.json) (Rocky y AlmaLinux 8/9/10), Docker, servidores nativos Rocky 8/9/10 y Alpine, ISO de la appliance (BIOS y UEFI), servidores Windows 2022/2025 y agente MSI | ~20 min |
| [`release.yml`](../../.github/workflows/release.yml) | push de una etiqueta `vX.Y.Z` | comprobaciones + integración completa, y luego publicación de las fuentes, la ISO y el agente de Windows probados en esa misma ejecución | ~25 min |

Publicar una versión: actualizar `VERSION` en `portal/loco/config.py` y el [historial de versiones](../../CHANGELOG.es.md) con sus traducciones
(`CHANGELOG.md` en inglés, `CHANGELOG.fr.md`: mismas versiones en el mismo orden, comprobado por las pruebas), y luego
`git tag -a v1.2.3 -m v1.2.3 && git push origin v1.2.3`. No se publica nada si una prueba falla.
Las imágenes de las VM se comprueban con su huella oficial antes de arrancar; los registros de cada prueba
se conservan 14 días. Las pruebas cubren la detección del equipo, el aislamiento de los
paquetes, Flatpak, una restauración y la desinstalación.

Historial: [CHANGELOG.es.md](../../CHANGELOG.es.md).

---

[← Volver a la presentación](../../README.es.md) · [Toda la documentación](README.md)
