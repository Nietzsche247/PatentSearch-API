"""Render every page template this app serves to static HTML files for the accessibility check (PatentRef checklist 7.6).

  python lapse_tools/a11y/render_pages.py <out dir> [<patentref checkout>]
  node lapse_tools/a11y/axe_check.js <out dir>

Uses the test settings of lapse_accounts/tests/conftest.py (a temporary Django database and a three-row search file), the
legal documents of the patentref checkout (default: the sibling directory named patentref, lapse, pr or trust), the
sign-up page also as a signed-in account sees it (the terms box and the key buttons; with a key shown), the error index as a browser asks for it, and sample contexts for the record page (with the family block, the superseded banner and its JSON as the view builds it), its not-found page and the unsubscribe page. Each page
is written as it is served; nothing outside <out dir> and a temporary directory is touched.
"""
import json
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
    # the sign-up page as a signed-in account sees it: the key box and the key buttons (the script shows them after sign-in)
    signup = (out / "signup.html").read_text(encoding="utf-8")
    swaps = [('<div id="auth" class="card">', '<div id="auth" class="card hidden">'),
             ('<div id="account" class="card hidden">', '<div id="account" class="card">')]
    for a, b in swaps:
        assert signup.count(a) == 1, a
        signup = signup.replace(a, b)
    (out / "signup_account.html").write_text(signup, encoding="utf-8")
    for a, b in [('<div id="key-none">', '<div id="key-none" class="hidden">'), ('<div id="key-have" class="hidden">', '<div id="key-have">'),
                 ('<div id="key-show" class="hidden">', '<div id="key-show">')]:
        assert signup.count(a) == 1, a
        signup = signup.replace(a, b)
    (out / "signup_key.html").write_text(signup, encoding="utf-8")
    r = c.post(tok, {"List-Unsubscribe": "One-Click", "from_page": "1"})
    (out / "unsubscribe_done.html").write_bytes(r.content)
    r = c.get("/u/" + "x" * 32)
    (out / "unsubscribe_unknown.html").write_bytes(r.content)
    from lapse_accounts import public, waitlist

    # the waitlist pages (wave 4 Q1) in each state: the form as served (closed until Privacy Policy beta-4 is in force),
    # the open form with an error, the answer after a post, and the confirmation page's two states
    (out / "waitlist_as_served.html").write_bytes(c.get("/waitlist/").content)
    base_ctx = {"data_version": "20261013.3", "as_of": "2026-10-13", "footer_lines": public.footer_lines(), "topics": waitlist.TOPICS.items(),
                "is_open": True, "opens_on": "2026-11-11", "chosen": ["rules"], "unconfirmed_days": waitlist.UNCONFIRMED_DAYS,
                "after_opening_days": waitlist.AFTER_OPENING_DAYS}
    for name, tpl, extra in (
            ("waitlist_form", "waitlist.html", {"state": "form"}),
            ("waitlist_error", "waitlist.html", {"state": "error", "error": "Tick at least one box.", "email": "ann@example.org"}),
            ("waitlist_check", "waitlist.html", {"state": "check", "email": "a***@example.org"}),
            ("waitlist_closed", "waitlist.html", {"state": "closed", "is_open": False}),
            ("waitlist_confirm_ask", "waitlist_confirm.html", {"state": "ask", "address": "a***@example.org", "names": "Rules updates", "token": "x" * 32}),
            ("waitlist_confirm_done", "waitlist_confirm.html", {"state": "done", "address": "a***@example.org", "names": "Rules updates"}),
            ("waitlist_confirm_unknown", "waitlist_confirm.html", {"state": "unknown"})):
        (out / f"{name}.html").write_text(render_to_string(f"lapse_accounts/{tpl}", dict(base_ctx, **extra)), encoding="utf-8")
    # the error index as a browser asks for it (a plain-text client gets the text itself)
    browser = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    for name, path, code in [("errors_index", "/docs/errors/", 200), ("errors_code", "/docs/errors/ERR_Q", 200), ("errors_unknown", "/docs/errors/NOPE", 404)]:
        r = c.get(path, HTTP_ACCEPT=browser)
        assert r.status_code == code and r["Content-Type"].startswith("text/html"), (path, r.status_code)
        (out / f"{name}.html").write_bytes(r.content)

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
        "family": family, "basis_text": "20 years from the filing date", "term_end_text": "",
        # the record as JSON, built as the view builds it: its long lines are what a 320 px screen has to wrap
        "status_json": json.dumps({k: v for k, v in rec.items() if k != "plain"}, indent=1)})
    (out / "record.html").write_text(html, encoding="utf-8")
    html = render_to_string("lapse_accounts/page_error.html", {
        "heading": "Not found", "message": "PatentRef has no record for this patent number. Check the number in the address.",
        "link_href": "/docs/", "link_text": "PatentRef documentation", "data_version": "20261013.3", "as_of": "2026-10-13",
        "footer_lines": public.footer_lines()})
    (out / "record_not_found.html").write_text(html, encoding="utf-8")
    print(f"{len(list(out.glob('*.html')))} pages in {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
