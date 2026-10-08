"""Gate 2.11: the root files, the error docs, the build facts endpoint, RFC 9457 problem details by content
negotiation, and the footer generated from LICENSES.md (gate 1.11)."""
import json

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


def test_footer_from_licenses(public_dir):
    lines = public.footer_lines()
    assert lines == ["Patent data from PatentsView and the USPTO, used under CC BY 4.0. Test line one.", "Second test line."]
    (public_dir / "LICENSES.md").write_text("# no section\n", encoding="utf-8")
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    assert public.footer_lines() == public.FALLBACK_FOOTER


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
