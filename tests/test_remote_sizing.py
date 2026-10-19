"""Géométrie du flux et message RFB standard utilisés par le navigateur."""
import base64
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js pour le module du navigateur")
class TestRemoteSizing(unittest.TestCase):
    def test_native_limits_text_console_growth_and_wire_layout(self):
        source = Path(__file__).resolve().parents[1] / "portal/static/remote-sizing.js"
        module = "data:text/javascript;base64," + base64.b64encode(source.read_bytes()).decode()
        script = r'''
import assert from 'node:assert/strict';
const {resolutionFor, resizePacket} = await import(process.argv[1]);
assert.deepEqual(resolutionFor(3840,2160,1280,900,'auto'), {width:1280,height:720});
assert.deepEqual(resolutionFor(1280,800,4096,2160,'auto'), {width:1280,height:800});
assert.deepEqual(resolutionFor(3840,2160,800,600,'native'), {width:3840,height:2160});
assert.deepEqual(resolutionFor(1024,640,1920,1080,'auto',true), {width:1920,height:1080});
assert.deepEqual(resolutionFor(1024,640,8000,4000,'auto',true), {width:4000,height:2160});
assert.equal(resolutionFor(0,0,800,600,'auto'), null);
assert.equal(resolutionFor(1024,640,NaN,600,'auto'), null);
assert.deepEqual([...resizePacket(1001,577)], [251,0,3,233,2,65,1,0,0,0,0,0,0,0,0,0,3,233,2,65,0,0,0,0]);
'''
        subprocess.run(["node", "--input-type=module", "-e", script, module],
                       check=True, capture_output=True, text=True)
