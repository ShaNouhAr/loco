"""Traductions du portail. Le français est la langue source : les textes du code servent de clés, l'anglais vient
de portal/i18n/en.json (texte français -> texte anglais). Langue de chaque personne : son choix (compte, ou cookie
avant connexion), sinon celle du navigateur, sinon le français.

  _("Texte")                       traduit (gabarits : {{ _("Texte") }}, avec %(nom)s pour les valeurs ;
                                   un « % » littéral s'écrit « %% », ici comme dans les gabarits)
  ngettext("1 poste", "%(num)s postes", n)   singulier / pluriel selon la langue (français : 0 et 1 au singulier)
  L_("Libellé")                    libellé d'une constante, traduit à chaque affichage dans la langue du moment
  N_("Libellé")                    marque seulement le texte (valeur enregistrée telle quelle, traduite à l'affichage)

tests/test_i18n.py vérifie que chaque texte marqué a sa traduction anglaise.
"""

import json
import os
import re

from flask import g, has_request_context, request
from markupsafe import escape

LANGS = {"fr": "Français", "en": "English"}
DEFAULT = "fr"
COOKIE = "loco_lang"
CATALOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n")
_catalogs = {}


def catalog(lang):
    if lang not in _catalogs:
        try:
            with open(os.path.join(CATALOG_DIR, f"{lang}.json"), encoding="utf-8") as f:
                _catalogs[lang] = json.load(f)
        except (OSError, ValueError):
            _catalogs[lang] = {}
    return _catalogs[lang]


def valid(lang):
    return lang if lang in LANGS else ""


def locale():
    """Langue de la requête en cours (fixée une fois par requête)."""
    if not has_request_context():
        return valid(os.environ.get("LOCO_LANG", "")) or DEFAULT
    if "lang" in g:
        return g.lang
    user = g.get("user")
    lang = (valid(user["lang"]) if user is not None and "lang" in user.keys() else "") \
        or valid(request.cookies.get(COOKIE, "")) \
        or request.accept_languages.best_match(list(LANGS)) or DEFAULT
    g.lang = lang
    return lang


def gettext(message):
    lang = locale()
    if lang == DEFAULT or not message:
        return message
    return catalog(lang).get(message) or message


def ngettext(singular, plural, n):
    lang = locale()
    if lang == DEFAULT:
        return singular if abs(n) <= 1 else plural
    entries = catalog(lang)
    return (entries.get(singular) or singular) if n == 1 else (entries.get(plural) or plural)


def _(message, **values):
    """Comme dans les gabarits : toujours mis en forme, un « % » littéral s'écrit « %% »."""
    return gettext(message) % values


def n_(singular, plural, n, **values):
    values.setdefault("num", n)
    return ngettext(singular, plural, n) % values


def N_(message):
    return message


class LazyText(str):
    """Libellé d'une constante. Sa valeur reste le texte français (base, comparaisons, tris) ; l'affichage, la mise
    en forme (%, f-string), les majuscules/minuscules et la concaténation donnent la langue de la requête."""

    def __str__(self):
        return gettext(str.__str__(self))

    def __html__(self):
        return escape(str(self))

    def __format__(self, spec):
        return format(str(self), spec)

    def __add__(self, other):
        return str(self) + other

    def __radd__(self, other):
        return other + str(self)

    def lower(self):
        return str(self).lower()

    def upper(self):
        return str(self).upper()

    def capitalize(self):
        return str(self).capitalize()


def L_(message):
    """Texte traduit plus tard, à l'affichage (libellés définis une fois pour toutes dans le code)."""
    return LazyText(message)


# ---------------------------------------------------------------- scripts servis aux postes
# Les scripts (shell, PowerShell) parlent la langue du poste qui les exécute : le serveur y écrit les deux versions
# de chaque message, et une petite fonction du script choisit (LANG sous Linux, langue de Windows).
_SLOT = re.compile(r"%\((\w+)\)s")


def _both(message, escape, values):
    out = []
    for text in (message, catalog("en").get(message) or message):
        parts, pos = [], 0
        for m in _SLOT.finditer(text):
            parts.append(escape(text[pos:m.start()].replace("%%", "%")))
            parts.append(values[m.group(1)])
            pos = m.end()
        parts.append(escape(text[pos:].replace("%%", "%")))
        out.append("".join(parts))
    return out


def sh_t(message, **values):
    """Message d'un script shell : « $(t "français" "english") », à mettre dans un echo "…" (la fonction t du
    script choisit la langue ; valeurs = expressions shell, ex. "$1")."""
    def escape(text):
        return text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    fr, en = _both(message, escape, values)
    return f'$(t "{fr}" "{en}")'


def ps_t(message, **values):
    """Message d'un script PowerShell : « (T "français" "english") » (valeurs = expressions PowerShell)."""
    def escape(text):
        return text.replace("`", "``").replace('"', '`"').replace("$", "`$")
    fr, en = _both(message, escape, values)
    return f'(T "{fr}" "{en}")'


def js_catalog():
    """Textes des scripts du navigateur, pour la langue de la requête (vide en français)."""
    lang = locale()
    if lang == DEFAULT:
        return {}
    entries = catalog(lang)
    return {k: entries[k] for k in js_messages() if k in entries}


_js_messages = None


def js_messages():
    """Textes marqués t("…") dans les scripts du portail (lus une fois)."""
    global _js_messages
    if _js_messages is None:
        static = os.path.join(os.path.dirname(CATALOG_DIR), "static")
        found = set()
        for name in os.listdir(static):
            if name.endswith(".js"):
                with open(os.path.join(static, name), encoding="utf-8") as f:
                    found.update(m.group(2) for m in re.finditer(r'\b(?:t|tr)\((["\'])((?:\\.|(?!\1).)*)\1', f.read()))
        _js_messages = found
    return _js_messages
