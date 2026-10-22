#!/bin/bash
# Migration local-repo -> loco. Chargé par l'installateur, avant de lire ses réglages.
# Les déplacements gardent les UID, clés et données ; une copie de secours est créée avant l'arrêt.
stash_self_client() {
    # Un serveur peut lui-même être branché comme poste : APT doit rester joignable pendant l'arrêt du cache.
    [ -z "${LOCO_CLIENT_STASH:-}" ] || return 0
    LOCO_CLIENT_STASH=$(mktemp -d /var/tmp/loco-client-migration.XXXXXX)
    # shellcheck disable=SC2317,SC2329  # appelée indirectement par le trap EXIT.
    restore_self_client() {
        local file
        for file in "$LOCO_CLIENT_STASH"/*; do
            [ -f "$file" ] || continue
            case "${file##*/}" in
                01*) mv "$file" /etc/apt/apt.conf.d/ ;;
                *) mv "$file" /etc/apt/sources.list.d/ ;;
            esac
        done
        rmdir "$LOCO_CLIENT_STASH"
    }
    trap restore_self_client EXIT
    for path in /etc/apt/sources.list.d/{local-repo,loco}.{list,sources} /etc/apt/apt.conf.d/01{local-repo,loco}-proxy; do
        if [ -f "$path" ]; then mv "$path" "$LOCO_CLIENT_STASH/"; fi
    done
}

migrate_local_repo() {
    local old=/etc/local-repo legacy_data legacy_backup old_uid new_uid path unit
    [ -f "$old/local-repo.conf" ] || return 0
    [ ! -e /etc/loco/loco.conf ] || die 'Deux installations existent (local-repo et loco) : migration refusée sans écrasement.' 'Two installations exist (local-repo and loco): migration refused without overwrite.'
    for path in /etc/loco /var/lib/loco /opt/loco /usr/local/lib/loco; do
        [ ! -e "$path" ] || die "Migration : destination déjà présente ($path)." "Migration: destination already present ($path)."
    done
    old_uid=$(id -u localrepo 2>/dev/null || true)
    new_uid=$(id -u loco 2>/dev/null || true)
    [ -z "$new_uid" ] || [ "$new_uid" = "$old_uid" ] || die 'Le compte loco existe déjà avec un autre UID.' 'The loco account already exists with a different UID.'
    # Configuration de l'ancien installateur, réservée à root.
    legacy_data=$(bash -c '. /etc/local-repo/local-repo.conf; printf %s "$DATA"')
    legacy_backup=$(bash -c '. /etc/local-repo/local-repo.conf; printf %s "${BACKUP_DIR:-/var/backups/local-repo}"')
    if [ "$legacy_data" = /srv/local-repo ]; then [ ! -e /srv/loco ] || die '/srv/loco existe déjà.' '/srv/loco already exists.'; fi
    if [ "$legacy_backup" = /var/backups/local-repo ]; then [ ! -e /var/backups/loco ] || die '/var/backups/loco existe déjà.' '/var/backups/loco already exists.'; fi
    say 'Migration de local-repo vers loco (comptes, clés et données conservés)' 'Migrating local-repo to loco (accounts, keys and data preserved)'
    if [ -x /usr/local/sbin/local-repo-backup ]; then
        /usr/local/sbin/local-repo-backup create
    fi
    stash_self_client
    if [ "$CONTAINER" = 0 ]; then
        for unit in local-repo-portal local-repo-stats.timer local-repo-nginx.path local-repo-backup.timer local-repo-acng-maintenance.timer; do
            systemctl disable --now "$unit" >/dev/null 2>&1 || true
        done
        systemctl stop local-repo-stats.service local-repo-backup.service local-repo-nginx.service nginx apt-cacher-ng >/dev/null 2>&1 || true
    fi
    if [ -n "$old_uid" ]; then
        runuser -u localrepo -- gpgconf --homedir /var/lib/local-repo/gnupg --kill all >/dev/null 2>&1 || true
        if [ -z "$new_uid" ]; then usermod -l loco -d /var/lib/loco localrepo; fi
        if getent group localrepo >/dev/null && ! getent group loco >/dev/null; then groupmod -n loco localrepo; fi
    fi
    # État Rocky : retirer uniquement les règles enregistrées par l'ancien installateur.
    if [ -f "$old/install-state/rocky-selinux-contexts" ] && command -v semanage >/dev/null; then
        while IFS= read -r path; do semanage fcontext -d "$path"; done < "$old/install-state/rocky-selinux-contexts"
        : > "$old/install-state/rocky-selinux-contexts"
    fi
    if [ -f "$old/install-state/rocky-firewall-zones" ] && systemctl is-active -q firewalld; then
        while IFS= read -r path; do firewall-cmd --permanent --zone="$path" --remove-service=local-repo >/dev/null; done < "$old/install-state/rocky-firewall-zones"
        rm -f /etc/firewalld/services/local-repo.xml
        : > "$old/install-state/rocky-firewall-zones"
    fi
    for path in /etc/local-repo /var/lib/local-repo /opt/local-repo /usr/local/lib/local-repo; do
        if [ -e "$path" ]; then mv "$path" "${path/local-repo/loco}"; fi
    done
    mv /etc/loco/local-repo.conf /etc/loco/loco.conf
    if [ "$legacy_data" = /srv/local-repo ] && [ -d "$legacy_data" ]; then mv "$legacy_data" /srv/loco; fi
    if [ "$legacy_backup" = /var/backups/local-repo ] && [ -d "$legacy_backup" ]; then mv "$legacy_backup" /var/backups/loco; fi
    # Ces binaires embarquent leur préfixe de compilation ; les reconstruire au nouveau chemin.
    rm -f /usr/local/lib/loco/apt/version /usr/local/lib/loco/acng/version
    # Le venv Rocky reste au même UID ; corriger ses shebangs après déplacement.
    python3 - <<'PY'
from pathlib import Path
p=Path('/etc/loco/loco.conf')
s=p.read_text().replace('/usr/local/lib/local-repo/', '/usr/local/lib/loco/')
s=s.replace('DATA=/srv/local-repo\n', 'DATA=/srv/loco\n').replace('BACKUP_DIR=/var/backups/local-repo\n', 'BACKUP_DIR=/var/backups/loco\n')
p.write_text(s)
for p in Path('/usr/local/lib/loco/venv/bin').glob('*'):
    if p.is_file() and not p.is_symlink():
        b=p.read_bytes()
        if b.startswith(b'#!'):
            p.write_bytes(b.replace(b'/usr/local/lib/local-repo/', b'/usr/local/lib/loco/'))
PY
    rm -f /etc/systemd/system/local-repo-*.{service,timer,path} /etc/systemd/system/apt-cacher-ng.service.d/local-repo.conf \
          /etc/apt-cacher-ng/zz-local-repo.conf /usr/local/sbin/local-repo-{user,backup,apt} \
          /etc/nginx/sites-{available,enabled}/local-repo-*.conf /etc/nginx/modules-enabled/99-local-repo-stream.conf \
          /etc/nginx/snippets/local-repo-*.conf /etc/nginx/conf.d/local-repo.conf /etc/nginx/local-repo-stream.conf \
          /etc/logrotate.d/local-repo-acng
    for path in /etc/hosts.allow /etc/hosts.deny /etc/nginx/nginx.conf; do
        if [ -f "$path" ]; then sed -i '/# local-repo$/d' "$path"; fi
    done
    # Les archives restent lisibles sous leur nom historique.
    for path in /var/log/nginx/local-repo*; do
        if [ -e "$path" ]; then mv "$path" "${path/local-repo/loco}"; fi
    done
    if [ "$CONTAINER" = 0 ]; then systemctl daemon-reload; fi
    say 'Migration des fichiers terminée ; installation et vérification de loco' 'File migration complete; installing and verifying loco'
}
