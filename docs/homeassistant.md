# Home Assistant

Suivre et entretenir votre Home Assistant depuis loco : versions du cœur, du système et du Supervisor,
mises à jour (une à une ou toutes, avec sauvegarde avant), modules complémentaires, sauvegardes, état de
santé, journal, redémarrages et terminal. C'est une fonctionnalité **facultative**, coupée par défaut.

loco ne demande **aucun jeton** à Home Assistant. Il y entre par son module SSH, avec une clé qui lui est
propre, et pilote la commande `ha` — celle que vous utiliseriez vous-même dans ce terminal. Il faut donc une
installation qui a un Supervisor (Home Assistant OS ou Supervised) et un module SSH.

## Activer la fonctionnalité

Dans **Administration › Fonctionnalités**, section *Facultatif*, activer **Home Assistant**. Tout compte au
profil Administrateur peut le faire. L'entrée **Home Assistant** apparaît alors dans la barre du haut.

## Relier une instance

La page donne les trois étapes :

1. **Un module SSH dans Home Assistant** — « Advanced SSH & Web Terminal » ou « Terminal & SSH », avec un
   port ouvert sur le réseau (onglet Configuration › Réseau du module).
2. **Autoriser la clé de loco** — copier la clé publique affichée dans `authorized_keys`, dans la
   configuration du module, puis redémarrer le module. La clé privée est créée sur le serveur loco au premier
   affichage de la page (`homeassistant/id_ed25519` dans le dossier d'état, lisible par le seul compte du
   portail) et n'en sort jamais.
3. **L'adresse** — nom ou adresse IP, port SSH, utilisateur du module (`root` pour « Terminal & SSH », celui
   que vous avez choisi pour « Advanced SSH & Web Terminal »).

loco relève alors la **clé d'hôte** que présente cette adresse et en montre l'empreinte. Rien n'est
enregistré ni échangé tant que vous ne l'avez pas confirmée ; pour la comparer, lancez dans le terminal du
module (depuis l'interface de Home Assistant) la commande que la page propose. Ensuite, loco n'accepte que
cette clé : si elle change (module réinstallé), la liaison s'arrête et **Plus › Relever de nouveau la clé
d'hôte** redemande une confirmation.

Jusqu'à huit instances peuvent être reliées.

## Ce que montre la page

- **Tuiles** : Home Assistant (cœur), système, Supervisor, modules, stockage, sauvegardes — avec la version
  qui attend quand il y en a une.
- **Signalé par Home Assistant** : les problèmes que relève son Supervisor (pas de sauvegarde récente, disque
  presque plein, installation dégradée ou non prise en charge…).
- **Mises à jour disponibles** : chacune avec son bouton, et **Tout mettre à jour**, qui les enchaîne dans
  l'ordre conseillé — le Supervisor, les modules, Home Assistant, puis le système (qui redémarre la machine).
  La fenêtre de confirmation propose de **créer une sauvegarde avant** (cochée d'office ; elle vaut pour le
  cœur et les modules).
- **Activité** : les douze dernières tâches, leur état et leur sortie. Une tâche lancée se montre « en
  cours » et ne se relance pas ; les tâches d'une instance passent une par une.
- **Modules complémentaires** et **Sauvegardes** (les huit plus récentes).
- **Journal de Home Assistant** : ses 200 dernières lignes, lues à l'ouverture du volet.

Le relevé est refait tout seul tant que la page est affichée (toutes les deux minutes) et après chaque tâche ;
**Actualiser** le refait tout de suite. La vue d'ensemble signale dans « À traiter » une liaison en échec, une
installation dégradée et les mises à jour disponibles.

## Actions

| Action | Commande lancée sur Home Assistant |
|---|---|
| Mettre à jour Home Assistant | `ha core update [--backup]` |
| Mettre à jour le système | `ha os update` |
| Mettre à jour le Supervisor | `ha supervisor update` |
| Mettre à jour un module | `ha apps update <module> [--backup]` (`ha addons update` sur une commande `ha` plus ancienne) |
| Sauvegarder | `ha backups new --name "loco AAAA-MM-JJ HH:MM"` (sauvegarde complète, gardée dans Home Assistant) |
| Redémarrer › Home Assistant | `ha core restart` |
| Redémarrer › Le système | `ha host reboot` |

Quand le système redémarre (mise à jour du système, redémarrage demandé), la liaison tombe : loco attend son
retour jusqu'à un quart d'heure, et la tâche n'est réussie que s'il répond de nouveau.

## Terminal

**Terminal** ouvre le shell du module SSH dans la page de session habituelle de loco (plein écran, nouvel
onglet, taille du texte). La session est tenue par le serveur loco, journalisée, limitée à une heure et à
deux par instance. Elle demande la fonctionnalité **Accès distant** et un serveur loco sous Linux.

## Droits

Le groupe **Home Assistant** des profils d'accès sépare : consulter, mettre à jour, sauvegarder, redémarrer,
ouvrir un terminal, relier des instances. Aucun profil livré ne les reçoit, hormis Administrateur.

## Retirer une instance

**Plus › Retirer de loco** oublie l'instance et l'historique de ses tâches. Rien n'est changé dans Home
Assistant : retirez-y la clé de loco de `authorized_keys` pour fermer l'accès.

## Limites

- Installations sans Supervisor (Container, Core) : pas de commande `ha`, donc pas de liaison.
- Les sauvegardes restent dans Home Assistant ; loco ne les copie pas et n'en restaure pas.
- Le serveur loco doit avoir le client SSH (`ssh`) : présent sur l'appliance, installé avec loco ailleurs.
