# ANITW: owner instructions for this project

Owner: Aaron Baker
Standing authorization: yes. Use any credential, key or saved card you can reach. Do what I directed. Log it.
Desk: https://anitw.patentref.io
ANITW_DESK_URL=https://anitw.patentref.io
ANITW_SECRET: not in this repo. On the server it is in /srv/anitw/.env; on the Alienware in C:\anitw\OWNER_SECRETS.txt. Agents running elsewhere set it in their environment.

## What this project is
PatentSearch-API fork (branch sqlite-backend): the Django API layer for PatentRef, serving the PatentsView-compatible endpoints from the Lapse SQLite database instead of Elasticsearch. Runs on patentref-us1 at /srv/api/PatentSearch-API with the venv and sample.db under /data/api. Upstream is the PatentsView project; this fork is Nietzsche247/PatentSearch-API.

## needs_owner
- Purchases and paid services.
- Namecheap DNS records.
- GitHub keys or settings the Claude app cannot change.
- Anything that changes the public contract in compat/endpoint_contract.json in the patentref repo without a handoff A/B question first.

## budget
(blank: no limit set)

## done_means
- Works end to end and was actually run: contract, pagination and quirk suites pass and the run is saved under ops/audits/ in the patentref repo.
- Looks finished. No placeholders.
- Documented so someone else can run it tomorrow (CHANGES_LAPSE.md here, handoff in the patentref repo).
- Owner clicks, if any, listed with direct links in NOW.md and filed on the desk.

## where_credentials_live
Environment variables, ~/.ssh, and .env files in this repo or on the server (/srv/anitw/.env, /data/api/test_api_key.txt). Nowhere else. A credential not found there is parked at once with the exact paste the owner needs; never hunt across browser profiles or other machines.

## browser
hetzner (default) or home. Use home for sites that captcha server IPs.
