"""Géométrie du flux et message RFB standard utilisés par le navigateur."""
import base64
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js pour le module du navigateur")
class TestRemoteSizing(unittest.TestCase):
    def test_adapted_resolution_bounds_and_wire_layout(self):
        source = Path(__file__).resolve().parents[1] / "portal/static/remote-sizing.js"
        module = "data:text/javascript;base64," + base64.b64encode(source.read_bytes()).decode()
        script = r'''
import assert from 'node:assert/strict';
const {resolutionFor, resizePacket} = await import(process.argv[1]);
// « Adaptée à la fenêtre » : le bureau demandé suit les pixels physiques de la fenêtre, borné à 160×100…4096×2160.
assert.deepEqual(resolutionFor(1280,900), {width:1280,height:900});
assert.deepEqual(resolutionFor(1920,1080), {width:1920,height:1080});
assert.deepEqual(resolutionFor(8000,4000), {width:4096,height:2160});
assert.deepEqual(resolutionFor(120,80), {width:160,height:100});
assert.equal(resolutionFor(0,600), null);
assert.equal(resolutionFor(NaN,600), null);
assert.deepEqual([...resizePacket(1001,577)], [251,0,3,233,2,65,1,0,0,0,0,0,0,0,0,0,3,233,2,65,0,0,0,0]);
'''
        subprocess.run(["node", "--input-type=module", "-e", script, module],
                       check=True, capture_output=True, text=True)
