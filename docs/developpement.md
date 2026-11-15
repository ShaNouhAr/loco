# Développement

## Organisation du dépôt

```text
portal/     portail web et scripts des postes, communs à Linux et Windows
conf/       gabarits nginx communs
linux/      installateur Linux (install.sh, remove.sh), outils d'administration, Docker, conteneur LXC,
            Alpine (lib/alpine.sh) et fabrique ISO (iso/)
windows/    installateur Windows (install.ps1, remove.ps1), service Windows, composants vérifiés
docs/       guides Linux et Windows
tests/      tests unitaires et de bout en bout (VM Linux, Windows Server)
install.sh  raccourci vers linux/install.sh
```


**Langues** : interface en français, en anglais ou en espagnol, au choix de chaque compte (menu du compte), sinon selon le
navigateur. Traductions : `portal/i18n/en.json` et `es.json` (texte français -> langue), vérifiées par `tests/test_i18n.py`.

## Tests

```bash
sudo apt-get install python3-flask apt-utils gnupg createrepo-c rpm
python3 -m unittest discover -s tests          # portail, dépôts APT et RPM, signatures réelles
tests/e2e.sh                                   # bout en bout, sur une machine JETABLE uniquement
tests/vm-e2e.sh debian-13                       # VM jetable ; nécessite QEMU, genisoimage, SSH et curl
tests/vm-rocky-server-e2e.sh rocky-8             # serveur Rocky 8 natif, SELinux et pare-feu actifs
tests/vm-rpm-e2e.sh rocky-9                     # serveur Debian 13 + poste Rocky 9, deux VM jetables
tests/vm-docker-e2e.sh                          # image Docker et docker compose dans une VM Debian 13 jetable
```

### Intégration continue et publication

Tout tourne sur les machines de GitHub : un fork n'a besoin d'aucun secret, d'aucun serveur ni d'aucun réglage
(il suffit d'activer *Actions* sur le fork).

| Workflow | Quand | Contenu | Durée |
|---|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | chaque push et pull request | `shellcheck`, `pyflakes`, tests unitaires, syntaxe PowerShell — sans VM | ~3 min |
| [`integration.yml`](../.github/workflows/integration.yml) | push touchant installeurs, agents, ISO ou tests VM ; à la demande ; avant chaque publication | une VM QEMU par entrée de [`tests/platforms.json`](../tests/platforms.json) (Debian 12/13, Ubuntu 22.04/24.04/26.04), serveur + poste pour [`tests/rpm-platforms.json`](../tests/rpm-platforms.json) (Rocky et AlmaLinux 8/9/10), Docker, serveurs natifs Rocky 8/9/10 et Alpine, ISO de l'appliance (BIOS et UEFI), serveurs Windows 2022/2025 et agent MSI | ~20 min |
| [`release.yml`](../.github/workflows/release.yml) | push d'un tag `vX.Y.Z` | vérifications + intégration complète, puis publication des sources, de l'ISO et de l'agent Windows testés dans ce même run | ~25 min |

Publier une version : mettre à jour `VERSION` dans `portal/loco/config.py` et le [CHANGELOG](../CHANGELOG.md), puis
`git tag -a v1.2.3 -m v1.2.3 && git push origin v1.2.3`. Rien n'est publié si un test échoue.
Les images des VM sont vérifiées par leur empreinte officielle avant le démarrage ; les journaux de chaque test
sont conservés 14 jours. Les tests couvrent la mise à jour depuis 1.9.0, la détection du poste, l'isolation des
paquets, Flatpak, une restauration et la désinstallation.

Historique : [CHANGELOG.md](../CHANGELOG.md).

---

[← Retour à la présentation](../README.md) · [Toute la documentation](README.md)
