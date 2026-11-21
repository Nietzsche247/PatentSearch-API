"""s1d, PatentRef's structure layer S1-D, is a private package and is not part of this repository (release AG, gate S1D.4 a):
the fork imports it through API/lapse_s1d.py only, from the installed package, and stops with S1DMissing when the package is
missing, when a stray folder named s1d would be read instead, when it is another version or serves other unit types, or when
it was written for another claimgraph than the installed one.

  python -m pytest lapse_accounts/tests/test_s1d_package.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from API import lapse_claimgraph, lapse_s1d

ROOT = Path(__file__).resolve().parents[2]
IMPORT = re.compile(r"^\s*(?:import\s+s1d\b|from\s+s1d\b)", re.M)


def run(code, extra_path=()):
    """A child Python with the fork root first on sys.path (as the API's process has it) and the given folders before it."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([*map(str, extra_path), str(ROOT), os.environ.get("PYTHONPATH", "")]),
               PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_the_fork_carries_no_s1d():
    assert not (ROOT / "s1d").exists()
    found = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file() and (p.suffix == ".whl" or p.name == "heads.tsv"
             or "/s1d/" in "/" + p.relative_to(ROOT).as_posix())]
    assert found == [], found


def test_only_the_adapter_and_the_s1d_tests_import_s1d():
    found = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith((".git/", "docs/node_modules/")) or "/node_modules/" in rel:
            continue
        if IMPORT.search(p.read_text(encoding="utf-8", errors="replace")):
            found.append(rel)
    # the endpoint tests build their structure file with s1d's own writer, as the corpus build did
    assert sorted(found) == ["API/lapse_s1d.py", "lapse_accounts/tests/test_structure_s1d.py"], found


def test_the_installed_package_is_what_the_api_runs():
    s = lapse_s1d.s1d
    assert not Path(s.__file__).resolve().is_relative_to(ROOT)
    assert lapse_s1d.VERSION == s.__version__ == "0.3.2" and lapse_s1d.PACKAGE == "s1d 0.3.2"
    assert lapse_s1d.S1D_VERSION == lapse_s1d.S1D_VERSION_DOCUMENTED == "s1d-11"
    assert lapse_s1d.UNIT_TYPES == ("Component", "StatedProblem") and lapse_s1d.STATED_TYPES == ("StatedProblem",)
    assert lapse_s1d.GENERATOR == f"s1d 0.3.2 (claimgraph {lapse_claimgraph.__version__})"
    assert lapse_s1d.WHEEL == "s1d-0.3.2-py3-none-any.whl"
    assert lapse_s1d.DF_SHARE == 0.002 and lapse_s1d.DF_SHARE_FALLBACK == 0.0002 and lapse_s1d.POSTINGS_BUDGET == 60000
    ans = lapse_s1d.build("1234567", [("1234567", "0", "1. A cup comprising a handle and a saucer.", "", "1", "")],
                          ["It is an object of the invention to provide a cup that is easier to hold when it is hot."], [])
    assert ans["structure_version"] == "s1d-11" and ans["left_out"] == 0
    assert {u["type"] for u in ans["units"]} == {"Component", "StatedProblem"}


def test_a_missing_package_stops_the_api_at_startup_with_what_to_install():
    r = run("import sys; sys.modules['s1d'] = None\nfrom API import lapse_s1d")
    assert r.returncode != 0
    last = r.stderr.strip().splitlines()[-1]
    assert last.startswith("API.lapse_s1d.S1DMissing: PatentRef's structure layer S1-D is not available: import s1d failed"), last
    assert "pip install --no-deps --no-index s1d-0.3.2-py3-none-any.whl" in last


def test_a_stray_folder_named_s1d_is_refused(tmp_path):
    stray = tmp_path / "stray"
    (stray / "s1d").mkdir(parents=True)
    (stray / "s1d" / "__init__.py").write_text('STRUCTURE_VERSION = "s1d-11"\n__version__ = "0.3.2"\n', encoding="utf-8")
    r = run("from API import lapse_s1d", extra_path=[stray])
    assert r.returncode != 0
    last = r.stderr.strip().splitlines()[-1]
    assert "S1DMissing" in last and "is not the installed package" in last, last


def test_another_version_is_refused(tmp_path):
    other = tmp_path / "other"
    (other / "s1d").mkdir(parents=True)
    (other / "s1d" / "__init__.py").write_text('STRUCTURE_VERSION = "s1d-12"\n__version__ = "0.4.0"\nUNIT_TYPES = ("Component",)\n'
                                               'CLAIMGRAPH_NEEDED = "0.3.0"\n', encoding="utf-8")
    meta = other / "s1d-0.4.0.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text("Metadata-Version: 2.1\nName: s1d\nVersion: 0.4.0\n", encoding="utf-8")
    r = run("from API import lapse_s1d", extra_path=[other])
    assert r.returncode != 0
    last = r.stderr.strip().splitlines()[-1]
    assert "S1DMissing" in last and "s1d 0.4.0 builds s1d-12, and this API documents s1d 0.3.2 with s1d-11" in last, last
