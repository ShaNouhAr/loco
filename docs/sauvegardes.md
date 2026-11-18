# Sauvegardes

La page **Administration › Serveur › Sauvegardes** réunit ce qui est sauvegardé — loco lui-même, vos Home
Assistant, vos machines Proxmox —, où, et quand. Elle a un onglet par source, et un pour la **destination
externe** : le NAS, le stockage S3 ou le serveur SFTP où loco dépose les copies.

Home Assistant et Proxmox VE sont des fonctionnalités **facultatives**, coupées par défaut : activez-les dans
**Administration › Fonctionnalités**, section *Facultatif*.

## Destination externe

Un stockage hors du serveur loco, la seule copie qui survit s'il est perdu :

- **Dossier / point de montage** — un 2ᵉ disque, ou un partage SMB/NFS de votre NAS monté par le système ; le
  compte `loco` doit pouvoir y écrire ;
- **S3 / Cloud** — tout stockage compatible S3 (AWS, MinIO, Backblaze B2, Wasabi…) ;
- **SFTP** — par clé SSH.

Avec une **phrase de passe**, tout ce qui part est chiffré en AES-256 (`openssl enc -aes-256-cbc -pbkdf2`) ;
gardez-la, elle est nécessaire pour restaurer. *Tester* dépose puis supprime un petit fichier.

On choisit ce qui y part : les sauvegardes de loco (*Copier aussi les sauvegardes de loco*, cochée par défaut),
et chaque Home Assistant séparément (voir plus bas). Les machines Proxmox n'y passent pas : Proxmox écrit
lui-même ses sauvegardes sur l'un de ses stockages — pour qu'elles aillent sur le même NAS, déclarez-le dans
Proxmox (*Déclarer un NAS…*).

Sur la destination, les noms ne se mélangent pas : `loco-<serveur>-<date>.tar.gz[.enc]` pour loco,
`homeassistant-<n°>-<date>-<sauvegarde>.tar[.enc]` pour Home Assistant. La conservation de l'un ne touche
jamais les fichiers de l'autre.

## loco

L'onglet résume les sauvegardes quotidiennes du serveur (la dernière, le nombre gardé, l'état de la copie
externe). Télécharger, restaurer et régler leur nombre se fait dans **Système › Sauvegardes**, comme avant.

## Home Assistant

Pour chaque instance reliée (voir [Home Assistant](homeassistant.md)) :

- **Copier maintenant** copie sa sauvegarde la plus récente — celles gardées dans Home Assistant même, pas sur
  un stockage réseau de Home Assistant. Cochez *créer d'abord une sauvegarde complète* pour en faire une juste
  avant ;
- **Planifier…** fait la même chose une fois, chaque jour, chaque semaine ou chaque mois, à l'heure du serveur
  loco ;
- **Copies gardées** (3 par défaut) : au-delà, les plus anciennes de cette instance sont supprimées de la
  destination à la copie suivante.

La sauvegarde est téléchargée par la liaison SSH, depuis l'API du Supervisor (le jeton du module passe par
l'entrée de `curl`, jamais en argument d'une commande ; sans `curl`, l'archive est retrouvée dans `/backup`).
Elle est vérifiée à la taille près, chiffrée si une phrase de passe est donnée, déposée, puis effacée du disque
de loco. Une sauvegarde déjà copiée ne repart pas. Une sauvegarde chiffrée par Home Assistant (clé de secours)
est copiée telle quelle : sa clé reste nécessaire pour la restaurer.

Droit : *Sauvegarder Home Assistant*.

## Proxmox VE

### Relier un serveur

loco passe par l'API de Proxmox avec un **jeton** créé pour lui. Dans le shell d'un nœud :

```sh
pveum role add LocoBackup --privs "VM.Audit VM.Backup Datastore.Audit Datastore.AllocateSpace Sys.Audit"
pveum user add loco@pve --comment loco
pveum acl modify / --users loco@pve --roles LocoBackup
pveum user token add loco@pve loco --privsep 0
```

La dernière commande affiche le secret du jeton, une seule fois. Dans loco : adresse (`192.168.1.10`, avec
`:port` s'il n'est pas 8006), identifiant `loco@pve!loco`, secret. Le secret est gardé chiffré et n'est plus
jamais affiché.

loco relève alors le **certificat** de Proxmox et en montre l'empreinte SHA-256 : comparez-la à celle de
Proxmox (le nœud › Système › Certificats, ou la commande `openssl` proposée) avant de confirmer. Rien n'est
envoyé avant, et le jeton ne part ensuite que vers ce certificat-là : s'il change (renouvelé), tout s'arrête
jusqu'à **Plus › Relever de nouveau le certificat…** et une nouvelle confirmation.

Il manque un droit au jeton ? La page le dit.

### Sauvegarder

La page liste les machines virtuelles et les conteneurs, avec leur dernière sauvegarde. Cochez, choisissez :

- le **stockage de Proxmox** — ceux qui acceptent les sauvegardes (*VZDump backup file*) : disque local, NAS
  déclaré dans Proxmox, Proxmox Backup Server ;
- le **mode** — *Instantané, sans arrêt* (par défaut), *Suspension* ou *Arrêt puis redémarrage*.

Puis **Sauvegarder maintenant**, ou **Planifier…** : une fois, chaque jour, chaque semaine ou chaque mois ; les
machines cochées, ou *toutes, y compris les futures* (hors modèles) ; et le nombre de **sauvegardes gardées par
machine**.

Chaque machine est une tâche Proxmox (`vzdump`, compression zstd), lancée l'une après l'autre et suivie jusqu'à
sa fin, avec son journal ; *Annuler* l'arrête sur Proxmox — un fichier inachevé peut alors rester sur le stockage
(à supprimer dans Proxmox), et la machine rester verrouillée — et éteinte en mode *Arrêt* (`pct unlock` ou
`qm unlock`, puis la redémarrer). Si loco redémarre
pendant ce temps, la tâche continue sur Proxmox et loco reprend son suivi.

La **conservation** d'une planification ne supprime que ses propres sauvegardes, repérées par leur note
(`loco plan <n°> <machine>`) : jamais celles faites autrement (à la main, par une tâche de Proxmox) ni les
sauvegardes protégées. loco l'applique lui-même, sans demander à Proxmox le droit plus large qu'exige
`prune-backups`.

### Déclarer un NAS

**Déclarer un NAS…** ajoute un partage **NFS** (serveur, dossier exporté) ou **SMB** (serveur, partage,
utilisateur, mot de passe, domaine) comme stockage de Proxmox réservé aux sauvegardes. Le mot de passe est remis
à Proxmox, qui le garde ; loco ne le conserve pas. Il faut en plus le droit `Datastore.Allocate` sur
`/storage` pour le jeton.

### Droits

*Consulter Proxmox*, *Sauvegarder les machines Proxmox* (lancer, planifier, annuler), *Relier des serveurs
Proxmox* (jeton, certificat, NAS).

## Planifications

À l'heure du serveur loco. Une occurrence manquée (serveur éteint) n'est pas rattrapée : la suivante a lieu
normalement. Une planification se met en pause, reprend ou se supprime ; les sauvegardes déjà faites restent où
elles sont. Sur une installation à deux serveurs, seul le serveur principal les lance.

---

[← Documentation](README.md)
