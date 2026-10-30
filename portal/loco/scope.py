"""Parc accessible d'un profil : tout le parc, ou les postes de groupes choisis et des postes choisis un à un.

Le parc limite ce qu'un compte voit et vise partout où il est question de postes : Mon parc, fiche, recherche, vue
d'ensemble, groupes, déploiements, scripts, planifications, sessions distantes et outils MCP (dont les droits sont
ceux du compte qui a créé le jeton). Les profils intégrés couvrent tout le parc ; les tâches de fond, sans compte,
aussi. Le journal reste global (droit « Consulter le journal »)."""
from flask import abort, g

from .db import db

# Droits qui agissent sur tout le parc quel que soit le profil (groupes dont dépendent les parcs des profils, clé
# d'inscription, adresses et seuils) : sans effet pour un profil limité à une partie du parc.
WHOLE_FLEET = frozenset({"fleet.groups", "fleet.settings"})


def declared(conn, role_id):
    """(tout le parc, groupes, postes) choisis pour un profil ; profil inconnu : rien."""
    row = conn.execute("SELECT whole_fleet FROM access_roles WHERE id=?", (role_id,)).fetchone()
    if row is None:
        return False, frozenset(), frozenset()
    if row["whole_fleet"]:
        return True, frozenset(), frozenset()
    groups = frozenset(r[0] for r in conn.execute("SELECT group_id FROM access_role_groups WHERE role_id=?", (role_id,)))
    devices = frozenset(r[0] for r in conn.execute("SELECT device_id FROM access_role_devices WHERE role_id=?", (role_id,)))
    return False, groups, devices


def resolve(conn, role_id):
    """Postes du parc d'un profil : None pour tout le parc, sinon les identifiants (membres actuels des groupes)."""
    whole, groups, devices = declared(conn, role_id)
    if whole:
        return None
    ids = set(devices)
    if groups:
        marks = ",".join("?" * len(groups))
        ids.update(r[0] for r in conn.execute(f"SELECT device_id FROM device_group_members WHERE group_id IN ({marks})",
                                              tuple(groups)))
    return frozenset(ids)


def visible():
    """Parc du compte courant (None = tout le parc), calculé une fois par requête ; sans compte (agents, tâches de
    fond) : tout le parc."""
    if "fleet_scope" not in g:
        user = g.get("user")
        g.fleet_scope = None if user is None else resolve(db(), user["role"])
    return g.fleet_scope


def whole():
    return visible() is None


def allows(dev_id):
    ids = visible()
    return ids is None or dev_id in ids


def sql(column="id"):
    """Filtre du parc à ajouter à une requête (« AND id IN (…) ») ; identifiants entiers venus de la base."""
    ids = visible()
    if ids is None:
        return ""
    return f" AND {column} IN ({','.join(str(int(i)) for i in sorted(ids)) or 'NULL'})"


def keep(rows, key=lambda r: r["id"]):
    """Lignes dont le poste est dans le parc."""
    ids = visible()
    return list(rows) if ids is None else [r for r in rows if key(r) in ids]


def require_whole():
    """Actions qui touchent des postes hors parc (groupes, clé d'inscription…) : réservées à tout le parc."""
    if not whole():
        abort(403)


def within(conn, role_id):
    """Le parc du profil role_id tient-il dans celui du compte courant ? Comparaison des groupes déclarés (pas de leurs
    membres du moment : un groupe qui grandit ne doit pas élargir un profil délégué) et des postes choisis."""
    user = g.get("user")
    if user is None:
        return True
    mine_whole, mine_groups, _devices = declared(conn, user["role"])
    if mine_whole:
        return True
    whole, groups, devices = declared(conn, role_id)
    return not whole and groups <= mine_groups and devices <= (visible() or frozenset())
