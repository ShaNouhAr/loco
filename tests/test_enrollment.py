"""Enrôlement : page par système, paquets .deb et .rpm de l'agent produits pour ce serveur."""

import gzip
import hashlib
import io
import os
import struct
import tarfile
import tempfile
import unittest

from test_fleet import Agent, FleetTest
from loco import agentpkg, config, debpkg, rpmpkg

SCRIPT = "#!/bin/sh\necho 'installation de l agent'\n"


def saved(data, suffix):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return f.name


def deb_members(data):
    """Membres de l'archive ar d'un .deb, relus sans dpkg."""
    members, pos = {}, 8
    assert data[:8] == b"!<arch>\n"
    while pos + 60 <= len(data):
        header = data[pos:pos + 60]
        size = int(header[48:58])
        members[header[:16].decode().strip()] = data[pos + 60:pos + 60 + size]
        pos += 60 + size + size % 2
    return members


def cpio_files(payload):
    """Fichiers d'une charge utile cpio « newc » : nom → (mode, contenu)."""
    files, pos = {}, 0
    while True:
        fields = [int(payload[pos + 6 + 8 * i:pos + 14 + 8 * i], 16) for i in range(13)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        name = payload[pos + 110:pos + 110 + namesize - 1].decode()
        pos += 110 + namesize
        pos += -pos % 4
        if name == "TRAILER!!!":
            return files
        files[name] = (mode, payload[pos:pos + size])
        pos += size
        pos += -pos % 4


class TestAgentPackages(unittest.TestCase):
    def test_deb_holds_the_install_script_and_runs_it_on_configure(self):
        data = agentpkg.build_deb(SCRIPT, "2.21.0", "https://loco.test:8443")
        path = saved(data, ".deb")
        self.addCleanup(os.unlink, path)
        fields = debpkg.deb_fields(path)                                     # relu par le lecteur des dépôts APT
        self.assertEqual((fields["Package"], fields["Version"], fields["Architecture"], fields["Depends"]),
                         ("loco-client", "2.21.0", "all", "python3, systemd"))
        self.assertIn("https://loco.test:8443", fields["Description"])
        members = deb_members(data)
        self.assertEqual(list(members), ["debian-binary", "control.tar.gz", "data.tar.gz"])
        self.assertEqual(members["debian-binary"], b"2.0\n")
        with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as tar:
            postinst = tar.extractfile("./postinst").read().decode()
            prerm = tar.extractfile("./prerm").read().decode()
            self.assertEqual(tar.getmember("./postinst").mode, 0o755)
            md5sums = tar.extractfile("./md5sums").read().decode()
        self.assertIn("configure) sh /usr/share/loco-client/install.sh", postinst)
        self.assertIn("remove) if [ -x /usr/local/lib/loco-client/loco-client.py ]", prerm)
        with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as tar:
            script = tar.extractfile("./usr/share/loco-client/install.sh").read()
            self.assertEqual({m.uname for m in tar.getmembers()}, {"root"})
        self.assertEqual(script.decode(), SCRIPT)
        self.assertIn(hashlib.md5(script).hexdigest() + "  usr/share/loco-client/install.sh", md5sums)

    def test_rpm_headers_digests_and_payload_are_consistent(self):
        data = agentpkg.build_rpm(SCRIPT, "2.21.0", "https://loco.test:8443")
        path = saved(data, ".rpm")
        self.addCleanup(os.unlink, path)
        self.assertEqual(rpmpkg.rpm_fields(path), {"name": "loco-client", "version": "2.21.0", "release": "1",
                                                   "arch": "noarch"})               # binaire, pas un paquet source
        header, signature, start, end = rpmpkg.read_rpm(path)
        raw_header, payload = data[start:end], data[end:]
        self.assertEqual(signature[273], hashlib.sha256(raw_header).hexdigest())
        self.assertEqual(signature[269], hashlib.sha1(raw_header).hexdigest())
        self.assertEqual(signature[1004], hashlib.md5(raw_header + payload).digest())
        self.assertEqual(signature[1000], [len(raw_header) + len(payload)])
        self.assertEqual(header[5092], [hashlib.sha256(payload).hexdigest()])
        self.assertEqual(header[1024], "sh /usr/share/loco-client/install.sh\n")       # %post
        self.assertIn('if [ "$1" -eq 0 ]', header[1025])                              # %preun : retrait final
        self.assertEqual(header[1049][:2], ["python3", "systemd"])
        files = cpio_files(gzip.decompress(payload))
        self.assertEqual(files["./usr/share/loco-client/install.sh"][1].decode(), SCRIPT)
        self.assertEqual(signature[1007], [len(gzip.decompress(payload))])
        self.assertEqual(header[1035][1], hashlib.sha256(SCRIPT.encode()).hexdigest())
        # Régions : la dernière entrée des données renvoie au début de l'index (format exigé par rpm 4.14 et plus).
        count = struct.unpack(">I", raw_header[8:12])[0]
        tag, kind, offset, n = struct.unpack(">IIiI", raw_header[-16:])
        self.assertEqual((tag, kind, offset, n), (63, 7, -count * 16, 16))


class TestEnrollmentPage(FleetTest):
    def test_page_offers_each_system_with_its_installer(self):
        page = self.admin.c.get("/enrolement").get_data(as_text=True)
        key = Agent.key()
        for tab in ('data-tab="windows"', 'data-tab="debian"', 'data-tab="redhat"'):
            self.assertIn(tab, page)
        self.assertIn("Télécharger le MSI", page)
        self.assertIn(f'href="/agent/loco-client.deb?key={key}" download', page)
        self.assertIn(f'href="/agent/loco-client.rpm?key={key}" download', page)
        self.assertIn(f"sudo apt install ./loco-client_{config.VERSION}_all.deb", page)
        self.assertIn(f"sudo dnf install ./loco-client-{config.VERSION}-1.noarch.rpm", page)
        self.assertIn("/linux/setup.sh | sudo sh", page)
        self.assertIn('href="/enrolement" class="nav-enroll on"', page)
        self.assertNotIn("Brancher un poste", self.admin.c.get("/").get_data(as_text=True))
        self.assertIn('href="/enrolement">Ajouter un poste', self.admin.c.get("/parc").get_data(as_text=True))

    def test_packages_need_the_enrollment_key_and_carry_this_server(self):
        self.assertEqual(self.admin.c.get("/agent/loco-client.deb?key=faux").status_code, 403)
        self.assertEqual(self.admin.c.get("/agent/loco-client.rpm").status_code, 403)
        key = Agent.key()
        anonymous = Agent({"hostname": "x"}, "192.168.43.70").c                      # sans session, comme un poste
        deb = anonymous.get(f"/agent/loco-client.deb?key={key}")
        self.assertEqual(deb.status_code, 200)
        self.assertIn(f'filename="loco-client_{config.VERSION}_all.deb"', deb.headers["Content-Disposition"])
        with tarfile.open(fileobj=io.BytesIO(deb_members(deb.data)["data.tar.gz"])) as tar:
            script = tar.extractfile("./usr/share/loco-client/install.sh").read().decode()
        self.assertIn(f"--server '{config.HTTPS_URL}' --key '{key}'", script)
        rpm = anonymous.get(f"/agent/loco-client.rpm?key={key}")
        self.assertEqual((rpm.status_code, rpm.mimetype), (200, "application/x-rpm"))
        self.assertEqual(rpm.data[:4], b"\xed\xab\xee\xdb")

    def test_page_follows_rights_and_features(self):
        import test_permissions
        browser, profile = test_permissions.TestPermissions.browser, test_permissions.TestPermissions.profile
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        apt_only = browser(self, "depots", profile(self, "depots", {"linux.view"}))
        page = apt_only.c.get("/enrolement").get_data(as_text=True)
        self.assertIn("/linux/setup.sh", page)
        self.assertNotIn("loco-client.deb", page)                                # clé d'inscription réservée
        self.assertNotIn('data-tab="windows"', page)
        self.assertIn('href="/enrolement"', page)
        nothing = browser(self, "rien", profile(self, "rien", {"dashboard.view"}))
        self.assertEqual(nothing.c.get("/enrolement").status_code, 403)
        self.assertNotIn('href="/enrolement"', nothing.c.get("/").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
