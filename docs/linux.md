# Guide d'exploitation : serveur Linux

Ce guide couvre le déploiement de loco dans un réseau d'entreprise (lab, site, agence) et son
exploitation courante. Pour une présentation rapide, voir le [README](../README.md).

## 1. Prérequis

| Élément | Valeur |
|---|---|
| Système | Debian 12/13, Ubuntu 22.04/24.04/26.04, Rocky Linux 9/10 (amd64 vérifié, VM recommandée, voir § 10 pour LXC) |
| Ressources | 2 vCPU, 2 Go de RAM ; disque selon les caches (50 Go et plus conseillés) |
| Droits | root (installation), aucun conteneur Docker |
| Sortie Internet | HTTP/HTTPS vers les dépôts Debian/Ubuntu, Microsoft (Windows Update), `dl.flathub.org`, miroirs RPM (§ 12) ; directe ou par proxy (§ 5) |

Ports ouverts sur le serveur :

| Port | Service | Qui y accède |
|---|---|---|
| 8443/tcp | Interface (HTTPS) + dépôts, installeurs et scripts des postes | postes (`--allow`), administrateurs (`--admin-allow`) |
| 8088/tcp | Amorçage HTTP des postes (désactivable : `--no-http`) | postes |
| 3142/tcp | Cache APT (apt-cacher-ng) | postes Linux |
| 80/tcp, 443/tcp | Cache Windows Update (désactivable : `--no-wu`) | postes Windows (via le DNS) |

## 2. Installation

```bash
git clone https://github.com/ShaNouhAr/loco.git
cd loco
sudo ./install.sh --allow "10.20.0.0/16" --admin-allow "10.20.99.0/24"
```

L'installation affiche l'adresse de l'interface, l'empreinte de l'autorité de certification et un
**mot de passe initial** pour le compte `admin`, à changer à la première connexion. Il n'est écrit nulle part
sur le disque ; s'il est perdu : `sudo loco-user passwd admin`.

Toutes les options : `sudo ./install.sh --help`. Les réglages sont conservés dans
`/etc/loco/loco.conf` ; relancer `install.sh` (avec ou sans options) applique les changements
et met à jour le logiciel. La sortie détaillée d'apt est dans `/var/log/loco-install.log`.

## 3. Réseaux autorisés

- `--allow` : réseaux qui utilisent les dépôts et caches (apt-cacher-ng, cache Windows Update, Flathub,
  dépôts locaux, scripts). Par défaut : réseaux privés (RFC 1918, ULA IPv6).
- `--admin-allow` : réseaux qui ouvrent l'interface et `/metrics`. Par défaut : comme `--allow`.
  Recommandé : le seul réseau d'administration.

Le filtrage est appliqué par nginx (tous les ports web) et par apt-cacher-ng lui-même (tcpwrappers). Le serveur lui-même (127.0.0.1) est toujours autorisé. Un pare-feu réseau en amont reste conseillé.

## 4. Autorité de certification et HTTPS

L'installation crée une autorité locale (`/etc/loco/tls/ca.crt`, 10 ans) qui signe le certificat
du serveur (825 jours, renouvelé par `install.sh` s'il expire dans moins de 30 jours ou si l'IP change).
Les postes doivent faire confiance à cette autorité pour utiliser HTTPS.

**Par GPO (recommandé sur un domaine)** : Gestion des stratégies de groupe › Configuration ordinateur ›
Stratégies › Paramètres Windows › Paramètres de sécurité › Stratégies de clé publique ›
Autorités de certification racines de confiance › Importer `ca.crt`.

**À la main** : commandes de la page *Administration › Système* (Windows et Linux). Vérifier l'empreinte
SHA-256 affichée par l'installation avant d'importer.

**Linux, premier branchement sans l'autorité** : `curl -fsSL http://SERVEUR:8088/linux/setup.sh | sudo sh -s -- --trust-ca`
importe l'autorité (empreinte affichée, à comparer) puis configure le dépôt en HTTPS.

**Certificat d'entreprise** : `sudo ./install.sh --tls-cert serveur.crt --tls-key serveur.key`
(retour à l'autorité locale : supprimer `/etc/loco/tls/custom` puis relancer).

Une fois l'autorité déployée partout, `--no-http` ferme le port 8088.

## 5. Proxy d'entreprise

```bash
sudo ./install.sh --proxy http://proxy.entreprise:3128
```

apt-cacher-ng et les téléchargements du portail (logiciels Windows, paquets par lien) passent par le proxy.
Le **cache Windows Update** et le **cache Flathub** sortent en direct : autoriser le serveur à joindre
`*.windowsupdate.com`, `*.dl.delivery.mp.microsoft.com` et `dl.flathub.org` sur les ports 80 et 443.

Le cache Windows Update résout les noms avec `--resolvers` (par défaut 1.1.1.1 et 9.9.9.9). Si le DNS public
est bloqué, indiquer des résolveurs internes **qui ne redirigent pas** les domaines Windows Update vers ce
serveur (par exemple les redirecteurs de l'Active Directory), sinon le cache s'interrogerait lui-même.

## 6. Windows Update

Le DNS du réseau doit répondre l'IP du serveur pour `windowsupdate.com` et `dl.delivery.mp.microsoft.com`
(sous-domaines compris). Pour un DNS Active Directory, la page *Windows Update* fournit `loco-dns.ps1`
à lancer sur un contrôleur de domaine (`-Remove` pour revenir en arrière).

Seuls ces domaines sont relayés ; seuls les fichiers immuables (nom contenant une empreinte ou un identifiant
unique) sont gardés en cache, les listes de certificats et métadonnées passant toujours chez Microsoft.

## 7. Comptes et audit

- Rôles : **Administrateur** (tout) et **Lecture seule** (consultation, copie des commandes).
- Création dans *Administration › Comptes* : un mot de passe temporaire est affiché une seule fois et doit
  être changé à la première connexion (12 caractères minimum, sans le nom du compte).
- Une session inactive 8 h est fermée ; changer de mot de passe ferme les autres sessions du compte.
- Après 5 échecs sur un compte (ou 20 depuis une adresse) en 15 min, la connexion est bloquée 15 min.
- *Administration › Journal* : connexions, échecs, modifications, avec compte et adresse ; aussi dans
  journald (`journalctl -u loco-portal`).

En ligne de commande (root) : `loco-user list | add NOM [admin|viewer] | passwd NOM | enable NOM | disable NOM`.

## 8. Sauvegarde et restauration

Une sauvegarde quotidienne (03:30) est écrite dans `/var/backups/loco` ; les 3 plus récentes sont
gardées (`--backup-keep`, `--backup-dir`). Elle contient configuration, comptes, journal, clés (autorité,
GPG), dépôt APT, catalogue Windows et dépôt Flatpak, **sans les caches**. Les archives contiennent des clés
privées : les copier vers un stockage protégé.

```bash
sudo loco-backup create            # à la demande
sudo loco-backup list
```

**Restauration (même serveur ou nouveau serveur)** :

```bash
sudo ./install.sh                        # sur le nouveau serveur, même --data qu'avant
sudo loco-backup restore loco-ANCIEN-20261013-033000.tar.gz
sudo ./install.sh                        # régénère services et certificat serveur pour la nouvelle IP
```

La configuration propre au serveur (IP, ports, réseaux) est gardée ; l'autorité est restaurée, donc les
postes n'ont rien à refaire. Pensez à mettre à jour le DNS (Windows Update) si l'IP change.

## 9. Mise à jour, supervision, désinstallation

- **Mise à jour** : `git pull && sudo ./install.sh`. Les migrations de la base sont automatiques.
  Avant une mise à jour majeure du système ou de l'application, créez une sauvegarde et validez
  la cible sur une VM de test. La prise en charge d'une version future doit être vérifiée par les tests.
- **Système du serveur** (noyau, OpenSSL, Squid, nginx, Python…) : **Administration → Mises à jour** liste les
  paquets en attente, correctifs de sécurité en tête (dépôt `*-security` de Debian/Ubuntu, avis de sécurité DNF de
  Rocky), avec le noyau en cours et installé, les raisons d'un redémarrage et les services qui utilisent encore
  d'anciennes bibliothèques. *Installer les correctifs de sécurité* ou *Tout mettre à jour*, puis *Redémarrer le
  serveur* si besoin. Un agent root séparé (`loco-agent`) revalide et exécute les demandes : le portail reste sans
  privilèges. Les fichiers de configuration modifiés par loco sont conservés (`--force-confold`) et les services de
  loco touchés sont relancés. Vérification automatique chaque jour. En console : `loco-update status|check|security|
  system|reboot`. Droits : *Mettre à jour le serveur*, *Redémarrer le serveur*. Dans un conteneur LXC, le noyau est
  celui de l'hôte (à mettre à jour sur Proxmox).
- **Supervision** : `https://SERVEUR:8443/metrics` (format Prometheus, réseaux `--admin-allow`) : état des
  services, tailles des caches, disque, jours avant expiration du certificat, date de la dernière sauvegarde.
  Contrôle simple : `https://SERVEUR:8443/healthz` → `ok`.
- **Désinstallation** : `sudo /opt/loco/remove.sh` (garde les données) ou `--purge` (efface tout sauf
  les sauvegardes). Seuls les paquets installés par loco sont retirés.

## 10. Dépannage

| Symptôme | Piste |
|---|---|
| L'installation s'arrête | Le message indique la ligne en cause ; détails d'apt : `/var/log/loco-install.log` |
| « le port N est déjà utilisé » | Libérer le port ou changer `--https-port` / `--http-port` ; `--no-wu` libère 80/443 |
| Interface : 403 | L'adresse du poste n'est pas dans `--admin-allow` |
| Poste Linux : erreur de certificat | Autorité non importée : `setup.sh … --trust-ca` ou GPO |
| Windows : `irm` échoue en HTTPS | Autorité absente du magasin « Racines de confiance » de la machine |
| Logiciel Windows bloqué | Pas d'argument silencieux : le renseigner (souvent `/S`) ; délai : `$env:LOCO_TIMEOUT` |
| Poste Red Hat : « Dépôt elN indisponible » | Créer la version dans *Linux › Paquets*, ou `--no-local` |
| Poste Red Hat : paquet introuvable après `--remove` | Normal : les dépôts d'origine sont rétablis ; `dnf makecache` |
| Flatpak sur Ubuntu 24.04 **en conteneur LXC** : « Couldn't connect » | Le profil AppArmor `flatpak` d'Ubuntu bloque le réseau sous un noyau Proxmox : `ln -s /etc/apparmor.d/flatpak /etc/apparmor.d/disable/ && apparmor_parser -R /etc/apparmor.d/flatpak`, ou utiliser une VM |
| Journaux | `journalctl -u loco-portal -u nginx -u apt-cacher-ng`, `/var/log/nginx/loco-*.log` |

## 11. Dépôts APT et nouvelles versions de Linux

Dans **Linux › Paquets**, ajoutez un dépôt avec sa distribution (`debian` ou `ubuntu`), son nom
**de code exact** et, facultativement, le numéro de version affiché. Par exemple `debian/trixie` ou
`ubuntu/noble`. Le dépôt commence vide ; envoyez les `.deb` compatibles dans le dépôt sélectionné.
Les index signés et les fichiers sont indépendants sous `/srv/loco/apt/repositories/DISTRIBUTION/SUITE/`.
Les architectures sont celles des paquets (`amd64`, `arm64`, `all`…) et APT choisit celles du poste.

Pour ajouter une prochaine version, la même procédure suffit : aucun numéro de version n'est figé
par l'application. **Créer un dépôt ne certifie pas la compatibilité de l'application ni des paquets** :
validez les dépendances, l'installation et la restauration sur la distribution cible avant le déploiement.
Les noms de code proviennent des [versions Debian](https://www.debian.org/releases/) et des
[versions Ubuntu](https://ubuntu.com/project/docs/release-team/list-of-releases/).

En ligne de commande :

```bash
sudo loco-apt add debian trixie 13
sudo loco-apt add ubuntu noble 24.04
sudo loco-apt list
sudo loco-apt rebuild
```

Le script des postes détecte `ID` et `VERSION_CODENAME` dans `/etc/os-release`, puis télécharge une
source deb822 dédiée. Un dépôt absent provoque un arrêt avant de remplacer la source existante.
Pour une dérivée, choisissez explicitement sa base :

```bash
curl -fsSL https://SERVEUR:8443/linux/setup.sh | sudo sh -s -- --distribution ubuntu --suite noble
```

`--cache-only` configure le cache APT sans dépôt local. `--no-proxy` configure le dépôt sans cache.
Les dépôts officiels du poste restent configurés comme auparavant ; ceux servis en HTTPS sortent en direct.
Après une mise à niveau majeure du poste, relancez le script pour sélectionner son nouveau dépôt.
Lors d'un changement de dépôt, les `.deb` en cache sur le poste sont nettoyés pour éviter la réutilisation
d'un fichier provenant de l'ancienne version. Les paquets déjà installés ne sont pas réinstallés
automatiquement ; pour une compilation différente, utilisez une nouvelle version du paquet.

### Transition depuis le dépôt historique

La mise à jour 1.1.0 conserve `/apt/`, son contenu, la clé GPG et les configurations des postes déjà
branchés. Aucune compatibilité des anciens paquets n'est devinée. Utilisez *Copier vers un autre dépôt*
après validation ; l'original demeure disponible pour les autres postes. Relancez ensuite `setup.sh`
sur les postes concernés. `--legacy` garde explicitement le dépôt historique partagé.

La sauvegarde contient tous les dépôts et leur registre. La restauration, suivie de `install.sh`,
régénère leurs index et signatures. Deux fichiers ayant les mêmes nom/version/architecture peuvent
coexister dans des dépôts différents ; dans un même dépôt, un contenu différent exige une nouvelle
version du paquet.

### Entretien de la matrice de compatibilité

`tests/platforms.json` est la liste des systèmes vérifiés par le workflow : distribution, nom de code,
numéro, image officielle et manifeste d'empreintes. Une nouvelle version s'ajoute dans ce fichier ;
aucune liste dupliquée n'est à maintenir dans le workflow. `upgrade_from` désigne une ancienne version
réellement compatible avec le système, ou reste vide pour un système nouvellement pris en charge.
Les VM de test sont jetables et leurs journaux sont archivés. Le workflow actif contrôle
chaque push/PR, chaque lundi et chaque lancement manuel.

Pour reproduire un test, installez `qemu-system-x86`, `qemu-utils`, `genisoimage`, `curl`, `openssh-client`
et `git`, puis lancez `tests/vm-e2e.sh debian-13` depuis un clone avec les tags. KVM accélère le test
si `/dev/kvm` est accessible ; sinon QEMU utilise l'émulation. Les résultats sont dans `test-results/`.
Les images ne démarrent qu'après comparaison avec l'empreinte téléchargée en HTTPS du serveur officiel.

## 12. Famille Red Hat (RHEL, Rocky Linux, AlmaLinux)

**Cache des dépôts officiels.** Rocky Linux (`dl.rockylinux.org`), AlmaLinux (`repo.almalinux.org`) et EPEL
(`dl.fedoraproject.org`) sont relayés sous `/rpmcache/` avec vérification de leurs certificats. Les `.rpm`
et les métadonnées au nom unique sont gardés 6 mois (`--rpm-cache-size`), `repomd.xml` deux minutes.
Les postes vérifient chaque paquet avec les clés GPG de leur distribution, comme en accès direct. Ce cache
sort en direct, y compris avec `--proxy` : autoriser ces trois miroirs sur le port 443.

**Dépôts locaux.** Un dépôt par version majeure, commun à toute la famille : `el9` sert RHEL 9, Rocky
Linux 9 et AlmaLinux 9. `el8`, `el9` et `el10` sont créés à l'installation ; une future version s'ajoute
dans *Linux › Paquets* (« + Nouveau dépôt », Rocky, AlmaLinux, RHEL), sans modifier l'application. Les métadonnées
(`repomd.xml`) sont signées par la clé du dépôt et contrôlées par dnf (`repo_gpgcheck=1`) ; elles contiennent
l'empreinte de chaque paquet. Un paquet marqué pour une autre version (`.el8` envoyé dans `el9`) est refusé,
de même qu'un paquet de même nom/version au contenu différent.

**Postes.**

```bash
curl -fsSL https://SERVEUR:8443/rpm/setup.sh | sudo sh               # dépôt local + cache
curl -fsSL https://SERVEUR:8443/rpm/setup.sh | sudo sh -s -- --epel  # installe EPEL, via le cache
curl -fsSL http://SERVEUR:8088/rpm/setup.sh | sudo sh -s -- --trust-ca   # autorité pas encore importée
curl -fsSL https://SERVEUR:8443/rpm/setup.sh | sudo sh -s -- --remove    # retour à l'état d'origine
```

Le script lit `/etc/os-release`, choisit `elN` et vérifie qu'il existe avant de modifier le poste. Sur
Rocky Linux et AlmaLinux, il crée `/etc/yum.repos.d/loco-cache.repo` (BaseOS, AppStream,
CRB ou PowerTools, extras et EPEL via le cache, mêmes clés GPG) et désactive les dépôts équivalents
d'origine, notés dans `/etc/loco-rpm.state` ; `--remove` les rétablit à l'identique.
`--no-cache` n'ajoute que le dépôt local ; `--no-local`, que le cache.

**RHEL.** Les dépôts Red Hat sont réservés aux systèmes abonnés (certificats propres à chaque machine) :
ils restent en accès direct, ou passent par Red Hat Satellite le cas échéant. Sur RHEL, loco fournit
le dépôt local `elN` et EPEL via le cache (`--epel` installe `epel-release` ; activer aussi CodeReady Builder
avec `subscription-manager`). CentOS Stream et Oracle Linux sont traités comme RHEL.

**Validation.** `tests/vm-rpm-e2e.sh` démarre un serveur Debian 13 et un poste par entrée de
`tests/rpm-platforms.json` (Rocky Linux et AlmaLinux 8, 9 et 10), installe depuis le dépôt local, depuis
le cache et depuis EPEL, contrôle le service par le cache, puis vérifie le retour exact à l'état d'origine.
Une nouvelle version de Rocky Linux ou d'AlmaLinux s'ajoute dans ce fichier (image cloud officielle et
manifeste d'empreintes).


## 13. Déploiement Docker

L'image reprend le même code et la même configuration que l'installation native : à chaque démarrage,
`linux/docker/entrypoint.sh` traduit les variables de `.env` en options de `linux/install.sh --container`, qui régénère
la configuration (nginx, apt-cacher-ng, portail), puis `supervisord` lance les services.

```bash
cd linux/docker                 # docker-compose.yml, Dockerfile et .env.example
cp .env.example .env            # au minimum LOCO_HOST, l'adresse annoncée aux postes
sudo ./up.sh
docker compose logs loco  # adresse, empreinte de l'autorité, mot de passe initial (une seule fois)
```

**Variables.** Elles correspondent aux options d'`install.sh` (`LOCO_ALLOW` ↔ `--allow`, `LOCO_PROXY` ↔ `--proxy`,
`LOCO_WU=0` ↔ `--no-wu`, `LOCO_HTTP=0` ↔ `--no-http`…, liste commentée dans `.env.example`) et font foi : une
variable retirée revient à sa valeur par défaut au démarrage suivant (`docker compose up -d`). Seules les
tailles de cache non renseignées sont calculées au premier démarrage puis conservées. Certificat
d'entreprise : monter le dossier (ligne commentée de `docker-compose.yml`) et renseigner `LOCO_TLS_CERT` et
`LOCO_TLS_KEY` ; pour revenir à l'autorité locale, supprimer `/etc/loco/tls/custom` dans le conteneur.

**Réseau.** Le conteneur utilise le réseau de l'hôte (`network_mode: host`) : le cache Windows Update écoute
sur 80 et 443, et le filtrage par réseau voit l'adresse réelle des postes. Ports à ouvrir et prérequis
réseau : identiques à l'installation native (sections 1 et 3). Au démarrage, un port déjà pris sur l'hôte
arrête le conteneur avec un message explicite (`docker compose logs`).

**Données.** Volumes nommés : `loco-data` (`/srv/loco`, dépôts et caches, le plus volumineux),
`loco-state` (base, clé de signature), `loco-etc` (configuration, autorité, certificats),
`loco-apt-cache`, `loco-logs` (journaux nginx, pour les statistiques Windows Update) et
`loco-backups`. `LOCO_DATA_DIR` et `LOCO_BACKUP_DIR` les remplacent par des dossiers de l'hôte. Les comptes
`loco` (990) et `apt-cacher-ng` (991) ont des identifiants fixes : les volumes restent valides d'une
image à l'autre.

**Exploitation.**

```bash
docker compose exec loco loco-user passwd admin       # accès perdu
docker compose exec loco loco-backup create           # sauvegarde immédiate
docker compose exec loco loco-backup restore /var/backups/loco/ARCHIVE
docker compose restart loco                                  # après une restauration
git pull && sudo ./up.sh                           # mise à jour
docker compose down                                                # arrêt (données conservées)
```

Les tâches des minuteries systemd sont faites par `loco-cron` : taille des caches toutes les
15 minutes, rechargement de nginx quand la liste des domaines Windows Update change, rotation des journaux
puis sauvegarde (si `LOCO_BACKUP_KEEP` > 0) chaque jour à 03:30, heure de `TZ`. Journaux des services :
`docker compose logs`. État : `/healthz` (contrôle de santé Docker), page *Système* et `/metrics`.

**Sécurité.** Le conteneur démarre sans aucune capacité Linux, sauf celles nécessaires aux processus root
(changement de propriétaire et d'utilisateur, ports 80/443), avec `no-new-privileges`. Le portail, nginx
(processus de travail) et apt-cacher-ng tournent sous leurs comptes dédiés, comme en natif. En revanche le
confinement systemd du portail (système de fichiers en lecture seule, filtrage des appels système) n'existe
pas dans le conteneur : l'installation native reste recommandée lorsque c'est possible.

**Validation.** `tests/vm-docker-e2e.sh` installe Docker (paquets Debian) dans une VM Debian 13 jetable,
construit l'image, branche la VM elle-même comme poste (dépôt local et cache), vérifie les services, la
sauvegarde, la conservation des données et de l'autorité quand le conteneur est recréé, et l'application
des variables (Windows Update et HTTP désactivés puis rétablis).

## 14. Conteneur LXC Proxmox VE

`linux/lxc/proxmox-lxc.sh` se lance en root sur l'hôte Proxmox VE (7, 8 ou 9, amd64), seul ou depuis une copie
du projet. Il crée un conteneur **non privilégié** (`nesting=1`, démarré avec l'hôte), attend le réseau, puis
fait l'installation native d'`install.sh` dans le conteneur : mêmes services systemd, mêmes options, même
portail que sur une machine Debian.

```bash
curl -fsSLO https://raw.githubusercontent.com/ShaNouhAr/loco/main/linux/lxc/proxmox-lxc.sh
bash proxmox-lxc.sh --help
bash proxmox-lxc.sh --hostname loco --ip 10.20.0.15/24 --gw 10.20.0.1 --vlan 20 --disk 400 \
    -- --allow "10.20.0.0/16" --admin-allow "10.20.99.0/24" --proxy http://proxy.exemple.lan:3128
```

**Choix automatiques.** Numéro libre suivant, stockage du disque `local-lvm`, puis `local-zfs`, puis le
premier stockage actif acceptant les conteneurs ; modèles sur `local` ou le premier stockage compatible ;
pont `vmbr0` ou le premier pont `vmbr*` ; modèle Debian 13 (Debian 12 sur Proxmox 7 ou si Debian 13 n'est
pas proposé), téléchargé par `pveam` s'il manque, avec contrôle d'empreinte. Chaque choix se force par une
option (`--storage`, `--template-storage`, `--bridge`, `--ctid`…).

**Version installée.** Lancé seul, le script installe la dernière version publiée sur GitHub (archive
téléchargée par le conteneur en HTTPS) ; `--version v1.4.0` fixe une version, `--source DIR` installe une
copie locale du projet (hôte sans accès à GitHub, version de test). Lancé depuis une copie du projet, il
installe cette copie.

**Disque.** Dépôts et caches sont dans le disque du conteneur (`--disk`, 100 Go par défaut) ; les tailles
maximales des caches sont calculées à l'installation d'après l'espace libre. Pour agrandir ensuite :
`pct resize N rootfs +200G`, puis ajuster les tailles avec `install.sh` (options `--wu-cache-size`…).

**Exploitation depuis l'hôte.**

```bash
pct exec N -- loco-user passwd admin              # accès perdu
pct exec N -- loco-backup create                  # sauvegarde immédiate
bash proxmox-lxc.sh --ctid N --update                   # mise à jour (dernière version publiée)
bash proxmox-lxc.sh --ctid N --update -- --no-wu        # mise à jour et changement d'option
pct destroy N --purge                                   # suppression complète
```

Les sauvegardes Proxmox (`vzdump`) du conteneur s'ajoutent à celles de loco. Les caches se
reconstituent seuls : les exclure évite de sauvegarder des dizaines de gigaoctets, par exemple
`vzdump N --exclude-path /srv/loco/wucache --exclude-path /srv/loco/flatpakcache
--exclude-path /srv/loco/rpmcache --exclude-path /var/cache/apt-cacher-ng`.

**Particularités.** Le pare-feu Proxmox est activé sur l'interface (`firewall=1`) mais n'a d'effet que s'il
est activé pour le conteneur : ouvrir alors les ports de la section 1. Le filtrage d'apt-cacher-ng repose
sur tcpwrappers, qui fonctionne en conteneur. Sur un hôte dont AppArmor bloque Flatpak dans les conteneurs,
installer avec `-- --no-flatpak`.

**Validation.** Testé sur Proxmox VE 9.2 : création en DHCP depuis le script seul (téléchargement de la
version publiée), mise à jour `--update` depuis une copie locale, puis test de bout en bout complet
(`tests/e2e.sh` : postes, Flatpak, sauvegarde, restauration, désinstallation) dans le conteneur.


## 14. Serveur natif sur Rocky Linux

Le même `sudo ./install.sh` prend en charge Rocky Linux 9 et 10 (amd64 validé). Rocky 8 reste un
poste client compatible, mais n’est pas une plateforme serveur prise en charge. L’installateur utilise
DNF, active CRB et installe EPEL si nécessaire. Le portail dispose d’un environnement Python isolé,
avec dépendances et empreintes SHA-256 figées dans `lib/requirements-rocky.txt` (Python 3.11 sur Rocky 9).
Les bases, clés, chemins, options et commandes d’administration sont les mêmes que sur Debian.

SELinux reste en mode strict : contextes des fichiers et caches, ports HTTP et sorties de nginx sont
configurés. Si firewalld est actif, le service `loco` ouvre les seuls ports des fonctions activées,
dans les zones actives. Les ACL nginx continuent de limiter les réseaux clients et administrateurs.
Le backend apt-cacher-ng écoute uniquement sur `127.0.0.1:23142` ; nginx filtre les connexions au port
3142. Le port 23142 ne doit jamais être exposé.

Sur Rocky 10, apt-cacher-ng n’étant pas disponible dans EPEL, la source Debian 3.7.5-1.3 et ses
correctifs sont téléchargés avec des empreintes fixées puis compilés dans `/usr/local/lib/loco/acng`.
Une relance réutilise cette compilation. Un timer assure l’expiration quotidienne du cache et logrotate conserve sept journaux. Les versions des sources et dépendances doivent être mises
à jour lors des prochaines releases du projet ; elles ne suivent pas automatiquement DNF.

Les métadonnées de désinstallation sont réservées à root dans `/etc/loco/install-state` et restent propres au serveur lors d’une restauration.

`sudo ./remove.sh` retire les paquets ajoutés par le projet et restitue les réglages nginx, SELinux et
firewalld qu’il a ajoutés. Les paquets déjà présents restent installés. Les données et sauvegardes
restent conservées, sauf demande de purge des données. Les modifications apportées manuellement à
`nginx.conf` après installation sont conservées lors de la désinstallation.

Validation : `tests/vm-rocky-server-e2e.sh rocky-9 rocky-10`, également lancé par la CI. Les VM officielles
sont vérifiées par empreinte ; les tests couvrent SELinux actif, firewalld, import APT/RPM, installation
DNF d’un paquet local, cache APT, refus d’un client hors ACL, relance, sauvegarde/restauration et purge.

Migration depuis local-repo : [guide loco 2.0](migration-loco.md).

## 15. Appliance ISO (Alpine Linux, BusyBox)

L'ISO x86_64 installe Alpine 3.24 et loco en une seule procédure, avec un
assistant visuel ou un fichier de réponses. Disque, DHCP ou adresse fixe,
clavier, mots de passe root/admin, réseaux autorisés et fonctions sont choisis
avant l'installation. Le disque est effacé après confirmation. Les paquets
sont embarqués : l'installation fonctionne sans Internet.

Après éjection de l'ISO et redémarrage, la console affiche le portail et
l'empreinte de la CA. `setup-loco` change le réseau, le nom, les mots de passe
et l'accès SSH. Dans le portail, **Administration → Configuration** et
**Mises à jour** disposent des équivalents CLI `loco-config` et `loco-update`.
Les opérations sont revalidées par un agent root séparé ; le portail reste
sans privilèges. Les installations de loco et réglages en échec disposent d'un
retour arrière automatique de la configuration et de la base. Les mises à jour
système suivent `apk upgrade` dans la branche Alpine installée.

Le cache APT passe par nginx ; OpenRC et supervisord remplacent systemd.
Minimum : 2 Go de RAM, disque de 8 Gio, BIOS ou UEFI sans Secure Boot.
Les détails, le fichier de réponses, la construction et les tests BIOS/UEFI
sont dans le [guide de l'appliance](../linux/iso/README.md).
