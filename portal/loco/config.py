"""Configuration lue dans l'environnement (unité systemd ou supervisord, générés par install.sh)."""

import os
import re

VERSION = "2.105.0"

DATA = os.environ.get("LOCO_DATA", "/srv/loco")
STATE = os.environ.get("LOCO_STATE", "/var/lib/loco")
ETC = os.environ.get("LOCO_ETC", "/etc/loco")
HOST = os.environ.get("LOCO_HOST", "localhost")
ACNG_PORT = os.environ.get("LOCO_ACNG_PORT", "3142")
WU_PROXY_PORT = os.environ.get("LOCO_WU_PROXY_PORT", "3128")
# Proxy-cache Windows Update (Squid) réellement installé : posé par install.sh avec le cache WU, absent sous Windows.
WU_PROXY = os.environ.get("LOCO_WU_PROXY", "0") == "1"
# Image Docker : supervisord à la place de systemd (voir docker/).
CONTAINER = os.environ.get("LOCO_RUNTIME") == "container"
# Serveur Windows natif : service Windows, nginx et waitress (voir windows/).
WINDOWS = os.name == "nt" or os.environ.get("LOCO_RUNTIME") == "windows"
# Appliance Alpine (ISO) : OpenRC lance supervisord ; mises à jour et configuration depuis le portail.
ALPINE = os.environ.get("LOCO_RUNTIME") == "alpine"
APPLIANCE = ALPINE
# Serveur Linux installé par install.sh (systemd ou OpenRC) : l'agent root loco-agent met le système à jour depuis
# le portail. L'image Docker se met à jour par l'image, le serveur Windows par Windows Update.
SERVER_UPDATES = not CONTAINER and not WINDOWS
# Cache APT servi par nginx (pas d'apt-cacher-ng) : Windows et Alpine.
APT_NGINX = WINDOWS or ALPINE
GPG = os.environ.get("LOCO_GPG", "gpg")

# Adresse HTTPS (interface, et postes qui font confiance à l'autorité locale).
HTTPS_URL = os.environ.get("LOCO_ADMIN_URL", "https://localhost:8443").rstrip("/")
# Adresse HTTP des postes clients ; vide si le HTTP est désactivé (--no-http).
HTTP_URL = os.environ.get("LOCO_PUBLIC_URL", "").rstrip("/")

TLS_DIR = os.environ.get("LOCO_TLS_DIR", os.path.join(ETC, "tls"))
SECRETS_PATH = os.environ.get("LOCO_SECRETS", os.path.join(ETC, "portal.secret"))
WU_LOG = os.environ.get("LOCO_WU_LOG", "/var/log/nginx/loco-wu.log")
GNUPGHOME = os.environ.get("LOCO_GNUPGHOME", os.path.join(STATE, "gnupg"))
BACKUP_DIR = os.environ.get("LOCO_BACKUP_DIR", "/var/backups/loco")

APT_DIR = os.path.join(DATA, "apt")
POOL_DIR = os.path.join(APT_DIR, "pool")
APT_REPOSITORIES_DIR = os.path.join(APT_DIR, "repositories")
APT_SUITE_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
APT_DISTRIBUTIONS = {"debian": "Debian", "ubuntu": "Ubuntu"}
# Dépôts Debian/Ubuntu créés d'office (comme el8/el9/el10 côté Red Hat) : (distribution, nom de code, version affichée).
APT_DEFAULT_REPOSITORIES = (("debian", "bookworm", "12"), ("debian", "trixie", "13"),
                            ("ubuntu", "jammy", "22.04"), ("ubuntu", "noble", "24.04"))
WIN_DIR = os.path.join(DATA, "win")
TMP_DIR = os.path.join(DATA, ".tmp")
# Journal des versions, livré à la racine de l'installation (/opt/loco) à côté de portal/, consultable depuis le portail.
CHANGELOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "CHANGELOG.md")
DB_PATH = os.path.join(STATE, "portal.db")
STATS_PATH = os.path.join(STATE, "stats.json")
WU_DOMAINS_PATH = os.path.join(STATE, "wu-domains.txt")
NGINX_MAP_DIR = os.path.join(STATE, "nginx")
FLATPAK_ENABLED = os.environ.get("LOCO_FLATPAK", "1") == "1"
# Dépôt Flatpak local : ostree n'existe pas sous Windows ; le cache Flathub y fonctionne.
FLATPAK_LOCAL = FLATPAK_ENABLED and not WINDOWS
FLATPAK_REPO = os.path.join(DATA, "flatpak", "repo")
FLATHUB_URL = "https://dl.flathub.org/repo/"

# Famille Red Hat (RHEL, Rocky Linux, AlmaLinux…) : dépôts RPM locaux par version majeure (el8, el9…)
# et cache des miroirs officiels. RHEL lui-même exige un abonnement : ses dépôts ne passent pas par le cache.
RPM_ENABLED = os.environ.get("LOCO_RPM", "1") == "1"
RPM_DIR = os.path.join(DATA, "rpm")
RPM_REPOSITORY_RE = re.compile(r"^el([1-9][0-9]?)$")
RPM_DEFAULT_REPOSITORIES = ("el8", "el9", "el10")
RPM_UPSTREAMS = {
    "rocky": ("Rocky Linux", "https://dl.rockylinux.org/pub/rocky/"),
    "almalinux": ("AlmaLinux", "https://repo.almalinux.org/almalinux/"),
    "epel": ("EPEL", "https://dl.fedoraproject.org/pub/epel/"),
}

DEFAULT_WU_DOMAINS = ["windowsupdate.com", "dl.delivery.mp.microsoft.com"]

# Arguments silencieux proposés par défaut selon le type de fichier.
DEFAULT_ARGS = {"msi": "/qn /norestart", "msu": "/quiet /norestart", "exe": "", "ps1": "", "file": ""}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
FLATPAK_REF_RE = re.compile(r"^(app|runtime)/[A-Za-z0-9_][A-Za-z0-9_.-]{2,254}/[a-z0-9_]{1,16}/[A-Za-z0-9_.-]{1,64}$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
USER_AGENT = f"loco/{VERSION}"

# Comptes et sessions
MIN_PASSWORD = 12
SESSION_IDLE_HOURS = 8
LOGIN_WINDOW = 15 * 60          # fenêtre d'observation des échecs (s)
LOGIN_MAX_PER_USER = 5          # échecs avant blocage temporaire du compte
LOGIN_MAX_PER_IP = 20           # échecs avant blocage temporaire de l'adresse
