"""Programme de l'agent pour les tests : le vrai service de fichiers et le vrai serveur RFB de loco-client.py,
branché sur un écran synthétique qui note les clics et les frappes reçus (fichier LOCO_TEST_EVENTS)."""
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_agent():
    spec = importlib.util.spec_from_file_location("loco_client", os.path.join(ROOT, "portal", "agents", "loco-client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Screen:
    """Dégradé connu : pixel (x, y) = rouge 200, vert 3*y, bleu 2*x (octets BGRX)."""
    width, height, interval = int(os.environ.get("LOCO_TEST_WIDTH", "64")), 40, 0

    def __init__(self):
        self.events = open(os.environ["LOCO_TEST_EVENTS"], "a")

    def grab(self):
        return b"".join(bytes(((2 * x) % 256, (3 * y) % 256, 200, 0)) for y in range(self.height) for x in range(self.width))

    def log(self, *event):
        self.events.write(json.dumps(event) + "\n")
        self.events.flush()

    def key(self, sym, down):
        self.log("key", sym, down)
        return True

    def motion(self, x, y):
        self.log("motion", x, y)

    def button(self, number, down):
        self.log("button", number, down)

    def set_cursor(self, enabled):
        pass

    def cursor(self):
        return None

    def flush(self):
        pass

    def close(self):
        pass


if __name__ == "__main__":
    agent = load_agent()
    if sys.argv[1] == "desktop":
        screen = Screen()
        try:
            agent._rfb_serve(screen)
        except EOFError:
            pass
        finally:
            screen.events.close()
    elif sys.argv[1] == "files":
        sys.exit(agent.files_service())
