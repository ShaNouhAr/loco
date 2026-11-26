[English](../high-availability.md) · **Français** · [Español](../es/high-availability.md)

# Haute disponibilité

Deux serveurs loco, une seule **IP virtuelle** : le **principal** sert le portail, les dépôts et les agents ; le **secours** réplique tout en continu et prend l’IP automatiquement si le principal tombe (keepalived / VRRP). Les postes, le portail et les dépôts ne connaissent que l’IP virtuelle, inchangée lors d’une bascule. Dans **Administration → Serveur → Haute disponibilité** :

- **Mettre en place** : sur le premier serveur, **Activer** demande une IP virtuelle libre et affiche un **code d’appairage** à usage unique ; sur un second serveur neuf (même version de loco), **Rejoindre** avec l’adresse du principal et ce code copie toute la configuration et les dépôts, puis arme la bascule. loco règle lui‑même son adresse sur l’IP virtuelle et étend son certificat à l’IP virtuelle et aux deux serveurs.
- **Réplication** : configuration, base et dépôts passent **chiffrés** (AES‑256‑GCM) d’un serveur à l’autre, authentifiés par un secret de grappe dont le portail ne garde que l’empreinte. Les caches (Windows Update, APT…) ne sont pas répliqués : ils se reconstruisent. Pendant une mise à jour, le contrôle de santé ne déclenche pas de bascule.
- **Bascule et supervision** : la page montre le rôle de chaque serveur, qui porte l’IP virtuelle, la fraîcheur de la réplication, l’état de keepalived et les derniers événements. **Basculer vers le secours** fait une bascule volontaire (maintenance planifiée) ; le secours reste passif (en lecture) tant qu’il ne porte pas l’IP virtuelle.

Réservé au droit **Configurer le serveur**. Indisponible avec Docker ; `keepalived` est inclus dans l’appliance et installé à la demande sur Debian/Ubuntu et Red Hat.

---

[← Retour à la présentation](../../README.fr.md) · [Toute la documentation](README.md)
