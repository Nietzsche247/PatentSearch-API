"""The registry core comes from the patentref clone, never from this repo: `patentref_registry` in
LAPSE_REPO_DIR/registry/py (/srv/lapse/registry/py on the box), the way API/lapse_group.py takes the status engine from
the same clone. One implementation of the canonical form, the bundle, the PII scan and the database calls, tested in
the patentref repo against a local Postgres; this app adds HTTP, the Supabase JWT check and per-address limits."""
import importlib
import os
import sys

from django.conf import settings

_mods = {}


def repo_dir():
    return getattr(settings, "LAPSE_REPO_DIR", None) or os.environ.get("LAPSE_REPO_DIR", "/srv/lapse")


def module(name):
    """patentref_registry.<name>, importing it from the clone on first use."""
    if name in _mods:
        return _mods[name]
    d = os.path.join(repo_dir(), "registry", "py")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    mod = importlib.import_module(f"patentref_registry.{name}")
    _mods[name] = mod
    return mod
