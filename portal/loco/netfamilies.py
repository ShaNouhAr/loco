"""Familles d'équipements réseau : ce que loco envoie à chacune et comment il lit ses réponses.

Pour chaque famille : l'invite de commande, la pagination à couper, l'élévation des droits, les commandes du relevé
(modèle, numéro de série, version, ports, processeur, mémoire, température) et leur lecture, la commande qui rend la
configuration et les lignes qui changent sans que la configuration change (horodatages), puis la façon d'installer un
firmware et de redémarrer. Une commande que l'équipement ne connaît pas ne fait pas échouer le relevé : la case reste
vide.

Stormshield SNS n'a pas de ligne de commande de ce genre : son relevé, sa sauvegarde et son firmware passent par l'API
HTTPS de l'équipement (module sns) ; son SSH, s'il est activé, ne sert qu'au terminal.
"""
import re
from dataclasses import dataclass, field

from .i18n import L_

DAY = 86400
UNITS = {"year": 365 * DAY, "week": 7 * DAY, "day": DAY, "hour": 3600, "minute": 60, "second": 1}


def _cisco_prompt(base):
    return re.compile(r"(?:^|\n)(?P<base>" + re.escape(base) + r")(?P<mark>(?:\([^)\n]{1,40}\))?[>#]) ?$")


def _exos_prompt(base):
    return re.compile(r"(?:^|\n)\*?\s?(?:\([^)\n]{0,40}\)\s?)?(?P<base>" + re.escape(base) + r")\.\d+\s?(?P<mark>[>#]) ?$")


CISCO_LEARN = re.compile(r"(?:^|\n)(?P<base>[A-Za-z0-9][A-Za-z0-9._:/@-]{0,79})(?P<mark>(?:\([^)\n]{1,40}\))?[>#]) ?$")
EXOS_LEARN = re.compile(r"(?:^|\n)\*?\s?(?:\([^)\n]{0,40}\)\s?)?(?P<base>[A-Za-z0-9][^\n.#>]{0,79}?)\.\d+\s?(?P<mark>[>#]) ?$")
CISCO_ERROR = re.compile(r"^\s*%(?!\s*$)|^\s*\^\s*$", re.M)
EXOS_ERROR = re.compile(r"^\s*%%? |^\s*Error: ", re.M)


@dataclass
class Family:
    key: str
    label: str
    vendor: str
    kind: str                       # switch | firewall
    learn: re.Pattern = CISCO_LEARN
    prompt: object = _cisco_prompt
    prepare: tuple = ()
    elevate: str = ""
    logout: tuple = ("exit",)
    error: re.Pattern = CISCO_ERROR
    inventory: tuple = ()
    config: str = "show running-config"
    api: bool = False               # relevé et sauvegarde par l'API (Stormshield)
    firmware: str = ""              # façon d'installer un firmware : "" = à la main sur l'équipement
    firmware_ext: tuple = ()
    reboot: tuple = ()              # (commande, réponses) du redémarrage
    hints: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- lecture commune

def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def uptime_seconds(text):
    """« 12 weeks, 3 days, 4 hours, 22 minutes » / « 52 days 2 hours 34 minutes » / « 52 day(s), 02:34:07 » /
    « 12,03:27:44 » (jours,heures:minutes:secondes) → secondes."""
    text = (text or "").strip().lower()
    total, found = 0, False
    for n, unit in re.findall(r"(\d+)\s*(year|week|day|hour|minute|second)", text):
        total += int(n) * UNITS[unit]
        found = True
    m = re.search(r"(?:(\d+)\s*,\s*)?(\d{1,2}):(\d{2}):(\d{2})\b", text)
    if m:
        if m.group(1) and not re.search(r"\d+\s*day", text):
            total += int(m.group(1)) * DAY
        total += int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
        found = True
    return total if found else None


def _first(pattern, text, flags=re.M):
    m = re.search(pattern, text or "", flags)
    return m.group(1).strip() if m else ""


def _clip(value, limit=120):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value or "").strip()[:limit]


# --------------------------------------------------------------------------- Cisco IOS / IOS-XE

def parse_ios(out):
    """Relevé d'un Cisco IOS ou IOS-XE : `out` = {commande: sortie}."""
    ver = out.get("show version", "")
    info = {"os": "IOS XE" if re.search(r"IOS[ -]XE", ver) else "IOS"}
    info["version"] = (_first(r"Cisco IOS[ -]XE Software,.*?Version\s+([^\s,]+)", ver)
                       or _first(r"Cisco IOS Software.*?Version\s+([^\s,]+)", ver) or _first(r"\bVersion\s+([0-9][^\s,]*)", ver))
    m = re.search(r"^(\S+) uptime is (.+)$", ver, re.M)
    if m:
        info["hostname"], info["uptime"] = m.group(1), uptime_seconds(m.group(2))
    info["image"] = _first(r'System image file is "([^"]+)"', ver)
    info["sw_image"] = _first(r"Software \(([A-Za-z0-9_-]+)\)", ver)
    info["model"] = _first(r"^Model [Nn]umber\s*:\s*(\S+)", ver) or _first(r"^cisco (\S+) .*?processor", ver, re.M | re.I)
    info["serial"] = _first(r"^System [Ss]erial [Nn]umber\s*:\s*(\S+)", ver) or _first(r"Processor board ID (\S+)", ver)
    info["install_mode"] = bool(re.search(r"^\*?\s*\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+INSTALL\s*$", ver, re.M)
                                or info["image"].endswith("packages.conf"))
    inv = out.get("show inventory", "")
    if not info["model"] or not info["serial"]:
        m = re.search(r"PID:\s*([^\s,]+)\s*,.*?SN:\s*([^\s,]+)", inv)
        if m:
            info["model"] = info["model"] or m.group(1)
            info["serial"] = info["serial"] or m.group(2)
    # Ports : « show interfaces status » d'un switch, sinon « show ip interface brief » d'un routeur.
    ports, up = 0, 0
    for line in out.get("show interfaces status", "").splitlines():
        m = re.match(r"^((?:Gi|Fa|Te|Tw|Fo|Hu|Twe|Fiv|Eth|Et|Ap|Mg)\S*\d)\s", line)
        if not m or line.startswith(("Po", "Vl")):
            continue
        status = re.search(r"\s(connected|notconnect|disabled|err-disabled|inactive|monitoring|sfpAbsent|xcvrAbsent|suspended|down|up)\s", line + " ")
        if status:
            ports += 1
            up += status.group(1) in ("connected", "monitoring", "up")
    if not ports:
        for line in out.get("show ip interface brief", "").splitlines():
            m = re.match(r"^((?:Gigabit|Fast|TenGigabit|TwentyFive|Forty|Hundred)?Ethernet\S*)\s+\S+\s+\S+\s+\S+\s+(.*?)\s+(up|down)\s*$", line)
            if m:
                ports += 1
                up += m.group(3) == "up"
    if ports:
        info["ports"], info["ports_up"] = ports, up
    cpu = _first(r"one minute:\s*(\d+)%", out.get("show processes cpu | include CPU utilization", ""))
    if cpu:
        info["cpu"] = int(cpu)
    m = re.search(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)", out.get("show processes memory | include Processor Pool", ""))
    if m and int(m.group(1)):
        info["mem"] = round(100 * int(m.group(2)) / int(m.group(1)), 1)
    env = out.get("show environment all", "")
    temp = _first(r"(?:Temperature Value|Inlet Temperature Value|Temperature)\s*:\s*(\d+)\s*Degree", env, re.I | re.M)
    if temp:
        info["temperature"] = int(temp)
    m = re.search(r"(\d+) bytes total \((\d+) bytes free\)", out.get("dir flash:", ""))
    if m:
        info["flash_total"], info["flash_free"] = int(m.group(1)), int(m.group(2))
    return info


IOS_VOLATILE = re.compile(r"^(?:! Last configuration change at .*|! NVRAM config last updated at .*|! No configuration change since last restart.*|"
                          r"ntp clock-period \d+|Building configuration\.\.\.|Current configuration ?: ?\d+ bytes)\s*$", re.M)


# --------------------------------------------------------------------------- Cisco Small Business (CBS, SG)

def parse_sb(out):
    ver, system = out.get("show version", ""), out.get("show system", "")
    info = {"os": "Cisco Business"}
    info["version"] = (_first(r"Active-image:.*\n\s*Version:\s*(\S+)", ver) or _first(r"^\s*SW version\s+(\S+)", ver)
                       or _first(r"^\s*Version:\s*(\S+)", ver))
    info["image"] = _first(r"Active-image:\s*(\S+)", ver)
    info["inactive"] = _first(r"Inactive-image:.*\n\s*Version:\s*(\S+)", ver)
    info["hostname"] = _first(r"^System Name:\s*(\S+)", system)
    up = _first(r"^System Up ?Time[^:]*:\s*(.+)$", system)
    info["uptime"] = uptime_seconds(up) if up else None
    descr = _first(r"^System Description:\s*(.+)$", system)
    inv = out.get("show inventory", "")
    m = re.search(r"PID:\s*(\S+)\s+VID:\s*\S*\s+SN:\s*(\S+)", inv)
    info["model"] = m.group(1) if m else (descr.split()[0] if descr else "")
    info["serial"] = m.group(2) if m else ""
    ports, up_n = 0, 0
    for line in out.get("show interfaces status", "").splitlines():
        m = re.match(r"^((?:gi|fa|te|xg|mg)\d\S*)\s", line, re.I)
        if m:
            ports += 1
            up_n += bool(re.search(r"\sUp\s", line + " "))
    if ports:
        info["ports"], info["ports_up"] = ports, up_n
    cpu = _first(r"one minute:\s*(\d+)%", out.get("show cpu utilization", ""))
    if cpu:
        info["cpu"] = int(cpu)
    temp = _first(r"(?:Temperature|Temp)[^\n:]*:\s*(\d+)\s*(?:C\b|°C|Celsius)", out.get("show environment", ""), re.I | re.M)
    if temp:
        info["temperature"] = int(temp)
    return info



# --------------------------------------------------------------------------- Extreme EXOS / Switch Engine

def parse_exos(out):
    ver, sw = out.get("show version", ""), out.get("show switch", "")
    info = {"os": "Switch Engine" if re.search(r"Switch Engine", ver) else "ExtremeXOS"}
    info["version"] = _first(r"^Image\s*:\s*(?:ExtremeXOS|Extreme Networks Switch Engine|Switch Engine)? ?version\s+(\S+)", ver, re.M | re.I) or _first(r"IMG:\s*(\S+)", ver)
    m = re.search(r"^Switch\s*:\s*(\S+)\s+(\S+)", ver, re.M)
    if m:
        info["part"], info["serial"] = m.group(1), m.group(2)
    info["hostname"] = _first(r"^SysName:\s*(\S+)", sw)
    info["model"] = _first(r"^System Type:\s*(\S+)", sw)
    up = _first(r"^System UpTime:\s*(.+)$", sw)
    info["uptime"] = uptime_seconds(up) if up else None
    info["image_selected"] = _first(r"^Image Selected:\s*(\S+)", sw)
    info["image_booted"] = _first(r"^Image Booted:\s*(\S+)", sw)
    info["primary"] = _first(r"^Primary ver:\s*(\S+)", sw)
    info["secondary"] = _first(r"^Secondary ver:\s*(\S+)", sw)
    ports, up_n = 0, 0
    for line in out.get("show ports no-refresh", "").splitlines():
        m = re.match(r"^\s*(\d+(?::\d+)?)\s+(.*)$", line)
        if not m:
            continue
        tokens = m.group(2).split()
        for i, tok in enumerate(tokens[:-1]):
            if tok in ("E", "D") and tokens[i + 1] in ("A", "R", "NP", "L", "D", "d"):
                ports += 1
                up_n += tokens[i + 1] == "A"
                break
    if ports:
        info["ports"], info["ports_up"] = ports, up_n
    m = re.search(r"^\s*System\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", out.get("show cpu-monitoring", ""), re.M)
    if m:
        info["cpu"] = round(float(m.group(4)))
    mem = out.get("show memory", "")
    total, free = _num(_first(r"Total DRAM \(KB\):\s*(\d+)", mem)), _num(_first(r"Free\s*\(KB\):\s*(\d+)", mem))
    if total and free is not None:
        info["mem"] = round(100 * (total - free) / total, 1)
    temp = _first(r"^Switch\s*:\s*\S+\s+(\d+(?:\.\d+)?)", out.get("show temperature", ""))
    if temp:
        info["temperature"] = round(float(temp))
    return info



# --------------------------------------------------------------------------- Extreme VOSS / Fabric Engine

def parse_voss(out):
    sysinfo = out.get("show sys-info", "")
    info = {"os": "Fabric Engine" if re.search(r"Fabric ?Engine", sysinfo + out.get("show software", ""), re.I) else "VOSS"}
    descr = _first(r"^\s*SysDescr\s*:\s*(.+)$", sysinfo)
    info["version"] = _first(r"\((\d+\.\d+\.\d+\.\d+[^)]*)\)", descr)
    info["hostname"] = _first(r"^\s*SysName\s*:\s*(\S+)", sysinfo)
    up = _first(r"^\s*SysUpTime\s*:\s*(.+)$", sysinfo)
    info["uptime"] = uptime_seconds(up) if up else None
    info["model"] = _first(r"^\s*ModelName\s*:\s*(\S+)", sysinfo) or _first(r"^\s*Chassis\s*:\s*(\S+)", sysinfo) or (descr.split()[0] if descr else "")
    info["serial"] = _first(r"^\s*Serial#\s*:\s*(\S+)", sysinfo)
    temp = re.search(r"Chassis Temperature\s*\n\s*-+\s*\n\s*(\d+)", sysinfo)
    if temp:
        info["temperature"] = int(temp.group(1))
    soft = out.get("show software", "")
    info["primary"] = _first(r"^(\S+)\s+\(Primary Release\)", soft)
    info["backup"] = _first(r"^(\S+)\s+\(Backup Release\)", soft)
    info["releases"] = re.findall(r"^((?:VOSS|FabricEngine|VSP|FE)\S*?\d+\.\d+\.\d+\.\d+\S*)", soft, re.M)[:8]
    info["auto_commit"] = _first(r"^\s*Auto Commit\s*:\s*(\S+)", soft)
    if not info["version"] and info["primary"]:
        info["version"] = _first(r"(\d+\.\d+\.\d+\.\d+)", info["primary"])
    ports, up_n = 0, 0
    for line in out.get("show interfaces gigabitEthernet", "").splitlines():
        m = re.match(r"^\s*\d+/\d+(?:/\d+)?\s+\d+\s.*\s(up|down)\s+(up|down)\s*$", line)
        if m:
            ports += 1
            up_n += m.group(2) == "up"
    if ports:
        info["ports"], info["ports_up"] = ports, up_n
    cpu = _first(r"Current utilization:\s*(\d+)", out.get("show khi performance cpu", ""))
    if cpu:
        info["cpu"] = int(cpu)
    mem = _first(r"Current utilization:\s*(\d+)", out.get("show khi performance memory", ""))
    if mem:
        info["mem"] = int(mem)
    return info


VOSS_VOLATILE = re.compile(r"^# (?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \w{3} +\d+ \d\d:\d\d:\d\d \d{4}.*$|^Preparing to Display Configuration\.\.\.$", re.M)


# --------------------------------------------------------------------------- familles

FAMILIES = {
    "cisco-ios": Family(
        "cisco-ios", "Cisco IOS / IOS-XE", "Cisco", "switch",
        prepare=("terminal length 0", "terminal width 0"), elevate="enable",
        inventory=("show version", "show inventory", "show interfaces status", "show ip interface brief",
                   "show processes cpu | include CPU utilization", "show processes memory | include Processor Pool",
                   "show environment all", "dir flash:"),
        firmware="ios", firmware_ext=(".bin", ".pkg"),
        reboot=("reload", ((r"Save\? \[yes/no\]:?\s*$", "no\r"), (r"\[confirm\]\s*$", "\r"))),
    ),
    "cisco-sb": Family(
        "cisco-sb", "Cisco Small Business (CBS, SG)", "Cisco", "switch",
        prepare=("terminal datadump", "terminal width 0"),
        inventory=("show version", "show system", "show inventory", "show interfaces status", "show cpu utilization", "show environment"),
        firmware="", firmware_ext=(".bin", ".ros"),
        reboot=("reload", ((r"\(Y/N\)\s*\[N\]\s*\??\s*$", "Y"), (r"\(Y/N\)\s*\[N\]\s*\??\s*$", "Y"))),
    ),
    "exos": Family(
        "exos", "Extreme EXOS / Switch Engine", "Extreme Networks", "switch",
        learn=EXOS_LEARN, prompt=_exos_prompt, prepare=("disable clipaging",), error=EXOS_ERROR,
        inventory=("show version", "show switch", "show ports no-refresh", "show cpu-monitoring", "show memory", "show temperature"),
        config="show configuration", firmware="exos", firmware_ext=(".xos",),
        reboot=("reboot", ((r"save configuration changes.*\(y - save and reboot, n - reboot without save[^)]*\)\s*:?\s*$", "n"),
                           (r"reboot the switch\?\s*\(y/N\)\s*$", "y"))),
    ),
    "voss": Family(
        "voss", "Extreme VOSS / Fabric Engine", "Extreme Networks", "switch",
        prepare=("terminal more disable",), elevate="enable", error=EXOS_ERROR,
        inventory=("show sys-info", "show software", "show interfaces gigabitEthernet", "show khi performance cpu",
                   "show khi performance memory"),
        firmware="voss", firmware_ext=(".tgz",),
        reboot=("reset -y", ()),
    ),
    "stormshield": Family(
        "stormshield", "Stormshield SNS", "Stormshield", "firewall", api=True, logout=(),
        firmware="sns", firmware_ext=(".maj",),
    ),
}
PARSERS = {"cisco-ios": parse_ios, "cisco-sb": parse_sb, "exos": parse_exos, "voss": parse_voss}
VOLATILE = {"cisco-ios": IOS_VOLATILE, "voss": VOSS_VOLATILE}
KINDS = {"switch": L_("Switch"), "firewall": L_("Pare-feu")}


def family(key):
    return FAMILIES.get(key)


def parse(key, out):
    """Relevé lisible d'une famille, chaque valeur bornée (texte venu de l'équipement)."""
    info = PARSERS[key](out)
    for name, value in list(info.items()):
        if isinstance(value, str):
            info[name] = _clip(value, 160)
        elif isinstance(value, list):
            info[name] = [_clip(v, 80) for v in value if isinstance(v, str)][:8]
    return info


def normalize_config(key, text):
    """Configuration sans ce qui change tout seul (horodatages, en-têtes) : deux relevés sans modification sont égaux."""
    pattern = VOLATILE.get(key)
    text = text.replace("\r\n", "\n").strip("\n")
    if pattern is not None:
        text = pattern.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    out, blank = [], False
    for line in lines:                       # lignes vides en série ramenées à une
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def version_key(version):
    """Ordre des versions, quelle que soit l'écriture : 15.2(7)E9, 17.09.04a, 33.6.1.14, 8.10.1.0, 4.3.29."""
    parts = re.findall(r"\d+|[A-Za-z]+", version or "")
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in parts)


FIRMWARE_VERSION = (
    re.compile(r"(?:^|[^\d.])(\d+\.\d+\.\d+\.\d+)(?![\d])"),             # EXOS, Cisco Business ; VOSS après son modèle
    re.compile(r"\.(\d{2}\.\d{2}\.\d{2}[a-z]?)\.SPA", re.I),              # IOS-XE : cat9k_iosxe.17.09.04a.SPA.bin
    re.compile(r"-mz\.(\d{3})-(\d+)\.([A-Z]+\d*[a-z]?)", re.I),          # IOS : c2960x-universalk9-mz.152-7.E9.bin
    re.compile(r"(\d+\.\d+\.\d+)"),                                       # Stormshield : fwupd-4.8.2-SNS-amd64-M.maj
)


def firmware_version(filename):
    """Version lue dans le nom d'un fichier de firmware, au mieux (l'administrateur peut la corriger)."""
    name = filename or ""
    m = FIRMWARE_VERSION[2].search(name)
    if m:
        major = m.group(1)
        return f"{major[:2]}.{major[2:]}({m.group(2)}){m.group(3)}"
    m = FIRMWARE_VERSION[1].search(name)
    if m:
        return m.group(1)
    m = re.search(r"[A-Za-z]\d{3,4}\.(\d+\.\d+\.\d+\.\d+)", name)        # VOSS8400.8.10.1.0.tgz : le modèle, puis la version
    if m:
        return m.group(1)
    for pattern in (FIRMWARE_VERSION[0], FIRMWARE_VERSION[3]):
        m = pattern.search(name)
        if m:
            return m.group(1)
    return ""


def stem(name):
    """Plateforme d'une image d'après son nom, version ôtée : c2960x-universalk9-mz.152-7.E9.bin → c2960x-universalk9,
    cat9k_iosxe.17.12.04.SPA.bin → cat9k-iosxe, image_cbs_ros_3.5.0.22_release….bin → image-cbs-ros."""
    base = re.split(r"[/:]", (name or "").lower())[-1]
    tokens = []
    for token in re.split(r"[._-]+", base):
        if not token or token[0].isdigit():
            break
        tokens.append(token)
    while tokens and tokens[-1] in ("mz", "m", "spa", "bin"):
        tokens.pop()
    return "-".join(tokens)


def platform(key, info):
    """Plateforme de l'image qui tourne (Cisco) : celle d'une image installable doit être la même. "" si inconnue."""
    info = info or {}
    if key == "cisco-ios":
        image = info.get("image") or ""
        if image and not image.endswith(".conf"):
            return stem(image)
        return stem(info.get("sw_image") or "")
    if key == "cisco-sb":
        return stem(info.get("image") or "")
    return ""


def same_version(a, b):
    """17.09.04a et 17.9.4a désignent la même version (IOS-XE écrit l'une ou l'autre selon la commande)."""
    return version_key(a) == version_key(b)
