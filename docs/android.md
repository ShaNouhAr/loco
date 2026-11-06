# Appareils Android

Gérer vos propres appareils Android (téléphone, tablette) depuis loco : voir et piloter l'écran,
parcourir les fichiers, ouvrir un terminal, et laisser un assistant IA faire la même chose à travers le
MCP. L'inscription se fait **sans QR code** : on installe une application, on saisit l'adresse du serveur
et une clé. C'est une fonctionnalité **expérimentale**, coupée par défaut.

Pas de mises à jour système ni de catalogue de logiciels sur Android : loco ne gère que l'écran, les
fichiers et le terminal. Rien n'est capturé en dehors d'une session que vous ouvrez depuis le portail, et
chaque capacité (écran, contrôle, fichiers) s'active sur l'appareil, à la main.

## Activer la fonctionnalité

Dans **Administration › Fonctionnalités**, section *Expérimental*, activer **Appareils Android**. Seul le
compte `admin` créé à l'installation peut le faire. Tant qu'elle est coupée, l'onglet Android de
l'enrôlement et le téléchargement de l'application n'existent pas.

## Installer l'application

Dans **Mon parc › Enrôlement**, onglet **Android** : télécharger `loco-agent.apk` et l'ouvrir sur
l'appareil. Android demande d'autoriser l'installation depuis cette source ; aucun compte Google n'est
nécessaire. On peut aussi ouvrir directement le lien de téléchargement depuis le navigateur de l'appareil
(une adresse HTTP est proposée pour un premier branchement, avant d'avoir importé l'autorité de loco).

L'application demande Android 8 (API 26) ou plus récent.

## Connecter l'appareil

Ouvrir l'application loco et saisir **l'adresse du serveur** et la **clé d'inscription** affichées sur la
page d'enrôlement. L'application récupère l'autorité (CA) du serveur et affiche son **empreinte SHA-256** :
la comparer à celle de la page d'enrôlement et ne confirmer que si elle correspond. Une fois confirmée,
l'appareil rejoint *Mon parc* et garde le contact tout seul.

Si le certificat du serveur est reconnu publiquement, l'application se connecte sans empreinte à confirmer.

## Ce que vous pouvez faire

Depuis la fiche de l'appareil, comme pour un poste :

- **Écran** — voir l'écran en direct et le piloter (toucher, faire défiler, clavier et souris). Les boutons
  *Retour*, *Accueil*, *Applications récentes* et *Notifications* sont dans la barre d'outils ; un clic
  droit fait *Retour*, un clic du milieu *Accueil*, la molette fait défiler.
- **Terminal** — un shell avec les droits de l'application (sans root). `cd` est conservé d'une commande à
  l'autre ; les variables d'environnement, non.
- **Fichiers** — parcourir, téléverser, télécharger, renommer et supprimer dans le stockage de l'appareil.
- **Assistants IA (MCP)** — les mêmes actions (terminal, fichiers, écran) sont offertes aux assistants, dans
  la limite des droits du jeton et du profil.

## Accorder les accès, sur l'appareil

Chaque capacité s'active depuis l'application, sur l'appareil, et se voit :

- **Écran distant** : *Activer l'écran distant* lance la capture (Android affiche son indicateur de partage
  tant qu'elle est active). Sans elle, l'écran reste invisible.
- **Contrôle** : *Ouvrir les réglages d'accessibilité* pour autoriser loco à toucher, faire défiler, revenir
  et écrire. Sans lui, l'écran est seulement visible.
- **Fichiers** : *Autoriser l'accès aux fichiers* (accès à tous les fichiers).
- **Arrière-plan** : *Autoriser l'arrière-plan* pour que l'appareil ne mette pas loco en veille et ne coupe
  pas le contact quand l'écran est éteint.

L'application reste visible (notification permanente) et rien n'est lu ou envoyé en dehors d'une session
ouverte depuis le portail.

## Retirer un appareil

Dans l'application, *Se retirer de loco* : l'appareil est retiré du serveur et l'agent s'arrête. On peut
l'inscrire à nouveau plus tard. Désinstaller l'application a le même effet côté appareil.

## Limites

- Pas de mises à jour système ni de catalogue de logiciels.
- Le pilotage de l'écran passe par un service d'accessibilité ; certaines applications sensibles (banque…)
  masquent leur contenu pendant la capture.
- L'application est signée avec la clé de release de loco ; mettre à jour l'APK depuis la même source
  conserve l'installation (une signature différente obligerait à désinstaller d'abord).
