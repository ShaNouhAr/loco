"""Serveur MCP (assistants IA) : jetons montrés une fois, autorisations une à une bornées par les droits du compte,
protocole des deux générations (initialize 2025-xx et 2026-07-28), outils branchés sur les fonctions du portail."""

import base64
import hashlib
import json
import re
import unittest

import test_portal as fixture
from test_fleet import LINUX, WINDOWS, Agent, FleetTest
from loco import auth, journal, mcp, permissions
from loco.db import transaction

MODERN = "2026-07-28"
TOKEN = re.compile(r"loco_mcp_[A-Za-z0-9_-]{43}")
ALL = list(mcp.TOOLS)


class Client:
    """Client MCP minimal : en-tête Authorization, sans cookie de session."""

    def __init__(self, token, version="2025-11-25"):
        self.c = fixture.loco.app.test_client()
        self.token, self.version, self.ids = token, version, 0

    def raw(self, message, headers=None):
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        return self.c.post("/mcp", data=message if isinstance(message, (str, bytes)) else json.dumps(message), headers=h)

    def request(self, method, params=None, headers=None, notification=False):
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            self.ids += 1
            message["id"] = self.ids
        h = {} if method == "initialize" else {"MCP-Protocol-Version": self.version}
        h.update(headers or {})
        return self.raw(message, h)

    def modern(self, method, params=None, headers=None, notification=False, version=MODERN):
        params = dict(params or {})
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": version, "io.modelcontextprotocol/clientCapabilities": {},
                           "io.modelcontextprotocol/clientInfo": {"name": "client-moderne", "version": "2.0"}}
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            self.ids += 1
            message["id"] = self.ids
        h = {"MCP-Protocol-Version": version, "Mcp-Method": method}
        if method == "tools/call":
            h["Mcp-Name"] = params.get("name", "")
        h.update(headers or {})
        return self.raw(message, h)

    def call(self, tool, **arguments):
        r = self.request("tools/call", {"name": tool, "arguments": arguments})
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()["result"]

    def data(self, tool, **arguments):
        result = self.call(tool, **arguments)
        assert not result["isError"], result["content"][0]["text"]
        return result["structuredContent"]

    def names(self):
        return [t["name"] for t in self.request("tools/list").get_json()["result"]["tools"]]


def create(browser, name, tools, expiry="90"):
    r = browser.post("/accounts/mcp/add", {"name": name, "expiry": expiry, "tools": list(tools)}, page="/accounts/mcp")
    found = TOKEN.search(r.get_data(as_text=True))
    return (found.group(0) if found else None), r


def token_row(name):
    with transaction() as conn:
        return conn.execute("SELECT * FROM mcp_tokens WHERE name=?", (name,)).fetchone()


class McpTest(FleetTest):
    def setUp(self):
        super().setUp()
        with transaction() as conn:
            conn.execute("DELETE FROM mcp_tokens")
            conn.execute("DELETE FROM settings WHERE key='features_disabled'")
            conn.execute("DELETE FROM scripts")
            conn.execute("DELETE FROM script_runs")
            conn.execute("DELETE FROM users WHERE username='marie'")
            conn.execute("DELETE FROM access_roles WHERE id='ia'")

    def tearDown(self):
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='marie'")
            conn.execute("DELETE FROM access_roles WHERE id='ia'")
            conn.execute("DELETE FROM mcp_tokens")

    def operator(self, extra=()):
        """Compte « marie » : inventaire et mises à jour, sans redémarrage ; peut créer des accès MCP."""
        grants = permissions.expand(permissions.VIEWER | {"fleet.scan", "fleet.update", "mcp.manage", *extra})
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO access_roles (id,label,label_key,description,permissions,builtin) "
                         "VALUES ('ia','Opérateur IA','opérateur ia','',?,0)", (json.dumps(sorted(grants)),))
            if not conn.execute("SELECT 1 FROM users WHERE username='marie'").fetchone():
                auth.create_user(conn, "marie", "Marie-Password-2026", "ia", must_change=False)
        fixture.reset_failures()
        browser = fixture.Browser()
        browser.login("marie", "Marie-Password-2026")
        return browser

    def agents(self):
        win, lin = Agent(WINDOWS, "192.168.43.50"), Agent(LINUX, "192.168.43.60")
        win.enroll()
        lin.enroll()
        win.checkin({"updates": [{"name": "KB5070001", "version": "KB5070001", "security": True}],
                     "apps": [{"name": "7-Zip 24.08 (x64)", "version": "24.08", "publisher": "Igor Pavlov"}]})
        lin.checkin({"updates": [{"name": "openssl", "version": "3.5.4-1", "security": True}, {"name": "tzdata", "version": "2026e"}],
                     "packages": [{"name": "openssl", "version": "3.5.3-1", "manual": False}, {"name": "htop", "version": "3.4.1", "manual": True}],
                     "system": {"kernel": "6.12.0-amd64", "cores": 4, "memory": 8 * 1024 ** 3}})
        return win, lin


# --------------------------------------------------------------------------- transport et authentification

class TestTransport(McpTest):
    def test_a_valid_token_is_required(self):
        anonymous = Client(None)
        r = anonymous.request("initialize", {"protocolVersion": "2025-11-25"})
        self.assertEqual(r.status_code, 401)
        self.assertIn('Bearer realm="loco"', r.headers["WWW-Authenticate"])
        self.assertEqual(Client("loco_mcp_" + "A" * 43).request("tools/list").status_code, 401)   # bon format, inconnu
        self.assertEqual(Client("jeton-quelconque").request("tools/list").status_code, 401)
        token, _ = create(self.admin, "Claude", ["list_devices"])
        client = Client(token)
        self.assertEqual(client.request("tools/list").status_code, 200)
        self.assertEqual(client.c.get("/mcp", headers={"Authorization": f"Bearer {token}"}).status_code, 405)
        r = client.c.delete("/mcp", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual((r.status_code, r.headers["Allow"]), (405, "POST"))
        # Ni session ni jeton CSRF : un cookie de session seul n'ouvre rien.
        self.assertEqual(self.admin.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code, 401)

    def test_foreign_origin_is_refused(self):
        token, _ = create(self.admin, "Claude", ["list_devices"])
        client = Client(token)
        self.assertEqual(client.request("tools/list", headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(client.request("tools/list", headers={"Origin": "https://repo.test:8443"}).status_code, 200)

    def test_malformed_messages(self):
        token, _ = create(self.admin, "Claude", ["list_devices"])
        client = Client(token)
        r = client.raw("{pas du json", {"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, -32700))
        r = client.raw([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], {"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, -32600))
        self.assertEqual(client.raw({"id": 1, "method": "ping"}).status_code, 400)
        self.assertEqual(client.raw({"jsonrpc": "2.0", "id": 1, "result": {}}).status_code, 202)   # réponse du client

    def test_disabled_feature_blocks_the_server_and_the_page(self):
        token, _ = create(self.admin, "Claude", ["list_devices"])
        with transaction() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES ('features_disabled', '[\"mcp\"]')")
        r = Client(token).request("tools/list")
        self.assertEqual(r.status_code, 403)
        self.assertIn("disabled", r.get_json()["error"]["message"])
        self.assertEqual(self.admin.c.get("/accounts/mcp").status_code, 403)
        self.assertNotIn('href="/accounts/mcp"', self.admin.c.get("/accounts").get_data(as_text=True))


# --------------------------------------------------------------------------- protocole

class TestLegacyProtocol(McpTest):
    def test_handshake_listing_and_calls(self):
        token, _ = create(self.admin, "Claude", ["list_devices", "get_server_status"])
        client = Client(token)
        r = client.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                          "clientInfo": {"name": "claude-code", "version": "2.1.296"}})
        result = r.get_json()["result"]
        self.assertEqual((result["protocolVersion"], result["serverInfo"]["name"]), ("2025-11-25", "loco"))
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertIn("get_device", result["instructions"])
        self.assertEqual(token_row("Claude")["last_client"], "claude-code 2.1.296")
        # Version inconnue du serveur : il propose la sienne (la plus récente de cette génération).
        offered = client.request("initialize", {"protocolVersion": "2024-11-05"}).get_json()["result"]["protocolVersion"]
        self.assertEqual(offered, "2025-11-25")
        self.assertEqual(client.request("notifications/initialized", notification=True).status_code, 202)
        self.assertEqual(client.request("ping").get_json()["result"], {})
        self.assertEqual(sorted(client.names()), ["get_server_status", "list_devices"])
        tool = next(t for t in client.request("tools/list").get_json()["result"]["tools"] if t["name"] == "list_devices")
        self.assertEqual(tool["annotations"]["readOnlyHint"], True)
        self.assertEqual(tool["inputSchema"]["type"], "object")
        result = client.call("get_server_status")
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertIn("loco_version", result["structuredContent"])
        # 2025-03-26 : pas de structuredContent ; en-tête absent = 2025-03-26.
        old = Client(token, "2025-03-26").call("get_server_status")
        self.assertNotIn("structuredContent", old)
        r = client.raw({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(client.request("tools/list", headers={"MCP-Protocol-Version": "1999-01-01"}).status_code, 400)

    def test_unknown_methods_tools_and_refused_tools(self):
        token, _ = create(self.admin, "Claude", ["list_devices"])
        client = Client(token)
        self.assertEqual(client.request("prompts/list").get_json()["error"]["code"], -32601)
        r = client.request("tools/call", {"name": "format_disk"})
        self.assertEqual(r.get_json()["error"]["code"], -32602)
        refused = client.call("reboot_device", device="1")         # outil connu mais non accordé à cet accès
        self.assertTrue(refused["isError"])
        self.assertIn("not allowed", refused["content"][0]["text"])
        bad = client.call("list_devices", limit=0)
        self.assertTrue(bad["isError"])
        self.assertIn("limit", bad["content"][0]["text"])
        self.assertTrue(client.call("list_devices", couleur="rouge")["isError"])


class TestModernProtocol(McpTest):
    def test_discover_list_and_call(self):
        token, _ = create(self.admin, "Moderne", ["list_devices", "get_server_status"])
        client = Client(token)
        discover = client.modern("server/discover").get_json()["result"]
        self.assertEqual(discover["supportedVersions"], [MODERN])
        self.assertEqual(discover["resultType"], "complete")
        self.assertEqual(discover["capabilities"]["tools"], {"listChanged": False})
        self.assertEqual(discover["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "loco")
        self.assertGreater(discover["ttlMs"], 0)
        listing = client.modern("tools/list").get_json()["result"]
        self.assertEqual((listing["resultType"], listing["cacheScope"]), ("complete", "private"))
        self.assertEqual(sorted(t["name"] for t in listing["tools"]), ["get_server_status", "list_devices"])
        called = client.modern("tools/call", {"name": "get_server_status", "arguments": {}}).get_json()["result"]
        self.assertEqual(called["resultType"], "complete")
        self.assertFalse(called["isError"])
        self.assertIn("loco_version", called["structuredContent"])
        self.assertEqual(token_row("Moderne")["last_client"], "client-moderne 2.0")
        self.assertEqual(client.modern("notifications/cancelled", notification=True).status_code, 202)
        # Valeur d'en-tête non sûre transmise en base64 : décodée avant comparaison.
        encoded = "=?base64?" + base64.b64encode(b"get_server_status").decode() + "?="
        r = client.modern("tools/call", {"name": "get_server_status"}, headers={"Mcp-Name": encoded})
        self.assertEqual(r.status_code, 200)

    def test_header_and_version_errors(self):
        token, _ = create(self.admin, "Moderne", ["list_devices"])
        client = Client(token)
        r = client.modern("tools/list", headers={"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, -32020))
        r = client.modern("tools/list", version="2099-01-01")
        error = r.get_json()["error"]
        self.assertEqual((r.status_code, error["code"]), (400, -32022))
        self.assertEqual(error["data"]["requested"], "2099-01-01")
        self.assertIn(MODERN, error["data"]["supported"])
        self.assertIn("2025-11-25", error["data"]["supported"])
        r = client.modern("tools/list", headers={"Mcp-Method": "tools/call"})
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, -32020))
        r = client.modern("tools/call", {"name": "list_devices"}, headers={"Mcp-Name": "get_device"})
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, -32020))
        r = client.modern("resources/list")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (404, -32601))


# --------------------------------------------------------------------------- autorisations

class TestPermissions(McpTest):
    def test_grants_are_bounded_by_the_account_rights_at_every_call(self):
        marie = self.operator()
        page = marie.c.get("/accounts/mcp").get_data(as_text=True)
        self.assertIn('value="reboot_device" role="switch" disabled', page)        # hors de ses droits : grisé
        self.assertEqual(create(marie, "Refus", ["list_devices", "reboot_device"])[1].status_code, 400)
        token, _ = create(marie, "Claude de Marie", ["list_devices", "scan_device"])
        client = Client(token)
        self.assertEqual(sorted(client.names()), ["list_devices", "scan_device"])
        # Le profil de marie perd l'inventaire : l'outil disparaît et son appel est refusé, sans toucher au jeton.
        with transaction() as conn:
            grants = permissions.expand(permissions.VIEWER | {"fleet.update", "mcp.manage"})
            conn.execute("UPDATE access_roles SET permissions=? WHERE id='ia'", (json.dumps(sorted(grants)),))
        self.assertEqual(client.names(), ["list_devices"])
        self.assertTrue(client.call("scan_device", device="1")["isError"])

    def test_reboot_needs_its_own_permission(self):
        win, _ = self.agents()
        token, _ = create(self.admin, "Maj", ["update_device"])
        refused = Client(token).call("update_device", device="pc-compta", reboot=True)
        self.assertTrue(refused["isError"])
        self.assertIn("reboot", refused["content"][0]["text"])
        token, _ = create(self.admin, "Maj et redémarrage", ["update_device", "reboot_device"])
        done = Client(token).data("update_device", device="pc-compta", reboot=True)
        self.assertTrue(done["queued"])
        with transaction() as conn:
            cmd = conn.execute("SELECT * FROM device_commands WHERE action='update'").fetchone()
        self.assertEqual(json.loads(cmd["params"]), {"reboot": True})

    def test_disabled_deleted_or_unfinished_accounts_and_expiry(self):
        marie = self.operator()
        token, _ = create(marie, "Claude de Marie", ["list_devices"])
        client = Client(token)
        self.assertEqual(client.request("tools/list").status_code, 200)
        with transaction() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE username='marie'")
        self.assertEqual(client.request("tools/list").status_code, 401)
        with transaction() as conn:
            conn.execute("UPDATE users SET disabled=0, must_change=1 WHERE username='marie'")
        self.assertEqual(client.request("tools/list").status_code, 401)
        with transaction() as conn:
            conn.execute("UPDATE users SET must_change=0 WHERE username='marie'")
            conn.execute("UPDATE mcp_tokens SET expires_at='2020-01-01 00:00:00'")
        self.assertEqual(client.request("tools/list").status_code, 401)
        with transaction() as conn:
            conn.execute("UPDATE mcp_tokens SET expires_at=''")
        self.assertEqual(client.request("tools/list").status_code, 200)
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='marie'")
            self.assertIsNone(conn.execute("SELECT 1 FROM mcp_tokens").fetchone())      # supprimé avec le compte
        self.assertEqual(client.request("tools/list").status_code, 401)

    def test_cancel_only_actions_this_access_may_start(self):
        win, _ = self.agents()
        token, _ = create(self.admin, "Annulation", ["cancel_device_action", "scan_device"])
        client = Client(token)
        client.data("scan_device", device="pc-compta")
        with transaction() as conn:
            dev_id = conn.execute("SELECT id FROM devices WHERE hostname='pc-compta'").fetchone()[0]
            conn.execute("INSERT INTO device_commands (device_id, action, params, created_at, created_by) "
                         "VALUES (?, 'reboot', '{}', '2026-10-30 10:00:00', 'admin')", (dev_id,))
            scan, reboot = (conn.execute("SELECT id FROM device_commands WHERE action=?", (a,)).fetchone()[0] for a in ("scan", "reboot"))
        self.assertTrue(client.call("cancel_device_action", device="pc-compta", action_id=reboot)["isError"])
        self.assertTrue(client.data("cancel_device_action", device="pc-compta", action_id=scan)["cancelled"])
        self.assertTrue(client.call("cancel_device_action", device="pc-compta", action_id=scan)["isError"])   # déjà annulée


# --------------------------------------------------------------------------- outils

class TestTools(McpTest):
    def test_fleet_queries(self):
        self.agents()
        token, _ = create(self.admin, "Lecture", ["list_devices", "get_device", "find_installed_software", "list_device_groups"])
        client = Client(token)
        listing = client.data("list_devices")
        self.assertEqual({d["hostname"] for d in listing["devices"]}, {"pc-compta", "srv-web"})
        self.assertEqual([d["hostname"] for d in client.data("list_devices", os="windows")["devices"]], ["pc-compta"])
        self.assertEqual([d["hostname"] for d in client.data("list_devices", search="trixie")["devices"]], ["srv-web"])
        linux = client.data("get_device", device="srv-web")
        self.assertEqual(linux["pending_updates_total"], 2)
        self.assertTrue(next(u for u in linux["pending_updates"] if u["name"] == "openssl")["security"])
        self.assertEqual((linux["packages"]["total"], linux["system"]["kernel"]), (2, "6.12.0-amd64"))
        by_id = client.data("get_device", device=str(linux["id"]))
        self.assertEqual(by_id["hostname"], "srv-web")
        self.assertTrue(client.call("get_device", device="inconnu")["isError"])
        found = client.data("find_installed_software", name="7-zip")
        self.assertEqual(found["devices"][0]["hostname"], "pc-compta")
        self.assertEqual(found["devices"][0]["matches"][0]["version"], "24.08")
        self.assertEqual(client.data("find_installed_software", name="htop")["devices"][0]["hostname"], "srv-web")
        self.assertEqual(client.data("list_device_groups"), {"groups": []})

    def test_actions_are_queued_traced_and_attributed(self):
        self.agents()
        token, _ = create(self.admin, "Opérations", ["scan_device", "install_software", "update_devices"])
        client = Client(token)
        self.assertTrue(client.data("scan_device", device="srv-web")["queued"])
        self.assertFalse(client.data("scan_device", device="srv-web")["queued"])     # déjà en attente
        with transaction() as conn:
            conn.execute("INSERT INTO win_apps (slug, name, version, kind, args, filename, sha256, status, detect) "
                         "VALUES ('7zip', '7-Zip', '25.01', 'msi', '/qn', '7z.msi', '', 'ready', '7-Zip*')")
        self.assertTrue(client.data("install_software", device="pc-compta", software="7-Zip")["queued"])
        self.assertTrue(client.call("install_software", device="srv-web", software="7zip")["isError"])
        done = client.data("update_devices", devices=["pc-compta", "srv-web"])
        self.assertEqual(sorted(done["queued"]), ["pc-compta", "srv-web"])
        with transaction() as conn:
            commands = conn.execute("SELECT action, params, created_by FROM device_commands ORDER BY id").fetchall()
            events = conn.execute("SELECT username, action, detail FROM audit WHERE detail LIKE '%MCP « Opérations »%'").fetchall()
        self.assertEqual([c["action"] for c in commands], ["scan", "install-app", "update", "update"])
        self.assertEqual({c["created_by"] for c in commands}, {"admin"})
        self.assertEqual(json.loads(commands[1]["params"]), {"slug": "7zip"})
        self.assertEqual({e["action"] for e in events}, {"parc.scan", "parc.install-app", "parc.mise-a-jour-selection"})
        self.assertEqual({e["username"] for e in events}, {"admin"})

    def test_scripts_library_and_assistant_commands(self):
        self.agents()
        with transaction() as conn:
            conn.execute("INSERT INTO scripts (name, description, platform, content, version, created_at, updated_at) "
                         "VALUES ('Espace disque', 'df', 'linux', 'df -h', 3, '2026-10-30', '2026-10-30')")
        token, _ = create(self.admin, "Scripts", ["list_scripts", "get_script", "run_script", "run_command", "get_script_run", "list_script_runs"])
        client = Client(token)
        self.assertEqual(client.data("list_scripts")["scripts"][0]["name"], "Espace disque")
        self.assertEqual(client.data("get_script", script="Espace disque")["content"], "df -h")
        stale = client.call("run_script", script="Espace disque", version=2, devices=["srv-web"])
        self.assertTrue(stale["isError"])
        started = client.data("run_script", script="Espace disque", version=3, devices=["srv-web", "pc-compta"])
        self.assertEqual((started["targets"], started["skipped"]), (["srv-web"], ["pc-compta"]))
        command = client.data("run_command", platform="linux", content="uptime\r\n", title="Charge", devices=["srv-web"])
        run = client.data("get_script_run", run_id=command["run_id"])
        self.assertEqual(run["name"], "MCP : Charge")
        self.assertEqual([t["hostname"] for t in run["targets"]], ["srv-web"])
        with transaction() as conn:
            row = conn.execute("SELECT * FROM script_runs WHERE id=?", (command["run_id"],)).fetchone()
        self.assertEqual((row["content"], row["created_by"], row["script_id"]), ("uptime\n", "admin", None))
        self.assertEqual(len(client.data("list_script_runs")["runs"]), 2)
        self.assertTrue(client.call("run_command", platform="linux", content="id", devices=["pc-compta"])["isError"])

    def test_journal_and_server_status(self):
        token, _ = create(self.admin, "Audit", ["search_journal", "get_server_status"])
        client = Client(token)
        events = client.data("search_journal", text="Audit")["events"]
        self.assertEqual(events[0]["action"], "mcp.acces.ajout")
        self.assertEqual(events[0]["label"], "Jeton MCP créé")
        self.assertTrue(client.call("search_journal", since="hier")["isError"])
        status = client.data("get_server_status")
        self.assertNotIn("fleet", status)                 # le parc n'est pas accordé à cet accès

    def test_ambiguous_hostname_asks_for_the_id(self):
        Agent(WINDOWS, "192.168.43.50").enroll()
        Agent(WINDOWS, "192.168.43.51").enroll()                 # même nom, autre poste
        token, _ = create(self.admin, "Lecture", ["get_device"])
        result = Client(token).call("get_device", device="pc-compta")
        self.assertTrue(result["isError"])
        self.assertIn("Several devices", result["content"][0]["text"])


# --------------------------------------------------------------------------- page d'administration

class TestAdministration(McpTest):
    def test_token_is_shown_once_and_stored_hashed(self):
        token, r = create(self.admin, "Claude Code", ["list_devices", "get_device"], expiry="30")
        page = r.get_data(as_text=True)
        self.assertIn("claude mcp add --transport http loco https://repo.test:8443/mcp", page)
        row = token_row("Claude Code")
        self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(dict(row)))
        self.assertEqual(row["hint"], token[:13])
        self.assertTrue(row["expires_at"])
        later = self.admin.c.get("/accounts/mcp").get_data(as_text=True)
        self.assertNotIn(token, later)
        self.assertIn(token[:13], later)
        with transaction() as conn:
            event = conn.execute("SELECT * FROM audit WHERE action='mcp.acces.ajout' ORDER BY id DESC").fetchone()
        self.assertIn("2 outil(s)", event["detail"])

    def test_form_validation(self):
        self.assertIsNone(create(self.admin, "", ["list_devices"])[0])
        self.assertIsNone(create(self.admin, "Sans autorisation", [])[0])
        self.assertEqual(create(self.admin, "Forgé", ["format_disk"])[1].status_code, 400)
        self.assertEqual(create(self.admin, "Forgé", ["list_devices"], expiry="7")[1].status_code, 400)
        with transaction() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM mcp_tokens").fetchone())

    def test_edit_and_revoke(self):
        token, _ = create(self.admin, "Claude", ["list_devices"])
        row = token_row("Claude")
        self.admin.post(f"/accounts/mcp/{row['id']}/edit", {"name": "Claude Desktop", "tools": ["list_devices", "get_device"]},
                        page="/accounts/mcp")
        self.assertEqual(sorted(Client(token).names()), ["get_device", "list_devices"])
        marie = self.operator()
        mine, _ = create(marie, "Claude de Marie", ["list_devices"])
        page = marie.c.get("/accounts/mcp").get_data(as_text=True)
        self.assertNotIn("Claude Desktop", page)                          # accès de l'admin : invisible pour marie
        self.assertEqual(marie.post(f"/accounts/mcp/{row['id']}/revoke", page="/accounts/mcp").status_code, 404)
        self.assertEqual(marie.post(f"/accounts/mcp/{row['id']}/edit", {"name": "x", "tools": ["list_devices"]},
                                    page="/accounts/mcp").status_code, 404)
        # L'administrateur voit et révoque l'accès de marie, mais ne modifie pas ses autorisations.
        theirs = token_row("Claude de Marie")
        self.assertIn("Claude de Marie", self.admin.c.get("/accounts/mcp").get_data(as_text=True))
        self.assertEqual(self.admin.post(f"/accounts/mcp/{theirs['id']}/edit", {"name": "x", "tools": ["list_devices"]},
                                         page="/accounts/mcp").status_code, 404)
        self.admin.post(f"/accounts/mcp/{theirs['id']}/revoke", page="/accounts/mcp")
        self.assertEqual(Client(mine).request("tools/list").status_code, 401)
        with transaction() as conn:
            events = [r["action"] for r in conn.execute("SELECT action FROM audit WHERE action LIKE 'mcp.%' ORDER BY id")]
        self.assertEqual(events[-1], "mcp.acces.revocation")
        self.assertIn("mcp.acces.modification", events)

    def test_page_access_menu_and_journal(self):
        self.assertIn('href="/accounts/mcp"', self.admin.c.get("/accounts").get_data(as_text=True))
        viewer = fixture.Browser()
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='lecteur-mcp'")
            auth.create_user(conn, "lecteur-mcp", "Lecteur-Password-2026", "viewer", must_change=False)
        viewer.login("lecteur-mcp", "Lecteur-Password-2026")
        self.assertEqual(viewer.c.get("/accounts/mcp").status_code, 403)
        self.assertEqual(journal.describe("mcp.acces.revocation")["category"], "mcp")
        with transaction() as conn:
            conn.execute("DELETE FROM users WHERE username='lecteur-mcp'")


if __name__ == "__main__":
    unittest.main()
