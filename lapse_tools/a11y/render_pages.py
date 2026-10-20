"""Render every page template this app serves to static HTML files for the accessibility check (PatentRef checklist 7.6).

  python lapse_tools/a11y/render_pages.py <out dir> [<patentref checkout>]
  node lapse_tools/a11y/axe_check.js <out dir>

Uses the test settings of lapse_accounts/tests/conftest.py (a temporary Django database and a three-row search file), the
legal documents of the patentref checkout (default: the sibling directory named patentref, lapse, pr or trust), and
sample contexts for the record page (with the family block and the superseded banner) and the unsubscribe page. Each page
is written as it is served; nothing outside <out dir> and a temporary directory is touched.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from django.conf import settings
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client

# importing the tests conftest sets the test environment and calls django.setup()
from lapse_accounts.tests import conftest


def main(out, repo=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if repo is None:
        repo = next((ROOT.parent / n for n in ("patentref", "lapse", "pr", "trust") if (ROOT.parent / n / "docs" / "legal").exists()), None)
    if repo is None:
        sys.exit("no patentref checkout found: pass its path as the second argument")
    settings.LAPSE_LEGAL_DIR = str(Path(repo) / "docs" / "legal")
    settings.LAPSE_LICENSES_PATH = str(Path(repo) / "LICENSES.md")
    settings.LAPSE_EMAIL_DB = str(Path(conftest._TMP) / "email.sqlite3")
    call_command("migrate", verbosity=0, interactive=False)
    call_command("createcachetable", verbosity=0)
    conftest._build_search_db(conftest.os.environ["LAPSE_SQLITE_PATH"])
    c = Client()
    from lapse_accounts import legal, mail

    pages = {"signup": "/api/v1/meta/signup/", "status": "/api/v1/meta/status/", "legal_index": "/docs/legal/"}
    for name in legal.DOCS:
        pages[f"legal_{name}"] = f"/docs/legal/{name}"
    tok = mail.unsubscribe_url("ann@example.org", "monitor-hit").replace("https://patentref.io", "")
    pages["unsubscribe_ask"] = tok
    for name, path in pages.items():
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code)
        (out / f"{name}.html").write_bytes(r.content)
    r = c.post(tok, {"List-Unsubscribe": "One-Click", "from_page": "1"})
    (out / "unsubscribe_done.html").write_bytes(r.content)
    r = c.get("/u/" + "x" * 32)
    (out / "unsubscribe_unknown.html").write_bytes(r.content)
    from lapse_accounts import public

    rec = {"plain": "As of 2026-10-13, the term end computed from the dates in the USPTO record for US 4310730 is 1999-01-12 "
                    "(20 years from the filing date), a date that has passed (data version 20261013.3).",
           "status": "expired_term", "basis": ["term_20y"], "permalink": "https://patentref.io/p/4310730?v=20261013.3",
           "as_of": "2026-10-13", "data_version": "20261013.3", "lapse_date": None, "reinstated_date": None,
           "reinstatement_possible": False, "reinstatement_basis": "35 USC 154: the term ended", "expiry_date_estimated": "1999-01-12"}
    family = {"members": [{"office": "DE", "number": "3030000", "kind": "A1", "published": "1981-03-01"},
                          {"office": "GB", "number": "2058000", "kind": "A", "published": "1981-04-08"}],
              "global_dossier_url": "https://globaldossier.uspto.gov/result/application/US/06060492/1",
              "credit": "Family data from Google Patents Public Datasets.", "retrieved_at": "2026-10-15", "more": False}
    html = render_to_string("lapse_accounts/record.html", {
        "rec": rec, "pid": "4310730", "title": "Example title as printed", "superseded": {"requested": "20261006.4", "current": "20261013.3"},
        "data_version": "20261013.3", "as_of": "2026-10-13", "footer_lines": public.footer_lines() + [family["credit"]],
        "family": family, "basis_text": "20 years from the filing date", "term_end_text": "", "status_json": '{"status": "expired_term"}'})
    (out / "record.html").write_text(html, encoding="utf-8")
    print(f"{len(list(out.glob('*.html')))} pages in {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
