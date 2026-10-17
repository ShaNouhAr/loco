"""Les téléchargements interrompus/corrompus ne deviennent jamais des images de test en cache."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash') and shutil.which('sha256sum'), 'Tests VM Linux')
class TestVmImageDownload(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'bin').mkdir(); (self.root / 'work').mkdir()
        self.image = b'OFFICIAL_IMAGE_BYTES'
        digest = hashlib.sha256(self.image).hexdigest()
        self.manifest = self.root / 'platforms.json'
        self.manifest.write_text(json.dumps([{'id': 'fixture', 'image': 'https://cloud.debian.org/fixture.qcow2',
                                             'checksums': 'https://cloud.debian.org/fixture.sha256'}]))
        curl = self.root / 'bin/curl'
        curl.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]; target=Path(args[args.index('-o')+1]); sums=any(a.endswith('.sha256') for a in args)
if sums:
 target.write_text("''' + digest + '''  fixture.qcow2\\n")
 sys.exit(92 if os.environ.get('MODE')=='manifest-fail' else 0)
target.write_bytes(b'PARTIAL' if os.environ.get('MODE') in ('interrupted','corrupt') else b'OFFICIAL_IMAGE_BYTES')
sys.exit(92 if os.environ.get('MODE')=='interrupted' else 0)
''')
        curl.chmod(0o755)
        self.library = Path(__file__).resolve().parents[1] / 'tests/lib-vm.sh'
        self.cached = self.root / 'cache/fixture-fixture.qcow2'

    def fetch(self, mode):
        script = '. "$1"; WORK="$2"; LOCO_VM_IMAGE_CACHE="$3"; image=$(vm_image "$4" fixture) || exit 1; printf "%s" "$image"'
        return subprocess.run(['bash', '-c', script, 'fixture', str(self.library), str(self.root / 'work'),
                               str(self.root / 'cache'), str(self.manifest)], text=True, capture_output=True,
                              env=dict(os.environ, PATH=str(self.root / 'bin') + os.pathsep + os.environ['PATH'], MODE=mode))

    def test_interrupted_transfer_in_command_substitution_does_not_publish_partial_image(self):
        self.assertNotEqual(self.fetch('interrupted').returncode, 0)
        self.assertFalse(self.cached.exists())

    def test_wrong_checksum_does_not_publish_a_successful_but_corrupt_transfer(self):
        result = self.fetch('corrupt')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Empreinte de l'image incorrecte", result.stderr)
        self.assertFalse(self.cached.exists())

    def test_manifest_failure_stops_before_downloading_the_image(self):
        self.assertNotEqual(self.fetch('manifest-fail').returncode, 0)
        self.assertFalse(self.cached.exists())
        self.assertFalse(self.cached.with_suffix(self.cached.suffix + '.part').exists())

    def test_valid_download_is_published_and_valid_cache_survives_later_image_network_failure(self):
        result = self.fetch('success')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.cached))
        self.assertEqual(self.cached.read_bytes(), self.image)
        self.assertFalse(self.cached.with_suffix(self.cached.suffix + '.part').exists())
        self.assertEqual(self.fetch('interrupted').returncode, 0)
        self.assertEqual(self.cached.read_bytes(), self.image)
