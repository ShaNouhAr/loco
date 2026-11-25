# Proxmox VE : système, mises à jour, terminal

Un serveur Proxmox relié à loco pour ses [sauvegardes](sauvegardes.md#proxmox-ve) peut l'être aussi pour son
**système** : il apparaît alors dans **Mon parc**, comme un Home Assistant, avec ses mises à jour, ses ressources
et un bouton *Mettre à jour*, et il a sa propre page (le nom du serveur dans Mon parc, ou *Système et mises à jour*
sur sa carte de la page Sauvegardes).

## Pourquoi une liaison SSH en plus du jeton

L'API de Proxmox sait dire quelles mises à jour attendent, mais **pas les installer** : le bouton *Upgrade* de son
interface ouvre un shell réservé à `root@pam` connecté, refusé à tout jeton d'API. loco entre donc par **SSH**,
avec une clé qui lui est propre — la même démarche que pour Home Assistant. Le jeton reste celui des sauvegardes ;
il donne aussi les ressources du serveur (processeur, mémoire, disque système) montrées dans Mon parc.

## Relier le système

Sur la page du serveur, *Gérer aussi le système de ce Proxmox* :

1. **Autoriser la clé de loco** : ouvrez le shell du nœud (interface de Proxmox › le nœud › *Shell*) et lancez la
   commande proposée, qui ajoute la clé publique de loco à `/root/.ssh/authorized_keys`. La clé privée ne quitte
   jamais le serveur loco ; elle est distincte de celle de Home Assistant.
2. **Relever la clé d'hôte** : d'ordinaire la même adresse que l'API, le port 22 et `root`. loco montre l'empreinte
   de la clé d'hôte de Proxmox ; comparez-la à celle que donne, dans le shell du nœud,
   `for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done`, puis confirmez. Rien n'est échangé avant.

*Retirer la liaison SSH* (menu *Plus*) arrête la gestion du système sans toucher aux sauvegardes ; retirez aussi la
clé de loco de `authorized_keys`.

## Ce que montre la page

- la version de Proxmox VE, le nœud, le **noyau** en marche — et *redémarrage requis* quand un noyau plus récent
  est déjà installé ;
- les **mises à jour** disponibles, celles de sécurité en tête, les nouveaux paquets et ceux qu'une mise à jour
  retirerait, d'après la simulation de `apt-get dist-upgrade` (rien n'est changé pour la lire) ;
- les machines, le processeur, la mémoire, le disque système et la durée d'allumage ;
- l'**activité** : chaque tâche avec son journal, en direct pendant une mise à jour.

Si loco tourne lui-même sur ce Proxmox, la page le dit (il se reconnaît à ses adresses MAC dans la configuration
des machines).

## Mettre à jour

*Mettre à jour* relit les dépôts puis installe tout avec `apt-get dist-upgrade` — ce que fait le bouton *Upgrade*
de Proxmox ; jamais `apt-get upgrade`, que Proxmox déconseille. Les fichiers de configuration modifiés sur le
serveur sont gardés. La mise à jour tourne **sur le serveur lui-même** (unité `systemd-run`, journal dans
`/var/lib/loco-upgrade/`) : elle va au bout même si la liaison tombe ou si loco redémarre, et loco reprend son
suivi. Les machines virtuelles continuent de tourner ; l'interface web de Proxmox peut se recharger.

Cochez *Redémarrer le serveur ensuite* pour enchaîner un redémarrage — seulement si la mise à jour a réussi.
loco refuse de lancer une mise à jour qui retirerait le paquet `proxmox-ve` (dépôts mal réglés).

*Rechercher les mises à jour* relit seulement les dépôts (`apt-get update`) ; Proxmox le fait aussi chaque jour.

Dans Mon parc, le bouton de la ligne, la sélection, *Tout mettre à jour* et la planification de tout le parc lancent
la même mise à jour. Un Proxmox **redémarre aussi depuis Mon parc** — sélection, *Mettre à jour puis redémarrer si
nécessaire* (seulement si la mise à jour installe un noyau à charger), bouton *Redémarrer* de sa ligne quand un
redémarrage est requis —, comme un poste, à une différence près : loco attend d'abord la fin des mises à jour en
cours sur les postes du parc, une heure au plus, pour ne couper aucune installation dans une machine virtuelle. La
tâche dit ce qu'elle attend (« En attente de la fin des mises à jour du parc : 2 postes en cours. »). Le bouton
*Redémarrer* de sa page, lui, redémarre aussitôt.

## Redémarrer

*Redémarrer* arrête toutes les machines virtuelles et les conteneurs du serveur le temps du redémarrage ; ceux
réglés pour démarrer avec lui repartent ensuite. loco attend que le serveur réponde de nouveau, redémarré (autre
identifiant de démarrage). Si loco tourne sur ce Proxmox, il s'arrête aussi quelques minutes et reprend le suivi à
son retour.

## Terminal

*Terminal* ouvre un shell **root** sur le nœud, tenu par loco comme celui de Home Assistant (session journalisée).

## Droits

*Consulter Proxmox*, *Mettre à jour Proxmox* (rechercher et installer), *Redémarrer Proxmox*, *Ouvrir un terminal
Proxmox*, *Relier des serveurs Proxmox* (aussi la liaison SSH). Aucun profil livré ne les donne, sauf
Administrateur.
