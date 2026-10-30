"""Journal d'audit : libellés lisibles, catégories et filtres (page Journal et export JSON)."""

import datetime
from .i18n import L_, _

# Catégories : clé -> (libellé, préfixes « x. » ou codes exacts, sous-codes compris). Une action appartient à la
# première catégorie qui la reconnaît, dans cet ordre : les sessions distantes avant le parc, les connexions par annuaire
# avant les comptes.
CATEGORIES = {
    "connexions": (L_("Connexions"), ("connexion", "deconnexion", "sso.connexion", "sso.echec")),
    "distant": (L_("Accès distant"), ("parc.session.", "parc.fichiers.", "parc.bureau.")),
    "parc": (L_("Parc"), ("parc.", "groupes.machines", "groupes.appartenance", "deploiement.", "planifications.")),
    "logiciels": (L_("Logiciels Windows"), ("windows.", "groupes.")),
    "linux": (L_("Dépôts Linux"), ("linux.", "rpm.", "flatpak.")),
    "wu": (L_("Windows Update"), ("windows_update.",)),
    "comptes": (L_("Comptes et profils"), ("comptes.", "compte.", "profils.", "sso.")),
    "serveur": (L_("Serveur"), ("systeme.", "alertes.")),
    "journal": (L_("Journal"), ("journal.",)),
    "scripts": (L_("Scripts"), ("scripts.",)),
    "mcp": (L_("Serveur MCP"), ("mcp.",)),
}

LABELS = {
    "connexion": L_("Connexion"), "deconnexion": L_("Déconnexion"), "connexion.echec": L_("Échec de connexion"),
    "connexion.bloquee": L_("Connexion bloquée (trop d'échecs)"), "sso.connexion": L_("Connexion par annuaire"), "sso.echec": L_("Annuaire injoignable ou en erreur"),
    "sso.demande": L_("Demande d'accès (annuaire)"), "sso.approbation": L_("Accès approuvé (annuaire)"), "sso.test": L_("Test d'un annuaire"),
    "sso.fournisseur.ajout": L_("Annuaire ajouté"), "sso.fournisseur.modification": L_("Annuaire modifié"),
    "sso.fournisseur.suppression": L_("Annuaire supprimé"),
    "comptes.ajout": L_("Compte créé"), "comptes.modification": L_("Compte modifié"), "comptes.suppression": L_("Compte supprimé"),
    "comptes.reinitialisation": L_("Mot de passe réinitialisé"), "comptes.mot_de_passe": L_("Mot de passe défini"),
    "compte.mot_de_passe": L_("Mot de passe changé"), "profils.ajout": L_("Profil créé"), "profils.modification": L_("Profil modifié"),
    "profils.droits": L_("Droits d'un profil modifiés"), "profils.suppression": L_("Profil supprimé"),
    "parc.inscription": L_("Poste inscrit"), "parc.oubli": L_("Poste oublié"), "parc.cle": L_("Nouvelle clé d'inscription"),
    "parc.seuil": L_("Seuil des pastilles"), "parc.adresse-annoncee": L_("Adresse du serveur annoncée aux agents"),
    "parc.adresse-retiree": L_("Adresse annoncée retirée"), "parc.tout-mettre-a-jour": L_("Mise à jour de tout le parc"),
    "parc.update": L_("Mise à jour demandée"), "parc.scan": L_("Inventaire demandé"), "parc.reboot": L_("Redémarrage demandé"),
    "parc.uninstall": L_("Retrait de l'agent demandé"), "parc.install-app": L_("Installation demandée"),
    "parc.session.ouverture": L_("Session distante ouverte"), "parc.session.fin": L_("Session distante terminée"),
    "parc.session.fermeture": L_("Session distante fermée"),
    "groupes.machines": L_("Groupe de machines modifié"), "groupes.appartenance": L_("Groupes d'un poste modifiés"),
    "groupes.logiciels": L_("Groupe de logiciels modifié"), "groupes.ajout-logiciel": L_("Logiciel ajouté à un groupe"),
    "groupes.suppression": L_("Groupe supprimé"), "groupes.renommage": L_("Groupe renommé"),
    "deploiement.preparation": L_("Déploiement préparé"), "deploiement.confirmation": L_("Déploiement confirmé"),
    "deploiement.annulation": L_("Déploiement annulé"),
    "windows.ajout": L_("Logiciel ajouté au catalogue"), "windows.modification": L_("Logiciel modifié"),
    "windows.suppression": L_("Logiciel supprimé"), "windows.actualisation": L_("Installeur retéléchargé"),
    "windows.winget": L_("Logiciel importé depuis WinGet"), "windows.winget-maj": L_("Nouvelle version WinGet"),
    "linux.ajout": L_("Paquet APT ajouté"), "linux.copie": L_("Paquet APT copié"), "linux.suppression": L_("Paquet APT supprimé"),
    "linux.depot.ajout": L_("Dépôt APT créé"), "rpm.ajout": L_("Paquet RPM ajouté"), "rpm.suppression": L_("Paquet RPM supprimé"),
    "rpm.depot.ajout": L_("Dépôt RPM créé"), "flatpak.ajout": L_("Application Flatpak ajoutée"),
    "flatpak.suppression": L_("Application Flatpak supprimée"), "windows_update.domaines": L_("Domaines Windows Update modifiés"),
    "systeme.configuration": L_("Configuration du serveur"), "systeme.fonctionnalites": L_("Fonctionnalités activées ou désactivées"), "systeme.check": L_("Recherche de mises à jour"),
    "systeme.update-loco": L_("Mise à jour de loco"), "systeme.update-system": L_("Mise à jour du système"), "systeme.update-security": L_("Correctifs de sécurité du serveur"),
    "systeme.reboot": L_("Redémarrage du serveur"), "systeme.heure": L_("Heure du serveur modifiée"), "systeme.qemu-agent": L_("Agent invité QEMU installé"), "systeme.set-repo": L_("Source des mises à jour modifiée"), "systeme.configure": L_("Réglages du serveur"), "journal.export": L_("Journal exporté"),
    "journal.conservation": L_("Conservation du journal modifiée"),
    "scripts.ajout": L_("Script créé"), "scripts.modification": L_("Script modifié"), "scripts.suppression": L_("Script supprimé"),
    "scripts.execution": L_("Script exécuté sur des postes"),
    "scripts.historique": L_("Historique des scripts nettoyé"),
    "compte.2fa.activee": L_("Double authentification activée"), "compte.2fa.desactivee": L_("Double authentification désactivée"),
    "compte.2fa.codes": L_("Codes de secours régénérés"), "comptes.2fa.reinitialisation": L_("Double authentification retirée d'un compte"),
    "parc.mise-a-jour-selection": L_("Mise à jour des postes sélectionnés"), "parc.install-package": L_("Installation de paquet demandée"),
    "planifications.creation": L_("Planification créée"), "planifications.modification": L_("Planification modifiée"),
    "planifications.suppression": L_("Planification supprimée"), "planifications.pause": L_("Planification suspendue"),
    "planifications.reprise": L_("Planification reprise"), "planifications.execution": L_("Planification exécutée"),
    "systeme.restauration": L_("Restauration d'une sauvegarde"),
    "systeme.certificat.renouvellement": L_("Certificat HTTPS renouvelé"), "systeme.certificat.import": L_("Certificat HTTPS importé"),
    "systeme.certificat.retrait": L_("Certificat importé retiré"),
    "systeme.autorite.import": L_("Autorité d'entreprise importée"), "systeme.autorite.retrait": L_("Autorité d'entreprise retirée"),
    "systeme.autorite.demande": L_("Demande de certificat préparée"), "systeme.autorite.certificat": L_("Certificat d'entreprise importé"),
    "systeme.autorite.bascule": L_("Autorité du certificat changée"), "systeme.sauvegarde.telechargement": L_("Sauvegarde téléchargée"),
    "systeme.sauvegarde.suppression": L_("Sauvegarde supprimée"), "systeme.sauvegarde.retention": L_("Conservation des sauvegardes modifiée"),
    "systeme.sauvegarde.hors-site": L_("Copie hors-site configurée"),
    "systeme.shell.activation": L_("Shell root activé"), "systeme.shell.desactivation": L_("Shell root désactivé"),
    "systeme.shell.ouverture": L_("Shell root ouvert"), "systeme.shell.fermeture": L_("Shell root fermé"),
    "systeme.shell.refus": L_("Activation du shell root refusée"),
    "alertes.configuration": L_("Alertes configurées"),
    "mcp.acces.ajout": L_("Jeton MCP créé"), "mcp.acces.modification": L_("Outils d'un jeton MCP modifiés"),
    "mcp.acces.revocation": L_("Jeton MCP révoqué"),
    "parc.session.commande": L_("Commande lancée via MCP"),
    "parc.fichiers.ecriture": L_("Fichier écrit via MCP"), "parc.fichiers.modification": L_("Fichiers organisés via MCP"),
    "parc.fichiers.suppression": L_("Fichier supprimé via MCP"), "parc.bureau.action": L_("Action sur le bureau via MCP"),
}
FAILURES = ("echec", "bloquee", "refus")
PERIODS = {"jour": (L_("Aujourd'hui"), 0), "7j": (L_("7 jours"), 6), "30j": (L_("30 jours"), 29), "tout": (L_("Tout"), None)}


PAGE = 100


def _matches(action, prefixes):
    return any(action.startswith(p) if p.endswith(".") else action == p or action.startswith(p + ".")
               for p in prefixes)


def category(action):
    return next((key for key, (_label, prefixes) in CATEGORIES.items() if _matches(action, prefixes)), "")


def describe(action):
    """{label, category, category_label, failure} d'un code d'action (« parc.adresse-annoncee »…)."""
    key = category(action)
    label = LABELS.get(action) or action.replace("_", " ").replace(".", " · ").replace("-", " ").capitalize()
    return {"label": label, "category": key, "category_label": CATEGORIES[key][0] if key else "Autre",
            "failure": action.endswith(FAILURES)}


def _like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sql(prefixes):
    parts, values = [], []
    for p in prefixes:
        if p.endswith("."):
            parts.append("action LIKE ? ESCAPE '\\'")
            values.append(_like(p) + "%")
        else:
            parts.append("action = ? OR action LIKE ? ESCAPE '\\'")
            values += [p, _like(p) + ".%"]
    return "(" + " OR ".join(parts) + ")", values


def _category_sql(key):
    """Même règle que category() : reconnue par cette catégorie et par aucune des précédentes."""
    clause, values = _sql(CATEGORIES[key][1])
    for other in CATEGORIES:
        if other == key:
            break
        before, more = _sql(CATEGORIES[other][1])
        clause += " AND NOT " + before
        values += more
    return clause, values


def bounds(args):
    """Période demandée -> (début, fin) inclus au format de la colonne « at », ou None. ValueError si invalide."""
    period = args.get("periode", "")
    if period in PERIODS and PERIODS[period][1] is not None:
        day = datetime.date.today() - datetime.timedelta(days=PERIODS[period][1])
        return day.isoformat() + " 00:00:00", None
    start, end = (datetime.datetime.strptime(args[k], "%Y-%m-%d").strftime("%Y-%m-%d") if args.get(k) else None
                  for k in ("debut", "fin"))
    if start and end and start > end:
        raise ValueError(_('La date de début doit précéder la date de fin.'))
    return (start + " 00:00:00" if start else None), (end + " 23:59:59" if end else None)


def where(args, skip=()):
    """Clause SQL et paramètres des filtres (période, catégorie, compte, échecs, recherche), sauf ceux de « skip »."""
    clauses, values = [], []
    start, end = bounds(args)
    if start:
        clauses.append("at >= ?")
        values.append(start)
    if end:
        clauses.append("at <= ?")
        values.append(end)
    if args.get("categorie") in CATEGORIES and "categorie" not in skip:
        clause, more = _category_sql(args["categorie"])
        clauses.append(clause)
        values += more
    if args.get("compte"):
        clauses.append("username = ?")
        values.append(args["compte"])
    if args.get("echecs") == "1":
        clauses.append("(action LIKE '%echec' OR action LIKE '%bloquee')")
    text = (args.get("q") or "").strip()[:100]
    if text:
        clauses.append("(detail LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\'"
                       " OR ip LIKE ? ESCAPE '\\')")
        values += ["%" + _like(text) + "%"] * 4
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", values


def category_counts(conn, args):
    """Nombre d'événements par catégorie avec les autres filtres de la page (clé « » : actions non classées)."""
    sql, values = where(args, skip=("categorie",))
    counts = dict.fromkeys(CATEGORIES, 0)
    for row in conn.execute("SELECT action, COUNT(*) FROM audit" + sql + " GROUP BY action", values):
        key = category(row[0])
        counts[key] = counts.get(key, 0) + row[1]
    return counts


def day_label(stamp):
    day = datetime.date.fromisoformat(stamp[:10])
    delta = (datetime.date.today() - day).days
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Hier"
    days = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    months = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
              "novembre", "décembre")
    return f"{days[day.weekday()].capitalize()} {day.day} {months[day.month - 1]}" + (f" {day.year}" if day.year != datetime.date.today().year else "")
