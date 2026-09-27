"""Create (or reuse) a local API key for testing and write it to <data>/test_api_key.txt.

  set DJANGO_SETTINGS_MODULE=pvapi.settings.lapse_local
  python lapse_tools/create_test_key.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pvapi.settings.lapse_local")

import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402

from API.models import APIUserKey  # noqa: E402

out = Path(settings.DATABASES["default"]["NAME"]).parent / "test_api_key.txt"
if out.exists():
    key = out.read_text().strip()
    try:
        APIUserKey.objects.get_from_key(key)
        print("existing key ok:", out)
        sys.exit(0)
    except APIUserKey.DoesNotExist:
        pass
_, key = APIUserKey.objects.create_key(name="lapse-local-test", username="lapse-local", email="lapse@localhost")
out.write_text(key)
print("created key, written to", out)
