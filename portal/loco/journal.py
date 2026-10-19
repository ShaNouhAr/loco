"""Journal d'audit : libellés lisibles, catégories et filtres (page Journal et export JSON)."""

import datetime

# Catégories : clé -> (libellé, préfixes « x. » ou codes exacts, sous-codes compris). Une action appartient à la
# première catégorie qui la reconnaît, dans cet ordre : les sessions distantes avant le parc, les connexions SSO
# avant les comptes.
CATEGORIES = {
    "connexions": ("Connexions", ("connexion", "deconnexion", "sso.connexion", "sso.echec")),
    "distant": ("Accès distant", ("parc.session.",)),
    "parc": ("Parc", ("parc.", "groupes.machines", "groupes.appartenance", "deploiement.")),
    "logiciels": ("Logiciels Windows", ("windows.", "groupes.")),
    "linux": ("Dépôts Linux", ("linux.", "rpm.", "flatpak.")),
    "wu": ("Windows Update", ("windows_update.",)),
    "comptes": ("Comptes et profils", ("comptes.", "compte.", "profils.", "sso.")),
    "serveur": ("Serveur", ("systeme.",)),
    "journal": ("Journal", ("journal.",)),
}

LABELS = {
    "connexion": "Connexion", "deconnexion": "Déconnexion", "connexion.echec": "Échec de connexion",
    "connexion.bloquee": "Connexion bloquée (trop d'échecs)", "sso.connexion": "Connexion SSO", "sso.echec": "Échec de connexion SSO",
    "sso.demande": "Demande d'accès SSO", "sso.approbation": "Accès SSO approuvé",
    "sso.fournisseur.ajout": "Fournisseur SSO ajouté", "sso.fournisseur.modification": "Fournisseur SSO modifié",
    "sso.fournisseur.suppression": "Fournisseur SSO supprimé",
    "comptes.ajout": "Compte créé", "comptes.modification": "Compte modifié", "comptes.suppression": "Compte supprimé",
    "comptes.reinitialisation": "Mot de passe réinitialisé", "comptes.mot_de_passe": "Mot de passe défini",
    "compte.mot_de_passe": "Mot de passe changé", "profils.ajout": "Profil créé", "profils.modification": "Profil modifié",
    "profils.droits": "Droits d'un profil modifiés", "profils.suppression": "Profil supprimé",
    "parc.inscription": "Poste inscrit", "parc.oubli": "Poste oublié", "parc.cle": "Nouvelle clé d'inscription",
    "parc.seuil": "Seuil des pastilles", "parc.adresse-annoncee": "Adresse du serveur annoncée aux agents",
    "parc.adresse-retiree": "Adresse annoncée retirée", "parc.tout-mettre-a-jour": "Mise à jour de tout le parc",
    "parc.update": "Mise à jour demandée", "parc.scan": "Inventaire demandé", "parc.reboot": "Redémarrage demandé",
    "parc.uninstall": "Retrait de l'agent demandé", "parc.install-app": "Installation demandée",
    "parc.session.ouverture": "Session distante ouverte", "parc.session.fin": "Session distante terminée",
    "parc.session.fermeture": "Session distante fermée",
    "groupes.machines": "Groupe de machines modifié", "groupes.appartenance": "Groupes d'un poste modifiés",
    "groupes.logiciels": "Groupe de logiciels modifié", "groupes.ajout-logiciel": "Logiciel ajouté à un groupe",
    "groupes.suppression": "Groupe supprimé",
    "deploiement.preparation": "Déploiement préparé", "deploiement.confirmation": "Déploiement confirmé",
    "deploiement.annulation": "Déploiement annulé",
    "windows.ajout": "Logiciel ajouté au catalogue", "windows.modification": "Logiciel modifié",
    "windows.suppression": "Logiciel supprimé", "windows.actualisation": "Installeur retéléchargé",
    "windows.winget": "Logiciel importé depuis WinGet", "windows.winget-maj": "Nouvelle version WinGet",
    "linux.ajout": "Paquet APT ajouté", "linux.copie": "Paquet APT copié", "linux.suppression": "Paquet APT supprimé",
    "linux.depot.ajout": "Dépôt APT créé", "rpm.ajout": "Paquet RPM ajouté", "rpm.suppression": "Paquet RPM supprimé",
    "rpm.depot.ajout": "Dépôt RPM créé", "flatpak.ajout": "Application Flatpak ajoutée",
    "flatpak.suppression": "Application Flatpak supprimée", "windows_update.domaines": "Domaines Windows Update modifiés",
    "systeme.configuration": "Configuration du serveur", "systeme.fonctionnalites": "Fonctionnalités activées ou désactivées", "systeme.check": "Recherche de mises à jour",
    "systeme.update-loco": "Mise à jour de loco", "systeme.update-system": "Mise à jour du système",
    "systeme.reboot": "Redémarrage du serveur", "systeme.configure": "Réglages du serveur", "journal.export": "Journal exporté",
    "journal.conservation": "Conservation du journal modifiée",
}
FAILURES = ("echec", "bloquee")
PERIODS = {"jour": ("Aujourd'hui", 0), "7j": ("7 jours", 6), "30j": ("30 jours", 29), "tout": ("Tout", None)}


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
        raise ValueError("La date de début doit précéder la date de fin.")
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
