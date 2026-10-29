# Journal des versions

## 2.93.0 — 2026-10-29

- **Windows Update : section « Diriger les postes » désencombrée.** Les deux méthodes (DNS, proxy via l'agent) tiennent en deux colonnes courtes et équilibrées ; le détail des conditions et la commande manuelle PowerShell passent dans un dépliant « Réglage manuel et dépannage du proxy » — fini le pavé de texte et la commande qui débordait.

## 2.92.0 — 2026-10-29

- **Réglages du parc et Configuration, plus aboutis.** *Réglages du parc* : l'adresse actuelle du serveur passe dans un encart avec son état juste à côté, et le champ de nouvelle adresse est clairement étiqueté. *Configuration* (appliance) : les menus « activé / désactivé » deviennent des **interrupteurs** (Accès HTTP, Cache Windows Update, Red Hat, Flatpak), chacun avec une courte description ; et chaque limite de cache affiche son **usage réel** en dessous.

## 2.91.0 — 2026-10-29

- **Composeur d'enrôlement plus sobre.** Les cases du composeur passent en **interrupteurs** (comme la page Fonctionnalités) : une liste encadrée, un libellé et sa description à gauche, l'interrupteur à droite, la ligne grisée quand elle est éteinte. L'option « première installation » est mise à part. La commande générée est inchangée.

## 2.90.0 — 2026-10-29

- **Composeur d'enrôlement.** La page *Enrôlement* devient un sélecteur : on **coche ce que le poste reçoit** — **Gestion (agent)**, **Dépôt loco**, **Cache des mises à jour** (et, côté Windows, **Rediriger Windows Update**), avec une case **Tout** — et la **commande à lancer se génère en direct**, par système (Windows, Debian/Ubuntu, Red Hat). Le téléchargement du paquet pour un poste hors ligne et le déploiement en masse (GPO, Intune) restent dans « Autres méthodes ».
- **Dépôts Debian/Ubuntu par défaut.** loco crée désormais d'office les dépôts **Debian 12 et 13, Ubuntu 22.04 et 24.04** (comme el8/el9/el10 côté Red Hat) : plus besoin de les créer à la main avant de brancher un poste Debian. La section *Vos dépôts* est revue — le bouton « Ajouter des paquets » passe dans l'en-tête du dépôt choisi.
- **Cache : noms plus parlants dès la mise à jour, repli plus pratique.** Les libellés « Mise à jour Windows », « Métadonnées du dépôt »… apparaissent maintenant tout de suite après une mise à jour de loco (l'agent reconstruit l'index du cache à son démarrage), sans attendre une analyse manuelle. Et quand la liste complète est dépliée, elle reste à hauteur limitée : plus besoin de descendre jusqu'en bas pour la replier.

## 2.89.0 — 2026-10-28

- **Liste du cache plus lisible.** Dans les pages **Windows Update** et **Paquets Linux**, la liste des fichiers en cache n'affiche plus que les **5 plus récents** — le reste se déplie (et se replie) d'un clic — et chaque ligne indique **à quoi le fichier correspond** (« Mise à jour Windows », « Définitions Microsoft Defender », « Microsoft Edge », « Métadonnées du dépôt »…), avec le nom de fichier juste en dessous. Les paquets .deb et .rpm gardent leur nom tel quel, déjà parlant.

## 2.88.0 — 2026-10-28

- **Voir et vider les caches.** Les pages **Windows Update** et **Paquets Linux** listent maintenant ce qui est réellement en cache — chaque fichier (mise à jour Windows, paquet Debian ou Ubuntu, miroir Red Hat ou EPEL) avec son origine, sa taille et sa date de mise en cache — et permettent de **supprimer un élément** ou de **vider tout le cache** d'un clic. Les tranches d'un même fichier (Windows Update) sont regroupées en un seul objet ; un fichier supprimé repart simplement de son serveur d'origine à la prochaine demande d'un poste, rien n'est perdu.
  - La liste se filtre, se rafraîchit en direct et reste bornée aux objets les plus récents sur un gros cache. L'analyse et la purge passent par l'agent (le contenu des caches n'est lisible que par lui) ; côté APT, le cache nginx de l'appliance comme apt-cacher-ng d'un serveur Debian sont pris en charge.
- **Pages revues.** Présentation plus sobre : un résumé factuel (taille du cache, part servie localement, 14 derniers jours) remplace les grandes vignettes, et le contenu du cache prend la place centrale.

## 2.87.0 — 2026-10-28

- **Mon parc : redémarrer depuis la liste.** Quand un poste attend un redémarrage, un bouton **Redémarrer** apparaît dans la liste, à l'endroit même de *Mettre à jour*. Si le poste a aussi des mises à jour en attente, les deux boutons cohabitent côte à côte. Le redémarrage est confirmé avant l'envoi et prévient la personne connectée.

## 2.86.0 — 2026-10-28

- **Écran de connexion repensé.** Une barre légère (loco, le serveur, la langue et le thème) et une carte centrée, plus aérée : titre et phrase d'accueil, champs plus confortables, et une mention **SSO** claire quand des annuaires d'entreprise sont actifs. Les erreurs (mot de passe incorrect, trop de tentatives) s'affichent dans la carte, une seule fois. Même présentation pour la double authentification (avec un lien pour changer de compte) et le choix du mot de passe à la première connexion.

## 2.85.0 — 2026-10-28

- **Annuaires et SSO, plus simples.** La page (renommée *Annuaires et SSO*) liste les annuaires dans un tableau : type, serveur, nombre de comptes, **interrupteur Actif / Inactif** (désactiver ferme les sessions de ses comptes), Tester et Modifier, ordre d'essai dans le menu. Les demandes d'accès en attente sont signalées avec un lien vers Comptes.
  - Ajout guidé par type : **Active Directory** (le domaine suffit ; contrôleur et compte de service facultatifs), **FreeIPA** (domaine et serveur ; comptes sous `cn=users,cn=accounts`, autorité FreeIPA indiquée), autre annuaire LDAP ou RADIUS. Les réglages techniques passent dans « Options avancées ». Le serveur accepte un port (`dc1:3269`, catalogue global en SSL).
  - L'écran de connexion parle de connexion SSO avec le compte d'entreprise.

## 2.84.0 — 2026-10-28

- **Autorité de certification au choix.** Nouvel onglet *Système › Certificat* avec un interrupteur **Autorité loco / Autorité d'entreprise** : le certificat du portail peut être signé par l'autorité de votre entreprise (AD CS, PKI interne…), à laquelle les postes du domaine font déjà confiance, et revenir à l'autorité loco à tout moment.
  - Mise en place guidée en quatre étapes : **importer l'autorité** (.cer, .crt, .pem ou chaîne .p7b) ; **obtenir le certificat** du serveur, soit par une **demande (CSR) générée par loco** — la clé privée ne quitte jamais le serveur, commande `certreq` pour AD CS fournie — soit avec un certificat et sa clé (PEM ou PFX) ; **diffuser la confiance** aux postes, avec le nombre de postes prêts ; **basculer**.
  - **Sans coupure pour le parc** : les agents (2.84 et plus) reçoivent un faisceau « autorité loco + autorité d'entreprise » par leur canal déjà sécurisé et le confirment ; la bascule prévient si des postes ne l'ont pas encore. L'autorité loco reste reconnue : le retour est toujours sûr.
  - Chaque certificat est vérifié avant d'être servi (clé assortie, adresse des agents couverte, chaîne signée par votre autorité) ; nginx est rechargé, ou l'ancien certificat est rétabli. Une mise à jour de loco garde le certificat d'entreprise tant qu'il est valable.
- **Mises à jour : listes plus courtes.** Le journal des versions et la liste des opérations n'affichent plus que les **5 plus récents** ; le reste se déplie (et se replie) d'un clic. Seule la version la plus récente est ouverte ; une opération choisie plus ancienne déplie sa liste d'elle-même.

## 2.82.0 — 2026-10-28

- **Page Mises à jour repensée.** Tout ce qui compte est visible d'un coup d'œil, en haut de page :
  - Une carte **loco** (version installée → disponible, date de publication, versions d'écart, « Au programme » avec les nouveautés, bouton **Installer**) et une carte **Système** (paquets, correctifs de sécurité, noyau, encart **À corriger en priorité** trié par gravité, boutons **Installer les correctifs** et **Tout mettre à jour**).
  - Un **bandeau de redémarrage** compact avec son bouton, quand un nouveau noyau ou des services l'exigent.
  - Les **nouveautés de la version disponible** se lisent avant d'installer : à chaque vérification, loco récupère le journal des versions publié avec elle (même API GitHub ou GitLab, aucun nouveau flux sortant).
  - **Journal des versions en frise** : nouveautés et version installée repérées, une ligne de résumé par version, l'historique ancien replié.
  - **Opérations** : la liste et le journal de l'opération choisie côte à côte, sans codes de couleur parasites.
  - La **source des mises à jour**, réglage rare, passe en bas de page, repliée.

## 2.81.0 — 2026-10-28

- **Scripts de base prêts à l'emploi.** Deux nouveaux scripts Windows arrivent dans la bibliothèque, à côté du pilote NVIDIA : **Réparer Windows (DISM + SFC)** (répare l'image avec `DISM /RestoreHealth` puis vérifie les fichiers système avec `SFC /scannow`, sortie nettoyée) et **Nettoyer les fichiers temporaires** (dossiers Temp, cache de Windows Update et corbeille, avec l'espace libéré). Modifiables et supprimables comme n'importe quel script.

## 2.80.0 — 2026-10-28

- **Journal des versions dans le portail.** La page *Administration → Mises à jour* affiche désormais un panneau **Journal des versions** : ce qui change à chaque version de loco, hors ligne, sans passer par GitHub. La version installée et celle disponible sont repérées.

## 2.79.0 — 2026-10-28

- **Pilote NVIDIA en un clic.** Un script prêt à l'emploi arrive dans la bibliothèque *Scripts* : il détecte le GPU NVIDIA du poste, récupère le dernier pilote **Game Ready (DCH)** directement chez NVIDIA, le compare à la version installée et l'installe en silencieux. À lancer sur les postes Windows, maintenant ou sur une planification. Modifiable et supprimable comme n'importe quel script.

## 2.78.0 — 2026-10-28

- **Détection des pilotes dans Windows Update.** L'inventaire ne regardait que les mises à jour logicielles : les pilotes (carte graphique NVIDIA/AMD, chipset…) distribués par Windows Update n'apparaissaient jamais. Ils sont maintenant détectés, affichés avec une étiquette **pilote** sur la fiche du poste, et installés en même temps que les autres mises à jour.

## 2.77.0 — 2026-10-28

- **Scripts Python.** En plus de PowerShell (Windows) et shell (Linux), la bibliothèque accepte des scripts **Python compatibles avec tout système** : l'agent les exécute avec l'interpréteur Python du poste (Windows comme Linux), en SYSTEM ou root. Nouvelle pastille de filtre et étiquette dédiées.

## 2.76.0 — 2026-10-28

- **Geler Windows Update sur tout un groupe.** Le menu « ⋯ » d'un groupe de machines propose de **bloquer** ou de **rendre** les mises à jour Windows pour tous ses membres Windows en une fois.

## 2.75.0 — 2026-10-28

- **Geler les mises à jour Windows par poste.** Sur la page d'un poste Windows, un interrupteur (décoché par défaut) empêche Windows d'installer ses mises à jour tout seul : c'est vous qui les lancez depuis loco. L'agent applique la consigne au prochain contact et la retire si vous décochez.

## 2.74.1 — 2026-10-27

- **Mon compte, plus lisible.** En-tête en étiquettes compactes (profil, type de compte, dernière connexion, création) avec un seul format de date. Onglet Sécurité : champs de largeur raisonnable et règles du mot de passe affichées à côté, en liste de vérification ; encart « Bon à savoir » plus discret ; double authentification avec son explication et son bouton sur une même ligne.

## 2.74.0 — 2026-10-27

- **Création de compte repensée.** La page Comptes affiche la liste en pleine largeur et un bouton **Nouveau compte** en haut (le doublon « Profils d'accès » disparaît, l'onglet reste). La création se fait dans une fenêtre en trois parties : l'identifiant avec sa règle affichée ; le **profil d'accès en cartes**, chacune avec ce qu'elle permet (seuls les profils que vous pouvez attribuer sont proposés) ; le mot de passe (saisi, généré au dé ou laissé vide) et le choix de le faire changer à la première connexion. Si la création est refusée (identifiant pris, mot de passe trop faible), la fenêtre se rouvre avec la saisie.

## 2.73.3 — 2026-10-27

- **Mon parc : « redémarrage nécessaire » à sa place.** L'information n'est plus une pastille qui passait à la ligne selon la place disponible, décalée sous le décompte : c'est désormais une ligne d'état discrète (icône et texte orangés), toujours sous le nombre de mises à jour, alignée avec lui. Le décompte et la pastille « sécurité » ne se coupent plus.

## 2.73.2 — 2026-10-27

- **Mon parc : en-tête allégé.** La case « Redémarrer si nécessaire » ne flotte plus entre les boutons : « Tout mettre à jour » (ou « Mettre à jour ce groupe ») ouvre une fenêtre qui récapitule l'opération et propose l'interrupteur **Redémarrer les postes si nécessaire**, seulement pour ceux dont une mise à jour l'exige.

## 2.73.1 — 2026-10-27

- **Groupes de machines et de logiciels : renommer et supprimer depuis la liste.** Chaque ligne a un menu « ⋯ » avec *Modifier les membres*, *Renommer…* (seul le nom change : membres, description et déploiements conservés ; un nom déjà pris est refusé) et *Supprimer…* (avec confirmation ; les appareils, logiciels et déploiements restent). Réservé aux comptes qui peuvent gérer les groupes.

## 2.73.0 — 2026-10-27

- **Nouvelle police : Bricolage Grotesque** à la place d'IBM Plex Sans. Plus de caractère dans les titres et les grands chiffres, tout en restant sobre et lisible en petit grâce à sa taille optique variable. Chiffres de largeur fixe pour les tableaux, et couverture complète du français et de l'espagnol (y compris « — » et « ’ », qui s'affichaient auparavant dans une autre police). IBM Plex Mono reste pour les adresses et le code.
- **Barre d'avancement des opérations du serveur** (*Administration → Mises à jour*) : pendant une opération, une carte en haut de la page montre l'étape en cours, le pourcentage et le temps écoulé.
  - **Mise à jour de loco** : téléchargement, vérification de l'empreinte, puis chacune des étapes de l'installeur (ports, paquets, copie du portail, certificat, nginx, services…).
  - **Mises à jour du système** : « paquet n sur N » d'après apk, dnf ou APT.
  - Autres opérations : barre animée.
  - Quand le portail redémarre en fin d'installation, la carte l'indique et la page se reconnecte d'elle-même.

## 2.72.0 — 2026-10-27

- **Shells plus agréables, comme un vrai terminal** (shell d'un poste et shell root du serveur) :
  - **Presse-papiers** : sélectionner copie ; clic droit copie la sélection ou colle s'il n'y en a pas ; **Ctrl+Maj+C / Ctrl+Maj+V** et Maj+Inser ; **Ctrl+C avec une sélection copie** (sans interrompre la commande), sans sélection il part au shell comme d'habitude.
  - **Liens cliquables** : Ctrl+clic (⌘+clic) ouvre l'adresse dans un nouvel onglet.
  - **Recherche dans l'historique** : Ctrl+Maj+F, Entrée / Maj+Entrée pour naviguer, résultats surlignés, Échap pour fermer.
  - **Taille du texte au clavier** : Ctrl + / Ctrl − / Ctrl 0, en plus des boutons A− / A+.
  - **Plein écran** : le clavier est verrouillé (navigateurs Chromium) pour que Ctrl+W, Ctrl+T… aillent au shell au lieu de fermer l'onglet.
  - **Rendu** : affichage WebGL (texte net, défilement immédiat, cadres et tableaux continus), police **JetBrains Mono** embarquée (flèches, tirets, symboles et cadres dans la même police, chargée avant l'ouverture pour des colonnes justes), largeur Unicode 11 (emojis et symboles larges alignés), contraste minimal garanti, historique de 10 000 lignes. Repli automatique sur l'ancien rendu si le navigateur n'a pas WebGL.
  - Un rappel des raccourcis s'affiche sous le terminal.

## 2.71.1 — 2026-10-27

- **Mise à jour automatique des agents Windows rétablie** : depuis la 2.39, le serveur annonçait aux postes l'empreinte du MSI d'origine alors qu'il leur sert le MSI « générique » (emplacements d'inscription vidés). L'agent Windows refusait donc chaque mise à jour (« empreinte du MSI incorrecte » dans son journal, toutes les 2 minutes) et restait sur sa version d'installation. L'empreinte annoncée est désormais celle du fichier réellement servi : les postes Windows se mettent à jour d'eux-mêmes et reçoivent la supervision de la 2.71.

## 2.71.0 — 2026-10-27

*Les versions 2.70.0 et 2.70.1 n'ont pas été publiées séparément (un test échouait sur Rocky 8) : leur contenu, décrit plus bas, est livré avec la 2.71.0.*

- **Supervision des postes** : chaque agent (Linux et Windows) joint à son contact, toutes les 2 minutes, un relevé : processeur et débit réseau en moyenne depuis le contact précédent, mémoire, swap ou fichier d'échange, chaque disque local avec son point de montage, charge (Linux) et durée depuis le démarrage. loco garde le dernier relevé et 7 jours d'historique.
  - **Fiche du poste → onglet Supervision** : valeurs actuelles (seuils signalés), courbes *Processeur et mémoire* et *Réseau* sur 24 h ou 7 jours (interrompues quand le poste était éteint, infobulle au survol), tableau des disques avec leur occupation. Un poste hors contact garde son dernier relevé, grisé.
  - **Mon parc → colonne Ressources** : mini-jauges processeur, mémoire et disque le plus rempli, en couleur d'état au-delà des seuils.
  - **Alertes** : trois nouvelles règles réglables pour les postes : disque presque plein, mémoire saturée et processeur saturé (moyenne sur 15 minutes, jamais sur un simple pic).
  - **En direct** : tant que l'onglet Supervision est ouvert et visible, l'agent envoie un relevé **toutes les 2 secondes** (processeur, mémoire, réseau) par le canal qu'il garde déjà ouvert avec le serveur. Une courbe des 3 dernières minutes défile, les valeurs se mettent à jour et un témoin « en direct » s'allume. Dès que l'onglet est quitté ou la fenêtre cachée, plus rien n'est demandé : aucune charge supplémentaire le reste du temps.
  - Les agents se mettent à jour automatiquement ; la supervision apparaît dès leur premier contact en 2.71.

## 2.70.1 — 2026-10-27

- **NTP : serveur saisi qui ne répond pas, signalé clairement** : quand aucun des serveurs saisis n'est utilisé, *Système → Heure et matériel* l'affiche (« Le serveur saisi ne répond pas : 192.168.100.23 ») et précise d'où vient l'heure (serveurs de secours de la distribution, avec leur adresse), au lieu d'un simple « synchronisée ». La pastille devient « synchronisée par les serveurs de secours ». Une indication donne la piste côté serveur NTP : accepter les clients du réseau (chrony : `allow`) et autoriser UDP 123 en entrée. Le tableau des sources montre aussi le nom saisi (par exemple pool.ntp.org) sous l'adresse.
- **« Utiliser uniquement ces serveurs »** : nouvelle option pour que les serveurs saisis soient les seules sources. Avec chrony, loco commente lui-même les sources de la distribution dans chrony.conf (pool, server, sourcedir), de façon réversible : décocher les rétablit à l'identique. Avec timesyncd, les serveurs de secours intégrés sont désactivés. Sans serveur saisi, l'option est ignorée, pour ne jamais laisser le serveur sans source.

## 2.70.0 — 2026-10-27

- **Certificat HTTPS : renouveler et importer depuis le portail** (*Système → Réseau et certificats*).
  - **Renouveler** : nouveau certificat signé par l'autorité loco (825 jours, adresse et noms actuels du serveur). nginx est testé avant d'être rechargé, et l'ancien certificat est rétabli en cas d'échec. Les postes qui font confiance à l'autorité ne voient aucune différence.
  - **Importer un certificat pour un nom DNS** (autorité d'entreprise, Let's Encrypt…) : chaîne PEM et clé, vérifiées (clé correspondante, validité, noms DNS). Il n'est servi qu'aux navigateurs qui ouvrent le portail par l'un de ses noms (SNI) ; les agents du parc, qui épinglent l'autorité loco, gardent le certificat loco sur l'adresse du serveur. Un certificat qui couvrirait cette adresse est refusé, puisque les agents perdraient la connexion. Le certificat importé peut être remplacé ou retiré, et son bloc nginx est régénéré à chaque mise à jour.

## 2.69.3 — 2026-10-27

- **Création de compte : mot de passe au choix** : le formulaire *Nouveau compte* propose un champ **Mot de passe**. On peut en saisir un, cliquer sur le **dé** pour en générer un aléatoire (lisible, sans caractères ambigus, affiché en clair et sélectionné ; chaque clic en propose un autre), ou laisser vide pour qu'il soit généré à la création. Une ligne d'aide vérifie en direct la règle (12 caractères minimum, sans le nom du compte). Un interrupteur **« À changer à la première connexion »** (activé par défaut) laisse le choix d'imposer ou non le changement. Après la création, l'encart **Identifiants à transmettre** affiche le compte et son mot de passe avec un bouton Copier. Un appel scripté qui n'envoie pas l'interrupteur garde le changement obligatoire.

## 2.69.2 — 2026-10-27

- **Téléchargements refusés « Certificat HTTPS invalide » (GIMP, SumatraPDF…)** : la vraie cause était l'horloge du serveur en retard. Un certificat émis après la date du serveur paraît « pas encore valide » : avec une horloge au 3 octobre, 7-Zip passe mais SumatraPDF et GIMP, dont les certificats sont plus récents, échouent. Le message désigne désormais l'horloge, avec la date du serveur, et renvoie vers *Système → Heure et matériel*.
- **NTP sur l'appliance Alpine 3.24** : son chrony.conf lit le dossier `conf.d` en tête puis désactive le port de commande (`cmdport 0`) plus bas ; le réglage de loco déposé dans `conf.d` était donc écrasé et l'état de l'heure restait illisible. loco s'inclut maintenant en fin de chrony.conf (et retire l'ancien fichier), ce qui rétablit la lecture de l'état et l'écart d'horloge dans le portail.

## 2.69.1 — 2026-10-27

- **Groupes de machines et de logiciels** : après l'enregistrement d'un groupe (création ou modification), on revient à la liste des groupes, positionné sur le groupe enregistré et surligné, au lieu de rester dans l'éditeur.

## 2.69.0 — 2026-10-27

- **Flux sortants à autoriser sur le pare-feu** : *Système → Réseau et certificats* liste désormais, sous les ports entrants, les destinations Internet que le serveur doit joindre **en sortie**, avec ports et protocoles : résolveurs DNS, serveurs NTP réglés, dépôts du système du serveur, source des mises à jour de loco (GitHub ou GitLab), miroirs Linux réellement vus dans le cache apt-cacher-ng, dépôts RPM et Flathub, domaines Windows Update, winget et hôtes des installeurs du catalogue Windows, et, s'ils sont configurés, serveur SMTP, webhook d'alertes et sauvegarde hors site (S3 ou SFTP). Tout est tiré de la configuration et des fonctionnalités actives ; un bouton copie la liste au format « une destination par ligne » pour l'équipe réseau.

## 2.68.0 — 2026-10-27

- **Heure du serveur, refonte NTP** : l'état de l'heure affiché était souvent faux. Il est désormais mesuré par l'agent root (le portail, non privilégié, ne peut pas interroger chrony) et le bloc *Système → Heure et matériel* a été repensé : pastille d'état claire (synchronisée / en cours / désactivée / gérée par l'hôte), résumé (heure locale, fuseau, **source en usage, strate, écart**) et tableau par serveur (en usage / joignable / injoignable…). La synchronisation NTP accepte **plusieurs serveurs** (IP v4/v6 ou noms, séparés par des espaces), prioritaires sur ceux de la distribution, et le réglage réseau passe avant le fuseau horaire. Sur l'appliance (chrony avec `cmdport 0`), le port de commande local est réactivé automatiquement pour que l'état redevienne lisible ; prise en charge de timesyncd (Debian/Ubuntu) et de chrony (Red Hat, Alpine), avec « include » pour les distributions sans *confdir* (Rocky, AlmaLinux) et persistance du choix au redémarrage sur l'appliance (OpenRC).

## 2.67.0 — 2026-10-27

- **Shell root : le terminal directement** : une fois le shell activé (et le serveur redémarré), *Administration → Shell root*, l'icône de la barre et Ctrl+K ouvrent directement le terminal root, sans page intermédiaire. Un bouton **« Désactiver le shell root »** bien visible sur le terminal ferme tous les shells et coupe l'accès (confirmation demandée) ; on revient alors à la mise en service. La page d'étapes ne sert plus que tant que le shell n'est pas actif. Le shell ne démarre qu'au premier échange du terminal (protégé contre le CSRF), jamais au simple affichage de la page.

## 2.66.0 — 2026-10-26

Cette version publie aussi le contenu de 2.64.0 et 2.65.0 (page Shell root dédiée, passe d'ergonomie et de design), non publiées séparément.

- **Intégration continue plus rapide et plus sobre, simple à reprendre** : chaque push ne lance plus que les vérifications rapides (lint, tests unitaires, syntaxe PowerShell — ~3 min, sans machine virtuelle). Les tests d'intégration complets (VM Linux, Docker, ISO, serveurs Windows) tournent quand un push touche les installeurs, les agents ou l'ISO, et toujours avant une publication : pousser un tag `vX.Y.Z` vérifie, teste et publie en un seul run (ISO et agent Windows publiés = ceux testés). Plus de bridage du parallélisme, QEMU installé sans paquets graphiques (43 paquets au lieu de 298), plus de lancement hebdomadaire automatique. Un fork fonctionne sans secret ni serveur : voir *Intégration continue et publication* dans le README.

## 2.65.0 — 2026-10-26

Passe d'ergonomie et de design sur tout le portail, pour se repérer plus vite.

- **Barre du haut désencombrée** : cinq entrées (Vue d'ensemble, Mon parc, Linux, Windows, Administration). La langue, l'apparence, le compte et la déconnexion sont réunis dans un **menu compte** (avatar). Plus rien ne déborde, même en 1280 px.
- **Administration rangée par sections** : le menu présente *Accès* (comptes, profils, annuaires), *Serveur* (système, mises à jour, configuration, fonctionnalités, shell root) et *Suivi* (journal, alertes). Les onglets ne montrent plus que ceux de la section en cours.
- **Enrôlement rejoint Mon parc**, en onglet à côté des appareils, des groupes et des déploiements.
- **Système en onglets** : *État* (services, emplacements, versions), *Sauvegardes*, *Réseau et certificats*, *Heure et matériel*. Les indicateurs du haut mènent à l'onglet correspondant, et les actions ramènent sur le bon onglet. Le panneau QEMU n'apparaît que s'il s'applique.
- **Vue d'ensemble** : les indicateurs des caches et ceux du serveur forment deux rangées distinctes.
- **Journal et opérations lisibles** : libellés pour la double authentification, les planifications, les sauvegardes, le shell root et les alertes, au lieu des codes techniques ; résumé lisible des paramètres des opérations du serveur.
- **Corrections** : la barre de sélection de *Mon parc* restait affichée sans sélection ; espacements dans *Mon compte* ; espaces manquants dans *Enrôlement* et *Windows Update* ; option « Redémarrer si nécessaire » allégée.

## 2.64.0 — 2026-10-26

- **Shell root mieux intégré** : il a désormais sa propre page dans le menu *Administration* (et donc dans la recherche Ctrl+K), avec son état en sous-titre (désactivé, redémarrage requis, actif). La page guide la mise en service en trois étapes — activer, redémarrer, ouvrir — et le terminal s'ouvre dans la même page. Quand le shell est actif, une icône terminal dans la barre du haut y donne accès en un clic. Le panneau a quitté la page *Système*.

## 2.63.0 — 2026-10-26

- **Shell root du serveur dans le navigateur** (*Système → Shell root du serveur*) : un terminal root sur le serveur loco lui-même (installation Linux ou appliance), réservé au compte « admin » de l'installation. **Désactivé par défaut** ; l'activation demande le mot de passe du compte admin et **ne prend effet qu'après un redémarrage du serveur** (bouton de redémarrage proposé). La désactivation est immédiate et ferme les shells ouverts. Chaque activation, ouverture et fermeture est journalisée. Le terminal est servi par l'agent root via un socket local réservé au portail.

## 2.62.0 — 2026-10-26

- **`ufw` présent aussi sur les appliances existantes** : en 2.59 le paquet `ufw` n'était ajouté qu'à l'image ISO (nouvelles installations). Il est désormais aussi dans la liste de paquets appliquée par `install.sh`, donc une appliance déjà installée reçoit `ufw` à sa prochaine mise à jour de loco. Toujours installé sans être activé ni configuré.
- **Message d'accueil (MOTD) de l'appliance** : la connexion à l'appliance (console et SSH) affiche désormais une bannière loco soignée — logo, version, système, adresse du portail et rappel des commandes console — régénérée à chaque démarrage.

## 2.61.0 — 2026-10-26

- **État NTP correct sur l'appliance Alpine** : *Système → heure du serveur* lisait l'état de synchronisation uniquement via `timedatectl`, absent sur l'appliance Alpine — le bandeau affichait donc toujours « synchronisation NTP désactivée », même quand chrony était actif et synchronisé. L'état (activé / synchronisé / en cours) est désormais lu depuis chrony quand `timedatectl` n'existe pas. À utiliser avec le correctif de 2.60 (rechargement de chrony par `rc-service`).

## 2.60.0 — 2026-10-26

- **Synchronisation NTP corrigée sur l'appliance Alpine** : le réglage du serveur NTP depuis *Système → heure du serveur* rechargeait le service via `systemctl`, absent sur l'appliance Alpine (OpenRC) — le nouveau serveur n'était donc pris en compte qu'au redémarrage. Le service est désormais rechargé avec `rc-service` quand `systemctl` n'existe pas. Validé de bout en bout contre un serveur NTP conteneurisé. Couvert par des tests (Debian → `systemctl`, Alpine → `rc-service`, conteneur → ignoré).

## 2.59.0 — 2026-10-26

- **`ufw` présent dans l'appliance (ISO)** : le pare-feu `ufw` est désormais installé dans l'image Alpine, mais **ni activé ni configuré** — il n'applique aucune règle et ne filtre rien par défaut. Il est simplement disponible si vous souhaitez poser des règles vous-même plus tard (`ufw enable`, `ufw allow …`).

## 2.58.0 — 2026-10-26

- **Ports réseau dans Système** : une nouvelle section *Administration → Système* récapitule les ports à ouvrir **en entrée** sur le pare-feu pour que l'applicatif fonctionne — interface et agents en HTTPS (8443), amorçage HTTP des postes, cache de paquets Linux (3142), cache Windows Update (80/443) et proxy Windows Update (3128). La liste s'adapte à la configuration et aux fonctionnalités activées, précise le public concerné (administration, postes Linux, postes Windows) et rappelle que le port interne de l'application reste en local.

## 2.57.0 — 2026-10-26

- **QR de double authentification fiable partout** : le code QR d'enrôlement s'affiche désormais même sans le paquet système `python3-qrcode` (appliance, installations minimales) grâce à un générateur QR embarqué sans dépendance. La page d'enrôlement liste aussi les **applications compatibles** (Microsoft Authenticator, Google Authenticator, Authy, 1Password, Bitwarden — toute application TOTP convient).

## 2.56.0 — 2026-10-25

- **Déploiement de paquets Linux** : la section *Déploiements* propose désormais, à côté des logiciels Windows, un store des paquets `.deb` et `.rpm` publiés dans loco. On choisit des paquets, on cible des postes ou des groupes Linux, on prévisualise puis on confirme ; chaque poste n'installe que les paquets compatibles avec son système (`.deb` → Debian/Ubuntu, `.rpm` → Red Hat), depuis le dépôt loco déjà configuré. L'avancement est suivi poste par poste comme pour Windows. Nouvelle action d'agent `install-package` (droit *Déployer un logiciel*).

## 2.55.0 — 2026-10-25

- **Pastille « redémarrage nécessaire »** dans *Mon parc* : l'ancien texte d'avertissement devient une pastille ambrée avec icône, alignée proprement avec les compteurs de mises à jour et la pastille de sécurité.

## 2.54.0 — 2026-10-25

- **Double authentification (TOTP)** pour les comptes locaux : depuis *Mon compte → Sécurité*, on l'active en scannant un QR (ou en saisissant la clé) avec une application d'authentification, puis un code à 6 chiffres est demandé à chaque connexion. Des **codes de secours** à usage unique permettent de se connecter en cas de perte du téléphone. Un administrateur peut retirer la 2FA d'un compte (droit *Réinitialiser les mots de passe*), et `loco-user mfa-reset NOM` la retire depuis la console en dernier recours. Le secret est chiffré au repos et les codes de secours sont hachés.

## 2.53.0 — 2026-10-24

- **Robustesse des tâches de fond** : les évaluations périodiques (alertes, copie hors-site) n'agissent plus que sur un seul processus à la fois grâce à un bail en base, pour éviter tout doublon si le serveur tourne avec plusieurs workers. Reprise automatique si le processus meneur s'arrête.
- **CI plus rapide** : les tests unitaires du portail s'exécutent désormais hors machine virtuelle, en quelques minutes, et conditionnent les tests VM plus longs.

## 2.52.0 — 2026-10-24

- **Sélection de postes et mise à jour groupée** (*Mon parc*) : cocher des postes dans la liste (case « tout sélectionner » dans l'en-tête) fait apparaître une barre discrète en haut du tableau avec le nombre sélectionné et un bouton **Mettre à jour les sélectionnés** (option *Redémarrer si nécessaire*). Le rafraîchissement automatique se met en pause pendant la sélection. Réservé au droit *Mettre à jour un poste*.

## 2.51.0 — 2026-10-24

- **Sauvegarde hors-site** (*Système → Sauvegardes*) : copie automatique de la dernière sauvegarde quotidienne vers un stockage externe — **S3-compatible** (cloud AWS/Backblaze/Wasabi/OVH/Scaleway/R2 ou MinIO on-prem), **SFTP** (clé SSH), ou **dossier / point de montage** (2ᵉ disque, support externe, partage SMB/NFS monté). Chiffrement AES-256 optionnel avant envoi, bouton **Tester**, secrets chiffrés au repos. La rétention suit le réglage « Sauvegardes conservées ». Une alerte « Copie hors-site en échec » prévient en cas de problème.
- **Unités sur les seuils d'alerte** (*Alertes → Règles*) : chaque seuil affiche son unité (jours, %, échecs / 24 h, mises à jour, postes).
- **Panneau Sauvegardes revu** : liste plus régulière (actions toujours alignées à droite) et copie hors-site intégrée au même panneau.

## 2.50.0 — 2026-10-23

- **Alertes et notifications** (*Administration → Alertes*) : être prévenu par **e-mail (SMTP)** ou **webhook** (Slack, Teams, Discord ou relais maison, avec signature HMAC) quand l'état du serveur ou du parc le demande. Règles activables avec seuils — service arrêté, certificat HTTPS qui expire, sauvegarde en retard, disque plein, échecs de connexion, mises à jour de sécurité en attente, postes hors contact — et une gravité minimale par canal. Bouton **Tester**, secrets chiffrés au repos, évaluation côté serveur toutes les minutes sans répétition, avec réémission en cas d'échec. Nouveau droit *Configurer les alertes* et interrupteur de fonctionnalité dédié.

## 2.49.0 — 2026-10-23

- **Sauvegardes repensées** (page *Système*) : liste plus compacte (date et taille sur une ligne, nom de fichier en dessous), actions groupées avec une icône de suppression, et une zone de réglages claire pour la rétention et la restauration depuis un fichier.
- **Message Windows Update corrigé** : l'avertissement « aucun poste n'est encore passé par loco » ne s'affiche plus dès qu'il n'y a pas eu de requête récente ; il n'apparaît que lorsque le cache n'a réellement rien servi (aucun trafic sur 14 jours et cache vide).
- **README retravaillé** : en-tête, badges, sommaire et sections détaillées repliables, pour une lecture plus agréable.

## 2.48.0 — 2026-10-23

- **Supprimer une sauvegarde** (page *Système › Sauvegardes*) : un bouton *Supprimer* sur chaque sauvegarde, avec une confirmation avant suppression définitive. Effectué par l'agent root. Réservé au droit *Configurer le serveur*.
- **Message Windows Update à jour** : quand aucun poste n'est encore passé par le cache, le message explique désormais que les postes Windows enrôlés sont dirigés automatiquement vers le cache (par proxy, activé par défaut) et renvoie à la section « Diriger les postes vers le cache » pour les postes non gérés, au lieu de ne parler que de la redirection DNS.

## 2.47.0 — 2026-10-23

- **Restaurer depuis un fichier téléversé** (page *Système › Sauvegardes*) : on peut envoyer une archive `.tar.gz` (une sauvegarde gardée ailleurs, ou venant d'un autre serveur) pour la restaurer. Le fichier est **vérifié avant toute écriture** — c'est bien une sauvegarde loco/local-repo (présence de la base et de la configuration, chemins sûrs, pas de `..` ni de lien symbolique, `.tar.gz` lisible) — côté portail puis une seconde fois par l'agent root avant d'appliquer.
- **Rétention des sauvegardes configurable** : un champ *Sauvegardes conservées* (1 à 60) écrit `BACKUP_KEEP` et supprime aussitôt les plus anciennes au-delà de la limite. Réservé au droit *Configurer le serveur*.

## 2.46.0 — 2026-10-23

- **Télécharger et restaurer les sauvegardes** (page *Système*) : chaque sauvegarde a un bouton **Télécharger** (pour la garder ailleurs) et un bouton **Restaurer** en un clic. La restauration passe par l'agent root — arrêt du portail, remise en place de la base, des comptes, des clés et des dépôts, régénération des services, puis redémarrage ; la configuration propre au serveur (adresse, ports, réseaux) est conservée, et un repli automatique rétablit l'état précédent en cas d'échec. La commande reste disponible pour restaurer sur un autre serveur. Réservé au droit *Configurer le serveur*.
- **Logos des logiciels dans « Derniers ajouts »** (Vue d'ensemble) : chaque logiciel récemment ajouté montre son logo (icône réelle pour les apps WinGet, sinon une pastille avec l'initiale), comme dans le Centre de logiciels.
- **Avertissement du Journal fermable** : la bannière « N échec(s) de connexion ces dernières 24 heures » a une croix pour la fermer ; elle reste masquée tant qu'aucun nouvel échec ne survient.

## 2.45.0 — 2026-10-23

- **Trouver ses machines dans la recherche (Ctrl+K)** : la palette de recherche de la barre de navigation liste désormais les **postes du parc** en plus des pages. On tape un nom de machine, une adresse IP ou un système, et on ouvre sa fiche d'un *Entrée*. Réservé aux profils qui ont accès au parc (droit *Voir le parc*).

## 2.44.0 — 2026-10-23

- **Brancher un hôte Proxmox VE sur le cache** : nouvelle section sur la page *Paquets*, à côté de « Brancher un poste Linux ». Une seule commande sur l'hôte Proxmox branche ses dépôts sur loco, au choix **sans abonnement** (gratuit, mis en cache) — recommandé — ou **entreprise** (abonnement requis, en direct HTTPS). Debian et le dépôt sans-abonnement passent par le cache ; `--trust-ca` pour le premier branchement, `--remove` pour revenir en arrière.
- **Les dépôts Proxmox sont remplacés, jamais dupliqués** : le script repère le dépôt Proxmox VE où qu'il soit — fichiers `.sources` (deb822, Proxmox 9) comme anciens `.list` dans `sources.list.d` et `sources.list` — le retire et écrit un unique `loco-proxmox.sources` avec la variante choisie. Les dépôts Debian et Ceph ne sont pas touchés.

## 2.43.0 — 2026-10-23

- **Carte « Agent invité QEMU » visible sur l'appliance** : l'appliance tourne sous Alpine (sans systemd), où la détection de VM échouait, donc le bouton d'installation restait caché. loco reconnaît maintenant une VM QEMU/KVM par son fabricant DMI. La carte (page *Système*) est désormais toujours présente, avec un message clair selon le serveur : installer sur une VM QEMU, « horloge/virtualisation gérée par l'hôte » dans un conteneur, « non applicable » sur un serveur physique.

## 2.42.0 — 2026-10-23

- **Heure du serveur, NTP dans les conteneurs** : sur un serveur conteneurisé (LXC), la carte *Système › Heure du serveur* indique désormais que l'horloge vient de l'hôte (la synchronisation NTP se règle sur l'hôte) au lieu d'un interrupteur sans effet ; sur une vraie VM ou un serveur physique, le réglage du serveur NTP amont fonctionne normalement.
- **Agent invité QEMU pré-installé sur l'appliance** : l'ISO installe et active désormais `qemu-guest-agent` d'office (l'appliance tourne toujours en VM Proxmox/QEMU) — arrêt propre, adresses IP dans Proxmox et sauvegardes cohérentes marchent dès l'installation. Sur les autres serveurs, la carte *Système › Agent invité QEMU* permet toujours de l'installer en un clic.

## 2.41.0 — 2026-10-23

- **Heure du serveur** (page *Système*) : choisir le **fuseau horaire** du serveur et, si on le souhaite, **synchroniser l'horloge sur un serveur NTP** amont (le serveur devient client NTP, il n'en héberge pas). Appliqué par l'agent — `timedatectl` pour le fuseau, `systemd-timesyncd` (Debian/Ubuntu) ou `chrony` (Red Hat) pour le NTP.
- **Agent invité QEMU** (page *Système*) : quand loco tourne dans une VM Proxmox/QEMU, une carte affiche l'état de `qemu-guest-agent` et permet de l'**installer en un clic** (arrêt propre, adresses IP dans Proxmox, gel des fichiers pour des sauvegardes cohérentes). Masquée sur les conteneurs et le matériel physique.

## 2.40.0 — 2026-10-22

- **Page *Réglages du parc* plus claire** : la carte de l'adresse du serveur affiche l'adresse actuelle avec une pastille de confirmation (« 3/3 agents l'ont confirmée ») et replie le mode d'emploi dans un « Comment changer l'adresse sans perdre les postes ? » en trois étapes ; une adresse annoncée en attente de bascule a son propre encart. Le seuil des pastilles gagne une légende de couleurs (à jour · 1 à N · N ou plus), et la clé d'inscription affiche un aperçu masqué.

## 2.39.0 — 2026-10-22

- **MSI Windows prêt à l'emploi** : « Télécharger le MSI » depuis la page *Enrôlement* donne désormais un installeur tout prêt — un **double-clic installe le service ET inscrit le poste**, sans aucune commande, comme le paquet `.deb` sous Linux. Le portail grave l'adresse de loco, la clé d'inscription et l'empreinte de l'autorité dans le MSI au téléchargement. La commande PowerShell et le déploiement en masse (GPO) restent disponibles sous « Autres méthodes » (MSI générique, sans inscription).
- **Mises à jour depuis GitLab** : loco peut tirer ses versions d'un **projet GitLab** (y compris un GitLab interne), pas seulement de GitHub. Dans *Mises à jour › Source des mises à jour*, indiquez `owner/repo` (GitHub) ou l'URL d'un projet GitLab. Section retravaillée, plus lisible.
- **Compteur de mises à jour de sécurité** du parc : au lieu d'un « dont N sécurité » en rouge, une **pastille bouclier** discrète, sur la liste du parc et la fiche de chaque poste.

## 2.38.0 — 2026-10-22

- **Vue d'ensemble plus lisible** : les tuiles (caches, Processeur, Mémoire, Disque) s'enroulent au lieu d'être tassées sur une seule ligne, donc plus de valeurs coupées. La tuile **Mémoire** montre désormais un **pourcentage** d'utilisation (comme le Processeur) avec « utilisée sur totale » en dessous, au lieu d'un nombre d'octets tronqué.
- **Terminal des postes Linux comme un vrai terminal** : la session ouvre une **invite colorée** par défaut (`utilisateur@poste : chemin`) quand le poste n'en a pas, pour une vraie allure de terminal dès l'ouverture. Le `.bashrc` du poste reste prioritaire : s'il définit déjà une invite colorée (ou un `PROMPT_COMMAND`), c'est la sienne qui s'affiche.

## 2.37.0 — 2026-10-22

- **Processeur et mémoire du serveur dans la vue d'ensemble** : deux tuiles, à côté de « Disque », montrent la charge du serveur sur lequel tourne loco — **Processeur** (pourcentage d'utilisation, charge sur 1 min et nombre de cœurs) et **Mémoire** (utilisée sur totale), avec une jauge (orange puis rouge quand ça se remplit). Les valeurs s'actualisent en direct comme le reste de la page. Lues sur le serveur (`/proc`) ; masquées si indisponibles.

## 2.36.0 — 2026-10-22

- **Terminal des postes repensé** (shell Linux comme PowerShell Windows, même affichage) : vraie palette ANSI 16 couleurs lisible, accordée au thème choisi (OLED, sombre, clair — fond blanc et couleurs adaptées en thème clair) ; le curseur et la sélection reprennent votre couleur d'accent et se mettent à jour en direct quand vous changez de thème ou d'accent. Police à chasse fixe moderne, interligne plus aéré et défilement doux pour les longues sorties, fond accordé à la surface (plus de liseré autour de la marge) et ascenseur fin et discret. Les boutons **A− / A+** règlent la taille du texte, mémorisée dans le navigateur.

## 2.35.0 — 2026-10-22

- **Page Mises à jour entièrement traduite** : l'agent d'administration (`locoadmin`) renvoie désormais un état structuré (codes) que le portail met en mots dans la langue de chaque personne — résumé, raisons de redémarrage, erreurs. Les journaux d'opération (vérification, mise à jour, redémarrage) suivent la langue du serveur (français, espagnol ou anglais selon sa locale), en anglais par défaut.

- **Adresses (URL) des pages en anglais**, identiques quelle que soit la langue choisie, pour une navigation uniforme : `/fleet`, `/accounts`, `/log`, `/system/updates`, `/enrollment`… (avant `/parc`, `/comptes`, `/journal`, `/systeme/mises-a-jour`, `/enrolement`). Les liens internes suivent automatiquement ; les adresses servies aux postes (dépôts, agents, scripts d'installation) ne changent pas.

- **Mise à jour de loco plus simple**, depuis la page *Mises à jour* : le bouton d'installation de la version publiée vaut désormais pour **tout serveur Linux** (systemd), et plus seulement pour l'appliance Alpine — l'archive est vérifiée (SHA-256) puis installée, avec retour automatique à la version précédente en cas d'échec. Une section **Dépôt GitHub** permet de pointer les mises à jour vers un autre dépôt « owner/repo » (par exemple votre fork) et de revérifier ; le choix est gardé dans `loco.conf` (et conservé d'une mise à jour à l'autre). Réglable aussi à l'installation avec `--github-repo owner/repo`.

- **Sélecteur de langue dédié** : une icône globe, en haut à droite et sur l'écran de connexion, ouvre un menu *Français / English / Español* accessible depuis n'importe quelle page. Le choix de langue sort du menu *Apparence* (palette) pour devenir une action d'un seul clic.

- **Mise à niveau depuis une ancienne version** : les motifs de redémarrage et erreurs déjà en cache restent lisibles sur la page Mises à jour et en console, jusqu'à la prochaine vérification par l'agent.


## 2.34.0 — 2026-10-21

- **Portail multilingue (français, anglais, espagnol)** : chaque personne choisit **Français**, **English** ou **Español** dans le menu *Apparence* (icône palette, en haut à droite, aussi sur l'écran de connexion) ; le choix est gardé dans son compte. Sans choix, le portail suit la langue du navigateur. Tout est traduit : pages, fenêtres, confirmations, messages après une action et erreurs, libellés (droits, journal, états, récurrences, profils intégrés), gestionnaire de fichiers, console à distance, recherche Ctrl+K. Dates, tailles (Go/GB, virgule ou point décimal) et nombres suivent la langue ; les récurrences des planifications sont rédigées dans l'ordre de chaque langue (« Every Tuesday and Thursday at 02:00 », « Cada martes y jueves a las 02:00 »).
- **Scripts des postes multilingues** : les commandes servies aux postes (configuration APT `setup.sh`, RPM `rpm-setup.sh`, Flatpak, installation de logiciels Windows `install.ps1`, zones DNS, agents Linux et Windows) écrivent leurs messages dans la langue du poste — français, espagnol ou anglais selon le système (`LANG` sous Linux, langue d'affichage de Windows). Le serveur place les trois versions dans le script ; la commande affichée dans le portail ne change pas.
- **Agents multilingues** : l'agent Linux (`loco-client`) et l'agent Windows (service, infobulle et fenêtre du compagnon de bureau, console et fichiers à distance) s'expriment dans la langue du poste (français, espagnol ou anglais) : état affiché, journal, messages renvoyés au portail (résultats d'action), messages de redémarrage.
- **Serveur en trois langues** : l'installateur (`install.sh`, `remove.sh`), la console de l'appliance (`setup-loco`, installateur de l'ISO, bannière de connexion) et les outils d'administration (`loco-user`, `loco-fleet`, `loco-backup`…) suivent la langue du serveur (`LANG`).
- Le français reste la langue de référence : les textes du code servent de clés, l'anglais est dans `portal/i18n/en.json` et l'espagnol dans `portal/i18n/es.json`. Un test refuse tout texte affiché sans sa traduction anglaise **et** espagnole, avec les mêmes valeurs à insérer.

## 2.33.0 — 2026-10-21

- **Paquets Linux plus clairs** : la page est en deux parties. **Brancher un poste Linux**, en trois étapes valables pour toutes les familles : le système du poste (Debian, Ubuntu ou Rocky, AlmaLinux, RHEL), ce que loco lui fournit (cache et vos dépôts, vos dépôts seulement, cache seulement, ou retirer loco), avec les options EPEL et « le poste n'a pas encore l'autorité de loco » en cases à cocher ; la commande à copier se compose seule. Puis **Vos dépôts** : la barre des versions, l'ajout de paquets au dépôt affiché et la liste. En tête, les tailles des deux caches, vos paquets et les postes Linux du parc. Cas particuliers (distributions dérivées, `wget`, clés, miroirs) regroupés.
- **Scripts** : compteurs (bibliothèque, postes en attente, exécutions avec échecs) ; bibliothèque filtrable (recherche, Windows / Linux) ; historique des 100 dernières exécutions avec filtres (en cours, avec échecs, réussies) et état de chaque exécution. **Vider la file** annule tous les envois pas encore reçus par les postes (planifiés compris), **Tout vider** supprime l'historique des exécutions terminées (celles en cours sont gardées), et chaque exécution terminée se retire de l'historique (aussi depuis sa page, qui propose en plus « Relancer… »). Le Journal garde la trace de ces nettoyages.

## 2.32.0 — 2026-10-21

- **Ajouter / modifier un logiciel Windows** : fenêtre réorganisée en étapes (installeur, puis nom et version), le type est **détecté** depuis le lien ou le fichier, et les réglages rarement utiles (type, arguments silencieux, nom dans Windows) sont repliés dans « Options d'installation », avec leur résumé. Pour un `.exe` sans arguments silencieux, les options s'ouvrent seules avec un avertissement (sinon l'installation attend un clic sur chaque poste).
- **Toutes les fenêtres** : titre et boutons restent visibles, seul le contenu défile ; fermeture par une croix ; un seul anneau de focus sur les champs (fini le double contour).
- **Paquets Linux : APT et RPM sur une seule page** (*Linux › Paquets*, qui remplace « Paquets APT » et « Paquets RPM ») : une barre réunit tous les dépôts, groupés par famille (Debian, Ubuntu · Red Hat), avec leur nombre de paquets ; un clic change de version au lieu d'une liste et d'un bouton « Afficher ». Le reste suit la famille choisie : cache (APT ou miroirs Red Hat), commande de branchement (`apt` ou `dnf`, option EPEL), ajout de `.deb` ou de `.rpm`. « + Nouveau dépôt » crée un dépôt Debian, Ubuntu ou Red Hat depuis la même fenêtre. Les adresses `/linux` et `/rpm`, les scripts des postes et les droits distincts APT / RPM ne changent pas.
- Liste des paquets en pleine largeur, avec la **description** de chaque paquet, sa date d'ajout et des actions en icônes (télécharger, copier vers un autre dépôt, supprimer) ; nombre de **postes du parc** sur la version affichée ; état vide explicatif ; options de branchement et miroirs regroupés.

## 2.31.0 — 2026-10-21

Interface plus moderne et plus rapide à parcourir, dans les trois thèmes :

- **Profondeur** : la page a un fond légèrement teinté et le contenu est posé sur des cartes blanches (ou anthracite) aux coins plus arrondis, avec une ombre douce ; les indicateurs deviennent des tuiles séparées. Titres, marges et tableaux plus aérés.
- **Navigation** : l'élément actif de l'en-tête est une pastille (son icône prend la couleur d'accent) ; les sous-menus (Mon parc, Administration, Logiciels) forment une barre où la page en cours ressort comme une carte ; onglets des fiches en pastilles ; filtres en pastilles arrondies, le filtre actif en plein.
- **Aller à une page (Ctrl+K)** : bouton loupe dans l'en-tête ; une fenêtre de recherche liste toutes les pages accessibles (y compris Groupes, Déploiements, Scripts, Planifications, Centre de logiciels…) et s'ouvre en tapant quelques lettres, flèches et Entrée. **« / »** place le curseur dans la recherche de la page (parc, catalogue, journal…).
- **Lignes cliquables** : dans les listes du parc, des scripts, des déploiements et des groupes, un clic n'importe où sur la ligne ouvre l'élément (Ctrl+clic : nouvel onglet) ; boutons, cases et liens de la ligne gardent leur action.
- Boutons, champs et menus un peu plus grands et plus arrondis, fenêtres de dialogue au fond flouté, écran de connexion sur un dégradé discret.

## 2.30.0 — 2026-10-21

- **Mises à jour du système du serveur depuis le portail**, sur toutes les installations Linux (Debian, Ubuntu, Rocky Linux, et l'appliance Alpine qui l'avait déjà) : *Administration → Mises à jour* liste les paquets en attente (noyau, OpenSSL, Squid, nginx, Python, glibc…), avec les **correctifs de sécurité en tête** (dépôt `-security` de Debian/Ubuntu, avis de sécurité DNF avec leur gravité sur Rocky) et les paquets importants repérés (« noyau », « service loco », « socle »). Filtres et recherche dans la liste.
- **Installer les correctifs de sécurité** seuls ou **tout mettre à jour** ; les fichiers de configuration posés par loco sont conservés, et les services de loco qui utilisent encore d'anciennes bibliothèques (portail, nginx, Squid, apt-cacher-ng) sont relancés automatiquement. Le journal de chaque opération s'affiche en direct.
- **Redémarrage** : la page indique pourquoi il est nécessaire (nouveau noyau installé, demande d'un paquet, bibliothèques remplacées encore chargées par le système) et les services qui utilisent d'anciennes bibliothèques ; bouton *Redémarrer le serveur*. Dans un conteneur LXC, le noyau est signalé comme géré par l'hôte.
- Vérification automatique chaque jour et au redémarrage. En console : `loco-update status|check|security|system|reboot`. Les opérations passent par un agent root séparé (`loco-agent`, service systemd désormais installé partout) qui revalide chaque demande : le portail reste sans privilèges.

## 2.29.2 — 2026-10-21

Passe de sécurité et de fiabilité :

- **Proxy Windows Update jamais envoyé sans Squid** : sur une installation `--no-wu` ou un serveur loco sous Windows (pas de proxy-cache), la fonctionnalité « Proxy Windows Update », active par défaut depuis 2.26, poussait quand même le proxy aux postes Windows, dont les mises à jour échouaient. Le portail sait désormais si Squid est installé ; sinon la fonctionnalité est coupée, la page *Windows Update* l'indique et la commande manuelle n'est plus proposée.
- **Agent Windows : le proxy est réellement retiré quand loco ne répond plus** (annoncé en 2.25 mais absent) : après trois contacts manqués d'affilée (environ 6 min), ou si loco refuse le poste, le proxy WinHTTP posé par loco est retiré et Windows Update repasse en direct ; il est reposé au premier contact réussi.
- **Squid durci** : le proxy refuse de relayer vers le serveur lui-même (127.0.0.0/8, ::1) et vers le lien local (169.254.0.0/16, fe80::/10), pour qu'un poste ne puisse pas atteindre par rebond les services internes de loco.
- **Gestionnaire de fichiers** : les champs renvoyés par l'agent (type, droits, dates) sont validés et échappés avant affichage, pour qu'un poste compromis ne puisse rien injecter dans la page (déjà bloqué par la politique de sécurité du contenu, désormais aussi à la source).
- **Planifications** : la personne qui modifie ou reprend une planification en devient responsable (ses droits sont revérifiés à l'envoi aux postes, son nom figure au journal), au lieu de l'auteur d'origine.

## 2.29.1 — 2026-10-21

- Gestionnaire de fichiers : le fil d'Ariane ne double plus le « / » de la racine (« / root » au lieu de « / / root »), et un dossier qui ne contient que des fichiers cachés n'est plus annoncé « vide » : le nombre d'éléments cachés est indiqué, avec un lien pour les afficher.

## 2.29.0 — 2026-10-21

- **Planifications repensées, et récurrentes** : nouvel onglet *Mon parc → Planifications* (liste avec action, cibles, récurrence en clair, prochaine et dernière exécution, état ; modifier, mettre en pause, reprendre, supprimer). Mises à jour et scripts se planifient **une fois, chaque jour, chaque semaine** (jours au choix) **ou chaque mois** (jour fixe ou dernier jour du mois), sur tout le parc, des groupes (membres résolus à chaque exécution) ou des appareils. Un composant « Quand » commun (choix segmenté, jours en pastilles, résumé en clair avec la prochaine exécution) remplace les champs date/heure. En contexte : bouton calendrier à côté de *Mettre à jour* sur la fiche d'un poste et *Planifier…* à côté de *Tout mettre à jour* (fenêtre de planification), choix « Maintenant / Planifier » à l'exécution d'un script ; la fiche d'un poste affiche les planifications qui le concernent.
- Une planification due devient une action au prochain contact des agents ; les occurrences manquées ne s'accumulent pas. Un script modifié depuis sa planification la met en pause (reprise avec la version actuelle, à la demande). Les droits de l'action planifiée sont exigés à la création et revérifiés à l'envoi. Les planifications ponctuelles de la 2.24 (champ date/heure) sont remplacées par ce système.

## 2.28.0 — 2026-10-20

- **Icônes œil / œil barré** pour afficher ou masquer : sur tous les champs de mot de passe (connexion, premier mot de passe, compte, annuaires), et à la place de la case « Fichiers cachés » du gestionnaire de fichiers.
- **Windows Update, dernières requêtes** : seuls les postes Windows sont nommés (un poste Debian partageant l'adresse n'apparaît plus). Derrière un NAT, quand plusieurs postes Windows ont la même adresse vue par loco, tous sont nommés (« pc-a ou pc-b ») avec la mention « adresse partagée (NAT) », au lieu d'en choisir un au hasard.
- Gestionnaire de fichiers : la barre d'outils ne touche plus les bords du panneau.

## 2.27.0 — 2026-10-20

- **Nouveau logo de l'application** (bâtiment, main et souris) : vectorisé (SVG), fond transparent, il prend la couleur du thème (clair, sombre doux, OLED) et suit la couleur d'accent choisie. Il remplace le carré dans la barre de navigation et apparaît en grand sur les écrans de connexion et de premier mot de passe. L'icône de l'onglet du navigateur utilise le même logo et s'adapte au mode clair ou sombre du navigateur.

## 2.26.0 — 2026-10-20

- **Proxy Windows Update activé par défaut**, et réglable dans *Administration › Fonctionnalités* (« Proxy Windows Update (sans DNS) »). Les postes Windows gérés sont dirigés vers le cache par leur proxy WinHTTP dès l'installation, sans aucune redirection DNS ; couper la fonctionnalité fait retirer le proxy par les agents. La page *Windows Update* montre l'état au lieu d'un interrupteur dédié.
- **Gestionnaire de fichiers** : correction du bouton vide (« Coller ») qui apparaissait dans la barre, et démarrage à la **racine du disque système (`C:\`)** sous Windows (toujours `/root` sous Linux, l'agent agissant en root/SYSTEM avec tous les droits).

## 2.25.0 — 2026-10-20

- **Cache Windows Update par proxy, sans DNS** : loco embarque un proxy-cache (Squid) atteint par le réglage **proxy WinHTTP** des postes Windows. Nouvelle alternative à la redirection DNS : sur la page *Windows Update*, un interrupteur « Diriger les postes gérés » fait régler par l'agent loco le proxy WinHTTP de chaque poste Windows vers loco (par poste, réversible, l'agent le retire s'il n'arrive plus à joindre loco ou au retrait de l'agent) ; aucune zone DNS à éditer. Le proxy met en cache les mêmes fichiers immuables (HTTP) et relaie le HTTPS sans le déchiffrer — même couverture que la voie DNS. La commande manuelle (`netsh winhttp set proxy …`) est aussi proposée sur les pages *Windows Update* et *Enrôlement*. Le service est installé avec le cache Windows Update (option `--wu-proxy-port`, 3128 par défaut) sur Debian/Ubuntu, Rocky/AlmaLinux, Alpine, l'ISO et le conteneur, et apparaît dans la page *Système*. Validé en réel : Windows Update d'un poste passe par le proxy (recherche et téléchargement), fichier WU mis en cache (MISS puis HIT), et installation complète de loco amenant Squid actif.

## 2.24.0 — 2026-10-20

- **Planification ponctuelle des mises à jour et des scripts** : choisir une date et une heure (heure du serveur) au lieu de « au prochain contact ». La commande reste en attente et n'est transmise à l'agent qu'à partir de l'instant choisi. Disponible pour *Mettre à jour* (un poste depuis sa fiche, via « Planifier une mise à jour »), *Tout mettre à jour* (parc ou groupe) et l'exécution d'un script (page d'exécution). Une action planifiée en attente peut être **annulée** avant d'être transmise (bouton *Annuler* sur la fiche du poste, *Annuler les postes en attente* pour un script). La fiche du poste et le suivi d'exécution affichent l'instant planifié. Champ laissé vide = comportement actuel (dès le prochain contact).

## 2.23.0 — 2026-10-20

- **Gestionnaire de fichiers du poste** (onglet *Fichiers* de la fiche d'un appareil) : parcourir l'arborescence, téléverser (bouton ou glisser-déposer), télécharger, créer un dossier, renommer, copier, déplacer, supprimer, modifier un fichier texte dans l'éditeur intégré, et ajuster les droits (octal) sous Linux. Fil d'Ariane, raccourcis vers la racine et le dossier personnel, affichage optionnel des fichiers cachés et progression par transfert. Comme les terminaux et le bureau, tout passe par le relais HTTPS — l'agent exécute chaque opération en root (SYSTEM sous Windows) et le serveur ne se connecte jamais au poste ; aucune adresse ni aucun jeton de poste n'arrive au navigateur. Nouveau droit *Gérer les fichiers du poste* (réservé à l'administrateur par défaut), agent 2.23 minimum ; le rafraîchissement automatique de la fiche est suspendu tant que l'onglet est ouvert, pour ne pas interrompre un transfert ou une édition.

## 2.22.0 — 2026-10-20

- **Paquet « agent et dépôts »** (page Enrôlement, Debian/Ubuntu et Rocky/AlmaLinux/RHEL) : le même paquet `loco-client` confie l'autorité de loco au système, branche le poste sur le cache et le dépôt local de sa version — le cache seul si ce dépôt n'existe pas encore — puis installe l'agent. C'est désormais le choix recommandé ; « agent seul » reste proposé. Sa version est un cran au-dessus (`+depots`, `1.depots`) : passer de l'agent seul au paquet complet est une mise à jour qui conserve l'inscription du poste, et désinstaller le paquet complet retire aussi la configuration des dépôts (dépôts du système rétablis sur Red Hat). Les scripts de dépôts gagnent l'option `--from-package` (aucun appel à APT ni à DNF, occupés par l'installation). Validé par des installations, mises à jour et désinstallations réelles sur Debian 13 et Rocky Linux 9.
- **Paquets de l'agent servis aussi en HTTP** (port d'amorçage), comme le script : les commandes « autorité pas encore importée » de la page Enrôlement fonctionnent.
- **Agent Red Hat** : l'inventaire (`dnf check-update`, `updateinfo`) accepte l'import de la clé d'un dépôt signé ajouté depuis ; sans terminal, dnf refusait et signalait à tort une « mauvaise signature » jusqu'à ce que quelqu'un lance dnf à la main.
- **Comptes : origine de la dernière connexion** — sous la date, l'adresse depuis laquelle le compte s'est connecté en dernier (vue par loco), et un lien vers l'historique de ses connexions dans le Journal. L'adresse est enregistrée à chaque connexion ; pour les comptes existants, elle est reprise du journal (`loco-user list` l'affiche aussi). La liste des comptes passe à la ligne au lieu de rogner les actions.
- **Page Système repensée** : bilan en tête (services actifs, jours de validité du certificat, disque, dernière sauvegarde avec alerte si la sauvegarde quotidienne manque, version), service arrêté et certificat absent signalés en haut de page ; panneaux appariés (services et sauvegardes, certificat et autorité, emplacements et versions) ; bouton de téléchargement de l'autorité et commandes d'import par système, Rocky / AlmaLinux / RHEL compris.

## 2.21.0 — 2026-10-20

- **Page Enrôlement** (barre de navigation) : tout ce qui rattache un poste à loco, un onglet par système (Windows ; Debian, Ubuntu ; Rocky, AlmaLinux, RHEL), en étapes numérotées — installer l'agent, configurer les dépôts et caches (APT, DNF, Flatpak), Windows Update et logiciels du catalogue sous Windows. Elle remplace le bloc « Brancher un poste » de la vue d'ensemble et la fenêtre « Ajouter un poste » de Mon parc (dont le bouton mène désormais à la page). Chaque section suit les droits et fonctionnalités du compte ; la clé d'inscription reste réservée au droit d'inscription.
- **Paquets .deb et .rpm de l'agent**, produits par loco pour ce serveur (adresse, autorité épinglée et clé d'inscription incluses) : `sudo apt install ./loco-client_<version>_all.deb` ou `sudo dnf install ./loco-client-<version>-1.noarch.rpm` inscrit le poste aussitôt, et désinstaller le paquet retire l'agent. Le paquet ne contient que le script d'installation (l'agent se met ensuite à jour seul). Validés avec dpkg-deb, rpm 4.14 et 4.16 (empreintes d'en-tête et de charge utile), puis par une installation et une désinstallation réelles sur Debian 13 et Rocky Linux 9.

## 2.20.1 — 2026-10-20

- **Disponibilité et mises à jour distinctes dans Mon parc** : près du nom du poste, une pastille verte indique un contact de l’agent dans les six dernières minutes (trois cycles habituels) et une rouge l’absence de contact récent. Le voyant du volume de mises à jour se trouve désormais dans la colonne *Mises à jour*. La fiche reprend la disponibilité près du nom et le bilan des mises à jour près des compteurs. Le passage hors ligne est pris en compte par le rafraîchissement automatique, même sans nouvelle activité du poste.
- Les échanges du relais distant et les résultats d’actions maintiennent aussi le poste en ligne pendant une longue opération. L’horodatage est enregistré au plus une fois par cycle de contact pour éviter une écriture à chaque image noVNC.

## 2.20.0 — 2026-10-20

- **Réseau en direct sur la vue d'ensemble** : débits entrant et sortant du serveur, relevés toutes les 2 secondes sur l'interface de la route par défaut (à défaut, les interfaces physiques), avec le pic et la courbe des 2 dernières minutes (échelle commune aux deux sens, valeur d'un instant au survol). Le débit est calculé dans le navigateur entre deux relevés des compteurs : rien n'est mesuré ni conservé quand la page n'est pas affichée. Linux (`/proc/net/dev`) et serveur Windows natif (compteurs 64 bits de l'API `GetIfEntry2`, vérifiés en CI contre `Get-NetAdapterStatistics`).
- **Icônes de retour dans la barre de navigation**.
- Captures du README mises à jour.

## 2.19.0 — 2026-10-20

- **Interface repensée, sobre et cohérente** : un vrai système de design remplace les ajouts successifs — échelle typographique resserrée (12 à 24 px), espacements sur une grille de 4 px, rayons 6 px (commandes) et 8 px (panneaux), une seule ombre pour les éléments flottants. Police à chasse fixe réservée au code (commandes, chemins, adresses, versions, empreintes) : noms de postes, dates, comptes, compteurs et valeurs des indicateurs passent en police normale. Libellés des indicateurs en minuscules au lieu de capitales espacées, liens non soulignés hors du texte courant, navigation principale sans icônes sur grand écran, en-tête translucide.
- **Vue d'ensemble** : état du parc en barre segmentée avec la liste des états (au lieu de l'anneau), occupation du disque en lignes avec des barres d'une seule teinte (au lieu de la barre arc-en-ciel), graphique Windows Update en deux tons d'un même bleu (palette vérifiée pour le contraste et le daltonisme). « Activité récente », qui listait en fait les ajouts au catalogue, devient « Derniers ajouts ».
- **États nommés partout pareil** : À jour, Mises à jour en attente, À traiter, État inconnu — sur la vue d'ensemble, Mon parc, la fiche d'un poste et les groupes.
- **Ergonomie** : liste du parc en cartes sur mobile (plus de défilement horizontal) ; option « Redémarrer si nécessaire » présentée comme un bouton ; filtres et recherche des paquets sur une seule barre ; zones de dépôt de fichiers en français avec un bouton « Parcourir… » au lieu du champ natif ; chevrons discrets à la place des triangles natifs ; confirmation en bouton principal, rouge seulement pour les actions destructrices (supprimer, retirer, redémarrer…) ; filtres sélectionnés marqués sans aplat d'accent ; pastilles d'état teintées ; en-têtes de colonnes numériques alignés.

## 2.18.0 — 2026-10-20

- **Paquets installés des postes Linux** (*Mon parc › fiche du poste*) : l'agent Linux remonte maintenant les paquets installés (nom, version, architecture, taille), les applications Flatpak et les caractéristiques du poste (processeur, mémoire, disque, noyau, architecture, virtualisation, démarrage). Un onglet *Paquets installés* les liste, avec une recherche et des filtres (tous, installés volontairement, à mettre à jour — la version candidate est alors montrée en vis-à-vis) ; un onglet *Flatpak* et un bloc *Caractéristiques* dans *Détails* complètent la fiche. L'inventaire détaillé est rangé dans une table à part, pour que la liste du parc reste légère. Debian/Ubuntu (`dpkg-query`/`apt-mark`) et Red Hat (`rpm`/`dnf repoquery`) ; relevé au rythme habituel de l'inventaire et refait une fois après la mise à jour de l'agent.
- **Les déploiements vivent dans Mon parc** : un déploiement est une action sur les appareils (cibles, avancement poste par poste), comme les scripts. L'onglet *Déploiements* de Mon parc reste désormais dans Mon parc (sous-menu du parc, entrée « Mon parc » active) au lieu de basculer vers Windows. *Windows › Logiciels* garde le catalogue local, le centre de logiciels et les groupes de logiciels ; leurs boutons *Déployer* ouvrent la préparation dans Mon parc. Nouvelles adresses `/parc/deploiements…` ; les anciennes (`/windows-apps/deploiements…`, `/windows-apps/deployer`) redirigent, paramètres compris.
- L'entrée « Mon parc » est aussi active sur les pages Scripts.

## 2.17.1 — 2026-10-20

- **Résolution du poste toujours rétablie à la fermeture de la console** (agent Windows) : en 2.17.0, quand le navigateur fermait la session pendant qu'une demande d'image attendait un changement d'écran, le compagnon de bureau ne voyait pas la fermeture du canal ; il était arrêté de force au bout de 5 secondes, sans rétablir la résolution d'origine (le poste restait par exemple en 848 × 480). Le canal fermé est maintenant constaté aussitôt : le compagnon se termine proprement et rend la résolution initiale. Vérifié sur une suite de sessions avec changements de résolution, plein écran et zoom.

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
