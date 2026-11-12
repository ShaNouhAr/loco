# Serveur Windows natif

## Installer

Windows 11 Professionnel / Entreprise ou Windows Server 2022 / 2025, x64. Windows Server Core
et Desktop Experience utilisent le même installateur. Prévoir une adresse stable, un volume NTFS local
avec assez d'espace pour les caches et une connexion Internet pour télécharger les composants.
Lancer PowerShell 5.1 ou supérieur **en administrateur**, récupérer le projet (archive ZIP ou `git clone`)
puis lancer l'installateur du dossier `windows` :

```powershell
Invoke-WebRequest https://github.com/ShaNouhAr/loco/archive/refs/heads/main.zip -OutFile loco.zip
Expand-Archive loco.zip -DestinationPath .
cd loco-main
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\install.ps1
```

Les archives des composants proviennent de Python, nginx, WinSW et PyPI ; leurs versions
et SHA-256 sont fixés dans `windows/components.json` et vérifiés avant installation. Aucun autre logiciel
n'est installé sur le système : ni GnuPG, ni modification du `PATH`. Le Python embarqué
reste privé à l'application. Le service `loco` démarre automatiquement sous le compte virtuel
`NT SERVICE\loco` ; le portail est servi par Waitress et nginx. Les tâches de sauvegarde, de
renouvellement du certificat et de calcul des statistiques font partie du service.

Le script affiche l'adresse HTTPS, l'empreinte de l'autorité et le mot de passe initial `admin`, à changer
à la première connexion. Déployer `C:\ProgramData\loco\etc\tls\ca.crt` sur les postes pour utiliser
HTTPS, par exemple par GPO. L'autorité et les clés existantes sont conservées lors des relances.

Exemple avec des dossiers sur un autre volume et des réseaux explicites :

```powershell
.\windows\install.ps1 -HostAddress 10.20.0.15 `
  -Allow 10.20.0.0/16 -AdminAllow 10.20.99.0/24 `
  -DataPath D:\loco-data -BackupPath D:\loco-backups
```

Les dossiers de données et de sauvegarde doivent être locaux, distincts, non imbriqués et sans espaces.
Le programme est dans `%ProgramFiles%\loco` ; la configuration et l'état sont dans
`%ProgramData%\loco`. Les ACL donnent au service les droits nécessaires sur ses données,
ses journaux et ses certificats ; la configuration de l'installateur reste réservée à l'administrateur.

| Paramètre | Effet |
|---|---|
| `-HostAddress` | IPv4 ou nom DNS annoncé aux postes |
| `-Allow`, `-AdminAllow` | réseaux des postes et de l'interface ; réseaux privés par défaut |
| `-HttpsPort`, `-HttpPort`, `-NoHttp` | HTTPS 8443, amorçage HTTP 8088 ou HTTPS seul |
| `-Proxy http://proxy:3128`, `-NoProxy` | proxy de sortie ou suppression du proxy configuré |
| `-NoWU`, `-NoRpm`, `-NoFlatpak` | désactiver le cache Windows Update, les fonctions RPM ou le cache Flathub |
| `-WuCacheSize`, `-AptCacheSize`, `-RpmCacheSize`, `-FlatpakCacheSize` | plafond de chaque cache, par exemple `50g` |
| `-Resolvers 1.1.1.1,9.9.9.9` | DNS de sortie pour Windows Update ; ne doivent pas renvoyer le serveur local |
| `-MaxUpload 8g` | taille maximale d'un envoi |
| `-BackupKeep 3` | sauvegardes quotidiennes conservées ; `0` désactive la tâche quotidienne |

Les paramètres omis lors d'une relance reprennent la configuration existante. Les règles de pare-feu
du groupe `loco` sont limitées au programme nginx et aux réseaux configurés. Le portail applicatif
8089 reste en boucle locale, comme le port voisin 8090 du relais des sessions distantes. Le cache APT écoute sur 3142 ; Windows Update utilise 80 et 443.
L'installateur refuse les ports déjà occupés, notamment par IIS/HTTP.sys. Libérer ces ports ou utiliser
`-NoWU` si le cache Windows Update n'est pas nécessaire.

## Brancher les postes

Les commandes du portail sont communes aux serveurs Windows et Linux. Les dépôts APT sont organisés
par distribution et nom de version ; les dépôts RPM par version majeure `el8`, `el9`, `el10`.
Ajouter une version Debian/Ubuntu dans l'interface ne demande pas de modifier le programme.
Les nouveaux paquets sont indexés et signés par le programme : signatures OpenPGP (clé RSA 4096,
`%ProgramData%\loco\state\signing-key.json`, lisible par le seul service et les administrateurs),
vérifiées par les postes avec gpgv, sqv (apt de Debian 13) ou dnf comme pour un serveur Linux. Les signatures
des éditeurs sont conservées pour les contenus des caches. Le cache APT Windows est fourni par nginx à la place d'apt-cacher-ng.

Windows Update se branche par les réglages DNS proposés dans le portail. Les téléchargements HTTP
sont mis en cache ; le HTTPS est relayé au serveur Microsoft sans déchiffrement. Aucun rôle WSUS
n'est installé. Le catalogue de logiciels Windows reste disponible séparément.

Le cache Flathub fonctionne pour les postes Linux. La publication d'un **dépôt Flatpak local** demande
ostree et reste disponible uniquement lorsque le serveur loco tourne sous Linux ; l'interface
Windows affiche cette limite et ne propose pas d'import Flatpak local.

## Administrer, mettre à jour et restaurer

```powershell
$lr = "$env:ProgramFiles\loco\loco.cmd"
& $lr status
& $lr restart
& $lr user list
& $lr user add operateur viewer
& $lr user passwd admin
& $lr fingerprint
& $lr backup create
& $lr backup list
& $lr backup restore 'D:\loco-backups\loco-serveur-AAAAMMJJ-HHMMSS.tar.gz'
```

Exécuter ces commandes en administrateur. La restauration arrête le service, restaure les dépôts,
comptes et clés, puis redémarre le service. Les caches de téléchargement ne sont pas sauvegardés.
L'adresse, les chemins et les réseaux du serveur actuel sont gardés. Ces sauvegardes sont destinées
à une installation Windows ; elles ne constituent pas un format de migration entre Linux et Windows.
Copier régulièrement les archives sur un autre stockage, avec des droits adaptés : elles contiennent
les clés privées et les comptes.

Pour mettre à jour, récupérer les nouvelles sources puis relancer `windows\install.ps1` en administrateur.
Avant une mise à jour importante, créer une sauvegarde. Les composants sont téléchargés et vérifiés
avant l'arrêt du service. Consulter les journaux dans `%ProgramData%\loco\logs` et l'état dans
la page *Système* du portail.

```powershell
# Retire le service et le programme ; garde les données, comptes, clés et sauvegardes.
powershell -ExecutionPolicy Bypass -File "$env:ProgramFiles\loco\windows\remove.ps1"

# Effacement complet, avec confirmation.
powershell -ExecutionPolicy Bypass -File "$env:ProgramFiles\loco\windows\remove.ps1" -Purge
```

Windows 11 bloque par défaut l'exécution des scripts PowerShell : `-ExecutionPolicy Bypass` l'autorise
pour cette seule commande, sans changer la stratégie de la machine.

Après une désinstallation simple, relancer l'installateur depuis les sources retrouve les données.
`-Purge -Yes` permet un effacement sans interaction pour les déploiements automatisés.

## Dimensionnement et validation

nginx pour Windows utilise ici un seul worker et au maximum 1 000 connexions ; l'éditeur le décrit
comme une version bêta, adaptée à une charge limitée. Pour un parc important ou de forts débits
concurrents, privilégier le serveur Linux et mesurer la charge avant déploiement.
Voir les [limites officielles de nginx Windows](https://nginx.org/en/docs/windows.html).

La CI exécute l'installation native sur Windows Server 2022 et 2025, avec de vrais paquets construits
sur Linux : imports par HTTPS, contrôle des signatures, cache APT MISS/HIT, sauvegarde/restauration,
relance, réinstallation, désinstallation conservant les données et purge. Les lecteurs de paquets
Python sont également comparés aux outils Debian/RPM sur Linux, et les signatures OpenPGP vérifiées par
gpg, gpgv et sqv. Windows 11 (25H2) a été vérifié dans une machine de test distincte, avec de vrais postes :
Debian 13 (dépôt local HTTPS vérifié par sqv, paquets Debian via le cache APT) et Rocky Linux 9 (dépôt `el9`
vérifié par dnf, BaseOS via le cache RPM), ainsi qu'une mise à jour Windows de 88 Mo servie par le cache.

Migration depuis local-repo : [guide loco 2.0](migration-loco.md).
