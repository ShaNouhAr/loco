"""Base tenue ouverte par le portail en service : le journal SQLite n'est plus rangé à chaque requête, les écritures
validées sans attendre le disque sont synchronisées dans la seconde, et tout redevient sûr dès que la connexion
permanente manque (ligne de commande, tests, défaut)."""
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import test_portal as fixture
from test_fleet import Agent, FleetTest, LINUX, device
from loco import config, db as dbmod, fleet, schedules, wake
from loco.db import transaction


def wait(condition, seconds=10):
    """Attend qu'une condition devienne vraie (fil de fond) ; renvoie sa dernière valeur."""
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        value = condition()
        if value:
            return value
        time.sleep(0.02)
    return condition()


def synced(path=None):
    """Instant de la dernière synchronisation notée par la connexion permanente (None : jamais)."""
    with closing(sqlite3.connect(path or config.DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (dbmod.SYNC_KEY,)).fetchone()
    return row[0] if row else None


class TempDatabase(unittest.TestCase):
    """Base à part, que rien d'autre n'ouvre : on y observe le journal (fichier -wal) sans interférence."""

    def setUp(self):
        dbmod.release()
        self.dir = tempfile.TemporaryDirectory(prefix="lr-keeper-")
        self.path = str(Path(self.dir.name, "portal.db"))
        self.create(self.path)
        for patch in (mock.patch.object(config, "DB_PATH", self.path), mock.patch.object(dbmod, "SYNC_SECONDS", 0.05),
                      mock.patch.object(dbmod, "WATCH_SECONDS", 0.1), mock.patch.object(dbmod, "RETRY_SECONDS", 0.1)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(dbmod.release)          # exécuté en premier : plus rien ne tient la base avant de l'effacer

    @staticmethod
    def create(path):
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE notes (text TEXT)")
            conn.commit()

    def write(self, text="x", relaxed=False):
        """Une requête du portail qui écrit, puis rend sa connexion (comme en fin de requête)."""
        with fixture.loco.app.test_request_context():
            conn = dbmod.db()
            mode = dbmod.relax() if relaxed else None
            level = conn.execute("PRAGMA synchronous").fetchone()[0]
            conn.execute("INSERT INTO notes VALUES (?)", (text,))
            conn.commit()
        return mode, level

    def notes(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return [r[0] for r in conn.execute("SELECT text FROM notes ORDER BY rowid")]


class TestWithoutKeeper(TempDatabase):
    def test_tools_and_tests_open_and_close_the_database_as_before(self):
        mode, level = self.write(relaxed=True)
        self.assertEqual((mode, level), (False, 2))             # pas de connexion permanente : la validation attend le disque
        self.assertFalse(os.path.exists(self.path + "-wal"))     # dernière connexion fermée : journal recopié et rangé
        self.assertIsNone(synced())
        self.assertEqual(self.notes(), ["x"])

    def test_release_without_keeper_is_harmless(self):
        dbmod.release()
        dbmod.release()


class TestKeeper(TempDatabase):
    def test_the_journal_is_no_longer_put_away_after_each_request(self):
        self.assertTrue(dbmod.keep_open())
        self.write("a")
        self.assertGreater(os.path.getsize(self.path + "-wal"), 0)       # la base reste tenue : rien n'est recopié ici
        self.write("b")
        self.assertEqual(self.notes(), ["a", "b"])
        dbmod.release()
        self.assertFalse(os.path.exists(self.path + "-wal"))             # arrêt du service : rangé, comme avant
        self.assertEqual(self.notes(), ["a", "b"])

    def test_relaxed_writes_are_synced_within_the_window(self):
        dbmod.keep_open()
        mode, level = self.write(relaxed=True)
        self.assertEqual((mode, level), (True, 1))               # validée sans attendre le disque…
        stamp = wait(synced)
        self.assertIsNotNone(stamp)                              # … puis synchronisée par la connexion permanente
        self.assertLessEqual(abs(int(stamp) - time.time()), 5)

    def test_any_request_that_wrote_is_followed_by_a_sync_and_a_reader_is_not(self):
        dbmod.keep_open()
        with fixture.loco.app.test_request_context():
            dbmod.db().execute("SELECT COUNT(*) FROM notes").fetchone()
        time.sleep(0.4)
        self.assertIsNone(synced())                              # lecture seule : la connexion permanente dort
        self.write()                                             # écriture en mode sûr : suivie aussi (relais distant…)
        self.assertIsNotNone(wait(synced))

    def test_durable_puts_the_connection_back_in_safe_mode(self):
        dbmod.keep_open()
        with fixture.loco.app.test_request_context():
            conn = dbmod.db()
            self.assertTrue(dbmod.relax())
            conn.execute("INSERT INTO notes VALUES ('relâchée')")
            conn.commit()
            dbmod.durable()
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            conn.execute("INSERT INTO notes VALUES ('sûre')")
            conn.commit()
        self.assertEqual(self.notes(), ["relâchée", "sûre"])

    def test_relax_never_interrupts_a_transaction_already_open(self):
        dbmod.keep_open()
        with fixture.loco.app.test_request_context():
            conn = dbmod.db()
            conn.execute("INSERT INTO notes VALUES ('en cours')")
            self.assertFalse(dbmod.relax())                      # SQLite refuserait : on reste en mode sûr, sans erreur
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            conn.commit()
        self.assertEqual(self.notes(), ["en cours"])

    def test_keep_open_twice_keeps_a_single_connection(self):
        dbmod.keep_open()
        first = dbmod._keeper
        dbmod.keep_open()
        self.assertIs(dbmod._keeper, first)
        dbmod.release()
        self.assertFalse(first.thread.is_alive())
        self.assertTrue(dbmod.keep_open())                       # et le service peut la reprendre
        self.assertIsNot(dbmod._keeper, first)

    def test_safe_mode_comes_back_while_the_keeper_is_in_trouble(self):
        dbmod.keep_open()
        keeper = dbmod._keeper
        with mock.patch.object(dbmod._Keeper, "_sync", side_effect=sqlite3.OperationalError("disk I/O error")), \
                self.assertLogs("loco.db", level="WARNING"):
            self.write(relaxed=True)
            self.assertTrue(wait(lambda: not keeper.healthy))
            mode, level = self.write(relaxed=True)
            self.assertEqual((mode, level), (False, 2))           # en défaut : chaque validation attend de nouveau le disque
        self.assertTrue(wait(lambda: keeper.healthy))             # rétablie : elle rattrape la synchronisation manquée
        self.assertIsNotNone(wait(synced))
        self.assertEqual(self.write(relaxed=True), (True, 1))

    def test_a_journal_grown_large_is_copied_back_and_emptied(self):
        with mock.patch.object(dbmod, "WAL_LIMIT", 64 * 1024):
            dbmod.keep_open()
            with fixture.loco.app.test_request_context():
                conn = dbmod.db()
                conn.execute("PRAGMA wal_autocheckpoint = 0")    # comme si des lecteurs gênaient chaque point de contrôle
                conn.executemany("INSERT INTO notes VALUES (?)", [("x" * 2000,) for _ in range(200)])
                conn.commit()
            self.assertGreater(os.path.getsize(self.path + "-wal"), 64 * 1024)
            self.assertTrue(wait(lambda: os.path.getsize(self.path + "-wal") == 0))
        self.assertEqual(len(self.notes()), 200)                 # rien de perdu : tout est dans la base
        self.assertTrue(dbmod._keeper.healthy)

    def test_a_database_replaced_under_the_portal_is_taken_again(self):
        dbmod.keep_open()
        self.write("ancienne")
        fresh = self.path + ".restore"
        self.create(fresh)
        os.replace(fresh, self.path)                             # restauration : autre fichier au même chemin
        for suffix in ("-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)
        keeper = dbmod._keeper
        self.assertTrue(wait(lambda: keeper.held == dbmod._db_file()))
        self.write("nouvelle", relaxed=True)
        self.assertEqual(self.notes(), ["nouvelle"])
        self.assertIsNotNone(wait(synced))                       # et c'est bien la nouvelle base qui est synchronisée

    def test_a_missing_database_is_waited_for(self):
        dbmod.keep_open()
        keeper = dbmod._keeper
        gone = self.path + ".gone"
        with self.assertLogs("loco.db", level="WARNING"):
            os.replace(self.path, gone)
            self.assertTrue(wait(lambda: not keeper.healthy))
            with closing(sqlite3.connect(":memory:")) as other:
                self.assertFalse(dbmod.relax(other))
        os.replace(gone, self.path)
        self.assertTrue(wait(lambda: keeper.healthy))

    @unittest.skipUnless(hasattr(os, "fork"), "fork absent de ce système")
    def test_a_forked_process_does_not_rely_on_the_parent_keeper(self):
        dbmod.keep_open()
        self.assertTrue(dbmod._keeper.alive())
        pid = os.fork()
        if pid == 0:                                             # enfant : le fil du parent n'existe pas ici
            code = 1
            try:
                with closing(sqlite3.connect(":memory:")) as other:
                    alone = not dbmod._keeper.alive() and dbmod.relax(other) is False
                code = 0 if alone and dbmod.keep_open() and dbmod._keeper.pid == os.getpid() else 2
                dbmod.release()
            finally:
                os._exit(code)
        self.assertEqual(os.waitpid(pid, 0)[1], 0)
        self.assertTrue(dbmod._keeper.alive())                   # celui du parent n'a pas bougé
        self.assertEqual(self.write(relaxed=True), (True, 1))


class TestCheckin(FleetTest):
    """Contact d'un agent : présence et mesures validées sans attendre le disque, commandes toujours en mode sûr."""

    def setUp(self):
        super().setUp()
        dbmod.release()
        self.addCleanup(dbmod.release)
        with transaction() as conn:
            conn.execute("DELETE FROM device_commands")
            conn.execute("DELETE FROM settings WHERE key=?", (dbmod.SYNC_KEY,))
        self.agent = Agent(LINUX, "192.168.43.60")
        self.agent.enroll()

    def levels(self, inventory=None):
        """(mode à l'enregistrement de la présence, mode au traitement des commandes) pendant un contact."""
        seen = {}
        real_arrived, real_materialize = wake.arrived, schedules.materialize

        def arrived(conn, dev_id):
            seen["presence"] = (conn.execute("PRAGMA synchronous").fetchone()[0], conn.in_transaction)
            return real_arrived(conn, dev_id)

        def materialize(conn):
            seen["commands"] = (conn.execute("PRAGMA synchronous").fetchone()[0], conn.in_transaction)
            return real_materialize(conn)

        with mock.patch.object(wake, "arrived", arrived), mock.patch.object(schedules, "materialize", materialize):
            r = self.agent.checkin(inventory)
        self.assertEqual(r.status_code, 200)
        return seen["presence"], seen["commands"], r.get_json()

    def test_without_the_keeper_a_contact_waits_for_the_disk_as_before(self):
        presence, commands, _answer = self.levels()
        self.assertEqual((presence, commands), ((2, True), (2, False)))

    def test_a_plain_contact_does_not_wait_for_the_disk_and_is_synced_after(self):
        dbmod.keep_open()
        with mock.patch.object(dbmod, "SYNC_SECONDS", 0.05):
            presence, commands, answer = self.levels()
            self.assertEqual(presence, (1, True))                # présence : sans attendre le disque
            self.assertEqual(commands, (2, False))               # commandes : mode sûr, présence déjà validée
            self.assertEqual(answer["commands"], [])
            self.assertIsNotNone(wait(synced))
        self.assertTrue(device(self.agent.uid)["last_seen"])

    def test_an_inventory_waits_for_the_disk(self):
        dbmod.keep_open()
        presence, commands, _answer = self.levels({"updates": []})
        self.assertEqual((presence[0], commands[0]), (2, 2))

    def test_a_transmitted_command_is_written_in_safe_mode(self):
        dbmod.keep_open()
        dev = device(self.agent.uid)
        self.admin.post(f"/fleet/{dev['id']}/action", {"action": "scan"}, page=f"/fleet/{dev['id']}")
        modes = []
        real = fleet._payload

        def payload(dev, cmd):
            modes.append(dbmod.db().execute("PRAGMA synchronous").fetchone()[0])
            return real(dev, cmd)

        with mock.patch.object(fleet, "_payload", payload):
            presence, commands, answer = self.levels()
        self.assertEqual(presence[0], 1)
        self.assertEqual(modes, [2])                             # notée « transmise » en mode sûr, avant la réponse
        self.assertEqual([c["action"] for c in answer["commands"]], ["scan"])
        with transaction() as conn:
            self.assertEqual(conn.execute("SELECT state FROM device_commands").fetchone()[0], "sent")


if __name__ == "__main__":
    unittest.main()
