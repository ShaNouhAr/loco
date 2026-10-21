"""Outil : textes marqués sans traduction anglaise, en JSON {texte: emplacement} (python3 tests/i18n_missing.py)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_i18n  # noqa: E402
from loco import i18n  # noqa: E402

catalog = i18n.catalog("en")
missing = {k: v for k, v in test_i18n.all_messages().items() if not catalog.get(k)}
print(json.dumps(missing, ensure_ascii=False, indent=0))
