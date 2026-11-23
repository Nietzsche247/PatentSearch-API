"""The kinds of client that call PatentRef, counted per UTC day (release AH, PatentRef 1.25.0). OFF unless
settings.LAPSE_CLIENT_COUNTS is on (it is off by default: the privacy policy does not name these counts yet).

When on, lapse_accounts.requestlog adds one to a count per (UTC day, area, kind) for every response, in the usage-log file:

  client_counts(day TEXT, area TEXT, kind TEXT, n INTEGER, PRIMARY KEY (day, area, kind))

Counts only: the User-Agent is read to choose a kind and never kept; no address, key, prefix, path or time of a request is
stored with a count. A request sent with one of the service's own keys (lapse_accounts.status.own_key_prefixes) or by one of
the service's own tools (a User-Agent patentref-<name> other than the MCP server and the agent skills) counts under the kind
"own". Rows older than requestlog.RETENTION_DAYS are deleted; the daily counts job (patentref ops/systemd/counts.sh, through
manage.py client_counts day --out) keeps one file a day of counts. Nothing of this is shown on any page.

  area   data (under /api/v1/ but not /api/v1/meta/), meta (/api/v1/meta/), file (/llms.txt, /llms-full.txt, /openapi.json,
         /changes.txt, /LICENSES.md and the IndexNow key file), page (everything else the API serves: the home page, /agents/,
         the record pages, the demo, the triage and legal pages)
  kind   patentref-mcp/<version>             the PatentRef MCP server (User-Agent patentref-mcp/<version>)
         patentref-skill/<skill>/<version>   a PatentRef agent skill (User-Agent patentref-skill/<skill>/<version>)
         agent/<name>                        an AI assistant fetching a page for a person (ChatGPT-User, Claude-User, ...)
         ai-crawler/<name>                   an AI company's crawler (GPTBot, ClaudeBot, PerplexityBot, CCBot, ...)
         crawler/<name>                      a search engine's or another known crawler (Googlebot, bingbot, ...)
         tool/<name>                         a known HTTP tool or library (curl, python-requests, httpx, ...)
         browser                             a web browser (Mozilla/5.0 with a browser engine and no crawler word)
         own                                 one of the service's own keys or tools
         none                                no User-Agent
         other                               anything else (and any kind past MAX_KINDS_A_DAY in one day)
"""
import re
from datetime import datetime, timezone

from django.conf import settings

MCP = re.compile(r"patentref-mcp/([0-9][0-9A-Za-z.+-]{0,19})(?:[\s;/(]|$)")
SKILL = re.compile(r"patentref-skill/([a-z0-9][a-z0-9._-]{0,39})/([0-9][0-9A-Za-z.+-]{0,19})(?:[\s;(]|$)")
FILES = ("/llms.txt", "/llms-full.txt", "/openapi.json", "/changes.txt", "/LICENSES.md")
INDEXNOW_FILE = re.compile(r"/[0-9a-f]{32}\.txt")
MAX_KINDS_A_DAY = 400   # distinct (area, kind) pairs a worker counts in one day; the rest count as other
# (words looked for in the lower-case User-Agent, the name counted), in this order: the earliest entry that matches decides
AGENTS = (("chatgpt-user", "chatgpt"), ("claude-user", "claude"), ("claude-web", "claude"), ("perplexity-user", "perplexity"),
          ("mistralai-user", "mistral"), ("duckassistbot", "duckassist"), ("meta-externalfetcher", "meta"), ("gemini-deep-research", "gemini"),
          ("google-notebooklm", "notebooklm"), ("kagi-fetcher", "kagi"), ("phind", "phind"), ("cursor/", "cursor"))
AI_CRAWLERS = (("gptbot", "gptbot"), ("oai-searchbot", "oai-searchbot"), ("claudebot", "claudebot"), ("claude-searchbot", "claude-searchbot"),
               ("anthropic-ai", "anthropic-ai"), ("perplexitybot", "perplexitybot"), ("ccbot", "ccbot"), ("bytespider", "bytespider"),
               ("amazonbot", "amazonbot"), ("meta-externalagent", "meta-externalagent"), ("cohere-ai", "cohere-ai"), ("youbot", "youbot"),
               ("diffbot", "diffbot"), ("ai2bot", "ai2bot"), ("timpibot", "timpibot"), ("omgilibot", "omgilibot"), ("mistralai", "mistralai"),
               ("applebot-extended", "applebot-extended"), ("google-cloudvertexbot", "google-vertex"))
CRAWLERS = (("googlebot", "googlebot"), ("google-inspectiontool", "googlebot"), ("bingbot", "bingbot"), ("yandex", "yandex"),
            ("duckduckbot", "duckduckbot"), ("baiduspider", "baiduspider"), ("applebot", "applebot"), ("slurp", "yahoo"),
            ("petalbot", "petalbot"), ("seznambot", "seznambot"), ("mojeekbot", "mojeekbot"), ("qwantbot", "qwantbot"),
            ("facebookexternalhit", "facebook"), ("twitterbot", "twitterbot"), ("linkedinbot", "linkedinbot"), ("slackbot", "slackbot"),
            ("discordbot", "discordbot"), ("telegrambot", "telegrambot"), ("whatsapp", "whatsapp"), ("ahrefsbot", "ahrefsbot"),
            ("semrushbot", "semrushbot"), ("mj12bot", "mj12bot"), ("dotbot", "dotbot"), ("uptimerobot", "uptimerobot"),
            ("archive.org_bot", "internet-archive"), ("ia_archiver", "internet-archive"), ("indexnow", "indexnow"))
TOOLS = (("headlesschrome", "headless-chrome"), ("python-requests", "python-requests"), ("python-httpx", "python-httpx"),
         ("python-urllib", "python-urllib"), ("aiohttp", "aiohttp"), ("httpie", "httpie"), ("curl/", "curl"), ("wget/", "wget"),
         ("postmanruntime", "postman"), ("insomnia", "insomnia"), ("axios", "axios"), ("node-fetch", "node-fetch"), ("undici", "undici"),
         ("go-http-client", "go-http-client"), ("okhttp", "okhttp"), ("apache-httpclient", "apache-httpclient"), ("java/", "java"),
         ("libwww-perl", "libwww-perl"), ("guzzlehttp", "guzzle"), ("faraday", "faraday"), ("rest-client", "rest-client"),
         ("ruby", "ruby"), ("powershell", "powershell"), ("dart:io", "dart"), ("httr", "r-httr"), ("r-curl", "r-curl"),
         ("deno/", "deno"), ("bun/", "bun"), ("reqwest", "reqwest"), ("lapse-contract-runner", "own"))
CRAWLER_WORDS = ("bot", "crawler", "spider", "scraper", "fetcher", "headless")
BROWSER_ENGINES = ("applewebkit/", "gecko/", "trident/", "presto/")


def on():
    return bool(getattr(settings, "LAPSE_CLIENT_COUNTS", False))


def _first(ua, table):
    for word, name in table:
        if word in ua:
            return name
    return None


def kind(user_agent):
    """The kind of client a User-Agent names (module text); the User-Agent itself is never kept."""
    raw = (user_agent or "").strip()
    if not raw:
        return "none"
    ua = raw.lower()[:512]
    if ua.startswith("patentref-mcp/"):
        m = MCP.match(ua[:64])
        return f"patentref-mcp/{m.group(1)}" if m else "patentref-mcp/other"
    if ua.startswith("patentref-skill/"):
        m = SKILL.match(ua[:128])
        return f"patentref-skill/{m.group(1)}/{m.group(2)}" if m else "patentref-skill/other"
    if ua.startswith("patentref-"):
        return "own"   # the service's own tools: the deploy's checks, the builders' checks
    for table, prefix in ((AGENTS, "agent/"), (AI_CRAWLERS, "ai-crawler/"), (CRAWLERS, "crawler/"), (TOOLS, "tool/")):
        name = _first(ua, table)
        if name:
            return "own" if name == "own" else prefix + name
    if ua.startswith("mozilla/") and any(e in ua for e in BROWSER_ENGINES) and not any(w in ua for w in CRAWLER_WORDS):
        return "browser"
    if any(w in ua for w in CRAWLER_WORDS):
        return "crawler/other"
    return "other"


def area(path):
    p = path or "/"
    if p.startswith("/api/v1/meta/"):
        return "meta"
    if p.startswith("/api/v1/"):
        return "data"
    if p in FILES or INDEXNOW_FILE.fullmatch(p):
        return "file"
    return "page"


def day(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def counter_key(ts, path, user_agent, own):
    """(UTC day, area, kind) of one response; kind is own for the service's own keys and tools."""
    k = kind(user_agent)
    return day(ts), area(path), ("own" if own else k)
