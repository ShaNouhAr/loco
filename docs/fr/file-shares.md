[English](../file-shares.md) · **Français** · [Español](../es/file-shares.md)

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

**Pare-feu** : Samba n'a besoin que du port **445/TCP** (NetBIOS, ports 137 à 139, est coupé). loco l'ouvre lui-même
sur le pare-feu actif du serveur — ufw (Debian, Ubuntu, l'appliance) ou firewalld —, pour les réseaux des postes, tant
que les partages sont servis, et le referme quand on les coupe ; ses règles portent la marque `loco-samba`, celles de
l'administrateur ne sont jamais touchées, et un pare-feu allumé après coup reçoit la règle dans les dix minutes. La page
dit ce qu'il en est (*Pare-feu du serveur*). Un pare-feu **entre** les postes et le serveur doit, lui, laisser passer
445/TCP (*Système › Réseau* le liste).

**Adresses** : la page donne, à copier, l'adresse du serveur et celle de chaque partage pour chaque système —
`\\serveur\partage` pour Windows (Explorateur, *Connecter un lecteur réseau*), `smb://serveur/partage` pour Linux
(Fichiers, Dolphin…) et macOS (Finder › *Aller* › *Se connecter au serveur*).

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
a fait de la place. Entre deux relevés, un gros envoi peut dépasser un peu la limite. Les postes déjà connectés passent
en lecture seule eux aussi (voir [Connexions en cours](#connexions-en-cours)).

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
n'est pas touché. Désactivé ou retiré, il perd aussitôt ses connexions en cours.

Le mot de passe est remis à Samba par l'entrée standard de `smbpasswd` : il ne passe jamais par les arguments d'une
commande, n'est écrit ni dans la base de loco ni dans son journal, et loco ne peut plus l'afficher — transmettez-le à
la personne par un moyen sûr. Samba le conserve d'une façon moins protégée qu'un mot de passe de loco (empreinte NT,
sans sel) : choisissez-en un qui ne sert nulle part ailleurs.

## Connexions en cours

L'onglet *Connexions* montre, relevée toutes les quinze secondes, une ligne par connexion d'un poste : l'utilisateur,
les partages qu'il a ouverts, le poste et l'heure de connexion.

**Couper** termine la connexion : le processus de Samba qui la sert — celui que Samba désigne lui-même, loco n'en
arrête jamais un autre — se ferme proprement. Un fichier en cours d'enregistrement peut rester incomplet. Le poste se
reconnecte de lui-même tant que l'utilisateur garde ses droits : pour l'en empêcher, cochez *Désactiver aussi* dans la
fenêtre de confirmation (il se réactive dans l'onglet *Utilisateurs*). Chaque coupure est inscrite au journal.

Samba n'applique sa configuration qu'aux connexions qui s'ouvrent. loco ferme donc lui-même, à chaque changement, ce
qui n'est plus permis sur les connexions déjà ouvertes : celles d'un utilisateur désactivé ou retiré, et le partage
sur les connexions dont le droit a changé (retiré, passé en lecture seule parce que le partage est plein, ou rendu) ou
qui n'est plus servi. Le poste qui y a encore droit le rouvre de lui-même, avec ses nouveaux droits.

## Ouvrir un partage dans le navigateur

*Ouvrir*, sur la carte d'un partage, l'affiche dans loco, avec un gestionnaire de fichiers complet :

- **parcourir** : fil d'Ariane, dossiers précédent et suivant, tri par nom, taille ou date, fichiers cachés à afficher
  ou non, filtre du dossier et **recherche dans les sous-dossiers** (sans casse ni accents), espace disque façon
  TreeSize, propriétés (taille d'un dossier et de son contenu) ;
- **téléverser** des fichiers ou des **dossiers entiers** (bouton *Téléverser*, ou glisser-déposer depuis
  l'ordinateur, sur la liste ou sur un dossier) : l'envoi se fait par morceaux, reprend après une coupure du réseau,
  garde la date de modification d'origine ; plusieurs fichiers partent à la fois, chacun avec sa progression ;
- **télécharger** un fichier, ou un dossier et toute une sélection en **archive ZIP** composée à la volée ;
- **aperçu** des photos, des PDF, du son et des vidéos, avec les voisins du dossier (flèches ← →) ; **éditeur** pour
  les fichiers texte (Ctrl+S enregistre) — un fichier modifié entre-temps par quelqu'un d'autre n'est pas écrasé ;
- **nouveau dossier** ou **fichier texte**, **renommer**, **copier**, **couper** et **coller** (dans le partage, ou
  vers un autre partage ouvert ensuite), déplacer en glissant des lignes sur un dossier (Ctrl : copier),
  **supprimer** — après confirmation, sans corbeille ;
- **sélection** de plusieurs éléments (cases, Maj ou Ctrl + clic, Ctrl+A) avec une barre en bas de l'écran
  (télécharger, copier, couper, supprimer), **clic droit** et bouton ⋮ sur chaque ligne, **raccourcis clavier**
  (Entrée, Suppr, F2, Ctrl+C / X / V, Retour arrière, Alt + ← / →, `/` pour chercher ; liste par le bouton clavier).

Un nom déjà pris à destination est soumis **avant** le moindre envoi ou collage : *Remplacer* (*Fusionner* pour un
dossier envoyé), *Garder les deux* (« nom (2) »), *Ignorer*, une fois pour toutes si l'on veut. La place occupée sur
la taille limite est affichée en haut : un envoi qui la dépasserait est refusé d'avance, et **un partage plein ne peut
plus que diminuer** (supprimer, renommer, déplacer) tant qu'il n'est pas repassé sous sa limite.

**Qui ouvre quoi.** Le droit *Ouvrir les partages dans le navigateur* est nécessaire. Avec *Gérer les partages de
fichiers*, il ouvre **tous** les partages en lecture et écriture. Sinon, il ouvre les partages de l'**utilisateur
Samba lié au compte loco**, avec ses droits sur le réseau : en lecture, le gestionnaire ne propose ni envoi, ni
création, ni modification, ni suppression ; un utilisateur Samba désactivé n'ouvre plus rien. Un compte qui n'a que ce
droit voit, dans *Partages de fichiers*, la liste de ses partages (place, adresse sur le réseau, *Ouvrir*) et rien de
leur gestion. Les envois, créations, renommages, copies, déplacements et suppressions sont inscrits au journal.

**Comment c'est fait.** Le portail ne touche jamais lui-même aux dossiers des partages. Un service à part, lancé par
l'agent root sous le compte propriétaire des partages (`loco-share`, sans aucun droit root), fait chaque opération
dans le dossier du partage : un chemin y est suivi un dossier après l'autre, **sans jamais traverser un lien
symbolique** (les liens sont montrés, jamais suivis). Pour un envoi ou un téléchargement, il passe au portail le
fichier déjà ouvert. Les fichiers créés depuis loco ont les mêmes propriétaire et droits que ceux posés par Samba
(`0660`, dossiers `2770`). Les noms qu'un poste Windows ne pourrait pas ouvrir (`\ / : * ? " < > |`, point ou espace
final, `CON`, `NUL`…) sont refusés. Une page HTML ou une image SVG d'un partage se télécharge, sans jamais s'exécuter
dans le navigateur. Les envois en cours attendent dans le dossier caché `.loco-envois` du partage, qui n'apparaît pas
dans loco ; ceux qu'un redémarrage a interrompus en sont effacés au bout d'un jour.

## Droits

*Consulter les partages de fichiers* et *Gérer les partages de fichiers* (installer Samba, créer et modifier les
partages, ajouter les utilisateurs Samba, choisir leur mot de passe et leurs droits, couper une connexion), et *Ouvrir
les partages dans le navigateur* (voir plus haut) : à cocher dans les profils d'accès. Le profil Administrateur a les
trois.

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
Sur des connexions déjà ouvertes (Samba 4.23, clients `smbclient`) : coupure à la demande de la seule connexion
visée, et perte immédiate de l'accès pour un utilisateur désactivé, un droit retiré, un passage en lecture seule
(partage plein, puis retour à l'écriture) et un partage retiré.
L'installation par la page, sur un loco Debian 13 ; pas encore sur l'appliance elle-même, ni avec un poste macOS.

Le gestionnaire de fichiers, sur un loco Debian 13 (Samba 4.22 installé par la page, portail confiné par systemd) dans
Chromium : envoi d'un fichier de 20 Mo par morceaux puis téléchargement à l'identique (même empreinte), morceau demandé
(lecture d'une vidéo), archive ZIP, aperçu d'une photo et d'un PDF, nouveau dossier, copie, renommage, éditeur,
recherche dans les sous-dossiers, suppression ; service lancé sous `loco-share`, relancé avec l'agent root ; fichiers
créés avec les mêmes propriétaire et droits que par Samba, date d'origine gardée ; relus et complétés par un
utilisateur Samba avec `smbclient`. Les droits (gestionnaire, lecteur, rédacteur, sans accès), la taille limite et le
partage plein, les liens symboliques et les noms refusés sont couverts par les tests automatiques. Pas encore sur
l'appliance, ni avec Firefox ou Safari.

---

[← Retour à la présentation](../../README.fr.md) · [Toute la documentation](README.md)
