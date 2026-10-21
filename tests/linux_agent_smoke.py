"""Exécute les primitives de l'agent avec le Python système, notamment Python 3.6 sur Rocky 8."""
import importlib.util
from pathlib import Path
import ssl
import sys

source = Path(__file__).resolve().parents[1] / 'portal' / 'agents' / 'loco-client.py'
spec = importlib.util.spec_from_file_location('loco_client', str(source))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
agent.LANG_EN = False  # messages en français (langue de référence)

ctx = agent.context()
assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
if hasattr(ssl, 'TLSVersion'):
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
else:
    assert ctx.options & ssl.OP_NO_TLSv1 and ctx.options & ssl.OP_NO_TLSv1_1
identity = agent.identity()
assert identity['hostname'] and identity['os']['family'] in ('debian', 'rhel')
assert identity['addresses'], 'aucune adresse de poste détectée'
code, output = agent.run([sys.executable, '-c',
                          'import sys; print("sortie"); print("erreur", file=sys.stderr); sys.exit(7)'])
assert code == 7 and 'sortie' in output and 'erreur' in output, (code, output)
assert agent.run(['/loco-agent-test-inexistant'])[0] == 127
assert agent.run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.1)[0] == 124
print('Agent Linux : TLS vérifié, identité et processus fonctionnels (Python ' + sys.version.split()[0] + ')')
