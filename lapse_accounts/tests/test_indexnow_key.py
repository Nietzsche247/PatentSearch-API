"""Release AH (PatentRef 1.25.0): the IndexNow key file at the site root, GET /<key>.txt (lapse_accounts/public.py indexnow_file),
for the one key the patentref clone's public/indexnow.txt names; any other name of 32 hex characters answers 404, and the
root files beside it keep their routes.

  python -m pytest lapse_accounts/tests/test_indexnow_key.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import hashlib

from django.conf import settings
from django.urls import resolve

from lapse_accounts import public


def made_up(word):
    """A key of the right form, made here, so no key-like literal sits in this file."""
    return hashlib.md5(word.encode("utf-8")).hexdigest()


def test_the_key_file_answers_the_named_key_only(client, monkeypatch, tmp_path):
    key, other = made_up("indexnow-test"), made_up("another")
    (tmp_path / "indexnow.txt").write_text(key + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_PUBLIC_DIR", str(tmp_path))
    r = client.get(f"/{key}.txt")
    assert r.status_code == 200 and r.content.decode("utf-8") == key + "\n"
    assert r["Content-Type"] == "text/plain; charset=utf-8" and r["Cache-Control"] == "public, max-age=86400"
    assert client.get(f"/{other}.txt").status_code == 404
    assert client.post(f"/{key}.txt").status_code == 405
    assert resolve(f"/{key}.txt").url_name == "indexnow-key"
    assert resolve("/llms-full.txt").url_name == "public-file" and resolve("/llms.txt").url_name == "public-file"


def test_no_file_or_a_key_of_another_form_gives_404(client, monkeypatch, tmp_path):
    key = made_up("indexnow-test")
    monkeypatch.setattr(settings, "LAPSE_PUBLIC_DIR", str(tmp_path))
    assert public.indexnow_key() is None and client.get(f"/{key}.txt").status_code == 404
    for bad in (key.upper(), key[:20], key + "0", "", "\n" + key):
        (tmp_path / "indexnow.txt").write_text(bad + "\n", encoding="utf-8")
        assert public.indexnow_key() is None, repr(bad)
        assert client.get(f"/{key}.txt").status_code == 404
