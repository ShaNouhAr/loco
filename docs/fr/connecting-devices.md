[English](../connecting-devices.md) · **Français** · [Español](../es/connecting-devices.md)

# Brancher les postes

Dépôts, caches et logiciels : une commande par poste, ou une stratégie de groupe pour tout le domaine. Pour gérer les postes eux-mêmes (inventaire, mises à jour, prise en main), voir [Mon parc](fleet.md).

Dans **Linux › Paquets**, créez d'abord les dépôts nécessaires : distribution, numéro de version
facultatif et nom de code (par exemple `debian/trixie` ou `ubuntu/noble`). Une future version s'ajoute
depuis cette page, sans modifier le code. Chaque dépôt a ses propres fichiers et index signés.

Les postes font confiance à l'autorité locale (`ca.crt`, déployée par GPO ou depuis la page
*Administration › Système*), puis :

```bash
# Debian / Ubuntu : dépôt local + cache APT
curl -fsSL https://<ip>:8443/linux/setup.sh | sudo sh
# … premier branchement, autorité pas encore importée (affiche son empreinte)
curl -fsSL http://<ip>:8088/linux/setup.sh | sudo sh -s -- --trust-ca
# Cache seul, sans dépôt local
curl -fsSL https://<ip>:8443/linux/setup.sh | sudo sh -s -- --cache-only

# Rocky Linux, AlmaLinux, RHEL 8+ : dépôt local elN + dépôts officiels via le cache (--epel : EPEL aussi)
curl -fsSL https://<ip>:8443/rpm/setup.sh | sudo sh

# Flatpak : Flathub via le cache + dépôt local « loco »
curl -fsSL https://<ip>:8443/flatpak/setup.sh | sudo sh
```

```powershell
# Windows, PowerShell administrateur : installe un ou plusieurs logiciels du catalogue
irm 'https://<ip>:8443/win/i/7-zip,vlc' | iex
```

Le script Windows vérifie le SHA-256 de chaque installeur, laisse 30 minutes à chacun (`$env:LOCO_TIMEOUT`)
et reconnaît les codes « redémarrage requis », « déjà installé » et « non applicable ».

Pour Windows Update, deux façons d'envoyer les téléchargements des postes vers loco, au choix sur la page
*Windows Update* : **par DNS** (le DNS du réseau renvoie l'IP du serveur pour les domaines de mise à jour ; un
script pour un DNS Active Directory est fourni) ou **par proxy, sans DNS** — loco embarque un proxy-cache (Squid) et
l'agent règle le proxy WinHTTP des postes Windows gérés vers loco (par poste, réversible, retiré si loco devient
injoignable). Les deux mettent en cache les mêmes fichiers immuables et relaient le HTTPS sans le déchiffrer. La
commande manuelle `netsh winhttp set proxy` est aussi proposée (pages *Windows Update* et *Enrôlement*).

Le script Linux lit `/etc/os-release` et sélectionne le dépôt correspondant au poste. Si ce dépôt
n'existe pas, il s'arrête avant de remplacer la configuration. Les dérivées peuvent choisir explicitement
`--distribution ubuntu --suite noble`. Les sources APT sont au format deb822 (`loco.sources`).

**Mise à jour depuis 1.0.0** : l'ancien dépôt `/apt/` et les postes déjà branchés restent fonctionnels.
L'action *Copier vers un autre dépôt* conserve les originaux. Vérifiez la compatibilité de chaque paquet,
puis relancez le script sur les postes. `--legacy` permet de conserver explicitement l'ancien dépôt partagé.

---

[← Retour à la présentation](../../README.fr.md) · [Toute la documentation](README.md)
