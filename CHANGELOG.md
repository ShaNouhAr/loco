# Journal des versions

## 2.17.0 — 2026-10-20

- **Console noVNC plus réactive** (agent Windows 2.17) : l'écran est lu par **duplication DXGI** — seulement quand il change, avec les zones modifiées — au lieu d'une capture GDI complète à chaque image (≈ 180 ms sur une machine virtuelle). Le compagnon garde la demande d'image tant que rien ne bouge et y répond dès le premier changement, sans retarder la saisie ; plus aucun échange ni calcul sur un bureau immobile (≈ 1 % d'un cœur contre ≈ 35 %). GDI prend le relais sur le bureau sécurisé, avec plusieurs écrans ou un pilote sans duplication, et la duplication est rouverte aussitôt après un changement de mode. Tampons réutilisés, au plus ~30 images par seconde. Mesuré sur la VM de démonstration : saisie → image modifiée de 257 à ~135 ms (pire cas 641 → ~250 ms), image complète de 310 à ~85 ms.
- **Relais plus court** : l'agent repart dès qu'une image est prête (en un seul échange) et, session ouverte, relance toutes les 12 ms (aller-retour pur du relais 48 → 33 ms) ; les transactions du relais, éphémères, ne forcent plus d'écriture disque à chaque requête (coût par requête 5,4 → 2,1 ms), le reste de loco gardant sa durabilité complète ; décodage base64 plus rapide dans le navigateur, interrogation espacée après une seconde de calme.
- **Plus d'ascenseurs en résolution adaptée** (y compris en sortant du plein écran) : l'image est toujours ajustée à la fenêtre, et le zoom y agrandit l'interface du poste sans défiler. En résolution du poste, le zoom agrandit l'image, avec des ascenseurs fins. Réglages d'affichage mémorisés par navigateur ; « Ajuster » par défaut.
- **Curseur unique et net** : sur les machines dont le pilote n'a pas de curseur matériel (VM, adaptateur de base), Windows dessine le curseur dans l'image ; l'agent le reconnaît et le navigateur n'affiche plus qu'un point de repère au lieu d'une seconde flèche décalée. Le curseur suit l'échelle du bureau affiché (zoom, ajustement).
- **Console repensée** : pastille d'état avec la latence vers le serveur (reprise dans le rail en plein écran), superposition de connexion puis de fin de session avec « Nouvelle session » en un clic, boutons à icônes, touches maintenues façon touches de clavier, libellés réduits aux icônes sur fenêtre étroite. En plein écran, un clic sur le bureau replie le rail de commandes.
- **Presse-papiers** : option d'envoi automatique du presse-papiers local au poste à chaque clic sur le bureau (autorisation du navigateur demandée une fois).
- Un arrêt anormal du compagnon de bureau est tracé dans le journal de l'agent ; une capture en échec retombe sur GDI au lieu de fermer la session.

## 2.16.0 — 2026-10-19

- **Console noVNC — plein écran repensé (bureau)** : l'image du poste occupe **tout l'écran**, sans en-tête. Les commandes (résolution, zoom, Ctrl+Alt+Suppr, raccourcis, touches maintenues, envoi de texte, presse-papiers, nouvel onglet, fermeture) passent dans un **rail latéral gauche rétractable** ; une poignée au bord de l'écran le déploie ou le replie.
- **Curseur du poste corrigé en plein écran** : le curseur logiciel de secours de noVNC (utilisé notamment sur les postes tactiles) était rendu hors de l'élément plein écran — donc **invisible en plein écran** et **décalé au retour**. Il est maintenant replacé dans la console le temps du plein écran, puis rétabli : curseur visible et aligné dans les deux cas.
- Le plein écran est piloté par une classe posée au changement d'état (indépendant du moteur) ; le plein écran du terminal est inchangé.

## 2.15.0 — 2026-10-19

- **Console noVNC — affichage repensé** : deux menus indépendants façon RDP. *Résolution* (Adaptée à la fenêtre, qui change la résolution Windows pour suivre la fenêtre en pixels physiques, ou Résolution du poste, qui garde et rétablit celle d'origine) et *Zoom* (Ajuster, ou 50/75/100/125/150/200 % avec défilement ; le pointeur reste juste à tout zoom car l'échelle propre de noVNC est pilotée). La résolution et le pourcentage obtenus restent affichés. Le bureau se centre, se défile dans les deux sens et remplit proprement la fenêtre sans bandes noires inutiles.
- **Compression JPEG adaptative du bureau** (agent Windows 2.15) : les zones complexes (photos, dégradés, fonds d'écran) sont envoyées en JPEG (sous-encodage Tight standard, décodé par noVNC), tandis que le texte, les aplats et les icônes restent **sans perte** (Tight zlib). Beaucoup moins de bande passante et un bureau plus fluide, sans perte visible sur l'interface. Repli automatique sur le zlib sans perte si l'encodeur n'est pas disponible.
- **Presse-papiers texte partagé** (agent Windows 2.15) : panneau *Presse-papiers* de la console. Le texte copié sur le poste s'affiche dans le navigateur (et, si celui-ci l'autorise, est recopié dans le presse-papiers local) ; *Envoyer au poste* place un texte dans le presse-papiers du poste pour l'y coller (Ctrl+V). Accents conservés (Latin-1), fins de ligne normalisées, taille bornée. Uniquement sur le bureau utilisateur, jamais sur le bureau sécurisé ; rien n'est conservé par loco.

## 2.14.0 — 2026-10-19

- **Annuaires LDAP et RADIUS à la place d'OpenID Connect** (*Administration › Annuaires*) : connexion depuis le formulaire habituel de loco avec un compte **LDAP** (Active Directory, OpenLDAP, 389 DS, FreeIPA ; LDAPS ou StartTLS obligatoire, autorité PEM facultative, recherche par compte de service ou liaison directe, filtres échappés selon la RFC 4515, mot de passe vide refusé) ou **RADIUS** (PAP, secret partagé, attribut Message-Authenticator envoyé et exigé dans les réponses contre BlastRADIUS). Annuaires ordonnés, bouton **Tester** sans création de compte, secrets chiffrés et jamais réaffichés. Chaque nouvelle identité attend une **approbation manuelle** dans *Comptes* ; aucun rapprochement avec un compte local. Modifier ou désactiver un annuaire ferme les sessions de ses comptes, le supprimer les désactive. Fonctionnalité désactivable. Les comptes locaux restent toujours disponibles.
- **OpenID Connect retiré** : les fournisseurs existants sont désactivés à la mise à jour et leurs transactions effacées ; les comptes concernés sont conservés sans accès et signalés dans *Comptes*, où **Mot de passe local** les convertit en comptes locaux. La bibliothèque PyJWT embarquée est supprimée. Droits renommés : « Gérer les annuaires », « Approuver les comptes d'annuaire ».
- **Centre de logiciels** : le « Store WinGet » est renommé « Centre de logiciels » dans toute l'interface (adresses et droits inchangés).
- **Profil « Opérateur »** : le profil intégré « Technicien du parc » est renommé, y compris sur les installations existantes (sauf si un profil personnalisé porte déjà ce nom).
- **Apparence** : menu dans l'en-tête et onglet de *Mon compte* ; nouveau thème **Sombre doux** (anthracite) à côté d'OLED et Clair, et **couleur d'accent** au choix (neutre, bleu, vert, violet, orange, rose) pour les boutons principaux, sélections, onglets, focus et la navigation active. Textes secondaires plus contrastés (WCAG AA) en clair et en OLED.
- **Scripts** (*Mon parc › Scripts*) : bibliothèque de scripts PowerShell (Windows) et shell (Linux) avec description, versions (une nouvelle à chaque changement de contenu) et auteur ; exécution sur des appareils ou groupes de machines (seuls les postes compatibles et actifs sont retenus), contenu figé et vérifié par son empreinte SHA-256 sur le poste, exécution en SYSTEM ou root avec un délai de 30 minutes ; suivi par exécution (réussis, en attente, échecs, sortie standard et erreurs, code de retour) et annulation des postes en attente. Droits distincts : consulter, gérer, exécuter (sensible, réservé par défaut aux administrateurs et revérifié avant l'envoi à l'agent). La version relue doit être celle exécutée. Fonctionnalité désactivable. Agents Windows et Linux 2.14.0 requis.
- **Avancement des déploiements en direct** : l'agent Windows remonte l'étape en cours de chaque installation (téléchargement de l'installeur avec pourcentage, vérification de l'empreinte, installation, logiciel n/total ; mises à jour Windows pour l'action Mettre à jour), au plus une fois par seconde. La page du déploiement affiche pour chaque poste une barre de progression et l'étape (« Téléchargement de 7-Zip · 45 % · logiciel 1/3 »), rafraîchies toutes les 3 secondes ; la fiche du poste reprend l'étape dans ses actions en attente. Les étapes invalides sont ignorées ; les anciens agents gardent l'affichage par états.
- **Fonctionnalités activables** (*Administration › Fonctionnalités*, réservé au compte local « admin » de l'installation, même pour les autres administrateurs) : Paquets APT, Paquets RPM, Flatpak, Cache Windows Update, Mon parc et agents, Logiciels Windows, Accès distant, Scripts, Annuaires. Une fonctionnalité désactivée disparaît de la navigation, des boutons et de la vue d'ensemble ; ses pages et API sont refusées avec un message explicite, ses droits deviennent inopérants, les agents reçoivent « réessayer plus tard » (503) quand le parc est coupé et les installations attendent la réactivation des logiciels Windows. Les dépendances sont appliquées (l'accès distant suit le parc). Sur l'appliance, les caches Windows Update, RPM et Flatpak sont aussi arrêtés ou relancés ; ailleurs, les services réseau restent démarrés (indiqué sur la page). Chaque changement est journalisé et les données sont conservées.
- **Panneau de commandes de la console noVNC** : Ctrl+Alt+Suppr, raccourcis (menu Démarrer, Exécuter, Explorateur, Afficher le bureau, Verrouiller, Gestionnaire des tâches, Alt+Tab, Alt+F4, Échap, Impr. écran, menu contextuel), touches Ctrl/Alt/Maj/Win maintenues jusqu'au prochain clic, et **Envoyer du texte** tapé caractère par caractère quelle que soit la disposition du clavier du poste (mots de passe, commandes ; rien n'est conservé).
- **Ctrl+Alt+Suppr réel sous Windows** : Windows n'accepte la séquence sécurisée que d'un service. Le compagnon de bureau la signale au service loco-client par un événement réservé à SYSTEM ; le service l'envoie (SendSAS) uniquement si une session de bureau à distance est ouverte, en autorisant la stratégie SoftwareSASGeneration le temps de l'appel puis en rétablissant sa valeur. L'écran « Sécurité Windows » s'affiche et se pilote dans la console. Touches Impr. écran, Menu et Pause ajoutées à l'agent Windows.
- **Mon compte repensé** : en-tête avec initiale, profil, type de compte et dates ; onglets Sécurité (règles du mot de passe vérifiées en direct, bouton Afficher), Apparence (aperçu des thèmes), Mes droits (regroupés par domaine avec leur nombre) et Activité récente (dix dernières actions du compte, lien vers son journal). Un lien direct vers un onglet (`/compte#apparence`) fonctionne aussi sans recharger la page.

## 2.13.4 — 2026-10-19

- Lien direct du MSI de l'agent : si le serveur ne peut pas encore fournir le MSI de sa version (GitHub momentanément injoignable…), il répond 503 avec la raison et « Réessayez dans un instant », et l'inscrit dans son journal, au lieu d'un 404 sans explication.
- Le MSI récupéré depuis la publication est écrit sous un nom temporaire unique avant d'être mis en place : deux téléchargements simultanés ne peuvent plus se gêner.

## 2.13.3 — 2026-10-19

- **MSI de l'agent Windows en téléchargement direct** dans la fenêtre *Ajouter un poste*, juste après les commandes : bouton « Télécharger le MSI » (version et empreinte SHA-256 affichées), liens directs à ouvrir depuis le navigateur du poste (HTTPS et, s'il est activé, HTTP sans avertissement de certificat), puis la commande `msiexec` prête à copier qui installe et inscrit le poste avec l'adresse de loco, la clé d'inscription et l'empreinte de l'autorité. Un simple double-clic sur le MSI installe le service sans l'inscrire : c'est indiqué.

## 2.13.2 — 2026-10-19

- **Le bureau à distance remplit la fenêtre** : en mode automatique, l'agent Windows choisit parmi les résolutions du pilote celle qui remplit le mieux la zone d'affichage (rapport d'image conservé), puis la plus proche de 100 %. Une fenêtre très large obtient par exemple 2560 × 1080 (21:9) au lieu de 1280 × 720 avec de grandes bandes noires.
- La taille demandée est calculée en pixels de l'écran et non en pixels CSS : avec un affichage mis à l'échelle (150 % sur un portable, par exemple), le bureau n'est plus réduit d'autant, et un pixel du bureau correspond à un pixel affiché. Le pourcentage indiqué à côté de la résolution suit la même règle (100 % = net).

## 2.13.1 — 2026-10-19

- **Résolution dynamique Windows corrigée** : le mode automatique change réellement la résolution du bureau Windows à mesure que la fenêtre est agrandie ou réduite, y compris en plein écran. L’agent utilise les résolutions acceptées par le pilote graphique ; la résolution initiale est restaurée à la fermeture. Les demandes pendant l’UAC sont appliquées au retour sur le bureau normal. Les postes multi-moniteurs et les sessions concurrentes conservent un ajustement de l’affichage sans reconfigurer les écrans.
- La taille demandée suit toute la surface disponible, sans rester limitée à la résolution ou au rapport d’image de départ. Si le pilote impose une taille différente, le navigateur ajuste le bureau entier à la fenêtre, en conservant son rapport d’image.
- **Machines virtuelles** : le pilote d’affichage de base de Windows (adaptateur VGA standard d’une VM sans pilote graphique, dont Proxmox par défaut) ne propose qu’une seule résolution et refuse tout changement ; l’image est alors ajustée à la fenêtre par le navigateur. Pour un vrai redimensionnement, utiliser un adaptateur VirtIO-GPU avec son pilote (virtio-win) ou le pilote du GPU physique.
- Le service laisse jusqu’à cinq secondes au compagnon de bureau pour rétablir la résolution d’origine à la fermeture de la session.

## 2.13.0 — 2026-10-19

- **Console noVNC adaptative** : résolution du flux ajustée à la fenêtre, au plein écran et à l’onglet dédié, avec conservation du rapport d’image et limitation à la source pour le bureau Windows, et retour au flux natif. Protocole RFB ExtendedDesktopSize borné ; repli sur l’ajustement local avec les anciens agents et les bureaux X11. L’écran physique du poste n’est pas reconfiguré.
- Les demandes d’image consécutives sont regroupées pour éviter une file de rafraîchissements pendant les redimensionnements et transitions UAC ; une demande de rafraîchissement complet reste prioritaire.
- **Console texte Linux** : redimensionnement réel du PTY, du nombre de colonnes/lignes et du rendu, y compris pour les dimensions qui ne sont pas des multiples de la taille des glyphes.
- **UAC Windows** : compagnon graphique SYSTEM dans la session interactive et suivi du bureau d’entrée, y compris le bureau sécurisé. Les transitions de capture et les saisies momentanément bloquées ne font plus tomber la connexion. Canal local restreint à SYSTEM et vérification du PID conservée ; le tray reste non privilégié.
- **Éditeur de profils d’accès** : catégories en navigation, recherche sans accents, filtres des droits accordés/actions sensibles, compteurs par domaine, sélection des droits visibles avec état partiel, indication des dépendances ajustées et barre d’enregistrement persistante. Profils protégés et limites de délégation conservés.

## 2.12.1 — 2026-10-19

- **Agents Windows mis à jour après le serveur** : le serveur ne proposait la nouvelle version de l'agent Windows que si son MSI avait déjà été téléchargé depuis le portail (bouton d'installation ou lien de déploiement). Sans cette action, les postes Windows restaient sur leur ancienne version. Désormais, dès qu'un agent Windows plus ancien que le serveur se présente, le serveur récupère le MSI de sa version sur GitHub (vérifié par SHA256SUMS) en arrière-plan, au plus une tentative toutes les dix minutes, et le propose au contact suivant.
- Test du journal rendu indépendant de la seconde à laquelle il s'exécute.

## 2.12.0 — 2026-10-19

- **Journal repensé** : événements groupés par jour (aujourd'hui, hier, puis la date), heure seule, actions en clair avec leur catégorie au lieu des codes techniques (le code reste en infobulle et dans l'export). Échecs signalés par une pastille rouge et résumés en tête de page sur les dernières 24 heures.
- **Filtres côté serveur** sur tout le journal (et plus seulement les 500 derniers événements) : période (aujourd'hui, 7 jours, 30 jours, tout ou dates au choix), catégorie avec son nombre d'événements, compte, échecs seulement, recherche ; pages de 100 événements (« Plus anciens »). Un clic sur un compte filtre ses événements. Sur mobile, le détail passe sous l'action.
- **Export et conservation dans des fenêtres** ouvertes depuis l'en-tête : l'export reprend la vue filtrée (ou une période, ou tout) et ajoute le libellé de chaque action et les filtres appliqués au fichier JSON.
- **Windows Update repensé** : indicateurs sur 14 jours (servi par le cache et part du trafic, pris sur Internet), taille du cache et état du service, dernière requête et nombre de postes ; graphique quotidien des 14 derniers jours ; alerte si aucun poste n'est encore passé par loco ou si le cache est arrêté.
- Domaines redirigés en liste compacte, modifiables dans une fenêtre ; explication du fonctionnement repliée.
- Dernières requêtes : nom du poste du parc à la place de son adresse (droit *Consulter les appareils*), source « cache » ou « Internet ».
- **Catalogue local repensé** : pastilles des points à traiter avec leur nombre (mises à jour à déployer sur le parc, arguments silencieux manquants, erreurs, téléchargements en cours) ; colonnes réduites à l'essentiel (logiciel, version, postes équipés et en retard avec un lien « déployer », taille, état). Type et origine passent sous le nom ; « Réessayer » sur un téléchargement en échec et « Renseigner » sur un installeur sans arguments, directement dans la liste.
- **Fiche d'un logiciel** : en-tête avec Modifier, Déployer et un menu Plus (fiche du store, retélécharger, supprimer) ; indicateurs (état de l'installeur, postes équipés, postes à mettre à jour, groupes) ; liste des postes avec leur version et « Déployer la mise à jour ». Après une modification depuis la fiche, on y revient.
- **Store WinGet repensé** : cartes compactes (quatre par ligne sur grand écran) entièrement cliquables vers la fiche ; bouton **Ajouter** directement sur chaque carte, qui reste sur la recherche ou la catégorie en cours pour en ajouter d'autres ; état « dans loco », « téléchargement » ou « erreur » pour les logiciels déjà ajoutés. Recherche et catégories en pastilles regroupées en tête ; liens en double vers le catalogue local retirés.
- **Fiche du store** : éditeur, version et identifiant sous le nom ; actions regroupées dans l'en-tête (Ajouter à loco ou Voir dans le catalogue local, Déployer) ; description et détails d'installation (installeur, arguments, empreinte) côte à côte ; avertissement quand le store propose une version plus récente que celle du catalogue local.
- **Groupes de logiciels et de machines repensés** : liste compacte avec la description, un aperçu des membres (icônes des logiciels, noms des appareils) et leur état résumé (logiciels prêts ou en erreur ; appareils à jour, en attente, nombreuses mises à jour) ; « Déployer » et, pour les machines, « Voir dans Mon parc » sur chaque ligne.
- **Éditeur de groupe** : membres affichés en premier avec un compteur en direct, filtre « membres seulement » et recherche ; icône, version et état de chaque logiciel, système et voyant de chaque appareil ; barre Enregistrer toujours visible en bas ; suppression du groupe dans le menu Plus. En lecture seule, seuls les membres sont listés.
- Les champs côte à côte (groupes, profils, configuration de l'appliance) sont de nouveau alignés.
- **Déploiements repensés** : liste avec pastilles par état (en cours, à confirmer, avec erreurs, terminés), logiciels nommés et barre d'avancement par déploiement (réussis, en attente, échecs). Le suivi d'un déploiement montre quatre indicateurs, la barre d'avancement et les postes en premier, avec le journal d'un échec déplié d'office ; les empreintes passent en infobulle. Confirmer, Abandonner et Annuler sont dans l'en-tête, et un déploiement non confirmable dit pourquoi (autre connexion, installeur pas prêt, droit manquant).
- **Préparation d'un déploiement** : sélections affichées en premier (avec les icônes des logiciels), compteurs en direct, appareils compatibles avant les autres, et barre « Vérifier avant de déployer » toujours visible avec le nom et l'option de réinstallation.
- Les listes actualisées en direct échappent aussi les guillemets des noms de fichiers demandés par les postes.

## 2.11.0 — 2026-10-19

- **Mon parc repensé** : postes classés du plus urgent au plus à jour ; adresse réelle du poste (et « via » l'adresse vue par loco quand un NAT les sépare) ; mises à jour en compact (système, logiciels, dont sécurité) ; redémarrage signalé ; groupes en étiquettes cliquables ; dernier contact en clair ; action en attente affichée à la place du bouton.
- **Filtres immédiats** : pastilles d'état avec leur nombre (tous, nombreuses mises à jour, en attente, état inconnu, à jour), Windows / Linux, groupe appliqué dès son choix, recherche ; « Tout mettre à jour » et « Déployer » suivent le groupe affiché.
- **Ajouter un poste** dans une fenêtre (commandes Windows et Linux, déploiement GPO) au lieu d'un panneau en bas de page.
- **Onglet Réglages** du parc : adresse du serveur annoncée aux agents, seuil des pastilles et clé d'inscription.

## 2.10.1 — 2026-10-19

- **Fiche d'un appareil repensée** : en-tête avec le nom, une pastille d'état courte (à jour, mises à jour en attente, nombreuses mises à jour, sans contact récent…), l'explication détaillée, le système, l'adresse du poste, le dernier contact en clair (« il y a 2 min ») et la version de l'agent ; groupes en étiquettes.
- Actions regroupées à droite : terminal et bureau à distance, **Mettre à jour** en bouton principal, et un menu **Plus** (mise à jour avec redémarrage, inventaire, déploiement, redémarrage, puis le retrait de l'agent, séparé et en rouge).
- Quatre indicateurs (mises à jour système dont sécurité, logiciels ou Flatpak, redémarrage, inventaire) et des **onglets** Mises à jour, Logiciels installés (avec filtre), Historique et Détails ; l'onglet choisi est conservé lors de l'actualisation automatique.
- Détails techniques séparés : adresses du poste, adresse vue par loco (NAT signalé), adresses du serveur connues de l'agent, identifiant et groupes.

## 2.10.0 — 2026-10-18

- **Graphiques dans la vue d'ensemble**, tracés en SVG par le serveur (aucune bibliothèque ni script, thèmes OLED et clair, infobulles au survol) :
  - **Windows Update sur 14 jours** : volume servi par le cache et volume téléchargé sur Internet, jour par jour, avec la part économisée. Historique reconstitué depuis les journaux nginx tournants (`.1`, `.2.gz`, `.AAAAMMJJ`), journal courant lu de façon incrémentale ;
  - **État du parc** : anneau vert, orange, rouge, gris (droit *Consulter les appareils*) ;
  - **Disque des données** : part de chaque cache (APT, Windows Update, RPM, Flathub), des dépôts et logiciels locaux, du reste et de l'espace libre.

## 2.9.0 — 2026-10-18

- **Changement d'adresse du serveur sans perdre les agents** : la future adresse (IP ou nom DNS) s'annonce dans *Mon parc › Adresse du serveur pour les agents* ou avec `loco-fleet announce https://hôte:port`. Chaque agent la reçoit à son contact suivant ; la page et `loco-fleet servers` indiquent combien de postes sont prévenus.
- Après le changement, un agent qui ne joint plus l'ancienne adresse essaie celles qu'il connaît et adopte la première qui répond avec un certificat de l'autorité loco épinglée : une adresse annoncée ne peut pas détourner les postes vers un autre serveur. Les réponses HTTP du serveur (jeton refusé…) ne déclenchent jamais de bascule.
- `setup-loco` (appliance) annonce la nouvelle adresse fixe et attend que les postes soient prévenus avant d'appliquer le réseau ; en DHCP, il prévient que les agents ne pourront pas suivre.
- Agents Windows et Linux 2.9.0 : liste des adresses mémorisée et déclarée au serveur, affichée par `loco-client status`.

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
