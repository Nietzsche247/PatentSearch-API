"""Gate 2.11: the root files, the error docs, the build facts endpoint, RFC 9457 problem details by content
negotiation, and the footer generated from LICENSES.md (gate 1.11)."""
import json
import os
from pathlib import Path

import pytest
from django.conf import settings

from lapse_accounts import public

LICENSES = """# License register

## 2. Tables
| a | b |

## Attribution lines

- Patent data from PatentsView and the USPTO, used under CC BY 4.0. Test line one.
- Second test line.

## 6. Something after
- not an attribution line
"""


@pytest.fixture
def public_dir(tmp_path, monkeypatch):
    (tmp_path / "llms.txt").write_text("# PatentRef\n\n> test guide\n", encoding="utf-8")
    (tmp_path / "llms-full.txt").write_text("# PatentRef full\n", encoding="utf-8")
    (tmp_path / "openapi.json").write_text(json.dumps({"openapi": "3.1.0", "paths": {}}), encoding="utf-8")
    (tmp_path / "changes.txt").write_text("# PatentRef API changes\n", encoding="utf-8")
    lic = tmp_path / "LICENSES.md"
    lic.write_text(LICENSES, encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_PUBLIC_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "LAPSE_LICENSES_PATH", str(lic), raising=False)
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    return tmp_path


def test_root_files_served(client, public_dir):
    r = client.get("/llms.txt")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/plain") and r.content.startswith(b"# PatentRef")
    r = client.get("/llms-full.txt")
    assert r.status_code == 200 and b"full" in r.content
    r = client.get("/openapi.json")
    assert r.status_code == 200 and r["Content-Type"] == "application/json" and r.json()["openapi"] == "3.1.0"
    r = client.get("/LICENSES.md")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/markdown") and b"Attribution lines" in r.content
    assert r["Access-Control-Allow-Origin"] == "*"
    # the public change list llms.txt links (2.11 off-box rerun 2026-10-11, finding C: a version signal)
    r = client.get("/changes.txt")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/plain") and r.content.startswith(b"# PatentRef API changes")


def test_root_file_missing_is_404(client, public_dir):
    (public_dir / "llms-full.txt").unlink()
    r = client.get("/llms-full.txt")
    assert r.status_code == 404 and r["Cache-Control"] == "no-store"
    assert client.get("/robots.txt").status_code == 404  # not in the map: falls through to upstream routing


def test_error_docs(client):
    r = client.get("/docs/errors/")
    assert r.status_code == 200 and b"ERR_Q" in r.content and b"ERR_NOT_IMPLEMENTED" in r.content
    r = client.get("/docs/errors/ERR_KEY")
    assert r.status_code == 200 and r.content.startswith(b"ERR_KEY\n403")
    assert client.get("/docs/errors/err_q/").status_code == 200
    assert client.get("/docs/errors/NOPE").status_code == 404


def test_error_docs_carry_no_internal_references(client):
    """2.11 off-box run 2026-10-11, doc defect 6: the public error pages named "PatentRef checklist 1.5"."""
    pages = [client.get("/docs/errors/").content.decode("utf-8")]
    pages += [client.get(f"/docs/errors/{code}").content.decode("utf-8") for code in public.ERROR_CODES]
    for text in pages:
        low = text.lower()
        for word in ("checklist", "gate ", "scaffold", "ops/", "handoff", "desk"):
            assert word not in low, (word, text[:80])
        assert chr(0x2014) not in text
    assert "does not count toward the monthly allowance" in public.ERROR_CODES["ERR_NOT_IMPLEMENTED"]
    assert "a v that never existed" in public.ERROR_CODES["NOT_FOUND"]


def test_error_docs_agree_with_the_guide(client):
    """2.11 off-box rerun 2026-10-11, findings B and D: the index says which codes the header carries (NOT_FOUND among
    them), and ERR_Q's sort rule reads as llms-full.txt section 2 does (text+keyword fields are sortable)."""
    index = client.get("/docs/errors/").content.decode("utf-8")
    assert public.ERROR_INDEX_NOTE in index
    assert "NOT_FOUND on /api/v1/lapse/status/ for a v that never existed" in public.ERROR_INDEX_NOTE
    assert "a sort on a text field without a keyword form" in public.ERROR_CODES["ERR_Q"]
    assert "q or s on the lapse group" in public.ERROR_CODES["ERR_Q"]
    assert "GET /api/v1/meta/ lists the data sets the build lacks" in public.ERROR_CODES["ERR_NOT_IMPLEMENTED"]


def test_footer_from_licenses(public_dir):
    lines = public.footer_lines()
    assert lines == ["Patent data from PatentsView and the USPTO, used under CC BY 4.0. Test line one.", "Second test line."]
    (public_dir / "LICENSES.md").write_text("# no section\n", encoding="utf-8")
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    assert public.footer_lines() == public.FALLBACK_FOOTER


REAL_LICENSES_COPY = Path(__file__).parent / "fixtures" / "LICENSES.md"  # verbatim copy of patentref LICENSES.md (3bf3b3f)


def _real_registers():
    """The register as served: the patentref clone on the box (settings default /srv/lapse/LICENSES.md),
    PATENTREF_LICENSES when set, and always the vendored copy, so the test runs off the box too."""
    paths = []
    for p in (os.environ.get("PATENTREF_LICENSES"), "/srv/lapse/LICENSES.md"):
        if p and Path(p).is_file():
            paths.append(Path(p))
    paths.append(REAL_LICENSES_COPY)
    return paths


@pytest.mark.parametrize("register", _real_registers(), ids=str)
def test_footer_from_the_real_register(register, monkeypatch):
    """Gate 1.11 re-audit 2026-10-08: the real file says "## 5. Attribution lines", which the first regex
    never matched, so every page showed the fallback. The footer must be the register's bullets."""
    text = register.read_text(encoding="utf-8")
    heading = [ln for ln in text.splitlines() if ln.startswith("## ") and "Attribution lines" in ln]
    assert heading, "the register has no Attribution lines section"
    want = []
    in_section = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            in_section = ln == heading[0]
            continue
        if in_section and ln.strip().startswith("- "):
            want.append(ln.strip()[2:].strip())
    assert len(want) >= 2
    monkeypatch.setattr(settings, "LAPSE_LICENSES_PATH", str(register), raising=False)
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    got = public.footer_lines()
    assert got == want
    assert got != public.FALLBACK_FOOTER
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})


@pytest.mark.parametrize("heading", ["## Attribution lines", "## 5. Attribution lines", "## 5 Attribution lines", "## 12.1. Attribution lines"])
def test_footer_heading_forms(heading, tmp_path, monkeypatch):
    lic = tmp_path / "LICENSES.md"
    lic.write_text(f"# r\n\n## 4. Before\n- no\n\n{heading}\n\nIntro sentence.\n\n- One.\n- Two.\n\n## 6. After\n- no\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_LICENSES_PATH", str(lic), raising=False)
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    assert public.footer_lines() == ["One.", "Two."]
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})


def test_footer_on_pages(client, public_dir):
    r = client.get("/api/v1/meta/signup/")
    assert r.status_code == 200 and b"Test line one." in r.content and b"Second test line." in r.content
    assert b'href="/LICENSES.md"' in r.content
    r = client.get("/api/v1/meta/status/")
    assert r.status_code == 200 and b"Test line one." in r.content


def test_meta_index(client, public_dir):
    r = client.get("/api/v1/meta/")
    assert r.status_code == 200
    body = r.json()
    assert body["data_version"] and r["X-Data-Version"] == body["data_version"]
    assert body["licenses_url"].endswith("/LICENSES.md") and body["openapi"].endswith("/openapi.json")
    assert body["attribution"].startswith("Patent data from PatentsView")
    assert isinstance(body["delta"], list) and isinstance(body["superseded_versions"], list)
    assert r["Cache-Control"] == "public, max-age=60"
    # 2026-10-11 audit 2, finding G1: no internal machinery in the public body; the test file serves only `patents`, so
    # every data set a build can lack is listed, the four classification sets included
    text = json.dumps(body).lower()
    for word in ("checklist", "gate", "scaffold", "ops/", "handoff", "desk", "count_deltas", "runner", "deploy.pins"):
        assert word not in text, word
    assert body["delta_note"] == public.DELTA_NOTE
    assert [d.split(":")[0] for d in body["deferred"]] == ["publications", "cpc_classes", "cpc_subclasses", "uspc_mainclasses", "wipo"]


def test_deferred_follows_the_served_indices():
    assert public.deferred_sets(None) == [t for _, t in public.DEFERRABLE_SETS]
    have = {"patents", "cpc_classes", "wipo", "publications"}
    assert [d.split(":")[0] for d in public.deferred_sets(have)] == ["cpc_subclasses", "uspc_mainclasses"]
    assert public.deferred_sets({i for i, _ in public.DEFERRABLE_SETS}) == []
    assert "patents" in public.served_indices()


def test_meta_index_reads_sidecars(client, public_dir, tmp_path, monkeypatch):
    version = client.get("/api/v1/meta/health/").json()["data_version"]
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    data_dir = tmp_path / "lapse"
    data_dir.mkdir()
    side = {"data_version": version, "build_id": version + "+abcdef12", "release": "20261006", "built_at": "2026-10-07T10:14:08Z",
            "sha256": {"api": "ab" * 32, "catalog": "cd" * 32}, "swapped": True, "swapped_at": "2026-10-07T14:08:00Z",
            "previous": "snapshot_20261006.1.db",
            "counts": {"api": [["patents", 9454161, 9454161, "equal"], ["new_table", None, 5, "no current"]], "catalog": []},
            "fee": {"current": {"lapsed": 3071358}, "next": {"lapsed": 3075179}}}
    (api_dir / "snapshot_current.json").write_text(json.dumps(side), encoding="utf-8")
    (data_dir / "refresh_versions.json").write_text(json.dumps({"builds": [
        {"data_version": "20260929.1", "status": "READY"}, {"data_version": "20261006.1", "status": "READY"},
        {"data_version": version, "status": "READY"}, {"data_version": "20261005.9", "status": "FAILED"}]}), encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"api_dir": str(api_dir), "lapse_data": str(data_dir),
                                                          "current_sidecar": str(api_dir / "snapshot_current.json")}, raising=False)
    body = client.get("/api/v1/meta/").json()
    assert body["as_of"] == "2026-10-06" and body["refreshed_at"] == "2026-10-07T14:08:00Z" and body["built_at"] == "2026-10-07T10:14:08Z"
    assert body["snapshot_sha256"] == "ab" * 32 and body["previous_version"] == "20261006.1"
    assert body["superseded_versions"] == ["20260929.1", "20261006.1"]
    assert body["delta"][0] == {"table": "patents", "current": 9454161, "next": 9454161, "added": 0, "removed": 0, "verdict": "equal"}
    assert body["delta"][1]["added"] is None and body["fee"]["next"]["lapsed"] == 3075179


def test_superseded_versions_leave_out_the_next_build(tmp_path):
    from lapse_accounts.public import served_versions, superseded_versions

    builds = [{"data_version": "20260929.1", "status": "READY"}, {"data_version": "20261006.1", "status": "READY"},
              {"data_version": "20261006.2", "status": "READY"}, {"data_version": "20261006.3", "status": "FAILED"},
              {"data_version": "20261006.4", "status": "READY_WITH_FLAGS"}]
    # gate 2.7: 20261006.4 built and waiting for its swap was listed as superseded while 20261006.2 served
    assert superseded_versions(builds, "20261006.2") == ["20260929.1", "20261006.1"]
    # once it serves, the one before it is superseded
    assert superseded_versions(builds, "20261006.4") == ["20260929.1", "20261006.1", "20261006.2"]
    # a newer build that served once and was rolled back stays superseded (its permalinks keep the banner)
    journal = tmp_path / "swap_journal.jsonl"
    journal.write_text("\n".join(json.dumps(j) for j in [
        {"op": "swap", "data_version": "20261006.4"}, {"op": "rollback", "data_version": "20261006.2"},
        {"op": "catalog_swap", "data_version": "20991231.1"}]) + "\nnot json\n", encoding="utf-8")
    served = served_versions(str(tmp_path))
    assert served == {"20261006.4", "20261006.2"}
    assert superseded_versions(builds, "20261006.2", served) == ["20260929.1", "20261006.1", "20261006.4"]
    # a version not of the form YYYYMMDD.N: every other READY build, as before
    assert superseded_versions(builds, "full2") == ["20260929.1", "20261006.1", "20261006.2", "20261006.4"]
    assert served_versions(str(tmp_path / "missing")) == set()


def _get(client, path, key=None, accept=None, **qs):
    headers = {}
    if key:
        headers["HTTP_X_API_KEY"] = key
    if accept:
        headers["HTTP_ACCEPT"] = accept
    return client.get(path, qs, **headers)


def test_problem_details_only_on_accept(client, user_key):
    _, key, _ = user_key
    # the upstream form, byte for byte, when the client does not ask for problem+json
    r = _get(client, "/api/v1/patent/", key, q="{not json")
    assert r.status_code == 400 and r.content == b'{"error":true}' and r["X-Status-Reason-Code"] == "ERR_Q"
    r = _get(client, "/api/v1/patent/", key, "application/json", q="{not json")
    assert r.content == b'{"error":true}'
    # the problem form when asked, headers unchanged
    r = _get(client, "/api/v1/patent/", key, "application/problem+json", q="{not json")
    assert r.status_code == 400 and r["Content-Type"] == "application/problem+json"
    body = r.json()
    assert body["type"] == "https://patentref.io/docs/errors/ERR_Q" and body["title"] == "Bad Request" and body["status"] == 400
    assert body["detail"] == r["X-Status-Reason"] and body["code"] == "ERR_Q" and body["instance"] == "/api/v1/patent/"
    assert body["data_version"] == r["X-Data-Version"]
    assert r["X-Status-Reason-Code"] == "ERR_Q" and "Accept" in r["Vary"]
    assert int(r["Content-Length"]) == len(r.content)
    # a 200 is never touched
    r = _get(client, "/api/v1/patent/", key, "application/problem+json, application/json", q='{"patent_id":"10000000"}')
    assert r.status_code == 200 and r.json()["error"] is False and r["Content-Type"].startswith("application/json")


def test_problem_details_403_404_429(client, user_key):
    _, key, _ = user_key
    r = _get(client, "/api/v1/patent/", None, "application/problem+json", q='{"patent_id":"10000000"}')
    assert r.status_code == 403 and r["Content-Type"] == "application/problem+json"
    assert r.json()["code"] == "ERR_KEY" and r.json()["type"].endswith("/ERR_KEY")
    r = _get(client, "/api/v1/patent/99999999/", key, "application/problem+json")
    assert r.status_code == 404
    b = r.json()
    assert b["status"] == 404 and b["detail"] == "Not found." and b["code"] == "NOT_FOUND"
    r = _get(client, "/api/v1/patent/99999999/", key)
    assert r.status_code == 404 and r.json() == {"detail": "Not found."}
    # throttled: the Free key's monthly cap in the test settings is 5
    last = None
    for _ in range(8):
        last = _get(client, "/api/v1/patent/", key, "application/problem+json", q='{"patent_id":"10000000"}')
        if last.status_code == 429:
            break
    assert last.status_code == 429 and last["Content-Type"] == "application/problem+json"
    b = last.json()
    assert b["code"] == "THROTTLED" and b["retry_after"] == int(last["Retry-After"]) and b["detail"].startswith("Request was throttled")


def test_problem_details_501(client, user_key):
    _, key, _ = user_key
    r = _get(client, "/api/v1/publication/", key, "application/problem+json", q='{"document_number":"1"}')
    assert r.status_code == 501 and r["Content-Type"] == "application/problem+json"
    b = r.json()
    assert b["code"] == "ERR_NOT_IMPLEMENTED" and "pre-grant publications are deferred" in b["detail"]
    assert r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"
    r = _get(client, "/api/v1/publication/", key, q='{"document_number":"1"}')
    assert r.status_code == 501 and r.content == b'{"error":true}'
    # finding G1: the reason names the data set and, for a classification set, where each patent carries it; no internal reference
    assert r["X-Status-Reason"] == "Endpoint not implemented: the 'publications' data set is not in this snapshot; pre-grant publications are deferred to a later build"
    r = _get(client, "/api/v1/cpc_class/", key)
    assert r.status_code == 501 and r.content == b'{"error":true}' and r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"
    assert r["X-Status-Reason"].startswith("Endpoint not implemented: the 'cpc_classes' data set is not in this snapshot; ")
    assert "cpc_current" in r["X-Status-Reason"]
    from API.lapse_errors import DEFERRED_INDICES
    for note in DEFERRED_INDICES.values():
        for word in ("checklist", "gate", "scaffold", "ops/", "patentref "):
            assert word not in note.lower(), (word, note)
