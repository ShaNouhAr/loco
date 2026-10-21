"""Restauration des archives Linux loco et local-repo vers les chemins du serveur courant."""
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
from .i18n import _


def restore_linux(archive, data, etc='/etc/loco', state='/var/lib/loco'):
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        names = {m.name for m in members}
        configs = ['etc/loco/loco.conf', 'etc/local-repo/local-repo.conf']
        config = next((name for name in configs if name in names), None)
        if 'portal.db' not in names or config is None:
            raise ValueError(_('Sauvegarde Linux loco ou local-repo attendue.'))
        with tar.extractfile(config) as source:
            match = re.search(r'^DATA=([/A-Za-z0-9._-]+)$', source.read().decode(), re.M)
        if not match:
            raise ValueError(_('Chemin des données absent de la sauvegarde.'))
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
                raise ValueError(_('Chemin ou type de fichier invalide dans la sauvegarde.'))
            if name in configs or any(name.startswith(p + '/install-state') for p, _ in roots[:2]):
                continue
            target = Path(state) / 'portal.db' if name == 'portal.db' else None
            for prefix, destination in roots:
                if name == prefix or name.startswith(prefix + '/'):
                    target = destination / name[len(prefix):].lstrip('/')
                    break
            if target is None:
                raise ValueError(_('Fichier étranger aux données de loco dans la sauvegarde.'))
            if any(p.is_symlink() for p in (target, *target.parents)):
                raise ValueError(_('Destination contenant un lien symbolique.'))
            plan.append((member, target))
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
    restore_linux(sys.argv[1], sys.argv[2])
