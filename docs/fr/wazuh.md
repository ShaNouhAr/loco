[English](../wazuh.md) · **Français** · [Español](../es/wazuh.md)

# Agent Wazuh

**Facultatif** — à activer dans *Administration › Fonctionnalités › Facultatif*.

Vous avez un manager [Wazuh](https://wazuh.com) et un parc déjà inscrit dans loco : loco pose l'agent Wazuh sur les
postes que vous choisissez, réglé pour votre manager, et vous dit où il en est. Quand vous retirez l'agent d'un poste,
loco peut aussi effacer son entrée sur le manager. Il ne remplace pas Wazuh et ne règle pas votre manager.

**Sommaire** —
[Régler](#régler) ·
[Installer](#installer-sur-des-postes) ·
[Suivre](#suivre-létat) ·
[Retirer](#retirer) ·
[Effacer l'entrée du manager](#effacer-lentrée-du-manager) ·
[Ce qui se passe sur le poste](#ce-qui-se-passe-sur-le-poste) ·
[Droits](#droits) ·
[Si ça ne se connecte pas](#si-ça-ne-se-connecte-pas)

---

## Régler

*Mon parc › Wazuh*, bloc **Réglages** :

| Réglage | Ce que c'est |
|---|---|
| **Adresse du manager** | le nom ou l'adresse IP de votre manager Wazuh, sans `https://` ni port |
| **Version de l'agent** | à choisir dans la liste des versions publiées par Wazuh. **Elle ne doit pas dépasser celle du manager** : un manager 4.14.9 refuse un agent 4.14.10 |
| **Groupe d'agents** *(optionnel)* | le groupe Wazuh où ranger les postes (`servers`, `docker`…). Il doit déjà exister sur le manager ; plusieurs groupes se séparent par des virgules |
| **Mot de passe d'enrôlement** *(optionnel)* | celui du manager, s'il en exige un (`<use_password>yes</use_password>`, fichier `authd.pass`) |

Le mot de passe est gardé chiffré. loco ne le réaffiche jamais et ne l'écrit pas au journal : seul le fait qu'il a
changé y figure.

Sans mot de passe dans loco (case *ne plus en envoyer*), une réinstallation retire du poste le fichier `authd.pass`
posé auparavant : un manager qui n'attend pas de mot de passe refuse l'agent qui en envoie un.

## Installer sur des postes

Deux chemins :

- **Un poste** — sur sa fiche, *Plus › Installer l'agent Wazuh* ; ou le bouton *Installer* de sa ligne, page Wazuh.
- **Plusieurs** — page Wazuh : cochez les postes, ajustez au besoin le groupe d'agents, puis
  *Installer sur les postes cochés*.

L'installation part au prochain contact du poste — en quelques secondes s'il est allumé, à son retour sinon.
La première fois, loco télécharge d'abord le paquet : la commande attend qu'il soit prêt.

Systèmes pris en charge : **Windows**, **Debian et Ubuntu**, **RHEL, Rocky Linux et AlmaLinux** (x86-64 ; ARM 64 bits
sous Linux). Android n'est pas concerné.

> Les postes n'ont pas besoin d'Internet. loco télécharge une fois le paquet officiel (`deb`, `rpm` ou `msi`) sur
> `packages.wazuh.com`, le contrôle avec l'empreinte SHA-512 que publie Wazuh, puis le remet aux postes par leur agent
> loco, qui vérifie à son tour son empreinte.

*Réappliquer* (poste déjà équipé) réinstalle l'agent avec les réglages actuels : utile après un changement de manager
ou de mot de passe. *Mettre à jour* apparaît quand la version du poste est plus ancienne que celle réglée.

## Suivre l'état

La page Wazuh et la fiche de chaque poste (onglet *Détails*) montrent ce que le poste remonte à chaque contact :

| État | Ce que ça veut dire |
|---|---|
| **Connecté** | l'agent tourne et échange avec le manager |
| **En attente du manager** | installé et démarré, pas encore accepté — voir [plus bas](#si-ça-ne-se-connecte-pas) |
| **Déconnecté** | le manager ne répond plus à ce poste |
| **Arrêté** | le service de l'agent Wazuh ne tourne pas |
| **Non installé** | pas d'agent Wazuh sur ce poste |
| **Manager Wazuh** | ce poste est le manager lui-même : loco n'y pose ni n'y retire d'agent, et ne le compte pas parmi les postes à équiper |
| **Inconnu** | l'agent loco du poste est antérieur à la 2.166.0 : il se met à jour seul, l'état suit |

S'y ajoutent la version installée, le numéro et le nom de l'agent chez Wazuh, et une précision quand le poste est
relié à un autre manager que celui réglé.

## Retirer

Fiche du poste, *Plus › Retirer l'agent Wazuh…*, ou *Retirer* sur sa ligne. L'agent est désinstallé du poste.

Si loco a accès au manager (ci-dessous), il efface ensuite l'entrée du poste sur le manager, et le compte rendu du
retrait, dans l'historique du poste, le dit : « Entrée 003 retirée du manager Wazuh. » Sinon, l'entrée reste sur le
manager : supprimez-la là-bas si le poste ne doit plus y figurer.

## Effacer l'entrée du manager

*Mon parc › Wazuh*, bloc **Retrait des entrées du manager** — facultatif, à régler une fois.

loco se connecte au manager en SSH, avec une clé qui lui est propre, et n'y lance qu'une commande :
`manage_agents -r <numéro>`. Trois étapes :

1. **Autoriser la clé de loco** — loco affiche une ligne à ajouter au fichier `~/.ssh/authorized_keys` du compte
   choisi sur le manager (`root` en général, `manage_agents` demandant les droits d'administration) :

   ```
   restrict,command="/var/ossec/bin/manage_agents -r $(printf %s \"$SSH_ORIGINAL_COMMAND\" | tr -cd 0-9)" ssh-ed25519 AAAA… loco@…
   ```

   Avec elle, la clé ne peut lancer que `manage_agents -r`, avec les seuls **chiffres** de ce qu'elle envoie : ni
   shell, ni redirection, ni autre commande (`restrict` coupe aussi terminal et tunnels). La clé privée reste sur le
   serveur loco.
2. **Relever la clé d'hôte** — indiquez le compte et le port SSH ; loco relève la clé que présente le manager et en
   montre l'empreinte. Comparez-la à celle du manager (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`), puis
   confirmez : loco n'échangera qu'avec cette clé-là.
3. **Essayer** — *Essayer l'accès* demande au manager de retirer un numéro qu'aucun agent ne porte : rien n'est retiré,
   mais loco sait que l'accès fonctionne. Il prévient si la clé ouvre un shell complet (ligne sans `command=`).

Ensuite, à chaque retrait réussi, loco efface l'entrée du poste. Par prudence, il ne le fait que si :

- le poste était relié au manager réglé (pas à un autre) ;
- le poste dit l'agent bien désinstallé ;
- le numéro de l'agent est connu — jamais `000`, le manager lui-même.

Changer l'adresse du manager dans les réglages oublie la clé d'hôte : elle se confirme de nouveau. *Retirer l'accès*
coupe tout de suite ; retirez aussi la ligne d'`authorized_keys` sur le manager.

## Ce qui se passe sur le poste

L'agent loco, qui tourne en root (SYSTEM sous Windows) :

1. reçoit le paquet de loco et vérifie son empreinte SHA-256 ;
2. l'installe avec le gestionnaire du système (`apt-get`, `dnf` ou `msiexec`), en donnant au paquet l'adresse du
   manager et le groupe — les variables de déploiement prévues par Wazuh ;
3. écrit le mot de passe d'enrôlement, s'il y en a un, dans le fichier que lit l'agent Wazuh :
   `/var/ossec/etc/authd.pass` (`root:wazuh`, mode 640) sous Linux, `authd.pass` dans le dossier de l'agent sous
   Windows (SYSTEM et administrateurs seulement) ;
4. active et démarre le service (`wazuh-agent`, `WazuhSvc` sous Windows) ;
5. attend une trentaine de secondes le premier échange avec le manager, puis rend compte.

Le mot de passe n'est jamais passé en argument d'une commande ni écrit dans une sortie : il voyage dans la commande
remise à l'agent loco, par son canal HTTPS, et loco ne le garde pas avec la commande.

Sur un poste déjà équipé, la mise à jour du paquet garde `ossec.conf` : loco y remplace seulement l'adresse du manager
si elle a changé.

## Droits

Trois droits, dans les profils d'accès :

| Droit | Permet |
|---|---|
| **Consulter Wazuh** | voir la page et l'état des postes |
| **Installer et retirer l'agent Wazuh** | poser l'agent, le mettre à jour, le retirer — sur les postes du parc de son profil |
| **Régler Wazuh** | adresse du manager, mot de passe d'enrôlement, groupe et version, accès de loco au manager |

Le profil Administrateur les a tous ; les autres profils doivent les recevoir. Les droits sont revérifiés au moment où
la commande part vers le poste.

## Si ça ne se connecte pas

Le compte rendu de l'installation (historique du poste) le dit : « installé et démarré ; pas encore connecté ».

| À vérifier | Comment |
|---|---|
| Le poste joint-il le manager ? | ports **1514** (échanges) et **1515** (enrôlement) ouverts du poste vers le manager |
| La version | l'agent ne doit pas être plus récent que le manager |
| `Invalid password` | le mot de passe ne correspond pas à `/var/ossec/etc/authd.pass` du manager (espace ou saut de ligne en trop compris) |
| `Invalid request for new agent` | le poste envoie un mot de passe alors que le manager n'en attend pas (`<use_password>no</use_password>`) : cochez *ne plus en envoyer*, puis *Réappliquer* |
| Le groupe | il doit exister sur le manager (`/var/ossec/bin/agent_groups -l`) |
| Un ancien agent du même nom | une entrée du même nom sur le manager peut bloquer l'enrôlement : la retirer, puis *Réappliquer* |

Sur le poste, le journal de l'agent Wazuh dit pourquoi : `/var/ossec/logs/ossec.log` (Linux),
`C:\Program Files (x86)\ossec-agent\ossec.log` (Windows).

---

[← Retour à la présentation](../../README.fr.md) · [Toute la documentation](README.md)
