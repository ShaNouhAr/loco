# Journal des versions

## 2.8.0 — 2026-10-18

- **Gestion des logiciels unifiée** : catalogue local, store WinGet, groupes de logiciels et suivi des déploiements dans les mêmes onglets. Déploiement depuis une fiche du store, du catalogue ou d'un appareil ; import WinGet partagé et empreinte vérifiée conservée lors des nouvelles tentatives.
- **Groupes de logiciels et de machines** : appartenances multiples, création et modification des membres ; filtre du parc et mise à jour limitée au groupe affiché. Suppression d'un groupe sans supprimer ses logiciels, appareils ou déploiements.
- **Déploiements préparés et confirmés** : combinaison de logiciels, lots, postes et groupes, déduplication, exclusions Windows/Linux explicites, sélection et versions figées. Conservation des installeurs confirmés même après modification ou suppression du catalogue ; sauvegarde compatible avec ces versions.
- **Suivi par poste** : commandes en attente pour les agents hors ligne, journaux et résultats, annulation des seules commandes non transmises. Versions déjà installées et installations identiques déjà en attente ignorées ; réinstallation explicite possible.
- **Droits distincts** : gestion des deux types de groupes, consultation des déploiements et déploiement sur plusieurs postes. Le déploiement collectif exige un droit réservé aux administrateurs par défaut, vérifié à nouveau avant transmission aux agents.

## 2.7.0 — 2026-10-18

- **Navigation** : Mon parc se trouve immédiatement après Vue d'ensemble.
- **Linux sans bureau** : la console noVNC rend un vrai terminal PTY et ses couleurs ANSI dans le flux RFB de l'agent, sans X11 ni connexion à Proxmox. Le repli exige aussi le droit Terminal distant ; un bureau Wayland ne déclenche pas de shell implicite. Console dédiée, taille réelle et plein écran restent disponibles.
- **Connexions SSO** : plusieurs fournisseurs, modèles Microsoft Entra ID, Google, GitHub, GitLab, Keycloak, Authentik, Okta, Auth0, Authelia, ZITADEL, Dex, Cognito, AD FS et OIDC générique. GitHub utilise OAuth 2 ; les autres utilisent OpenID Connect.
- **Approbation obligatoire** : un nouveau compte SSO n'a aucun droit ni session avant validation administrative et attribution explicite d'un profil. Les identités ne sont jamais rapprochées automatiquement par courriel. Les secrets sont chiffrés ; modifier, désactiver ou supprimer un fournisseur révoque ses sessions. La connexion locale est conservée.

## 2.6.1 — 2026-10-18

- **Vrai curseur dans le bureau distant** : les agents Windows et Linux envoient la forme du curseur du poste (flèche, texte, main, attente, redimensionnement…) avec sa transparence et son point chaud ; le navigateur l'affiche comme un curseur natif, sans décalage ni latence. Curseurs clairs ou à inversion bordés pour rester lisibles sur tous les fonds ; le point ne sert plus que de secours.
- **Journal** : export JSON d'une période ou du journal entier, export lui-même journalisé ; conservation configurable (7 jours à 2 ans ou sans limite), **30 jours par défaut**, avec rotation automatique. Nouveaux droits *Exporter le journal* (profil Auditeur) et *Configurer la conservation du journal* ; les profils intégrés suivent les nouveaux droits.
- Fiche d'un poste : boutons d'accès distant alignés et icône « nouvel onglet » centrée ; même correction pour les autres rangées de boutons.

## 2.6.0 — 2026-10-17

- **Console dans un onglet dédié** : terminal et noVNC sans la navigation du portail, barre d’outils compacte et surface disponible pour le poste ; plein écran, retour vers sa fiche et fermeture explicite.
- **Bureau natif et net** : capture Windows sans réduction imposée à 1920 × 1080, compression Tight sans perte sur Windows et X11, affichage ajusté sans agrandissement artificiel ou taille réelle 1:1 avec défilement et indication de résolution.
- **Fluidité** : rectangles modifiés au lieu d’images complètes, regroupement des paquets, envoi immédiat des saisies et des flux en attente, connexion HTTPS persistante Linux, suppression des attentes TCP inutiles sous Windows. Files bornées et acquittements conservés, reprise sans rejeu quand le récepteur ralentit.

## 2.5.0 — 2026-10-16

- **Profils d’accès granulaires** : 46 permissions par action, profils personnalisés réutilisables et modèles Administrateur, Lecture seule, Technicien du parc, Gestionnaire des dépôts et Auditeur. Duplication des modèles, attribution par compte et consultation de ses propres droits.
- Droits distincts pour inventaire, mises à jour individuelles ou globales, installation, redémarrage, retrait de l’agent, terminal et noVNC. Consultation et gestion des dépôts, du store, du serveur, des comptes, des profils et du journal séparées.
- Contrôle côté serveur de chaque route privée et navigation adaptée. Délégation limitée aux droits détenus, profils intégrés et accès propre protégés, dernier administrateur actif préservé lors de modifications concurrentes.
- Révocation des connexions et sessions distantes après changement de droits ; annulation des commandes encore en attente et vérification des droits avant leur transmission à l’agent. Audit détaillé des permissions accordées et retirées.
- Migration préservant les comptes et mots de passe existants. CLI `loco-user roles` et `loco-user set-role`. Formulaire de connexion immédiatement utilisable après révocation d’une session ; correction de l’envoi des actions du parc depuis le navigateur.

## 2.4.0 — 2026-10-16

- **Accès distant via l’agent** : terminal interactif PTY Linux et PowerShell ConPTY natif Windows, avec redimensionnement, arrêt à la fermeture et relais HTTPS sortant. Aucun accès aux consoles Proxmox.
- **Bureau noVNC** : clavier et souris sur une session Windows ouverte ou un bureau X11 Linux, sans serveur VNC à installer ni port entrant à ouvrir. Compagnon Windows inclus dans le MSI ; bibliothèques du navigateur servies localement.
- **Fenêtres de session** : plein écran avec barre d’outils et sortie explicite, et ouverture dans un nouvel onglet depuis la fiche du poste ou une session en cours.
- Accès réservé aux administrateurs, sessions liées à leur connexion, révocation et délais d’expiration, flux acquittés sans rejeu, files temporaires exclues des sauvegardes. Audit des ouvertures et fermetures sans contenu des sessions.

## 2.3.0 — 2026-10-16

- Store logiciels Windows alimenté par le catalogue communautaire Microsoft WinGet : catégories, recherche paginée, fiches, logos vérifiés et mis en cache, import d'installeurs vérifiés dans loco ; consultation du dernier index valide hors ligne.
- Agent Windows : icône de notification par session, pastille verte / orange / rouge et détails en lecture seule. L'utilisateur ne peut lancer aucune mise à jour depuis l'icône ; les jetons restent privés et le service publie uniquement l'état du poste.
- Même comptage dans Mon parc et sur Windows : vert aucune mise à jour, orange de 1 à 9, rouge dès 10 ; seuil rouge configurable. Un état indisponible reste gris. Installation, démarrage de session et retrait du compagnon gérés par le MSI.

## 2.2.0 — 2026-10-16

- **Mon parc** : agent `loco-client` pour Windows (service .NET Framework, installeur MSI déployable par GPO) et Linux
  (Debian, Ubuntu, Rocky Linux, AlmaLinux, RHEL ; systemd). Voyant vert / orange / rouge par poste, adresse IP,
  mises à jour en attente (dont sécurité), logiciels installés et historique des actions.
- Mise à jour d'un poste ou de tout le parc en un clic (Windows Update, APT, DNF, Flatpak, logiciels du catalogue),
  redémarrage facultatif, installation d'un logiciel du catalogue, inventaire, redémarrage et retrait de l'agent.
- Identité du poste indépendante de son adresse IP : un changement d'adresse ne le détache pas ; seul le retrait
  poussé depuis loco désinstalle l'agent. Autorité HTTPS épinglée, jetons hachés, liste d'actions fermée.
- Logiciels Windows : détection des versions installées sur le parc ; recherche et import depuis le catalogue winget
  (installeur officiel vérifié par SHA-256, options silencieuses et nom de détection), et nouvelles versions winget.
- Mise à jour automatique des agents ; MSI construit sous Linux (Mono, msitools) par la CI et publié avec la version.

## 2.1.3 — 2026-10-16

- Installation ISO et `setup-loco` : choix entre SSH avec le mot de passe root, SSH par clé publique, ou SSH désactivé. Le mode mot de passe ne demande aucune clé publique.
- Réponses automatiques : `SSH_MODE=password`, `key` ou `off` ; les anciens fichiers utilisant uniquement `SSH_KEY` restent compatibles.
- Configuration SSH commune à l'installation et à la console ; mots de passe vides refusés, mode par clé sans authentification par mot de passe, tests de connexions SSH réelles.

## 2.1.2 — 2026-10-16

- Portail : une requête HTTP reçue sur le port HTTPS redirige automatiquement vers HTTPS au lieu d'afficher « 400 Bad Request » ; l'hôte, le port, le chemin et les paramètres sont conservés.
- Configuration commune Linux, Alpine/ISO, Docker et Windows ; vérification de la redirection par des requêtes réelles GET, HEAD et POST dans les tests d'installation.

## 2.1.1 — 2026-10-16

- ISO : console de connexion fixée sur tty1 pour empêcher le getty de voler le clavier et l’écran de l’assistant sur tty7.
- Échap et Accueil reviennent au menu sans ouvrir de shell ; la console de dépannage est explicite et `exit` ramène à l’assistant.
- Annulation de la sélection des fonctions respectée ; test du clavier, des annulations et de la console dans une VM.

## 2.1.0 — 2026-10-15

- Appliance ISO Alpine 3.24 / BusyBox : assistant visuel, installation automatique par réponses, BIOS et UEFI, paquets embarqués et premier démarrage configuré.
- Administration graphique et CLI : configuration des accès, fonctions et caches, mises à jour de loco et du système, journaux en direct ; `setup-loco`, `loco-config` et `loco-update`.
- Agent OpenRC séparé du portail sans privilèges ; demandes revalidées, opérations sérialisées et retour arrière de loco / configuration / base en cas d’échec.
- Alpine devient une plateforme de l’installateur Linux commun, avec cache APT nginx partagé avec Windows, signatures et lecteurs de paquets Python, sauvegardes et désinstallation.
- CI Alpine native et ISO : archives vérifiées, retour arrière provoqué, installations BIOS / UEFI et redémarrages sans média ; publication des sources, ISO validée et SHA256SUMS.

## 2.0.0 — 2026-10-15

- Renommage complet en **loco** : dépôt GitHub, portail, modules Python, services, compte système, commandes, chemins et configurations des postes.
- Migration des serveurs Linux et Windows existants à la relance de l'installateur, avec sauvegarde préalable et conservation des comptes, UID Linux, certificats, clés, dépôts et caches.
- Les dépôts APT existants gardent leur Origin/Label signé pour que les anciens postes continuent leurs mises à jour sans confirmation manuelle ; les nouveaux dépôts portent loco.
- Mise à jour des postes APT/RPM au prochain setup, migration des origines Flatpak ; anciennes sauvegardes Linux et Windows restaurables.
- `linux/docker/up.sh` reprend les variables LR_ et copie les volumes historiques vers le projet loco ; les volumes d'origine sont conservés et l'ancien conteneur redémarre si la migration échoue.
- CI : migrations depuis local-repo 1.9.0 sur Debian, Ubuntu, Rocky, Docker et Windows Server 2022/2025.

## 1.9.0 — 2026-10-15

- **Organisation du projet par système**, dans une seule branche : `linux/` (installateur, administration,
  Docker, conteneur LXC Proxmox), `windows/` (installateur et service Windows), `portal/` et `conf/` communs,
  `docs/linux.md` et `docs/windows.md`. `sudo ./install.sh` reste valable (raccourci vers `linux/install.sh`),
  ainsi que les mises à jour des installations existantes ; l'arborescence installée ne change pas.
- Docker : `cd linux/docker && docker compose up -d --build` ; nom de projet fixe, volumes conservés.
- Conteneur LXC : `linux/lxc/proxmox-lxc.sh`.
- README : choix de l'installation, puis une partie Serveur Linux et une partie Serveur Windows.

## 1.8.0 — 2026-10-15

- Serveur natif Windows 11 Professionnel / Entreprise et Windows Server 2022 / 2025 : service Windows, Python embarqué, nginx et Waitress, sans Linux ni Hyper-V. Signatures OpenPGP des dépôts
  (clé RSA 4096) produites en Python : pas de GnuPG à installer, aucune modification du `PATH`.
- Installateur PowerShell relançable, composants vérifiés par SHA-256, compte de service virtuel, ACL et règles du pare-feu limitées aux réseaux autorisés.
- Dépôts APT et RPM indexés et signés sous Windows avec des lecteurs Python, comparés aux outils natifs Linux ; lecture des métadonnées sans charger tout le paquet en mémoire.
- Cache APT nginx, caches RPM/Flathub/Windows Update et catalogue Windows ; le dépôt Flatpak local reste réservé à Linux.
- Administration, sauvegardes SQLite, restauration avec arrêt du service, renouvellement HTTPS et désinstallation avec conservation des données ou purge explicite.
- Tests automatiques des serveurs Windows Server 2022 et 2025, imports de paquets gzip/xz/zstd, signatures, cache et cycle de maintenance.

## 1.7.0 — 2026-10-14

- Installation native du serveur sur Rocky Linux 8, avec le même `install.sh` et les mêmes fonctions que Rocky 9/10.
- Python 3.11 isolé, dépôt PowerTools et indexeur APT 2.6.1 compilé depuis les sources Debian avec contrôle SHA-256 ; aucun remplacement du Python système.
- Sélection du flux nginx 1.24 AppStream sur Rocky 8 si le nginx présent ne fournit pas `stream_ssl_preread`, nécessaire au relais Windows Update.
- Sauvegardes SQLite exécutées avec le runtime de l’application sur Rocky 8, compatible avec la copie en ligne de la base.
- Génération de l’autorité HTTPS compatible OpenSSL 1.1, sans extensions dupliquées, et vérification du certificat serveur par cette autorité.
- Désinstallation de l’indexeur privé et restauration du dépôt PowerTools si l’installateur l’avait activé.
- Test complet Rocky 8 ajouté à la CI, avec SELinux et firewalld actifs.

## 1.6.0 — 2026-10-14

- Installation native du serveur complet sur Rocky Linux 9 et 10 avec le même `install.sh` : DNF, EPEL/CRB, nginx, cache APT, dépôts APT/RPM, Flatpak et Windows Update.
- SELinux conservé actif, service firewalld dédié si le pare-feu tourne, filtrage du cache APT par nginx avec backend en boucle locale.
- Rocky 10 : cache APT compilé depuis les sources Debian 3.7.5-1.3 avec correctifs et empreintes figées.
- Désinstallation adaptée à DNF, restauration des réglages SELinux, du pare-feu et de nginx ajoutés par l’application.
- Tests du serveur dans des VM Rocky 9/10, ajoutés à la CI ; installation compatible avec une umask restrictive.

## 1.5.1 — 2026-10-14

- Vue d'ensemble, *Brancher un poste* : un onglet par type de poste (Debian / Ubuntu ; Rocky Linux,
  AlmaLinux, RHEL ; Flatpak ; Windows), chacun avec sa commande, le premier branchement et le lien vers
  ses options. Windows précise que Windows Update passe par le DNS, sans rien installer sur le poste.
- Onglets sur une seule ligne, défilants sur petit écran.

## 1.5.0 — 2026-10-14

- Mot de passe temporaire (première connexion, réinitialisation) : page dédiée, sans la navigation du
  portail, avec déconnexion possible ; aucune autre page n'est accessible avant le changement.
- Thème : OLED par défaut, bascule vers le thème clair par une icône discrète (lune) dans la barre de
  navigation et sur les écrans de connexion ; choix mémorisé par le navigateur.

## 1.4.0 — 2026-10-14

- **Conteneur LXC Proxmox VE** : `lxc/proxmox-lxc.sh`, à lancer sur n'importe quel hôte Proxmox VE 7, 8 ou 9,
  crée un conteneur Debian non privilégié (stockage, pont et modèle choisis selon l'hôte, IP fixe ou DHCP,
  VLAN) et y fait l'installation native ; options d'`install.sh` transmises après `--` ; mise à jour par
  `--update`, version fixée par `--version`, copie locale par `--source`.

## 1.3.0 — 2026-10-14

- **Déploiement Docker** (`docker compose up -d --build`), en plus de l'installation native qui reste
  recommandée : image Debian 13, configuration par variables (`.env.example`) appliquée à chaque démarrage
  par `install.sh --container`, services lancés par `supervisord`, tâches périodiques (statistiques,
  rechargement nginx, rotation des journaux, sauvegarde quotidienne) par `local-repo-cron`.
- Conteneur sans capacités superflues, `no-new-privileges`, comptes de service à identifiants fixes,
  contrôle de santé, volumes pour les données, la configuration et les sauvegardes.
- Le portail reconnaît le mode conteneur : état des services, commandes d'aide et restauration adaptées.
- Validation dans une VM Debian 13 jetable (`tests/vm-docker-e2e.sh`).

## 1.2.0 — 2026-10-13

- **Famille Red Hat** : cache des miroirs Rocky Linux, AlmaLinux et EPEL (paquets et métadonnées à nom
  unique gardés 6 mois, certificats des miroirs vérifiés) ; dépôts RPM locaux par version majeure
  (`el8`, `el9`, `el10`, ajout d'une future version depuis le portail), métadonnées signées, refus des
  paquets d'une autre version ou d'un contenu différent pour un même nom/version.
- Script `rpm/setup.sh` : détection du poste, dépôt local, dépôts officiels et EPEL via le cache avec leurs
  clés GPG, `--epel`, `--trust-ca`, `--no-cache`, `--no-local`, `--remove` (rétablissement exact des dépôts).
- RHEL : dépôt local et EPEL via ce serveur ; dépôts Red Hat (abonnement) en accès direct.
- Options `--no-rpm` et `--rpm-cache-size` ; sauvegarde, statistiques, `/metrics` et désinstallation
  couvrent les dépôts RPM.
- Tests réels (`rpmbuild`, `createrepo_c`, signatures) et validation de postes Rocky Linux / AlmaLinux 8, 9
  et 10 dans des VM, ajoutée à la CI.

## 1.1.0 — 2026-10-13

- Dépôts APT isolés par distribution et nom de version, création depuis le portail ou `local-repo-apt`.
- Détection automatique des postes Debian / Ubuntu, sources deb822 ; options `--cache-only`,
  `--distribution`, `--suite`, `--legacy`. Une version absente ne remplace pas la configuration du poste.
- Dépôt historique conservé, copie explicite de paquets entre dépôts ; publication sans remplacement
  d'un paquet/version par un contenu différent ; métadonnées signées préparées avant publication.
- Sauvegarde et restauration de tous les dépôts, migration du registre des versions.
- Corrections de compatibilité : HTTPS APT en accès direct, connexions sortantes du cache conservées,
  nettoyage du cache des postes lors d'un changement de dépôt ; prise en charge du canal de contrôle
  des nouvelles versions de Gunicorn et délais bornés pour vérifier le démarrage.
- Tests d'isolation et de signatures, test de migration depuis 1.0.0 ; matrice de VM Debian 12/13 et
  Ubuntu 22.04/24.04/26.04, images officielles vérifiées, contrôles hebdomadaires actifs dans la CI.
- Navigation Linux / Windows avec icônes et menus au survol, au clavier et au toucher ; icônes du compte.

## 1.0.0 — 2026-10-13

Première version destinée à un usage en entreprise.

### Ajouté
- **Flatpak** : cache Flathub (objets immuables gardés 6 mois, signatures Flathub vérifiées par les postes)
  et dépôt Flatpak local signé, alimenté par envoi de paquets `.flatpak` ; script `flatpak/setup.sh`.
- **Comptes nommés** (administrateur, lecture seule), mot de passe temporaire à changer, blocage après échecs,
  expiration des sessions, journal d'audit, gestion en ligne de commande (`local-repo-user`).
- **HTTPS partout** : postes clients en HTTPS par défaut, scripts suivant le protocole utilisé,
  `setup.sh --trust-ca` pour l'amorçage, `--no-http` pour fermer le port HTTP.
- **Réseaux autorisés** (`--allow`, `--admin-allow`) appliqués par nginx, apt-cacher-ng et systemd.
- **Proxy d'entreprise** (`--proxy`) pour apt-cacher-ng et les téléchargements du portail.
- **Sauvegardes** quotidiennes avec rotation, restauration sur le même serveur ou un nouveau
  (`local-repo-backup`).
- **Supervision** : `/metrics` (Prometheus) et `/healthz`.
- Page **Administration** (comptes, journal, système, sauvegardes).
- Tests unitaires, test de bout en bout (`tests/e2e.sh`) et intégration continue GitHub.
- Guide d'exploitation (`docs/EXPLOITATION.md`) et politique de sécurité (`SECURITY.md`).

### Modifié
- Politique de contenu stricte (CSP) : plus aucun script ni style en ligne.
- Unité systemd du portail confinée ; mots de passe d'apt-cacher-ng pour sa maintenance.
- Portail réorganisé en paquet Python ; base SQLite en WAL avec migrations versionnées.
- Interface : navigation regroupée, sélecteur de thème dans *Mon compte*.

### Sécurité
- Le compte unique des versions précédentes devient le compte `admin` (même mot de passe) ;
  le fichier `/etc/local-repo/admin-password` est supprimé.

## 0.3 — 2026-10-13
- Interface en HTTPS (autorité locale), refonte visuelle sobre (IBM Plex, thèmes clair et OLED).

## 0.2 — 2026-10-13
- Cache Windows Update limité aux domaines de la liste, fichiers immuables seuls en cache,
  corrections issues des tests (Debian 12/13, Ubuntu 24.04, Windows 11).

## 0.1 — 2026-10-12
- Première version : apt-cacher-ng et dépôt APT local, cache Windows Update, catalogue de logiciels Windows,
  portail web, `install.sh` et `remove.sh`.
