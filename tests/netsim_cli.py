#!/usr/bin/env python3
"""Ligne de commande simulée d'un switch, pour les tests de loco : python3 netsim_cli.py FAMILLE.

Ce que fait un vrai Cisco IOS, Cisco Small Business, Extreme EXOS ou VOSS au bout d'une session SSH : invite (avec le
compteur d'EXOS), écho de la saisie, pagination tant qu'elle n'est pas coupée, mode privilégié (enable) avec mot de
passe, identifiants demandés dans la session (Cisco Small Business), erreurs au format de chacun, questions de
confirmation (reload, reboot, install), et redémarrage simulé : la session se ferme, la durée d'allumage repart de zéro
et la version activée prend effet. Les sorties viennent de netsim_data.

Réglages par variables d'environnement : NETSIM_STATE (fichier d'état JSON), NETSIM_LEVEL=1 (compte non privilégié),
NETSIM_ENABLE (mot de passe enable), NETSIM_USER / NETSIM_PASSWORD (identifiants demandés par Cisco Small Business),
NETSIM_VARIANT (xe : Catalyst 9300 en mode INSTALL ; csr : routeur), NETSIM_FLASH (dossier des images copiées),
NETSIM_EXTRA (lignes ajoutées à la configuration), NETSIM_EXTRA_FILE (les mêmes, lues dans un fichier).
"""
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netsim_data as data  # noqa: E402

FAMILY = sys.argv[1] if len(sys.argv) > 1 else "cisco-ios"
VARIANT = os.environ.get("NETSIM_VARIANT", "")
STATE = os.environ.get("NETSIM_STATE", "")
FLASH = os.environ.get("NETSIM_FLASH", "")
BASE = {"cisco-ios": "15.2(7)E9", "cisco-sb": "3.5.0.22", "exos": "33.6.1.14", "voss": "8.10.1.0"}
if VARIANT == "xe":
    BASE["cisco-ios"] = "17.09.04a"
PAGERS = {"cisco-ios": " --More-- ", "cisco-sb": "More: <space>,  Quit: q or CTRL+Z, One line: <return> ",
          "exos": "Press <SPACE> to continue or <Q> to quit:", "voss": "----More (q=Quit, space/return=Continue)----"}
ERRORS = {"cisco-ios": "% Invalid input detected at '^' marker.", "cisco-sb": "% Unrecognized command",
          "exos": "%% Invalid input detected at '^' marker.", "voss": "% Invalid input detected at '^' marker."}
OUT = sys.stdout.buffer
IN = sys.stdin.buffer.raw if hasattr(sys.stdin.buffer, "raw") else sys.stdin.buffer


def load():
    state = {"boot": time.time() - 52 * 86400, "version": BASE[FAMILY], "pending": "", "boot_image": ""}
    if STATE and os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state.update(json.load(f))
    return state


def save(state):
    if STATE:
        with open(STATE + ".new", "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(STATE + ".new", STATE)


S = load()


def w(text):
    OUT.write(text.replace("\n", "\r\n").encode("utf-8"))
    OUT.flush()


def key():
    b = IN.read(1)
    if not b:
        sys.exit(0)
    return b.decode("latin-1")


def line(echo=True):
    buf, last = "", ""
    while True:
        c = key()
        if c == "\n" and last == "\r":
            last = ""
            continue
        last = c
        if c in "\r\n":
            OUT.write(b"\r\n")
            OUT.flush()
            return buf
        if c in "\x7f\b":
            if buf:
                buf = buf[:-1]
                if echo:
                    OUT.write(b"\b \b")
            continue
        if c == "\x03":
            return "\x03"
        buf += c
        if echo:
            OUT.write(c.encode("latin-1"))
            OUT.flush()


def uptime_text(seconds):
    d, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    return d, h, m, s


def dynamic(text):
    """Sorties à jour de l'état : version en service, durée d'allumage."""
    up = time.time() - S["boot"]
    d, h, m, s = uptime_text(up)
    text = text.replace(BASE[FAMILY], S["version"])
    if FAMILY == "cisco-ios":
        if VARIANT == "xe":
            text = text.replace("17.9.4a", S["version"])
        text = re.sub(r"uptime is .*", f"uptime is {d // 7} weeks, {d % 7} days, {h} hours, {m} minutes", text, count=1)
        if S.get("boot_image"):
            text = re.sub(r'System image file is "[^"]+"', f'System image file is "{S["boot_image"]}"', text)
    elif FAMILY == "exos":
        text = re.sub(r"System UpTime:.*", f"System UpTime:    {d} days {h} hours {m} minutes {s} seconds", text)
        if S.get("secondary"):
            text = re.sub(r"Secondary ver:.*", f"Secondary ver:    {S['secondary']}", text)
        if S.get("selected"):
            text = re.sub(r"Image Selected:.*", f"Image Selected:   {S['selected']}", text)
    elif FAMILY == "voss":
        text = re.sub(r"SysUpTime    : .*", f"SysUpTime    : {d} day(s), {h:02d}:{m:02d}:{s:02d}", text)
        if S.get("added"):
            text = text.replace("VOSS8.10.0.0.GA (Backup Release)", f"VOSS{S['added']}.GA\nVOSS8.10.0.0.GA (Backup Release)")
    elif FAMILY == "cisco-sb":
        text = re.sub(r"System Up Time \(days,hour:min:sec\):\s*.*", f"System Up Time (days,hour:min:sec):       {d},{h:02d}:{m:02d}:{s:02d}", text)
    return text


def outputs():
    out = dict(data.FAMILY_OUTPUTS[FAMILY])
    if FAMILY == "cisco-ios" and VARIANT == "xe":
        out["show version"] = data.XE_VERSION
        out.pop("show interfaces status", None)
    if FAMILY == "cisco-ios" and VARIANT == "csr":
        out["show version"] = data.CSR_VERSION
        out["show ip interface brief"] = data.CSR_BRIEF
        out.pop("show interfaces status", None)
    extra = os.environ.get("NETSIM_EXTRA", "")
    extra_file = os.environ.get("NETSIM_EXTRA_FILE", "")
    if not extra and extra_file and os.path.exists(extra_file):
        with open(extra_file, encoding="utf-8") as f:
            extra = f.read().strip("\n")
    if extra:                                   # configuration modifiée sur l'équipement
        key = "show configuration" if FAMILY == "exos" else "show running-config"
        out[key] = out[key].rstrip("\n").rsplit("\nend", 1)[0] + "\n" + extra + ("\nend\n" if out[key].rstrip().endswith("end") else "\n")
    if FAMILY == "cisco-ios" and FLASH:
        listing = "".join(f"    {i + 3}  -rwx {os.path.getsize(os.path.join(FLASH, n)):>11}  Nov 23 2026 10:00:00 +01:00  {n}\n"
                          for i, n in enumerate(sorted(os.listdir(FLASH))))
        out["dir flash:"] = data.IOS_DIR.replace("    3  -rwx        3096", listing + "    9  -rwx        3096")
    return out


class Cli:
    def __init__(self):
        self.paging, self.count, self.mode = True, 1, ""
        self.privileged = os.environ.get("NETSIM_LEVEL", "15") != "1" and FAMILY not in ("voss",)
        self.host = {"cisco-ios": "C9300-SIEGE" if VARIANT == "xe" else "RTR-WAN" if VARIANT == "csr" else "SW-CORE",
                     "cisco-sb": "SW-ETAGE1", "exos": "X440G2-BAT-A", "voss": "VSP-COEUR:1"}[FAMILY]

    def prompt(self):
        if FAMILY == "exos":
            text = f"* {self.host}.{self.count} # "
        else:
            text = self.host + (f"({self.mode})" if self.mode else "") + ("#" if self.privileged else ">")
        w(text)

    def page(self, text):
        lines = text.rstrip("\n").split("\n")
        if not self.paging:
            w("\n".join(lines) + "\n")
            return
        for i in range(0, len(lines), 20):
            w("\n".join(lines[i:i + 20]) + "\n")
            if i + 20 < len(lines):
                w(PAGERS[FAMILY])
                c = key()
                w("\r" + " " * len(PAGERS[FAMILY]) + "\r")
                if c in "qQ\x1a":
                    return

    def confirm(self, question):
        """Cisco IOS lit une ligne (Entrée confirme « [confirm] ») ; EXOS et Small Business, une seule touche."""
        w(question)
        if FAMILY in ("exos", "cisco-sb"):
            c = key()
            w(("Yes" if c.lower() == "y" else "No") + "\n")
            return c.lower() == "y"
        c = line()
        return c.strip().lower() in ("y", "yes", "") if "[confirm]" in question else c.strip().lower() in ("y", "yes")

    def reboot(self):
        S["boot"] = time.time()
        if S.get("pending"):
            S["version"], S["pending"] = S["pending"], ""
        save(S)
        w("\nConnection closed by foreign host.\n")
        sys.exit(0)

    def run(self, cmd):
        out = outputs()
        low = cmd.strip()
        if not low:
            return
        if FAMILY == "cisco-ios":
            if low == "enable":
                w("Password: ")
                if line(echo=False) == os.environ.get("NETSIM_ENABLE", "enable-secret"):
                    self.privileged = True
                else:
                    w("% Access denied\n")
                return
            if low.startswith("terminal length 0"):
                self.paging = False
                return
            if low.startswith("terminal width"):
                return
            if low == "configure terminal" and self.privileged:
                w("Enter configuration commands, one per line.  End with CNTL/Z.\n")
                self.mode = "config"
                return
            if self.mode == "config":
                if low == "end":
                    self.mode = ""
                elif low.startswith("boot system "):
                    S["boot_image"] = low.split()[-1]
                    save(S)
                elif low != "no boot system":
                    w(ERRORS[FAMILY] + "\n")
                return
            if low == "write memory":
                w("Building configuration...\n[OK]\n")
                if S.get("boot_image"):
                    m = re.search(r"\.(\d{3})-(\d+)\.([A-Z]+\d*)", S["boot_image"])
                    if m:
                        S["pending"] = f"{m.group(1)[:2]}.{m.group(1)[2:]}({m.group(2)}){m.group(3)}"
                        save(S)
                return
            m = re.match(r"verify /md5 flash:(\S+) (\S+)$", low)
            if m:
                path = os.path.join(FLASH or "/nonexistent", m.group(1))
                if not os.path.exists(path):
                    w(f"%Error opening flash:{m.group(1)} (No such file or directory)\n")
                    return
                digest = hashlib.md5(open(path, "rb").read()).hexdigest()
                w("......................Done!\n" + (f"Verified (flash:{m.group(1)}) = {digest}\n" if digest == m.group(2)
                                                      else f"%Error verifying flash:{m.group(1)}\nComputed signature  = {digest}\nSubmitted signature = {m.group(2)}\n"))
                return
            m = re.match(r"install add file flash:(\S+) activate commit prompt-level none$", low)
            if m:
                version = re.search(r"\.(\d{2}\.\d{2}\.\d{2}[a-z]?)\.SPA", m.group(1))
                w("install_add_activate_commit: START Mon Nov 23 10:52:01 UTC 2026\n--- Starting initial file syncing ---\n"
                  "Finished initial file syncing\n--- Starting Add ---\nSUCCESS: Finished Add\n--- Starting Activate ---\n"
                  "Install will reload the system now!\n")
                S["pending"] = version.group(1) if version else S["version"]
                self.reboot()
            if low == "reload":
                if self.confirm("Proceed with reload? [confirm]"):
                    self.reboot()
                return
        if FAMILY == "cisco-sb":
            if low == "terminal datadump":
                self.paging = False
                return
            if low.startswith("terminal width"):
                return
            if low == "reload":
                if self.confirm("This command will reset the whole system and disconnect your current session. Do you want to continue ? (Y/N)[N] "):
                    self.reboot()
                return
        if FAMILY == "exos":
            self.count += 1
            if low == "disable clipaging":
                self.paging = False
                return
            m = re.match(r"install image (\S+)$", low)
            if m:
                if self.confirm("Installing to secondary partition!\n\nInstalling to secondary...Do you want to continue? (y/N) "):
                    version = re.search(r"(\d+\.\d+\.\d+\.\d+)", m.group(1))
                    S["secondary"] = version.group(1) if version else "?"
                    S["selected"] = "secondary"
                    S["pending"] = S["secondary"]
                    save(S)
                    w("Installation complete.\n")
                return
            if low.startswith("use image"):
                return
            if low == "reboot":
                if self.confirm("Are you sure you want to reboot the switch? (y/N) "):
                    self.reboot()
                return
        if FAMILY == "voss":
            if low == "enable":
                self.privileged = True
                self.host = self.host
                return
            if low == "terminal more disable":
                self.paging = False
                return
            m = re.match(r"software add (\S+)$", low)
            if m:                                   # VOSS8400.8.10.2.0.tgz : le modèle, puis la version
                version = re.search(r"[A-Za-z]\d{3,4}\.(\d+\.\d+\.\d+\.\d+)", m.group(1)) or re.search(r"(\d+\.\d+\.\d+\.\d+)", m.group(1))
                S["added"] = version.group(1) if version else "?"
                save(S)
                w("Extracting files to /intflash/release/\nRelease added.\n")
                return
            m = re.match(r"software activate VOSS(\S+)\.GA$", low)
            if m:
                S["pending"] = m.group(1)
                save(S)
                w("Release activated, takes effect after reset.\n")
                return
            if low == "software commit":
                w("Software commit successful.\n")
                return
            if low == "reset -y":
                self.reboot()
        if low in ("exit", "logout", "quit"):
            sys.exit(0)
        if low in out and (self.privileged or "running" not in low and "configuration" not in low):
            self.page(dynamic(out[low]))
            return
        w("\n" + ERRORS[FAMILY] + "\n" if FAMILY == "exos" else ERRORS[FAMILY] + "\n")


def main():
    if sys.stdin.isatty():
        import tty
        tty.setraw(sys.stdin.fileno())
    cli = Cli()
    if FAMILY == "cisco-sb":
        for _attempt in range(3):
            w("\n\nUser Name:")
            user = line()
            w("Password:")
            password = line(echo=False)
            if user == os.environ.get("NETSIM_USER", "admin") and password == os.environ.get("NETSIM_PASSWORD", "secret"):
                break
            w("\n% Login invalid\n")
        else:
            sys.exit(1)
    elif FAMILY == "exos":
        w("\n\nExtremeXOS\nCopyright (C) 1996-2026 Extreme Networks. All rights reserved.\n"
          "==============================================================================\n\n")
    elif FAMILY == "voss":
        w("\n\n  Copyright (c) 2010-2026 Extreme Networks, Inc. All Rights Reserved.\n\n  VSP Operating System Software Build 8.10.1.0\n\n")
    else:
        w("\n")
    while True:
        cli.prompt()
        cmd = line()
        if cmd == "\x03":
            w("\n")
            continue
        cli.run(cmd)


if __name__ == "__main__":
    main()
