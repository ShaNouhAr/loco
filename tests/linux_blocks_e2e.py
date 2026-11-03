#!/usr/bin/env python3
"""Conteneur jetable (root) : vrais paquets, vrai apt ou dnf, et le vrai code de l'agent pour les mises à jour bloquées.

Dépôt local où chaque paquet a une version 2 : loco-a (bloqué), loco-dep (exige loco-a 2), loco-libre, loco-meta (tire
un nouveau paquet loco-nouveau-2, comme le méta-paquet d'un noyau). Usage : linux_blocks_e2e.py <chemin de loco-client.py>"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile


def sh(*args, check=True):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if check and r.returncode:
        sys.exit(f"ÉCHEC de {' '.join(args)} :\n{r.stdout}")
    return r.stdout


def expect(label, got, wanted):
    if got != wanted:
        sys.exit(f"ÉCHEC : {label} : {got!r} au lieu de {wanted!r}")
    print("ok :", label)


spec = importlib.util.spec_from_file_location("agent", sys.argv[1])
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.log = lambda message: print("journal de l'agent :", message)
work = tempfile.mkdtemp()
os.chmod(work, 0o755)                   # apt lit le dépôt sous son propre compte
repo = os.path.join(work, "repo")
os.makedirs(repo)
debian = bool(shutil.which("apt-get"))
PACKAGES = {"loco-a": {}, "loco-dep": {"2": ["loco-a (>= 2)"]}, "loco-libre": {}, "loco-meta": {"2": ["loco-nouveau-2"]}}


def build_deb(name, version, depends=()):
    root = os.path.join(work, f"{name}-{version}")
    os.makedirs(os.path.join(root, "DEBIAN"))
    with open(os.path.join(root, "DEBIAN", "control"), "w") as f:
        f.write(f"Package: {name}\nVersion: {version}\nArchitecture: all\nMaintainer: ci <ci@example.invalid>\nDescription: test\n"
                + (f"Depends: {', '.join(depends)}\n" if depends else ""))
    sh("dpkg-deb", "--root-owner-group", "-b", root, os.path.join(repo, f"{name}_{version}_all.deb"))


def build_rpm(name, version, depends=()):
    spec_file = os.path.join(work, f"{name}-{version}.spec")
    with open(spec_file, "w") as f:
        f.write(f"Name: {name}\nVersion: {version}\nRelease: 1\nSummary: test\nLicense: MIT\nBuildArch: noarch\n"
                + "".join(f"Requires: {d.replace(chr(40), str()).replace(chr(41), str())}\n" for d in depends) + "%description\ntest\n%files\n")
    sh("rpmbuild", "-bb", f"--define=_rpmdir {repo}", spec_file)


build = build_deb if debian else build_rpm


def publish():
    if debian:
        with open(os.path.join(repo, "Packages"), "w") as f:
            f.write(subprocess.run(["dpkg-scanpackages", "--multiversion", "."], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, check=True).stdout)
        with open("/etc/apt/sources.list.d/loco-test.list", "w") as f:
            f.write(f"deb [trusted=yes] file:{repo} ./\n")
        sh("apt-get", "update", "-q")
    else:
        sh("createrepo_c", repo)
        with open("/etc/yum.repos.d/loco-test.repo", "w") as f:
            f.write(f"[loco-test]\nname=test\nbaseurl=file://{repo}\ngpgcheck=0\nenabled=1\nmetadata_expire=0\n")
        sh("dnf", "-q", "makecache")


def installed(name):
    if debian:
        out = sh("dpkg-query", "-W", "-f=${Version}", name, check=False)
        return out if "no packages" not in out.lower() else ""
    out = sh("rpm", "-q", "--qf", "%{VERSION}", name, check=False)
    return "" if "not installed" in out else out


def versions():
    return {name: installed(name) for name in ("loco-a", "loco-dep", "loco-libre", "loco-meta", "loco-nouveau-2")}


for name in PACKAGES:
    build(name, "1")
publish()
sh(*(["apt-get", "-y", "-q", "install"] if debian else ["dnf", "-y", "-q", "install"]), *PACKAGES)
expect("tout en version 1", versions(), {"loco-a": "1", "loco-dep": "1", "loco-libre": "1", "loco-meta": "1", "loco-nouveau-2": ""})
build("loco-nouveau-2", "1")
for name, extra in PACKAGES.items():
    build(name, "2", extra.get("2", ()))
publish()


if debian:
    state = {}
    agent.apply_blocks({"update_blocks": {"patterns": ["loco-a", "loco-nouveau-*"]}}, state)
    expect("retenu sur le poste", sh("apt-mark", "showhold").split(), ["loco-a"])
    sh("apt-mark", "hold", "loco-libre")                       # retenue posée à la main : loco n'y touche jamais
    inventory = agent.scan(state)
    names = sorted(u["name"] for u in inventory["updates"])
    print("inventaire :", names)
    expect("inventaire : les bloqués sont signalés, pas la retenue locale", [n for n in names if n in ("loco-a", "loco-nouveau-2", "loco-libre")], ["loco-a", "loco-nouveau-2"])
    expect("le nouveau paquet vu dans les dépôts est retenu aussi", sorted(state["holds"]), ["loco-a", "loco-nouveau-2"])
    code, out = agent.update({"blocks": ["loco-a", "loco-nouveau-*"]}, None, state)
    expect("mise à jour sans échec", code, 0)
    expect("rien de bloqué ni de dépendant n'est installé",
           versions(), {"loco-a": "1", "loco-dep": "1", "loco-libre": "1", "loco-meta": "1", "loco-nouveau-2": ""})
    sh("apt-get", "-y", "-q", "upgrade", "--with-new-pkgs")     # comme le ferait une mise à jour automatique du système
    expect("une mise à jour hors de loco ne passe pas non plus", versions()["loco-a"], "1")
    code, out = agent.update({"only": {"packages": ["loco-a"]}, "blocks": ["loco-a", "loco-nouveau-*"]}, None, state)
    expect("sélection d'un paquet bloqué : écartée", (code, versions()["loco-a"]), (0, "1"))
    sh("apt-mark", "unhold", "loco-libre")
    code, out = agent.update({"blocks": ["loco-a", "loco-nouveau-*"]}, None, state)
    expect("le reste s'installe", (code, versions()["loco-libre"]), (0, "2"))
    expect("et le bloqué reste", versions()["loco-a"], "1")
    sh("apt-mark", "hold", "loco-meta")
    agent.apply_blocks({"update_blocks": {"patterns": []}}, state)
    expect("blocages levés : seule la retenue manuelle reste", sh("apt-mark", "showhold").split(), ["loco-meta"])
    expect("mémoire de l'agent vide", state["holds"], [])
    sh("apt-mark", "unhold", "loco-meta")
    code, out = agent.update({"blocks": []}, None, state)
    expect("après levée, tout s'installe", (code, versions()), (0, {"loco-a": "2", "loco-dep": "2", "loco-libre": "2", "loco-meta": "2", "loco-nouveau-2": "1"}))
else:
    inventory = agent.scan({"blocks": ["loco-a"]})
    expect("inventaire : dnf voit le bloqué pour le portail", "loco-a" in [u["name"] for u in inventory["updates"]], True)
    code, out = agent.update({"only": {"packages": ["loco-a"]}, "blocks": ["loco-a"]})
    expect("sélection d'un paquet bloqué : écartée", (code, installed("loco-a")), (0, "1"))
    code, out = agent.update({"blocks": ["loco-a"]})
    print(out[-600:])
    expect("mise à jour sans échec, y compris la dépendance impossible", code, 0)
    got = versions()
    expect("loco-a et ce qui l'exige restent en version 1", (got["loco-a"], got["loco-dep"]), ("1", "1"))
    expect("le reste s'installe", (got["loco-libre"], got["loco-meta"], got["loco-nouveau-2"]), ("2", "2", "1"))
    code, out = agent.update({"blocks": []})
    expect("après levée, tout s'installe", (code, installed("loco-a"), installed("loco-dep")), (0, "2", "2"))
print("=== réussi")
