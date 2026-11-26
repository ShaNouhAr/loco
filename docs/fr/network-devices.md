[English](../network-devices.md) · **Français** · [Español](../es/network-devices.md)

# Équipements réseau : switchs et pare-feu

> **Fonctionnalité expérimentale**, désactivée par défaut : un administrateur l'active dans *Administration ›
> Fonctionnalités* (section *Expérimental*). Elle apparaît alors dans *Systèmes › Réseau › Équipements réseau*.

loco gère sans agent vos **switchs Cisco et Extreme** et vos **pare-feu Stormshield**, avec un compte de
l'équipement, comme le ferait un administrateur :

| Famille | Liaison | Relevé et configuration | Firmware |
|---|---|---|---|
| Cisco IOS / IOS-XE (Catalyst, routeurs) | SSH | ligne de commande | copie scp, MD5 vérifié, `boot system` — ou `install add … activate commit` en mode INSTALL |
| Cisco Small Business (CBS, SG) | SSH | ligne de commande | signalé ; installation depuis l'interface web (TFTP seulement en ligne de commande) |
| Extreme EXOS / Switch Engine | SSH | ligne de commande | copie scp, `install image` sur la partition de secours |
| Extreme VOSS / Fabric Engine | SSH | ligne de commande | copie SFTP dans `/intflash`, `software add` / `activate`, `software commit` après le redémarrage |
| Stormshield SNS | API HTTPS (SSH pour le terminal) | API (`SYSTEM PROPERTY`, `CONFIG BACKUP`) | `SYSTEM UPDATE UPLOAD` puis `ACTIVATE` |

## Ce qu'il faut sur l'équipement

- **Cisco IOS / IOS-XE** : SSH activé (`ip ssh version 2`) et un compte de niveau 15 — ou un compte de niveau 1 avec
  le mot de passe *enable* enregistré dans loco. Pour le firmware : `ip scp server enable`.
- **Cisco Small Business** : SSH activé (`ip ssh server`) et un compte de niveau 15. Que le switch demande le compte
  par SSH ou lui-même une fois connecté (`ip ssh password-auth` absent), loco répond.
- **Extreme EXOS** : SSH activé (`enable ssh2`) et un compte d'administration.
- **Extreme VOSS** : SSH activé (`boot config flags sshd`) et un compte `rwa` ; SFTP pour le firmware.
- **Stormshield SNS** : un compte d'administration **sans second facteur** (TOTP), et l'accès à l'interface
  d'administration depuis loco. SSH est facultatif : il ne sert qu'au terminal.

Les vieux équipements qui ne parlent que les algorithmes SSH d'avant 2015 (clé d'hôte `ssh-rsa`, Diffie-Hellman
groupe 14 en SHA-1, chiffrement CBC) sont joints : loco les ajoute en dernier choix, quand son client SSH les connaît
encore.

## Relier un équipement

*Ajouter un équipement* : nom, famille, adresse, port SSH (et port HTTPS pour Stormshield), compte et mot de passe.
Le mot de passe est **chiffré** dans la base de loco ; il ne s'affiche plus, et il est remis à `ssh` par
`SSH_ASKPASS` — jamais dans les arguments d'une commande ni sur le disque en clair. loco montre ensuite l'**empreinte
de la clé d'hôte** SSH (et celle du **certificat** du pare-feu) : comparez-la, puis confirmez. Rien d'autre n'est
échangé avant, et loco refuse ensuite tout équipement qui présenterait une autre clé. Depuis un poste qui s'y est
déjà connecté, `ssh-keygen -lF ADRESSE` affiche l'empreinte qu'il connaît.

## Ce que montrent Mon parc et la page de l'équipement

Modèle, numéro de série, version (et image de démarrage), ports actifs sur le total, processeur, mémoire,
température et durée d'allumage — relevés toutes les quinze minutes, et à l'ouverture de la page. Une commande que
l'équipement ne connaît pas laisse simplement sa case vide. Injoignable, liaison en échec, sauvegarde en échec ou
firmware plus récent disponible se voient dans Mon parc et dans *À traiter* de la vue d'ensemble.

## Sauvegarde de la configuration

Chaque jour par défaut (toutes les six heures, chaque semaine, ou à la demande seulement), loco lit la configuration
(`show running-config`, `show configuration` pour EXOS, la sauvegarde complète `.na` pour Stormshield) et ne la
garde **que si elle a changé** : les lignes qui bougent seules (« Last configuration change at… », horodatage de
VOSS) ne comptent pas. La page montre l'historique, le nombre de lignes ajoutées et retirées, les **différences**
entre deux versions, et permet de télécharger chacune. La conservation (10 à 365 versions) se règle par équipement.

Les configurations contiennent souvent des secrets de l'équipement : elles sont **chiffrées** dans l'état de loco
(`/var/lib/loco/network/configs`), donc incluses dans la sauvegarde de loco lui-même, et chaque consultation ou
téléchargement est inscrit au journal. Le droit *Sauvegarder et lire les configurations* est distinct de celui de
consulter les équipements.

## Firmware

1. **Déposez** le fichier téléchargé chez le constructeur dans la *Bibliothèque de firmwares* (page Équipements
   réseau) : loco en lit la version dans le nom (corrigeable) et affiche son empreinte SHA-256 — comparez-la à celle
   publiée par le constructeur. *Modèles concernés* restreint le fichier à des modèles (`C9300-*, X440G2*`).
2. loco **signale** les équipements qui ont une version plus ancienne. Pour Cisco, seulement ceux de la **même
   plateforme** que l'image (une image de Catalyst 9300 n'est jamais proposée à un 2960X) ; EXOS, VOSS et Stormshield
   refusent d'eux-mêmes une image d'une autre plateforme.
3. *Installer le firmware* : la configuration est sauvegardée juste avant, puis l'image est envoyée et activée
   (voir le tableau en tête). Avec *Redémarrer ensuite*, loco suit le redémarrage et le retour de l'équipement et
   vérifie la nouvelle version. Les Catalyst en mode INSTALL et les pare-feu Stormshield redémarrent d'eux-mêmes à
   l'activation. Sur VOSS, loco valide la version (`software commit`) dès qu'il a rejoint le switch : sans cela, il
   reviendrait de lui-même à l'ancienne.

Vérifiez d'abord dans les notes de version du constructeur que la version convient au modèle et que la mise à jour
est possible depuis celle en service : loco n'en sait rien.

## Terminal et redémarrage

*Terminal* ouvre la ligne de commande de l'équipement dans le navigateur (session tenue par le serveur loco et
journalisée), avec le compte enregistré ; le mode privilégié (`enable`) se tape dans le terminal. *Redémarrer*
redémarre l'équipement et suit son retour : le trafic qui le traverse est coupé le temps du redémarrage, et les
modifications de configuration non enregistrées sont perdues — loco n'enregistre rien à votre place.

## Droits

*Consulter les équipements réseau*, *Sauvegarder et lire les configurations*, *Installer un firmware*, *Redémarrer
un équipement réseau*, *Ouvrir un terminal sur un équipement*, *Relier des équipements réseau* (ajouter, retirer,
compte et mot de passe, empreintes) : à cocher dans les profils d'accès.

## Ce qui a été vérifié

Chaque famille a été essayée avec le vrai client SSH de loco contre des **simulateurs** fidèles aux sorties
documentées de ces systèmes (invite, pagination, `enable`, identifiants demandés par l'équipement, questions de
confirmation, redémarrage), y compris un vieil IOS limité à `ssh-rsa` et Diffie-Hellman groupe 14 en SHA-1, et
contre une API Stormshield simulée d'après le client officiel. **Pas encore sur de vrais équipements** : les relevés
(ports, processeur, température) dépendent du modèle et de la version, et certaines cases peuvent rester vides.

---

[← Retour à la présentation](../../README.fr.md) · [Toute la documentation](README.md)
