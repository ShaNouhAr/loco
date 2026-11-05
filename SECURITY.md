# Sécurité

## Signaler une vulnérabilité

Ouvrez un avis privé sur GitHub (onglet *Security › Report a vulnerability*) plutôt qu'une issue publique.
Merci d'indiquer la version (`loco` en bas de la page de connexion), la distribution et les étapes
pour reproduire.

## Modèle de menace

loco est conçu pour un **réseau d'entreprise interne**. Il n'est pas prévu pour être exposé sur Internet.

| Élément | Mesures |
|---|---|
| Interface d'administration | HTTPS uniquement (TLS 1.2+), comptes nommés avec rôles, mots de passe hachés (scrypt, ou PBKDF2 avec les versions de Werkzeug des distributions plus anciennes), politique de longueur, changement imposé des mots de passe temporaires, blocage après échecs (par compte et par adresse), double authentification TOTP avec codes de secours, connexion possible par annuaire (LDAP, RADIUS) ou SSO OpenID Connect, sessions expirant après 8 h d'inactivité et invalidées au changement de mot de passe, jeton CSRF, cookie `Secure`/`HttpOnly`/`SameSite=Strict`, en-têtes CSP stricte (aucun script ni style en ligne), `X-Frame-Options: DENY`, `nosniff`, réseaux autorisés dédiés (`--admin-allow`), journal d'audit |
| Dépôts APT locaux | Index signés GPG (RSA 4096), clé générée sur le serveur ; paquets et index isolés par distribution/version ; noms de dépôts validés ; un même paquet/version ne peut pas être remplacé par un contenu différent |
| Dépôts RPM locaux | `repomd.xml` signé GPG et vérifié par dnf (`repo_gpgcheck=1`) ; il contient l'empreinte SHA-256 de chaque paquet |
| Cache RPM (Rocky Linux, AlmaLinux, EPEL) | Certificats des miroirs vérifiés ; paquets vérifiés par les postes avec les clés GPG de leur distribution (`gpgcheck=1`) |
| Dépôt Flatpak local | Commits et résumé signés GPG ; Flathub reste vérifié avec sa propre clé |
| Logiciels Windows | SHA-256 de chaque installeur vérifié par le script avant exécution ; commandes recommandées en HTTPS |
| Cache Windows Update | Ne relaie que les domaines de sa liste (HTTP et SNI) ; HTTPS relayé sans déchiffrement ; seuls les fichiers immuables sont mis en cache |
| apt-cacher-ng | Clients filtrés (tcpwrappers sur Debian/Ubuntu ; relais TCP nginx avec ACL et backend en boucle locale sur Rocky), pages de maintenance protégées par mot de passe |
| Portail (processus) | Utilisateur dédié sans shell, unité systemd confinée (système en lecture seule, aucune capacité, appels système filtrés, `systemd-analyze security` ≈ 1,5) ; nginx seul exposé |
| Secrets | Mots de passe du portail hachés ; identifiants de maintenance apt-cacher-ng dans des fichiers à accès restreint (0600 pour le secret, 0640 root/apt-cacher-ng pour sa configuration) ; clés privées et sauvegardes en 0600 |

## Limites connues

- Les `.rpm` des dépôts locaux ne sont pas re-signés un par un : leur intégrité repose sur les
  métadonnées signées (`repo_gpgcheck=1`), et non sur une signature par paquet (`gpgcheck=0`).
- Les dépôts RHEL (abonnement Red Hat) ne passent pas par le cache.
- Déploiement Docker : les processus gardent leurs comptes dédiés et le conteneur n'a que les capacités
  utiles, mais le confinement systemd du portail n'y est pas disponible ; l'installation native est plus
  cloisonnée. Le conteneur utilise le réseau de l'hôte.

- Le port HTTP d'amorçage (8088) sert scripts et installeurs sans chiffrement : un attaquant sur le réseau
  pourrait modifier un script. Déployez l'autorité de certification puis utilisez `--no-http`.
- `setup.sh --trust-ca` fait confiance à l'autorité reçue au premier contact (comparer l'empreinte affichée).
- La double authentification de loco ne s'applique qu'à la connexion par mot de passe local. Une connexion par annuaire ou
  par SSO suit les règles de l'annuaire ou du fournisseur d'identité.
- Les postes accèdent aux dépôts sans authentification ; la restriction se fait par réseau (`--allow`).
- Envoi syslog : en TCP et en UDP, les journaux circulent en clair ; préférez TLS. Le contenu des journaux des postes reste déclaratif
  (un poste compromis peut écrire ce qu'il veut dans ses propres lignes) : loco impose le nom d'hôte et retire les retours à la ligne et
  les caractères de contrôle, pour qu'un poste ne puisse ni se faire passer pour un autre ni forger une ligne. Pas de certificat client (mTLS).
- L'isolation APT évite de mélanger les dépôts ; elle ne prouve pas qu'un `.deb` est compatible avec
  sa destination. L'administrateur doit valider les dépendances et l'origine des paquets avant l'import
  ou la copie. Le dépôt historique et `setup.sh --legacy` restent partagés pour la transition.
