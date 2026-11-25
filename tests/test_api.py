"""API REST de loco (v1) : mêmes jetons, droits et outils que le serveur MCP, en requêtes HTTP ordinaires.

On vérifie l'authentification Bearer, le refus d'un outil non coché, la lecture des ressources (processeur, mémoire,
disques) d'un poste et du parc, le tri par usage, la description OpenAPI servie sans jeton, et qu'une action passée par
l'API est au journal avec « API « nom du jeton » »."""

import json
import unittest

import test_portal as fixture
from test_fleet import LINUX, Agent
from test_mcp import McpTest, create
from loco import config
from loco.db import transaction


def metrics(mem_used, cpu=10.0):
    return {"cpu": cpu, "cores": 4, "mem_total": 8 * 1024 ** 3, "mem_used": mem_used,
            "swap_total": 2 * 1024 ** 3, "swap_used": 0,
            "disks": [{"mount": "/", "fs": "ext4", "total": 100 * 1024 ** 3, "used": 40 * 1024 ** 3}],
            "load": [0.5, 0.4, 0.3], "net_rx": 1000, "net_tx": 500, "uptime": 123456}


class Rest:
    """Client HTTP minimal de l'API : en-tête Authorization, sans cookie ni jeton CSRF."""

    def __init__(self, token):
        self.c = fixture.loco.app.test_client()
        self.token = token

    def _headers(self, extra):
        h = dict(extra or {})
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get(self, path, query=None, headers=None):
        return self.c.get("/api/v1" + path, query_string=query, headers=self._headers(headers))

    def post(self, path, body=None, headers=None, raw=None):
        kw = {"data": raw} if raw is not None else {"json": body if body is not None else {}}
        return self.c.post("/api/v1" + path, headers=self._headers(headers), **kw)

    def delete(self, path, body=None, headers=None):
        return self.c.delete("/api/v1" + path, json=body if body is not None else {}, headers=self._headers(headers))


class ApiTest(McpTest):
    def linux(self, hostname, ip, mem_used):
        ident = dict(LINUX, hostname=hostname)
        agent = Agent(ident, ip)
        agent.enroll()
        agent.post("/agent/api/checkin", dict(agent.ident, metrics=metrics(mem_used)), agent.token)
        return agent


# --------------------------------------------------------------------------- authentification et transport

class TestAuth(ApiTest):
    def test_a_valid_token_is_required(self):
        anon = Rest(None)
        r = anon.get("/resources")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["WWW-Authenticate"], 'Bearer realm="loco"')
        self.assertEqual(Rest("loco_mcp_" + "A" * 43).get("/resources").status_code, 401)
        self.assertEqual(Rest("n-importe-quoi").get("/resources").status_code, 401)
        token, _ = create(self.admin, "Robot", ["list_resources"])
        self.assertEqual(Rest(token).get("/resources").status_code, 200)

    def test_feature_disabled(self):
        token, _ = create(self.admin, "Robot", ["list_resources"])
        with transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('features_disabled',?)",
                         (json.dumps(["mcp"]),))
        r = Rest(token).get("/resources")
        self.assertEqual(r.status_code, 503)
        with transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key='features_disabled'")

    def test_index_lists_routes(self):
        token, _ = create(self.admin, "Robot", ["list_resources", "get_device_resources"])
        data = Rest(token).get("").get_json()
        self.assertEqual((data["api"], data["version"]), ("loco", "v1"))
        self.assertEqual(data["loco_version"], config.VERSION)
        self.assertEqual(data["token"]["name"], "Robot")
        granted = {row["tool"]: row["allowed"] for row in data["routes"]}
        self.assertTrue(granted["list_resources"])
        self.assertFalse(granted["list_devices"])
        self.assertEqual(Rest(token).post("").status_code, 405)      # l'index est en lecture seule

    def test_openapi_is_public(self):
        spec = Rest(None).get("/openapi.json").get_json()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("/api/v1/resources", spec["paths"])
        self.assertIn("get", spec["paths"]["/api/v1/resources"])
        self.assertIn("security", spec)                              # Bearer global

    def test_unknown_route_and_method(self):
        token, _ = create(self.admin, "Robot", ["list_devices", "list_resources"])
        client = Rest(token)
        self.assertEqual(client.get("/nope").status_code, 404)
        r = client.post("/devices")                                 # /devices est en GET seulement
        self.assertEqual(r.status_code, 405)
        self.assertEqual(r.get_json()["allowed"], ["GET"])

    def test_tool_must_be_ticked(self):
        token, _ = create(self.admin, "Lecture", ["list_devices"])
        r = Rest(token).post("/devices/1/scan")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["tool"], "scan_device")


# --------------------------------------------------------------------------- ressources

class TestResources(ApiTest):
    def test_device_resources(self):
        agent = self.linux("srv-mem", "192.168.44.10", 2 * 1024 ** 3)
        token, _ = create(self.admin, "Robot", ["get_device_resources"])
        data = Rest(token).get("/devices/srv-mem/resources").get_json()
        self.assertEqual(data["hostname"], "srv-mem")
        self.assertTrue(data["online"])
        m = data["measures"]
        self.assertEqual(m["memory"]["total_bytes"], 8 * 1024 ** 3)
        self.assertEqual(m["memory"]["used_percent"], 25.0)
        self.assertEqual(m["cpu"]["cores"], 4)
        self.assertEqual(m["disks"][0]["mount"], "/")
        self.assertFalse(m["stale"])
        self.assertEqual(m["uptime_seconds"], 123456)
        del agent

    def test_resources_history_and_no_measure(self):
        self.linux("srv-hist", "192.168.44.11", 4 * 1024 ** 3)
        token, _ = create(self.admin, "Robot", ["get_device_resources"])
        data = Rest(token).get("/devices/srv-hist/resources", {"history": "1h"}).get_json()
        self.assertEqual(data["history"]["period"], "1h")
        self.assertTrue(data["history"]["points"])
        self.assertIsNotNone(data["history"]["points"][0]["cpu_percent"])
        # Poste sans relevé : measures nul, note explicite, pas d'erreur.
        quiet = Agent(dict(LINUX, hostname="srv-muet"), "192.168.44.12")
        quiet.enroll()
        data = Rest(token).get("/devices/srv-muet/resources").get_json()
        self.assertIsNone(data["measures"])
        self.assertIn("No measure", data["note"])

    def test_list_resources_sorted_by_memory(self):
        self.linux("srv-bas", "192.168.44.20", 1 * 1024 ** 3)
        self.linux("srv-haut", "192.168.44.21", 6 * 1024 ** 3)
        token, _ = create(self.admin, "Robot", ["list_resources"])
        data = Rest(token).get("/resources", {"sort": "memory", "os": "linux"}).get_json()
        names = [d["hostname"] for d in data["devices"]]
        self.assertLess(names.index("srv-haut"), names.index("srv-bas"))
        top = data["devices"][0]
        self.assertEqual(top["hostname"], "srv-haut")
        self.assertEqual(top["measures"]["memory"]["used_percent"], 75.0)

    def test_device_not_found(self):
        token, _ = create(self.admin, "Robot", ["get_device_resources"])
        r = Rest(token).get("/devices/absent-xyz/resources")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["tool"], "get_device_resources")


# --------------------------------------------------------------------------- actions et journal

class TestActions(ApiTest):
    def test_scan_is_queued_and_audited(self):
        self.linux("srv-scan", "192.168.44.30", 2 * 1024 ** 3)
        token, _ = create(self.admin, "Opérateur", ["scan_device"])
        r = Rest(token).post("/devices/srv-scan/scan")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["queued"])
        with transaction() as conn:
            event = conn.execute("SELECT action, detail FROM audit WHERE detail LIKE '%API « Opérateur »%' "
                                 "ORDER BY id DESC").fetchone()
        self.assertEqual(event["action"], "parc.scan")
        self.assertIn("srv-scan", event["detail"])

    def test_body_too_large(self):
        token, _ = create(self.admin, "Robot", ["update_devices"])
        from loco import mcp
        huge = "x" * (mcp.MAX_BODY + 10)
        r = Rest(token).post("/updates", raw=huge, headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_body_must_be_json_object(self):
        token, _ = create(self.admin, "Robot", ["update_devices"])
        r = Rest(token).post("/updates", raw="[1,2,3]", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
