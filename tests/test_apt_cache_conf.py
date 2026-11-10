"""Cache APT par nginx (appliance Alpine et serveur Windows) : sortie en HTTP ou en HTTPS avec repli, jamais d'index
périmé. Le comportement réel est essayé contre de faux dépôts avec le nginx d'Alpine (hors de cette suite) ; ici, ce
que le gabarit doit garder et ce que les installeurs doivent lui fournir."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = (ROOT / "conf" / "nginx-apt-proxy.conf").read_text(encoding="utf-8")


def block(name):
    """Corps d'un bloc « location … name { … } » du gabarit."""
    start = CONF.index(name)
    return CONF[CONF.index("{", start) + 1:CONF.index("}", start)]


class TestAptCacheTemplate(unittest.TestCase):
    def test_installers_provide_every_placeholder(self):
        wanted = set(re.findall(r"@([A-Z_]+)@", CONF))
        self.assertIn("CA_BUNDLE", wanted)                                # sortie HTTPS vérifiée
        linux = (ROOT / "linux" / "install.sh").read_text(encoding="utf-8") + (ROOT / "linux" / "lib" / "alpine.sh").read_text(encoding="utf-8")
        windows = (ROOT / "portal" / "locowin.py").read_text(encoding="utf-8")
        for name in sorted(wanted):
            with self.subTest(name):
                self.assertIn(f"LRT_{name}=", linux)
                self.assertRegex(windows, rf'"{name}":')

    def test_each_kind_of_file_goes_out_one_way_then_the_other(self):
        for first, second in (('"/by-hash/"', "@hash_other_way"), ('"/dists/"', "@dists_other_way"), ("deb|udeb", "@pool_other_way")):
            with self.subTest(first):
                self.assertIn(f"error_page 502 504 = {second};", block("location ~ " + first if first.startswith('"') else first))
                self.assertIn("proxy_pass $loco_apt_way://$http_host$request_uri;", block("location ~ " + first if first.startswith('"') else first))
                self.assertIn("proxy_pass $loco_apt_other://$http_host$request_uri;", block("location " + second))
        self.assertNotIn("proxy_pass http://", CONF)                       # plus aucune sortie figée en HTTP
        self.assertEqual(len(re.findall(r"proxy_pass ", CONF)), 6)

    def test_the_two_ways_are_opposite_and_known_repositories_start_with_https(self):
        ways = dict(re.findall(r"^\s+(\S+)\s+(https?);$", CONF[CONF.index("map $http_host $loco_apt_way"):CONF.index("map $loco_apt_way")], re.M))
        self.assertEqual(ways.pop("default"), "http")                      # un dépôt inconnu est joint comme le poste l'a demandé
        self.assertEqual(set(ways.values()), {"https"})
        self.assertLessEqual({"deb.debian.org", "security.debian.org", "archive.ubuntu.com", "security.ubuntu.com"}, set(ways))
        other = CONF[CONF.index("map $loco_apt_way $loco_apt_other"):]
        self.assertRegex(other, r"http\s+https;\s+https\s+http;")

    def test_https_is_verified(self):
        for line in ("proxy_ssl_verify on;", "proxy_ssl_server_name on;", "proxy_ssl_name $host;", "proxy_ssl_trusted_certificate @CA_BUNDLE@;"):
            self.assertIn(line, CONF)

    def test_an_index_is_never_served_stale_nor_in_part(self):
        """Un index dont la validité est passée fait échouer tout « apt-get update » du poste (constat d'une instance)."""
        for name in ('location ~ "/dists/"', "location @dists_other_way"):
            with self.subTest(name):
                self.assertIn("proxy_cache_use_stale off;", block(name))
                self.assertIn("max_ranges 0;", block(name))
                self.assertIn("proxy_cache_valid 200 2m;", block(name))
        for name in ("location @hash_other_way", "location @pool_other_way"):
            self.assertNotIn("proxy_cache_use_stale", block(name))          # paquets : immuables, une copie ancienne reste bonne

    def test_the_first_way_gives_up_fast(self):
        server = CONF[CONF.index("server {"):CONF.index("if ($loco_apt_self)")]
        self.assertIn("proxy_connect_timeout 5s;", server)
        self.assertIn("proxy_next_upstream_tries 2;", server)

    def test_it_is_still_not_an_open_proxy(self):
        self.assertIn("if ($loco_apt_self) {\n        return 403;", CONF)
        self.assertRegex(CONF, r"location / \{\s+return 403;")
        self.assertIn("include snippets/loco-clients.conf;", CONF)


if __name__ == "__main__":
    unittest.main()
