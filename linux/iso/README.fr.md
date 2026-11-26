[English](README.md) · **Français** · [Español](README.es.md)

# Appliance loco : Alpine Linux et BusyBox

L'ISO x86_64 installe Alpine 3.24 et loco sur disque. Elle démarre en BIOS ou
UEFI (Secure Boot désactivé). Les paquets et les sources sont sur le média :
l'installation fonctionne sans accès à Internet. Minimum : 2 Go de RAM et un
disque de 8 Gio ; prévoir davantage pour les dépôts et caches.

## Installation avec assistant

Télécharger `loco-VERSION-x86_64.iso` et `SHA256SUMS` dans les
[releases](https://github.com/ShaNouhAr/loco/releases), puis vérifier l'empreinte :

```sh
sha256sum --ignore-missing -c SHA256SUMS
```

1. Démarrer la machine ou la VM sur l'ISO. L'assistant visuel s'ouvre sur la
   console 7 (Alt+F7). `loco-installer` permet de le relancer depuis Alt+F1.
2. Choisir le clavier, le disque **entièrement effacé**, le nom, la carte réseau,
   DHCP ou adresse fixe, le fuseau et les mots de passe root et admin.
3. Choisir les réseaux autorisés et les fonctions : HTTP des clients,
   Windows Update, RPM, Flatpak. SSH est désactivé par défaut ; en l'activant,
   choisir **Mot de passe root** (aucune clé nécessaire) ou **Clé publique uniquement**.
4. Vérifier le récapitulatif, installer, retirer le média et redémarrer.

Le premier démarrage termine la configuration de loco, puis la console affiche
l'adresse du portail et l'empreinte SHA-256 de son autorité HTTPS. Comparer cette
empreinte avant de distribuer la CA aux postes. Les mots de passe saisis ne sont
pas affichés. Les arguments temporaires contenant le mot de passe admin sont
supprimés après une installation réussie.

L'assistant est une interface texte avec fenêtres, utilisable au clavier sur la
console locale ou la console d'une VM. L'administration courante dispose d'une
interface graphique web et de commandes CLI.

Dans l'assistant, **Échap** ou **Accueil** revient au menu de départ sans lancer
l'installation. La console root s'ouvre uniquement avec **Console de dépannage** ;
taper `exit` y ramène à l'assistant. **Alt+F1** ouvre une console de connexion
indépendante, **Alt+F7** retrouve l'assistant.

## Installation automatique et CLI

Copier [answers.example](answers.example), renseigner les deux mots de passe
et adapter les réglages. Une valeur par ligne, guillemets simples ou doubles
facultatifs : le fichier n'est jamais exécuté comme du shell.

- Mettre le fichier `loco-answers.conf` à la racine d'un volume nommé `LOCO`,
  puis démarrer sur l'ISO. Une pause de dix secondes permet d'annuler.
- Ou intégrer les réponses lors de la construction avec `--answers`.
- Ou, depuis la console live :

```sh
loco-installer --answers /chemin/reponses.conf --yes
```

`DISK=auto` exige exactement un disque fixe d'au moins 8 Gio. Avec plusieurs
disques, préciser le nom (par exemple `DISK=vda`). Le disque du média de démarrage
et celui des réponses sont exclus. Une réponse invalide ouvre l'assistant ;
avec `--yes`, elle provoque un échec avant tout effacement.

Conserver les réponses en mode 600 et retirer leur support après installation.
Une ISO construite avec des réponses contient leurs mots de passe : elle doit
rester privée. L'ISO publique n'embarque ni réponses ni clé SSH.

`SSH_MODE=password` active la connexion `ssh root@adresse-du-serveur` avec
`ROOT_PASSWORD`, sans `SSH_KEY`. `SSH_MODE=key` demande une clé dans `SSH_KEY` et
refuse les mots de passe SSH. `SSH_MODE=off` désactive SSH. Sans `SSH_MODE`, les
anciens fichiers gardent leur comportement : clé renseignée = SSH par clé,
sinon SSH désactivé. Après installation, `setup-loco` → **Accès SSH** permet de
changer le mode ; le mot de passe reste celui du compte root.

## Configuration et mises à jour

Dans le portail, **Administration → Configuration** règle les accès, le proxy
de sortie, les fonctions, les DNS du cache Windows Update, la taille des caches,
les envois et la conservation des sauvegardes. Les réseaux du portail doivent
inclure l'adresse du navigateur pour éviter de perdre l'accès.

**Administration → Mises à jour** affiche les versions disponibles, les paquets
Alpine, le besoin de redémarrage et le journal des opérations. L'agent vérifie
les versions chaque jour. Le portail s'exécute sous `loco` ; un service OpenRC
séparé, `loco-agent`, revalide les demandes avant de les appliquer en root.

Les mêmes opérations sont accessibles sur la console root :

```sh
setup-loco                       # assistant réseau, nom, mots de passe et SSH
loco-config show
loco-config set 'ALLOW=192.168.43.0/24' WU_CACHE_SIZE=50g BACKUP_KEEP=5
loco-update status
loco-update check
loco-update loco                  # dernière release stable, archive vérifiée par SHA-256
loco-update system                # apk upgrade, dans la branche Alpine installée
loco-update all
loco-update reboot
loco-backup create
```

Changer l'adresse ou le nom se fait avec `setup-loco` sur la console : la
connexion réseau peut être coupée et les postes doivent utiliser la nouvelle
adresse. Le certificat et les liens sont régénérés en conservant l'autorité.

Si une installation de loco ou un réglage échoue, l'agent restaure la
configuration et la base, puis relance les sources précédentes. Les sources des
deux versions sont conservées sous `/usr/local/src/`. Ce retour arrière couvre
l'application ; `apk upgrade` suit les transactions d'Alpine et ne restaure pas
automatiquement les anciens paquets système. Une montée vers une autre branche
Alpine reste une opération d'administration à préparer avec une sauvegarde.

Une seule opération privilégiée peut tourner à la fois (portail et CLI).
Les mises à jour nécessitent l'accès aux dépôts officiels Alpine et à GitHub,
directement ou par le proxy configuré.

Journaux : `/var/log/loco/`, `/var/lib/loco-agent/jobs/` et, au premier démarrage,
`/var/log/loco-firstboot.log`. Si celui-ci échoue, il sera repris au prochain
redémarrage ; son fichier d'arguments reste réservé à root.

## Construire l'ISO

Sur Alpine 3.24, installer les outils de construction puis utiliser un compte
non root membre du groupe `abuild`, autorisé à installer sa clé publique APK :

```sh
# en root :
apk add git xorriso squashfs-tools fakeroot abuild alpine-base mkinitfs \
    syslinux grub-bios grub-efi mtools dosfstools python3
addgroup mon-compte abuild
# reconnecter mon-compte, puis :
sh linux/iso/build.sh --outdir out
# options : --answers fichier-prive --aports clone-aports --source-archive loco-VERSION.tar.gz
```

Le build utilise les scripts officiels `mkimage` de la branche `3.24-stable`
d'aports et les dépôts officiels v3.24. Une clé de signature du modloop est
créée par `abuild-keygen` si `PACKAGER_PRIVKEY` n'est pas fournie ; cette clé
n'est jamais embarquée dans l'ISO. La vérification du média distribué repose
sur les empreintes de la release téléchargées en HTTPS. Secure Boot n'est pas
pris en charge.

Les sources embarquées proviennent uniquement des fichiers suivis par Git
(ajouter les nouveaux fichiers avec `git add` avant de construire). Les fichiers
locaux non suivis, notamment les réponses privées, sont exclus de l'archive
source. Depuis une extraction sans Git, fournir `--source-archive` avec
l'archive officielle correspondante.

La sortie est `out/loco-v3.24-x86_64.iso` et son `.sha256` ; la publication
renomme l'image avec la version de loco. Le runtime complet figure dans
[packages](packages), également utilisé par le profil et l'overlay. Le cache
APT utilise nginx, comme sous Windows ; OpenRC lance supervisord, le portail,
nginx, les tâches périodiques et l'agent d'administration.

## Validation et publication

```sh
tests/vm-alpine-server-e2e.sh       # serveur Alpine natif : paquets/signatures/cache, agent, rollback, retrait
tests/vm-alpine-iso-build.sh        # fabrique ISO, installe en BIOS puis UEFI
tests/vm-alpine-iso-e2e.sh out/loco-v3.24-x86_64.iso
```

Outils hôte : QEMU, `genisoimage`, `xorriso`, OVMF, SSH, Python et curl. KVM est
utilisé s'il est disponible, sinon QEMU utilise TCG. Les disques sont jetables.
Le test ISO éjecte le média, démarre sur le disque, vérifie le portail avec sa
CA, les commandes, les sauvegardes et un deuxième démarrage.

Le workflow **Release**, déclenché avec un tag existant, exige une CI réussie
pour ce commit. Il reprend exactement l'ISO testée dans l'artefact `loco-iso`,
crée l'archive source préfixée `loco-VERSION/`, calcule `SHA256SUMS` et publie
les trois fichiers. Aucune reconstruction de l'ISO n'a lieu à la publication.

---

[← Retour à la présentation](../../README.fr.md) · [Guide du serveur Linux](../../docs/fr/linux.md)
