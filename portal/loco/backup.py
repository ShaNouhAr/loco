"""Restauration des archives Linux loco et local-repo vers les chemins du serveur courant.
Lancé comme script par loco-backup (hors du paquet loco) : traduction autonome, dans la langue du serveur."""
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile


def _lang():
    value = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    if not value:
        for path in ("/etc/default/locale", "/etc/locale.conf"):
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        for key in ("LC_ALL=", "LC_MESSAGES=", "LANG="):
                            if line.strip().startswith(key):
                                value = line.strip()[len(key):].strip().strip('"')
                                break
                        if value:
                            break
            except OSError:
                pass
            if value:
                break
    value = value.lower()
    return "fr" if value.startswith("fr") else "es" if value.startswith("es") else "en"


LANG = _lang()


def t(fr, en, es):
    return fr if LANG == "fr" else es if LANG == "es" else en


def _build_plan(tar, data, etc, state):
    """Valide l'archive (membres, chemins, type) et associe chaque fichier à sa destination. Lève ValueError si
    l'archive n'est pas une sauvegarde loco/local-repo sûre. N'écrit rien : sert aussi à la vérification seule."""
    members = tar.getmembers()
    names = {m.name for m in members}
    configs = ['etc/loco/loco.conf', 'etc/local-repo/local-repo.conf']
    config = next((name for name in configs if name in names), None)
    if 'portal.db' not in names or config is None:
        raise ValueError(t('Sauvegarde Linux loco ou local-repo attendue.', 'A loco or local-repo Linux backup was expected.', 'Se espera una copia de seguridad Linux de loco o local-repo.'))
    with tar.extractfile(config) as source:
        match = re.search(r'^DATA=([/A-Za-z0-9._-]+)$', source.read().decode(), re.M)
    if not match:
        raise ValueError(t('Chemin des données absent de la sauvegarde.', 'Data path missing from the backup.', 'Falta la ruta de los datos en la copia de seguridad.'))
    old_data = match.group(1).lstrip('/')
    roots = [('etc/loco', Path(etc)), ('etc/local-repo', Path(etc)),
             ('var/lib/loco', Path(state)), ('var/lib/local-repo', Path(state))]
    roots.extend((old_data + '/' + name, Path(data) / name) for name in ('apt', 'win', 'flatpak', 'rpm'))
    plan = []
    for member in members:
        name = member.name.rstrip('/')
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
                or not (member.isdir() or member.isfile())):
            raise ValueError(t('Chemin ou type de fichier invalide dans la sauvegarde.', 'Invalid path or file type in the backup.', 'Ruta o tipo de archivo no válido en la copia de seguridad.'))
        if name in configs or any(name.startswith(p + '/install-state') for p, _ in roots[:2]):
            continue
        target = Path(state) / 'portal.db' if name == 'portal.db' else None
        for prefix, destination in roots:
            if name == prefix or name.startswith(prefix + '/'):
                target = destination / name[len(prefix):].lstrip('/')
                break
        if target is None:
            raise ValueError(t('Fichier étranger aux données de loco dans la sauvegarde.', 'File unrelated to loco data in the backup.', 'Archivo ajeno a los datos de loco en la copia de seguridad.'))
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError(t('Destination contenant un lien symbolique.', 'Destination contains a symbolic link.', 'Destino que contiene un enlace simbólico.'))
        plan.append((member, target))
    return plan


def verify(archive, data='/srv/loco', etc='/etc/loco', state='/var/lib/loco'):
    """Vrai si l'archive est une sauvegarde loco/local-repo valide et sûre ; lève ValueError (message traduit) sinon."""
    try:
        with tarfile.open(archive) as tar:
            _build_plan(tar, data, etc, state)
    except ValueError:
        raise
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ValueError(t("Archive illisible : ce n'est pas un .tar.gz valide.", 'Unreadable archive: not a valid .tar.gz.', 'Archivo ilegible: no es un .tar.gz válido.')) from error
    return True


def restore_linux(archive, data, etc='/etc/loco', state='/var/lib/loco'):
    with tarfile.open(archive) as tar:
        plan = _build_plan(tar, data, etc, state)
        # Tous les chemins sont vérifiés avant la première écriture.
        for member, target in plan:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.restore')
                with tar.extractfile(member) as source, open(temporary, 'wb') as output:
                    shutil.copyfileobj(source, output)
                os.chmod(temporary, member.mode & 0o777)
                os.replace(temporary, target)


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--verify':
        verify(sys.argv[2])
    else:
        restore_linux(sys.argv[1], sys.argv[2])
