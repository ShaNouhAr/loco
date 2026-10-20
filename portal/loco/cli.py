"""Commandes d'administration : python3 -m loco.cli <commande>.

  init                      migrations de la base, reprise de l'ancien mot de passe
  ensure-admin              crée le compte « admin » s'il n'existe aucun compte (mot de passe : $LOCO_PASSWORD)
  rebuild-apt               régénère et signe tous les dépôts APT
  apt-repo add DISTRIBUTION SUITE [VERSION]   crée un dépôt pour Debian / Ubuntu
  apt-repo list             liste les dépôts par distribution et version
  write-maps                régénère les listes de domaines Windows Update pour nginx
  flatpak-init              crée (si besoin) et signe le dépôt Flatpak local
  rpm-init                  clé publique et dépôts RPM el8, el9, el10 (créés si besoin, resignés)
  user list                 liste les comptes
  user roles                liste les identifiants des profils disponibles
  user set-role NOM PROFIL  attribue un profil (console root : récupération des accès)
  user add NOM [PROFIL]   crée un compte et affiche un mot de passe temporaire
  user passwd NOM           nouveau mot de passe temporaire (à changer à la connexion)
  user set-password NOM     définit le mot de passe depuis $LOCO_PASSWORD
  user enable|disable NOM   active / désactive un compte
  fleet servers             adresses du serveur transmises aux agents, et postes prévenus
  fleet announce URL        annonce une future adresse (https://hôte:port) avant de changer celle du serveur
  fleet forget URL          retire une adresse annoncée
"""

import datetime
import os
import sys

from . import app, auth, config, permissions, startup
from .db import transaction


def _audit(conn, action, detail):
    conn.execute("INSERT INTO audit (at, username, ip, action, detail) VALUES (?,?,?,?,?)",
                 (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "root", "console", action, detail))


def _user(conn, name):
    row = conn.execute("SELECT * FROM users WHERE username=?", (name.lower(),)).fetchone()
    if row is None:
        sys.exit(f"Compte inconnu : {name}")
    return row


def user_command(args):
    with transaction() as conn:
        labels = permissions.role_labels(conn)
        if args and args[0] == "roles":
            for key, label in labels.items():
                print(f"{key:<24} {label}")
            return
        if not args or args[0] == "list":
            for r in conn.execute("SELECT * FROM users ORDER BY username"):
                state = "désactivé" if r["disabled"] else ("à changer" if r["must_change"] else "actif")
                print(f"{r['username']:<24} {labels.get(r['role'], r['role']):<16} {state:<10} "
                      f"dernière connexion : {r['last_login'] or '—'}"
                      + (f" depuis {r['last_ip']}" if r["last_ip"] else ""))
            return
        cmd, name = args[0], (args[1] if len(args) > 1 else "")
        if not name:
            sys.exit(__doc__)
        if cmd == "add":
            role = args[2] if len(args) > 2 else "admin"
            if role not in labels or not config.USERNAME_RE.match(name.lower()):
                sys.exit("Nom ou profil invalide (voir loco-user roles).")
            if conn.execute("SELECT 1 FROM users WHERE username=?", (name.lower(),)).fetchone():
                sys.exit(f"Le compte {name} existe déjà.")
            password = auth.generate_password()
            auth.create_user(conn, name, password, role, must_change=True)
            _audit(conn, "comptes.ajout", f"{name.lower()} ({labels[role]})")
            print(f"Compte {name.lower()} créé. Mot de passe temporaire : {password}")
        elif cmd == "set-role":
            row = _user(conn, name)
            role = args[2] if len(args) == 3 else ""
            if role not in labels:
                sys.exit("Profil inconnu (voir loco-user roles).")
            if row["role"] == "admin" and not row["disabled"] and role != "admin" and auth.active_admins(conn) <= 1:
                sys.exit("Il doit rester au moins un administrateur actif.")
            conn.execute("UPDATE users SET role=? WHERE id=?", (role, row["id"]))
            permissions.revoke(conn, row["id"])
            _audit(conn, "comptes.modification", f"{row['username']} : profil {labels[role]}")
            print(f"Profil de {row['username']} : {labels[role]}")
        elif cmd == "passwd":
            row = _user(conn, name)
            password = auth.generate_password()
            auth.set_password(conn, row["id"], password, must_change=True)
            conn.execute("UPDATE users SET disabled=0 WHERE id=?", (row["id"],))
            _audit(conn, "comptes.reinitialisation", row["username"])
            print(f"Mot de passe temporaire de {row['username']} : {password}")
        elif cmd == "set-password":
            row = _user(conn, name)
            password = os.environ.get("LOCO_PASSWORD", "")
            problem = auth.password_problem(password, row["username"])
            if problem:
                sys.exit(problem)
            auth.set_password(conn, row["id"], password, must_change=False)
            _audit(conn, "comptes.mot_de_passe", row["username"])
        elif cmd in ("enable", "disable"):
            row = _user(conn, name)
            if cmd == "disable" and row["role"] == "admin" and not row["disabled"] and auth.active_admins(conn) <= 1:
                sys.exit("Il doit rester au moins un administrateur actif.")
            conn.execute("UPDATE users SET disabled=? WHERE id=?", (int(cmd == "disable"), row["id"]))
            permissions.revoke(conn, row["id"])
            _audit(conn, "comptes.modification", f"{row['username']} : {'désactivé' if cmd == 'disable' else 'réactivé'}")
            print(f"Compte {row['username']} {'désactivé' if cmd == 'disable' else 'réactivé'}.")
        else:
            sys.exit(__doc__)


def fleet_command(args):
    from . import addresses
    with transaction() as conn:
        if args[:1] == ["announce"] and len(args) == 2:
            try:
                url = addresses.announce(conn, args[1])
            except ValueError as e:
                sys.exit(f"Adresse refusée : {e}.")
            _audit(conn, "parc.adresse-annoncee", url)
        elif args[:1] == ["forget"] and len(args) == 2:
            if addresses.forget(conn, args[1]):
                _audit(conn, "parc.adresse-retiree", args[1])
        elif args != ["servers"]:
            sys.exit(__doc__)
        for i, (url, known, total) in enumerate(addresses.coverage(conn)):
            print(f"{url:<40} {'actuelle ' if i == 0 else 'annoncée '} {known}/{total} poste(s)")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    startup()
    cmd = argv[0]
    if cmd == "init":
        return
    if cmd == "ensure-admin":
        with transaction() as conn:
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                auth.create_user(conn, "admin", os.environ["LOCO_PASSWORD"], "admin",
                                 must_change=os.environ.get("LOCO_MUST_CHANGE") == "1")
                _audit(conn, "comptes.ajout", "admin (installation)")
                print("created")
    elif cmd == "rebuild-apt":
        from .linux import rebuild_apt, repositories
        rebuild_apt()
        for repo in repositories():
            rebuild_apt(repo["key"])
    elif cmd == "apt-repo":
        from .linux import create_repository, repositories
        if len(argv) == 2 and argv[1] == "list":
            for repo in repositories():
                print(f"{repo['key']:<32} {repo['label']}")
        elif len(argv) in (4, 5) and argv[1] == "add":
            key = create_repository(argv[2], argv[3], argv[4] if len(argv) == 5 else "")
            with transaction() as conn:
                _audit(conn, "linux.depot.ajout", key)
            print(key)
        else:
            sys.exit(__doc__)
    elif cmd == "write-maps":
        from .wupdate import write_wu_maps
        write_wu_maps()
    elif cmd == "rpm-init":
        import subprocess
        from . import rpmrepo
        os.makedirs(config.RPM_DIR, exist_ok=True)
        if config.WINDOWS:
            from . import signing
            key = signing.public_key(armored=True)
        else:
            key = subprocess.run([config.GPG, "--batch", "--armor", "--export"],
                                 env=dict(os.environ, GNUPGHOME=config.GNUPGHOME), check=True, capture_output=True).stdout
        tmp = os.path.join(config.RPM_DIR, ".loco.asc.tmp")
        with open(tmp, "wb") as f:
            f.write(key)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(config.RPM_DIR, "loco.asc"))
        existing = {r["name"] for r in rpmrepo.repositories()}
        for name in config.RPM_DEFAULT_REPOSITORIES:
            if name not in existing:
                rpmrepo.create_repository(name)
        for r in rpmrepo.repositories():
            rpmrepo.rebuild(r["name"])
    elif cmd == "flatpak-init":
        import subprocess
        from . import flatpak
        if not flatpak.repo_ready():
            os.makedirs(config.FLATPAK_REPO, exist_ok=True)
            subprocess.run(["ostree", "init", "--mode=archive-z2", f"--repo={config.FLATPAK_REPO}"], check=True)
        flatpak.update_repo()
    elif cmd == "user":
        user_command(argv[1:])
    elif cmd == "fleet":
        fleet_command(argv[1:])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    with app.app_context():
        main(sys.argv[1:])
