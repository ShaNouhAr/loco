# Appareils Android

Gérer vos propres appareils Android (téléphone, tablette) depuis loco : voir et piloter l'écran,
parcourir les fichiers, ouvrir un terminal, et laisser un assistant IA faire la même chose à travers le
MCP. L'inscription se fait **sans QR code** : on installe une application, on saisit l'adresse du serveur
et une clé. C'est une fonctionnalité **expérimentale**, coupée par défaut.

loco installe aussi des applications sur ces appareils : depuis des dépôts F-Droid vérifiés, ou en ouvrant
leur fiche Google Play (voir [Applications](#applications)). Pas de mises à jour du système Android. Rien
n'est capturé en dehors d'une session que vous ouvrez depuis le portail, et chaque capacité (écran, contrôle,
fichiers, installations) s'active sur l'appareil, à la main.

## Activer la fonctionnalité

Dans **Administration › Fonctionnalités**, section *Expérimental*, activer **Appareils Android**. Tout
compte au profil Administrateur peut le faire. Tant qu'elle est coupée, l'onglet Android de
l'enrôlement et le téléchargement de l'application n'existent pas.

## Installer l'application

Dans **Mon parc › Enrôlement**, onglet **Android** : télécharger l'APK et l'ouvrir sur l'appareil. Android
demande d'autoriser l'installation depuis cette source ; aucun compte Google n'est nécessaire. On peut aussi
ouvrir directement le lien de téléchargement depuis le navigateur de l'appareil (une adresse HTTP est
proposée pour un premier branchement, avant d'avoir importé l'autorité de loco). L'application s'installe
sous le nom **Agent loco** et demande Android 8 (API 26) ou plus récent.

## Inscription automatique (APK prêt à inscrire)

L'APK proposé par l'enrôlement porte, gravées dans son **bloc de signature**, l'adresse du serveur, la clé
d'inscription et l'empreinte de l'autorité de loco — exactement comme le MSI Windows préconfiguré. Ouverte
sur l'appareil, l'application **s'inscrit toute seule** : elle récupère l'autorité du serveur, vérifie que
son empreinte correspond à celle gravée, puis rejoint *Mon parc*, sans rien à saisir ni confirmer. Avec un
groupe de machines choisi à l'enrôlement, l'appareil le rejoint.

La configuration est posée dans le bloc de signature, que les schémas de signature v2/v3 ne couvrent pas :
**la signature de l'application reste valable** (aucune re-signature, aucune clé de signature sur le serveur)
et l'application se met à jour normalement ensuite. L'APK contient la clé d'inscription : ne le partager
qu'avec les personnes qui installent les appareils. Si l'empreinte reçue ne correspond pas à celle gravée
(mauvaise adresse, interception…), l'inscription automatique est **refusée** et l'application repasse à la
saisie manuelle.

## Connecter l'appareil à la main

Pour l'APK générique, replié sous *APK générique et saisie manuelle*, ouvrir l'application et saisir
**l'adresse du serveur** et la **clé d'inscription** affichées sur la page d'enrôlement. L'application
récupère l'autorité (CA) du serveur et affiche son **empreinte SHA-256** : la comparer à celle de la page
d'enrôlement et ne confirmer que si elle correspond. Une fois confirmée, l'appareil rejoint *Mon parc* et
garde le contact tout seul.

Si le certificat du serveur est reconnu publiquement, l'application se connecte sans empreinte à confirmer.

## Ce que vous pouvez faire

Depuis la fiche de l'appareil, comme pour un poste :

- **Écran** — voir l'écran en direct et le piloter (toucher, faire défiler, clavier et souris). Les boutons
  *Retour*, *Accueil*, *Applications récentes* et *Notifications* sont dans la barre d'outils ; un clic
  droit fait *Retour*, un clic du milieu *Accueil*, la molette fait défiler.
  Le doigt suit la souris en direct : maintenez le bouton pour un appui long, glissez pour faire défiler ou
  déplacer, l'écran bouge pendant le geste. Ce qui bouge est transmis en JPEG, léger, puis l'image redevient
  nette dès que l'écran se pose. Pour une image fluide, il faut le **partage d'écran** (voir plus bas) : le
  contrôle à distance seul reste limité par Android à trois images par seconde environ.
- **Terminal** — un shell avec les droits de l'application (sans root). `cd` est conservé d'une commande à
  l'autre ; les variables d'environnement, non.
- **Fichiers** — parcourir, téléverser, télécharger, renommer et supprimer dans le stockage de l'appareil.
- **Terminal adb** — un vrai terminal `adb shell`, avec les droits de l'utilisateur « shell » d'Android
  (voir [Terminal adb](#terminal-adb)).
- **Assistants IA (MCP)** — les mêmes actions (terminal, fichiers, écran) sont offertes aux assistants, dans
  la limite des droits du jeton et du profil, ainsi que l'outil `adb_shell` pour le shell adb.

## Accorder les accès, sur l'appareil

Chaque capacité s'active depuis l'application, sur l'appareil, et se voit :

- **Contrôle** : *Ouvrir les réglages d'accessibilité* pour autoriser loco à toucher, faire défiler, revenir
  et écrire. **Une fois ce service activé, il n'y a plus rien à refaire sur l'appareil** : loco montre aussi
  l'écran par lui à chaque session (Android 11 et plus), même après un redémarrage, sans rouvrir l'application.
  Android limite cette lecture à trois images par seconde environ. Si Android répond « accès refusé » (Android 13
  et plus, application installée depuis un fichier) : *Paramètres › Applications › Agent loco*, menu ⋮,
  *Autoriser les paramètres restreints*, puis recommencez.
- **Écran distant** : *Activer l'écran distant* lance le partage d'écran d'Android, plus fluide (Android affiche
  son indicateur de partage tant qu'il est actif). Android le redemande après chaque redémarrage de
  l'application ; ce bouton est donc facultatif dès que le contrôle est activé.
- **Tout autoriser une seule fois, par adb** : appairez l'appareil avec loco (voir *Terminal adb* plus bas). loco
  autorise alors lui-même le partage d'écran sans fenêtre d'accord, active le contrôle, lève le blocage des
  paramètres restreints, et l'application rallume seule le débogage sans fil quand Android le coupe.

Pendant toute session d'écran, l'appareil affiche la notification **« Contrôle à distance en cours »** : rien
n'est montré en dehors d'une session ouverte depuis le portail.

- **Fichiers** : *Autoriser l'accès aux fichiers* (accès à tous les fichiers).
- **Installations** : *Autoriser les installations*, pour que loco installe les applications que vous
  déployez. Android demande quand même votre confirmation pour chaque nouvelle application.
- **Arrière-plan** : *Autoriser l'arrière-plan* pour que l'appareil ne mette pas loco en veille et ne coupe
  pas le contact quand l'écran est éteint.

L'application reste visible (notification permanente) et rien n'est lu ou envoyé en dehors d'une session
ouverte depuis le portail.

## Réseaux de confiance

Dans l'application, le panneau **Réseaux de confiance** règle où l'appareil reste en contact avec loco. Ailleurs
l'agent est **en pause** : aucun contact avec le serveur, les sessions ouvertes se ferment, le débogage sans fil n'est
pas rallumé ; la notification de l'application dit pourquoi (« En pause · données mobiles »…), et il reprend seul dès
que l'appareil retrouve un réseau approuvé.

| Mode | L'agent est actif… |
|---|---|
| **Partout** (par défaut) | sur tout réseau, données mobiles comprises |
| **Hors données mobiles** | sur tout Wi-Fi ou réseau filaire, jamais sur le réseau mobile |
| **Wi-Fi choisis seulement** | uniquement sur les Wi-Fi approuvés par leur nom |

Le mode **Wi-Fi choisis** demande l'autorisation de **localisation**, réglée sur « Toujours autoriser », et la
localisation de l'appareil allumée : Android ne donne le nom d'un Wi-Fi qu'à cette condition (« toujours » parce
que l'agent tourne en arrière-plan). loco **ne lit jamais la position** ; l'autorisation n'est demandée que si vous
choisissez ce mode, et l'application vous guide pas à pas. Sans elle le nom du Wi-Fi est illisible et l'agent reste
en pause : il n'est jamais actif par défaut sur un réseau qu'il ne sait pas nommer. Un Wi-Fi s'approuve d'un bouton
quand l'appareil y est relié, ou par son nom ; la liste reste sur l'appareil.

Côté portail, la fiche de l'appareil indique seulement le réglage (« actif hors données mobiles seulement »,
« actif sur 2 Wi-Fi approuvés seulement ») : c'est ce qui explique qu'il apparaisse hors ligne par moments. Le
réglage appartient à la personne qui tient l'appareil ; il ne se change pas depuis le portail.

## Terminal adb

Le terminal de l'application a les droits de l'application loco. Le **terminal adb** est celui d'un
ordinateur de développement : `adb shell`, avec les droits de l'utilisateur « shell » d'Android. On peut y
lister, installer et désinstaller des applications (`pm`), lancer une activité (`am`), changer un réglage
(`settings`), simuler un toucher ou une saisie (`input`), lire les journaux (`logcat`) ou l'état du système
(`dumpsys`). C'est un vrai pseudo-terminal : `top` ou un éditeur fonctionnent.

Le serveur loco s'y connecte lui-même, sur le réseau local : l'appareil doit être sur le même réseau que loco.
L'application loco ne fait qu'indiquer à loco l'état du débogage et le port à utiliser.

### Prérequis sur le serveur

loco utilise l'outil `adb` du serveur :
- **Appliance :** inclus à partir de la 2.139.0.
- **Debian, Ubuntu et Docker :** paquet `adb`, installé par loco s'il est disponible. Avec Debian 12 et
  Ubuntu 22.04, il est trop ancien pour l'appairage sans fil : seul le port adb classique est utilisable.
- **Rocky Linux :** paquet `android-tools` d'EPEL.
- **Serveur loco Windows :** pas de terminal adb.

loco lance son propre serveur adb. Sa clé reste dans le dossier d'état de loco, lisible par lui seul, et il
n'écoute sur aucun port réseau.

### Appairer l'appareil (débogage sans fil, Android 11 et plus)

Sur la fiche de l'appareil, **Terminal adb** (ou *Plus › Débogage adb…*) ouvre la configuration :

1. **Options pour les développeurs** : dans *Paramètres › À propos*, touchez sept fois *Numéro de build*.
2. *Options pour les développeurs › Débogage sans fil* : activez-le, sur le même réseau Wi-Fi que loco.
3. *Associer l'appareil avec un code d'association* : saisissez dans loco le port et le code à six chiffres
   affichés par l'appareil, puis **Appairer**.

L'appairage se fait une seule fois. Le port de connexion change à chaque activation du débogage sans fil :
l'application loco le découvre sur l'appareil et l'indique à loco. Android coupe le débogage sans fil au
redémarrage ou quand l'appareil change de réseau : l'application loco (2.145.0 et plus) le rallume seule, avec
le droit que loco lui donne par adb à l'appairage. Sur un réseau Wi-Fi nouveau, Android demande encore de
confirmer sur l'appareil.

Au premier contact qui suit l'appairage, loco pose une fois pour toutes, par adb, ce qui évite tout geste
ensuite : partage d'écran sans fenêtre d'accord, contrôle (accessibilité) activé, paramètres restreints levés,
application dispensée de mise en veille. Le *Journal* le note (« parc.adb.autorisations »), et *Oublier* le
débogage adb arrête de garder le débogage sans fil allumé.

### Port adb classique

Pour un appareil dont adb écoute déjà sur le réseau (`adb tcpip 5555` lancé depuis un ordinateur, Android TV,
machine virtuelle), *Port adb classique* s'y connecte directement. La première fois, l'appareil demande
d'autoriser le débogage depuis loco.

### Droits et traces

L'ouverture d'un terminal adb, l'appairage et les commandes des assistants sont réservés au droit **Ouvrir un
terminal adb**, et inscrits au journal. Le code d'appairage n'est jamais écrit dans une commande ni dans le
journal. **Oublier** retire l'appareil des appareils adb de loco ; retirez aussi loco de la liste des appareils
associés, sur l'appareil.

## Déverrouiller l'écran à distance

Une fois l'appareil appairé en adb, **Déverrouiller** (sur sa fiche) réveille l'écran et rejoue le **schéma**,
le **PIN** ou le **mot de passe** que vous saisissez, par adb — sans toucher l'appareil. L'assistant IA
dispose de l'outil `adb_unlock`.

L'écran de verrouillage d'Android est une **surface sécurisée** : ni `screencap` ni scrcpy ne peuvent
l'afficher (image noire ou vide). Le déverrouillage se fait donc **à l'aveugle** : vous connaissez votre
secret, vous le saisissez dans loco, et loco l'injecte.

- **Schéma** — vous le tracez sur un pavé 3×3 dans le portail. Les 9 points sont **calculés** depuis la
  résolution de l'écran ; un **profil par constructeur** (Pixel/AOSP, Samsung, Xiaomi, OnePlus, Vivo,
  Motorola…) sert de point de départ. Si le schéma ne passe pas, ouvrez **Calage de la grille** : choisissez
  la marque, ou ajustez le centre et l'écart (en fractions de l'écran), puis testez — le réglage est mémorisé
  pour cet appareil. L'injection est rejouée jusqu'à trois fois (un geste tactile n'est pas toujours capté du
  premier coup), bien sous le seuil de blocage d'Android.
- **PIN** et **mot de passe** — saisis et validés par les touches, **sans aucun calage** (aucune coordonnée).

Le secret saisi (schéma, code, mot de passe) n'est **jamais** écrit au journal. Le déverrouillage à distance
relève du même droit **Ouvrir un terminal adb** : l'accès adb permet déjà d'injecter ces gestes.

## Contrôler l'écran à distance

Une fois l'appareil **déverrouillé**, **Contrôler l'écran** (sur sa fiche) diffuse son écran dans le navigateur et
le pilote, par adb — **sans dialogue MediaProjection**.

Le flux est **à basse latence** : loco lance *scrcpy-server* (vidéo seule, inclus dans loco, licence Apache 2.0) sur
l'appareil et relaie le **H.264 brut** ; le navigateur le décode avec **WebCodecs** (décodage matériel, rien à
transcoder sur le serveur). Si le navigateur n'a pas WebCodecs, ou si scrcpy échoue, loco **bascule automatiquement**
sur un flux **MJPEG** (`adb screenrecord` converti par **ffmpeg**, nécessaire sur le serveur pour ce repli).

- **Cliquer** sur l'écran = toucher ; **glisser** = balayer. Les coordonnées sont des fractions de l'écran : cela
  marche quelle que soit la résolution.
- Barre d'outils : **Retour**, **Accueil**, **Applications récentes**, **Marche/veille**, et un champ pour **saisir
  du texte**.
- L'écran de **verrouillage** ne s'affiche pas (surface sécurisée) : déverrouillez d'abord (ci-dessus).

**Garde-fou visible** : tant que loco pilote l'écran, l'appareil affiche une notification **« Contrôle à distance en
cours »** et contacte loco plus souvent, pour que l'indicateur apparaisse et disparaisse rapidement. L'agent reste
visible et l'accès s'appuie sur le débogage sans fil que vous avez autorisé.

## Applications

Le menu **Android** du portail réunit trois pages : *Catalogue*, *Dépôts F-Droid* et *Sources*.

### Sources : les dépôts F-Droid

loco lit l'index des dépôts F-Droid activés, chaque jour. **F-Droid** (des milliers d'applications libres,
compilées par F-Droid depuis leur code source) et **IzzyOnDroid** (applications libres publiées par leurs
développeurs) sont proposés d'office. Neuf dépôts d'éditeurs s'ajoutent en un clic depuis *Sources vérifiées
par loco* : Guardian Project (Orbot), Bitwarden, Brave, Molly, Session, Threema Libre, KDE, NewPipe et microG.
Leur clé a été contrôlée : celle qui signe l'index est identique à celle que publie l'éditeur.

Chaque index est signé. loco vérifie la signature avec la clé de la source, dont l'empreinte est enregistrée
à l'ajout, puis l'empreinte SHA-256 de l'index complet. Il refuse aussi un index plus ancien que le dernier
vérifié, pour qu'un dépôt compromis ou un intermédiaire ne puisse pas resservir d'anciennes versions.

**Un autre dépôt F-Droid** s'ajoute par son adresse (`https://…/fdroid/repo`) : loco lit la clé qui signe son
index et affiche son empreinte. Comparez-la à celle que publie l'éditeur, sur son site, avant de confirmer.
Une source est une clé de confiance : n'ajoutez que des dépôts dont vous connaissez l'éditeur.

Une source dont des applications sont au catalogue ne peut pas être retirée ; elle peut être désactivée.

### Catalogue

Dans *Dépôts F-Droid*, cherchez une application par son nom, ou parcourez la sélection *À découvrir* et les
catégories, puis **Ajouter**. Une application présente dans plusieurs sources est proposée depuis la
première, dans l'ordre de la page *Sources*. Sa fiche donne la version, l'Android minimum, les processeurs,
la taille, la licence, la clé de signature, et les avertissements de F-Droid (publicité, pistage, service en
ligne non libre…).

Choisissez **une seule source par application** : chaque dépôt signe ses APK avec sa propre clé, et Android
refuse de mettre à jour une application avec un APK signé par une autre clé.

### Google Play

Google ne permet à aucun outil tiers d'installer une application du Play Store, et loco ne télécharge rien
depuis Google Play. Dans *Catalogue*, **Ajouter depuis Google Play** prend le lien de la page de
l'application (`https://play.google.com/store/apps/details?id=…`) ou son nom de paquet. Au déploiement,
l'appareil ouvre la fiche dans le Play Store et vous touchez **Installer**. Google Play se charge ensuite des
mises à jour. Sur un appareil sans Play Store, la fiche s'ouvre dans la boutique installée ou, à défaut,
dans le navigateur.

### Déployer

Depuis le catalogue (**Déployer**), la fiche d'un appareil (*Plus › Installer des applications…*) ou
*Mon parc › Déploiements* (*Nouveau déploiement › Applications Android*). Comme pour les autres
déploiements, on choisit des appareils ou des groupes de machines, on vérifie la préparation, puis on
confirme. Les vagues sont disponibles.

Chaque appareil reçoit la version compatible avec son Android et son processeur. Une application déjà à jour
est ignorée, sauf avec *Réinstaller même si la version est déjà présente*. L'appareil doit avoir
l'application loco **2.139.0 ou plus récente**.

### Sur l'appareil

1. loco télécharge l'APK depuis la source et vérifie son empreinte SHA-256, inscrite dans l'index signé.
2. L'application loco le récupère auprès de loco, vérifie à son tour l'empreinte, le nom de l'application et
   sa clé de signature, puis le confie à Android.
3. **Une première installation se confirme sur l'appareil** : Android affiche sa fenêtre *Installer*. Elle
   s'ouvre directement si l'application loco est au premier plan ou si le contrôle (accessibilité) est actif ;
   sinon, une notification *Installer … ?* l'ouvre. Vous pouvez aussi la toucher depuis l'écran distant.
4. **Les mises à jour se font sans rien demander** sur Android 12 ou plus récent, pour les applications que
   loco a lui-même installées. Une application installée par Google Play ou une autre boutique reste mise à
   jour par celle-ci, et loco ne propose pas de la mettre à jour.

Une installation non confirmée dans les six heures est notée en échec. Le déploiement montre le compte rendu
de l'appareil (installée, refusée, incompatible…), et la fiche de l'appareil la version installée de chaque
application du catalogue, la boutique qui l'a installée et la version disponible.

### L'application loco elle-même

À partir de la 2.139.0, l'application loco **se met à jour seule** depuis loco quand le serveur passe à une
version plus récente, hors session distante en cours. Après une mise à jour, l'écran distant est à
réactiver : Android ne garde pas l'autorisation de capture. Elle **repart d'elle-même** après un redémarrage
de l'appareil ou une mise à jour. La 2.138.0 ne savait pas se mettre à jour : installez la 2.139.0 une fois à
la main, depuis l'enrôlement, par-dessus l'ancienne.

La mise à jour peut aussi **s'installer depuis l'application**, sans attendre : dès que loco en propose une,
l'écran de l'application affiche *Mise à jour de l'application* avec la version disponible et le bouton
**Installer la mise à jour**. Ouvrir l'application interroge loco tout de suite. Si l'autorisation d'installer
des applications manque, le bouton y conduit d'abord. Android ferme l'application pour la remplacer ; elle
repart seule, une notification *Application mise à jour* le confirme, et l'écran se rouvre quand Android le
permet. Ce qui a empêché l'installation (loco injoignable, refus sur l'appareil…) est écrit sous le bouton.
En bas de l'écran, la ligne de version indique *à jour* ou la version disponible. Hors de ses réseaux de
confiance, l'appareil ne contacte pas loco et le bouton n'est pas proposé.

## Retirer un appareil

Dans l'application, *Se retirer de loco* : l'appareil est retiré du serveur et l'agent s'arrête. On peut
l'inscrire à nouveau plus tard. Désinstaller l'application a le même effet côté appareil. Depuis le portail,
*Retirer l'agent* (fiche de l'appareil) fait de même au prochain contact ; l'application reste installée.

## Limites

- Pas de mises à jour du système Android. Pas d'installation depuis Google Play par loco : seulement la fiche
  ouverte sur l'appareil.
- Le terminal adb exige que l'appareil soit sur le réseau local de loco, et le débogage sans fil activé (coupé
  par Android à chaque redémarrage).
- Une première installation demande toujours une confirmation sur l'appareil : sans gestion d'entreprise
  Android (Device Owner), Android ne permet pas d'installer sans elle.
- **Vérification des développeurs de Google** : depuis le 30 septembre 2026 au Brésil, en Indonésie, à
  Singapour et en Thaïlande, et partout en 2027, un appareil certifié n'installe plus une application dont le
  développeur n'est pas enregistré auprès de Google. L'application loco, signée par votre clé, et les
  applications signées par F-Droid sont concernées. Il faudra activer une fois, sur chaque appareil, le
  « parcours avancé » de Google (mode développeur, redémarrage, puis un jour d'attente), ou enregistrer
  l'application loco auprès de Google (compte gratuit à distribution limitée).
- Le pilotage de l'écran passe par un service d'accessibilité ; certaines applications sensibles (banque…)
  masquent leur contenu pendant la capture.
- L'application est signée avec la clé de release de loco ; mettre à jour l'APK depuis la même source
  conserve l'installation (une signature différente obligerait à désinstaller d'abord).
