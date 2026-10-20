"""Gestionnaire de fichiers : ouverture de session relayée (réponse JSON), garde de version et de droit,
onglet de la fiche, et relais d'octets vers l'agent et retour (mêmes paquets acquittés que les terminaux)."""

import base64
import json
import struct
import unittest

import test_permissions
from test_fleet import FleetTest, Agent, LINUX, device
from loco.db import transaction


def frame(header, data=b""):
    blob = json.dumps(header).encode()
    return struct.pack(">II", len(blob), len(data)) + blob + data


class TestRemoteFiles(FleetTest):
    def setUp(self):
        super().setUp()
        self.agent = Agent(LINUX, "192.168.43.61")               # agent_version = version courante (>= 2.23)
        self.agent.enroll()
        self.dev_id = device(self.agent.uid)["id"]

    def open(self, browser=None, page="/"):
        return (browser or self.admin).post(f"/parc/{self.dev_id}/session", {"kind": "files"}, page=page)

    def test_open_returns_a_session_and_io_url_without_a_page(self):
        response = self.open()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("sid", body)
        self.assertEqual(body["io"], f"/parc/session/{body['sid']}/io")
        with transaction() as conn:
            row = conn.execute("SELECT kind,state FROM remote_sessions WHERE id=?", (body["sid"],)).fetchone()
            self.assertEqual((row["kind"], row["state"]), ("files", "waiting"))
            detail = conn.execute("SELECT detail FROM audit WHERE action='parc.session.ouverture' AND detail LIKE ?",
                                   ("%" + body["sid"] + "%",)).fetchone()[0]
        self.assertIn("files", detail)

    def test_old_agent_is_refused(self):
        stale = Agent(dict(LINUX, agent_version="2.22.0", hostname="old-linux"), "192.168.43.62")
        stale.enroll()
        response = self.admin.post(f"/parc/{device(stale.uid)['id']}/session", {"kind": "files"})
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"2.23", response.data)

    def test_tab_follows_version_and_right(self):
        page = self.admin.c.get(f"/parc/{self.dev_id}").get_data(as_text=True)
        self.assertIn('data-tab="fichiers"', page)
        self.assertIn('id="file-manager"', page)
        self.assertIn("files.js", page)
        # Agent trop ancien : pas d'onglet.
        stale = Agent(dict(LINUX, agent_version="2.22.0", hostname="old-linux"), "192.168.43.62")
        stale.enroll()
        old_page = self.admin.c.get(f"/parc/{device(stale.uid)['id']}").get_data(as_text=True)
        self.assertNotIn('data-tab="fichiers"', old_page)
        # Compte sans le droit « remote.files » mais avec la vue du parc : pas d'onglet, ouverture refusée.
        self.addCleanup(test_permissions.TestPermissions.cleanup_accounts, self)
        viewer = test_permissions.TestPermissions.browser(
            self, "nofiles", test_permissions.TestPermissions.profile(self, "nofiles", {"fleet.view"}))
        limited = viewer.c.get(f"/parc/{self.dev_id}").get_data(as_text=True)
        self.assertNotIn('data-tab="fichiers"', limited)
        self.assertEqual(self.open(browser=viewer, page="/compte").status_code, 403)

    def test_bytes_relay_to_the_agent_and_back(self):
        sid = self.open().get_json()["sid"]
        request = base64.b64encode(frame({"op": "hello", "id": 1})).decode()
        self.assertEqual(self.admin.post(f"/parc/session/{sid}/io", {"seq": 1, "data": request}).status_code, 200)
        # L'agent récupère la requête du navigateur (direction « in »).
        descriptor = next(d for d in self.exchange(sid).get_json()["sessions"] if d["id"] == sid)
        self.assertEqual(descriptor["kind"], "files")
        self.assertEqual(descriptor["packets"], [{"seq": 1, "data": request}])
        # L'agent répond (direction « out ») ; le navigateur la lit une fois, puis l'acquitte.
        answer = base64.b64encode(frame({"id": 1, "ok": True, "os": "linux"})).decode()
        self.exchange(sid, [{"id": sid, "ack": 1, "seq": 1, "data": answer, "state": "active"}])
        self.assertEqual(self.admin.post(f"/parc/session/{sid}/io", {}).get_json()["packets"],
                         [{"seq": 1, "data": answer}])
        self.assertEqual(self.admin.post(f"/parc/session/{sid}/io", {"ack": 1}).get_json()["packets"], [])

    def test_two_files_sessions_per_device_at_most(self):
        self.assertEqual(self.open().status_code, 200)
        self.assertEqual(self.open().status_code, 200)
        self.assertEqual(self.open().status_code, 409)

    def exchange(self, sid, updates=None):
        return self.agent.post("/agent/api/remote", {"sessions": updates or [{"id": sid, "state": "active"}]}, self.agent.token)


if __name__ == "__main__":
    unittest.main()
