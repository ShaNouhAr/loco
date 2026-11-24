# Partages de fichiers (Samba)

> **Fonctionnalité facultative**, désactivée par défaut : un administrateur l'active dans *Administration ›
> Fonctionnalités* (section *Facultatif*). Elle apparaît alors dans *Systèmes › Fichiers › Partages de fichiers*.

Le serveur loco partage des dossiers sur le réseau, en **SMB**, comme un petit NAS : les postes Windows, macOS et
Linux les ouvrent sous `\\serveur\partage` (ou `smb://serveur/partage`). Chaque partage vit sur une **partition déjà
en service** du serveur, a une **taille limite**, et ne s'ouvre qu'aux utilisateurs Samba choisis — comptes loco ou
utilisateurs Samba seuls — en lecture ou en lecture et écriture.

Possible sur l'appliance (ISO) et sur un serveur Debian ou Ubuntu installé par `install.sh` : c'est l'agent
d'administration du serveur (`loco-agent`, root) qui installe et règle Samba, le portail lui-même n'a aucun droit
root. Pas dans l'image Docker, ni sur un serveur Windows, ni (pour l'instant) sur un serveur Rocky / RHEL.

## Installer Samba

La page propose **Installer Samba** : loco l'installe depuis les dépôts du système (`samba-server` et
`samba-common-tools` sur l'appliance, `samba` sur Debian et Ubuntu), puis écrit sa propre configuration — la
configuration d'origine est gardée dans `/etc/samba/smb.conf.avant-loco`. Si Samba était déjà installé et réglé à la
main, loco ne touche à rien tant qu'un administrateur n'a pas cliqué *Laisser loco gérer Samba* (même copie gardée).

Réglages de loco :

- **comptes seulement** : aucun accès invité ni anonyme (on ne peut même pas lister les partages sans compte) ;
- **SMB 2 et plus récents** seulement, sur le port **445** ; NetBIOS (port 139, `nmbd`) arrêté ;
- **réseaux des postes de loco seulement** (*Administration › Configuration*, réseaux des postes) ;
- pas d'imprimantes, pas de dossiers personnels.

Ouvrez le port 445/TCP vers le serveur depuis les postes si un pare-feu les sépare (*Système › Réseau* le liste).

## Partitions et graphiques

loco propose les partitions **montées en écriture** du serveur, à droits Unix (ext4, XFS, Btrfs, F2FS…), hors des
dossiers du système ; il n'en formate ni n'en monte aucune. Pour chacune, une barre montre ce qui l'occupe : système
et autres données, fichiers des partages, place réservée aux partages (leurs tailles limites pas encore occupées) et
place libre. Les partages vont dans `/srv/partages` sur la partition racine, dans `<point de montage>/partages` sur les
autres.

Pour ajouter une partition (un second disque de la VM, par exemple), formatez-la et montez-la vous-même sur le
serveur, puis rouvrez la page.

## Créer un partage

*Ajouter un partage* : un nom (`Documents`, `Comptabilité`…), une partition choisie d'un clic, une **taille limite** —
au curseur, qui dessine le futur partage sur la barre de la partition — et les utilisateurs qui y accèdent. Aucun
chemin à taper : loco crée le dossier d'après le nom (`/srv/partages/comptabilite`, ou `comptabilite-2` s'il est déjà
pris) et ne reprend jamais un dossier existant qui contient des fichiers qui ne viennent pas de lui.

Les postes Windows voient le partage comme **un disque de la taille limite**, avec la place qu'il lui reste.
L'occupation est relevée toutes les deux minutes : **au-delà de la limite, le partage passe en lecture seule** pour
tout le monde, une alerte apparaît dans *À traiter* de la vue d'ensemble, et l'écriture revient d'elle-même dès qu'on y
a fait de la place. Entre deux relevés, un gros envoi peut dépasser un peu la limite.

Retirer un partage le retire du réseau ; **ses fichiers restent sur le serveur**, dans son dossier.

## Utilisateurs Samba

Tout se règle sur la page des partages, onglet *Utilisateurs* : rien dans *Mon compte*. Un utilisateur Samba est :

- **un compte loco** choisi dans la liste : il se connecte avec son identifiant loco, et il est désactivé ou retiré de
  Samba en même temps que son compte loco ;
- ou **un utilisateur Samba seul**, pour qui n'a pas de compte loco (un poste de numérisation, un collègue, une
  imprimante) : un identifiant propre aux partages, sans aucun accès à loco. Il ne peut pas reprendre l'identifiant
  d'un compte loco.

Dans les deux cas, l'administrateur choisit son **mot de passe Samba** et ses **droits, partage par partage** (aucun
accès, lecture, ou lecture et écriture) — depuis l'utilisateur, ou depuis chaque partage. On peut le **désactiver**
(il garde son mot de passe et ses droits pour être réactivé) ou le **retirer** de Samba ; le compte loco lié, lui,
n'est pas touché.

Le mot de passe est remis à Samba par l'entrée standard de `smbpasswd` : il ne passe jamais par les arguments d'une
commande, n'est écrit ni dans la base de loco ni dans son journal, et loco ne peut plus l'afficher — transmettez-le à
la personne par un moyen sûr. Samba le conserve d'une façon moins protégée qu'un mot de passe de loco (empreinte NT,
sans sel) : choisissez-en un qui ne sert nulle part ailleurs.

## Droits

*Consulter les partages de fichiers* et *Gérer les partages de fichiers* (installer Samba, créer et modifier les
partages, ajouter les utilisateurs Samba, choisir leur mot de passe et leurs droits) : à cocher dans les profils
d'accès.

## Sauvegarde, haute disponibilité, désactivation

- La description des partages, des utilisateurs et de leurs droits est dans la base de loco, donc dans sa sauvegarde.
  **Les fichiers des partages n'y sont pas** (sauvegardez-les à part), ni les mots de passe Samba : après une
  restauration sur un autre serveur, l'administrateur les choisit de nouveau (la page les signale « pas encore posés
  dans Samba »).
- En haute disponibilité, les partages ne sont pas répliqués vers le second serveur.
- Couper la fonctionnalité arrête Samba ; les fichiers restent en place et tout repart en la réactivant.

## Ce qui a été vérifié

Avec le vrai Samba d'Alpine 3.24 (celui de l'appliance) et de Debian 13, piloté par le vrai code de loco : droits en
lecture et en écriture, refus des mauvais mots de passe et de l'accès invité, passage en lecture seule une fois plein
puis retour, utilisateurs désactivés puis réactivés, taille limite vue par un vrai poste Windows 11 (SMB 3.1.1, signé).
L'installation par la page, sur un loco Debian 13 ; pas encore sur l'appliance elle-même, ni avec un poste macOS.

---

[← Documentation](README.md)
