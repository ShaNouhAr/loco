# Serveur MCP

![Jetons MCP et commande de connexion d'un assistant](media/mcp.webp)

loco expose un **serveur MCP** (Model Context Protocol, le protocole par lequel un LLM appelle des outils) : un LLM consulte et pilote loco depuis son **client MCP** — Claude Code, Cursor, VS Code ou tout client qui accepte un en-tête d’authentification. Dans **Administration → Serveur MCP**, ajoutez loco à **Claude** ou à **Codex** par un installateur (voir ci-dessous), ou créez un **jeton** par client et activez un à un les outils qu’il ouvre. Le LLM peut alors répondre à « quels postes attendent des mises à jour ? », lancer celles-ci, lire un journal système ou ouvrir le terminal d’un poste, dans les limites du jeton. Les clients qui se connectent depuis le cloud de leur éditeur (claude.ai, ChatGPT) ne joignent pas un loco installé sur un réseau local.

- **Outil par outil** : chaque jeton n’ouvre que les outils activés (lister ou consulter les postes, rechercher un logiciel installé, demander un inventaire, mettre à jour, redémarrer, installer un logiciel du catalogue, annuler une action en attente, lire et lancer les scripts de la bibliothèque, exécuter des commandes écrites par le LLM, shell adb d’un appareil Android, état du serveur, journal…). La fenêtre du jeton les range par domaine, avec leur nom MCP (`list_devices`, `terminal_run`…) et un repère **action** ou **sensible** ; préréglages **Lecture seule**, **Tout ce que je peux** ou **Rien**.
- **Accès distant en direct**, par les mêmes sessions que l’interface (droits **Ouvrir un terminal distant**, **Gérer les fichiers du poste**, **Ouvrir le bureau noVNC**) :
  - **terminal** : commande ou script (jusqu’à 64 Ko) dans le bash root (Linux) ou le PowerShell SYSTEM (Windows) du poste, sortie et code de sortie en quelques secondes, interrompue (Ctrl+C) au-delà du délai choisi (jusqu’à 5 min) ;
  - **terminal interactif** : le LLM tape et appuie sur des touches (`{enter}`, `{ctrl+c}`, `{esc}`, flèches…) et lit l’écran du terminal (120 × 40), pour vim, top, les invites oui/non ou PowerShell ; la session garde son état d’un appel à l’autre ;
  - **fichiers** : parcourir, lire (texte ou base64), écrire (remplacement atomique), créer un dossier, copier, déplacer, renommer, changer les droits, supprimer — outils distincts pour la lecture, l’écriture, l’organisation et la suppression ;
  - **bureau** : capture d’écran renvoyée en image au LLM (bureau Windows ou Linux, y compris GNOME en Wayland, écran de verrouillage, console texte d’un poste sans affichage ; zoom sur une zone), clic, saisie de texte, touches et raccourcis (Ctrl+Alt+Suppr compris), molette — chaque action renvoie une nouvelle capture.

  Une session par jeton, poste et type est réutilisée d’un appel à l’autre et se ferme après 5 minutes sans appel (2 pour le bureau) ; révoquer ou modifier le jeton la ferme aussitôt. Une personne passe avant le client MCP : si la limite de sessions du poste est atteinte, ouvrir une session depuis l’interface ferme d’abord les sessions MCP inactives. Un poste visé par un appel MCP est « préchauffé » une minute (relevé toutes les 2 s) pour que la session suivante s’ouvre vite, et l’agent espace ses relevés quand une session reste silencieuse. Après la première capture, seules les zones modifiées de l’écran transitent ; la saisie de texte suit la disposition réelle du clavier du poste (AZERTY, touches mortes, AltGr). Chaque commande, écriture, suppression et action sur le bureau entre au journal (le texte saisi n’y figure pas).
- **Jamais plus que son créateur** : un jeton est rattaché au compte qui le crée ; ses droits effectifs sont, à chaque appel, les outils activés **et** permis par le profil actuel de ce compte. Compte désactivé, supprimé ou à finaliser, annuaire coupé, jeton expiré ou révoqué : refus immédiat. L’agent du poste revérifie aussi les droits du compte avant d’exécuter.
- **Jeton montré une seule fois** (loco n’en garde qu’une empreinte), avec la commande `claude mcp add` et la configuration JSON déjà remplies. Expiration au choix (30 jours, 90 jours, 1 an, jamais).
- **Traçabilité** : chaque action passe par les mêmes fonctions que l’interface et entre au journal avec le nom du jeton (« · MCP « Claude Code » ») ; une commande écrite par le LLM est gardée telle quelle dans l’historique des scripts (« MCP : … »). La liste des jetons montre le dernier appel de chacun (date, adresse, client).
- **Protocole** : `https://<serveur>:8443/mcp`, transport HTTP « Streamable HTTP » sans session, en-tête `Authorization: Bearer <jeton>`. loco répond aux deux générations de MCP : la poignée de main `initialize` (2025-03-26 à 2025-11-25) et la génération 2026-07-28 (`server/discover`, métadonnées par requête). Servi uniquement en HTTPS, derrière les mêmes réseaux autorisés que l’interface ; une page web d’une autre origine est refusée. Les clients Node.js qui ne connaissent pas le certificat de loco l’acceptent avec `NODE_EXTRA_CA_CERTS=ca.crt` (autorité téléchargeable sur `/ca.crt`).

## Ajouter loco à Claude ou à Codex

Ni Claude ni Codex n’acceptent qu’un lien ajoute un serveur MCP. Le bloc **Ajouter loco à Claude ou à Codex** de la
page Serveur MCP prépare donc un **installateur** pour l’ordinateur où tourne l’application :

1. Cliquez sur **Claude** ou **Codex**. Dans la fenêtre : nom du jeton, durée, système de l’ordinateur (deviné d’après
   le navigateur) et outils — la consultation est cochée d’office, l’accès distant (fichiers, écran, terminal) jamais.
2. **Télécharger l’installateur** crée le jeton et renvoie un fichier qui le contient :
   - **Windows** — `ajouter-loco-a-claude.cmd` : ouvrez-le (Windows peut demander *Informations complémentaires*, puis
     *Exécuter quand même* : le fichier n’est pas signé) ;
   - **macOS** et **Linux** — `ajouter-loco-a-claude.command` / `.sh` : dans le Terminal, `sh` suivi du fichier.
3. L’installateur écrit la configuration MCP de l’application, puis s’efface :
   - **Claude Code** (commande `claude` et onglet *Code* de l’application de bureau) : entrée `loco` de type `http`,
     avec l’en-tête du jeton, dans `~/.claude.json` ;
   - **Codex** (application de bureau et commande `codex`) : section `[mcp_servers.loco]` (`url`, `http_headers`) dans
     `~/.codex/config.toml` — une ancienne section `loco` est remplacée.

   Le reste de ces fichiers est gardé, et une copie de l’ancien fichier est laissée à côté (`….avant-loco`).
4. Redémarrez l’application : loco apparaît dans ses serveurs MCP. Le jeton figure dans la liste de loco (« obtenu par
   l’installateur Claude ») ; modifiez ses outils ou révoquez-le comme les autres.

**Certificat.** Si le poste ne reconnaît pas le certificat de loco et que c’est l’autorité de loco qui l’a signé,
l’installateur l’affiche avec son empreinte SHA-256 et demande s’il faut lui faire confiance : magasin de l’utilisateur
sous Windows (Windows demande aussi confirmation), trousseau de session sous macOS ; sous Linux, réglage
`NODE_EXTRA_CA_CERTS` de Claude Code (`~/.claude/settings.json`) et, pour Codex, magasin du système (`sudo`).

Le jeton ne passe ni par une conversation ni par une ligne de commande : il est dans le fichier téléchargé, puis dans
la configuration de l’application.

### Sans installateur : connexion par loco

Un client MCP peut aussi se connecter **sans jeton préparé**, par OAuth 2.1 (spécification d’autorisation MCP) :

```bash
claude mcp add --scope user --transport http loco https://<serveur>:8443/mcp && claude mcp login loco
codex mcp add loco --url https://<serveur>:8443/mcp && codex mcp login loco
```

La commande `login` ouvre loco dans le navigateur, sur **Autoriser Claude Code** (ou Codex) : nom, durée et outils du
jeton, puis **Autoriser**. Le client découvre loco (`/.well-known/…`), s’enregistre seul (client public) et reçoit le
jeton après échange du code avec PKCE ; le code ne revient qu’à une application de l’ordinateur (boucle locale ou schéma
propre à une application). *Sign out* de Claude Code (`claude mcp logout loco`) retire aussi le jeton de loco.

Codex refuse cette connexion quand loco est joint par une adresse IP privée : il lui faut un nom, à déclarer dans
*Système › Réseau › Adresse du portail* (l’installateur, lui, n’est pas concerné).

Le droit **Créer des jetons MCP** (administrateurs par défaut) permet de créer ses propres jetons et de révoquer ceux des comptes que l’on gère. Fonction désactivable dans **Fonctionnalités** (« Serveur MCP »).

---

[← Retour à la présentation](../README.md) · [Toute la documentation](README.md)
