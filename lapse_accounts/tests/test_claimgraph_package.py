"""claimgraph, PatentRef's claim parser, is a private package and is not part of this repository (release Z): the fork
imports it through API/lapse_claimgraph.py only, from the installed package, and stops with ClaimgraphMissing when the
package is missing, when a stray folder named claimgraph would be read instead, or when the package builds another
structure version than the one the API documents. The extraction trial's engine (the patentref clone's
lapse/extract/units.py) takes claimgraph's names from the same module.

  python -m pytest lapse_accounts/tests/test_claimgraph_package.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from API import lapse_claimgraph

ROOT = Path(__file__).resolve().parents[2]
IMPORT = re.compile(r"^\s*(?:import\s+claimgraph\b|from\s+claimgraph\b)", re.M)


def run(code, extra_path=()):
    """A child Python with the fork root first on sys.path (as the API's process has it) and the given folders before it."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([*map(str, extra_path), str(ROOT), os.environ.get("PYTHONPATH", "")]),
               PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_the_fork_carries_no_claimgraph():
    assert not (ROOT / "claimgraph" / "__init__.py").exists()
    assert not (ROOT / "API" / "structure_claimgraph.py").exists()
    assert (ROOT / "lapse_accounts" / "tests" / "claim_fixtures" / "manifest.json").is_file()


def test_only_the_adapter_imports_claimgraph():
    found = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((".git/", "docs/node_modules/")) or "/node_modules/" in rel:
            continue
        if IMPORT.search(p.read_text(encoding="utf-8", errors="replace")):
            found.append(rel)
    assert found == ["API/lapse_claimgraph.py"], found
    assert len(IMPORT.findall((ROOT / "API" / "lapse_claimgraph.py").read_text(encoding="utf-8"))) == 1


def test_the_installed_package_is_what_the_api_runs():
    cg = lapse_claimgraph.claimgraph
    assert not Path(cg.__file__).resolve().is_relative_to(ROOT)
    assert lapse_claimgraph.__version__ == cg.__version__ == "0.2.0"
    assert lapse_claimgraph.STRUCTURE_VERSION == lapse_claimgraph.STRUCTURE_VERSION_DOCUMENTED == "2"
    assert lapse_claimgraph.GENERATOR == "claimgraph 0.2.0"
    for name in ("ClaimRow", "build_patent", "validate_patent", "check_patent", "find_mentions", "normalize_key"):
        assert callable(getattr(lapse_claimgraph, name)), name
    g = lapse_claimgraph.build_patent("1234567", [lapse_claimgraph.ClaimRow("1234567", "0", "1. A cup comprising a handle.", "", "1"),
                                                  lapse_claimgraph.ClaimRow("1234567", "1", "2. The cup of claim 1, wherein the handle is round.", "claim 1", "2")])
    assert lapse_claimgraph.validate_patent(g) == [] and g["structure_version"] == "2"
    refs = [t for c in g["claims"] for t in c["terms"] if t.get("antecedent")]
    assert refs and all("found_in" in t and "scope" not in t for t in refs)


def test_a_missing_package_stops_the_api_at_startup_with_what_to_install():
    r = run("import sys; sys.modules['claimgraph'] = None\nfrom API import lapse_claimgraph")
    assert r.returncode != 0
    last = r.stderr.strip().splitlines()[-1]
    assert last.startswith("API.lapse_claimgraph.ClaimgraphMissing: PatentRef's claim parser is not available: import claimgraph failed"), last
    assert "pip install --no-deps --no-index claimgraph-0.2.0-py3-none-any.whl" in last


def test_a_stray_folder_named_claimgraph_is_refused(tmp_path):
    stray = tmp_path / "stray"
    (stray / "claimgraph").mkdir(parents=True)
    (stray / "claimgraph" / "__init__.py").write_text('STRUCTURE_VERSION = "2"\n__version__ = "0.2.0"\n', encoding="utf-8")
    r = run("from API import lapse_claimgraph", extra_path=[stray])
    assert r.returncode != 0
    last = r.stderr.strip().splitlines()[-1]
    assert "ClaimgraphMissing" in last and "is not the installed package" in last, last


def test_another_structure_version_is_refused(tmp_path):
    other = tmp_path / "other"
    (other / "claimgraph").mkdir(parents=True)
    (other / "claimgraph" / "__init__.py").write_text('STRUCTURE_VERSION = "1"\n__version__ = "0.1.0"\n', encoding="utf-8")
    meta = other / "claimgraph-0.1.0.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text("Metadata-Version: 2.1\nName: claimgraph\nVersion: 0.1.0\n", encoding="utf-8")
    r = run("from API import lapse_claimgraph", extra_path=[other])
    assert r.returncode != 0
    last = r.stderr.strip().splitlines()[-1]
    assert "ClaimgraphMissing" in last and "builds structure version 1, and this API documents structure version 2" in last, last


def test_the_extraction_engine_reaches_claimgraph_through_the_adapter():
    repo = os.environ.get("LAPSE_REPO_DIR", "")
    units = Path(repo) / "lapse" / "extract" / "units.py"
    if not units.is_file():
        pytest.skip("LAPSE_REPO_DIR does not name a patentref checkout with the extraction engine")
    src = units.read_text(encoding="utf-8")
    assert not IMPORT.search(src) and "from API import lapse_claimgraph" in src
