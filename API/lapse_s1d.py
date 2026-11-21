"""The one module through which the fork reaches s1d, PatentRef's structure layer S1-D (release AG, API 1.24.0). The S1-D
endpoints (API/lapse_structure_s1d.py) take every s1d name from here.

s1d is a private package and is not part of this repository, as claimgraph is not: the release deploy installs its wheel into
the API's virtual environment. It is deterministic code over the published text (standard library only, with claimgraph for
the claims); no language model runs in it. When the package is not installed, when the s1d found is not the installed package
(a stray folder of that name, for example), when it is another version than the one the API documents, when it serves other
unit types, or when it was written for another claimgraph than the one installed, importing this module raises S1DMissing,
which says what to install, so the API stops at startup with that message instead of failing on a request.
"""
from importlib import metadata
from pathlib import Path

from API import lapse_claimgraph

S1D_VERSION_DOCUMENTED = "s1d-11"            # the version of the rules the docs pages and openapi.json describe
PACKAGE = "s1d 0.3.2"
WHEEL = "s1d-0.3.2-py3-none-any.whl"
UNIT_TYPES_DOCUMENTED = ("Component", "StatedProblem")


class S1DMissing(ImportError):
    """s1d is not installed in this Python environment, or not the package, version and unit types the API needs."""


def _missing(why):
    return S1DMissing(
        f"PatentRef's structure layer S1-D is not available: {why}. s1d is a private package that is not part of this "
        f"repository: install its wheel ({PACKAGE}) into the API's virtual environment with that environment's pip "
        f"(pip install --no-deps --no-index {WHEEL}), as the release deploy does, then reload the API.")


try:
    import s1d
except ImportError as e:
    raise _missing(f"import s1d failed ({e.__class__.__name__}: {e})") from e

try:
    _dist = metadata.distribution("s1d")
except metadata.PackageNotFoundError:
    raise _missing(f"the s1d at {getattr(s1d, '__file__', None)} is not an installed package") from None
_INSTALLED_AT = Path(str(_dist.locate_file("s1d/__init__.py"))).resolve()
if Path(str(getattr(s1d, "__file__", "") or "")).resolve() != _INSTALLED_AT:
    raise _missing(f"the s1d imported from {getattr(s1d, '__file__', None)} is not the installed package at {_INSTALLED_AT}")
if f"s1d {s1d.__version__}" != PACKAGE or s1d.STRUCTURE_VERSION != S1D_VERSION_DOCUMENTED:
    raise _missing(f"s1d {s1d.__version__} builds {s1d.STRUCTURE_VERSION}, and this API documents {PACKAGE} with "
                   f"{S1D_VERSION_DOCUMENTED}")
if tuple(s1d.UNIT_TYPES) != UNIT_TYPES_DOCUMENTED:
    raise _missing(f"s1d {s1d.__version__} gives the unit types {', '.join(s1d.UNIT_TYPES)}, and this API documents "
                   f"{', '.join(UNIT_TYPES_DOCUMENTED)}")
if s1d.CLAIMGRAPH_NEEDED != lapse_claimgraph.__version__:
    raise _missing(f"s1d {s1d.__version__} is written for claimgraph {s1d.CLAIMGRAPH_NEEDED}, and the API runs claimgraph "
                   f"{lapse_claimgraph.__version__}")

from s1d import check as _check  # noqa: E402  (after the checks above, so a wrong package never runs)
from s1d import links as _links  # noqa: E402
from s1d import parts as _parts  # noqa: E402
from s1d import patent as _patent  # noqa: E402
from s1d import stated as _stated  # noqa: E402
from s1d import structdb as _structdb  # noqa: E402

VERSION = s1d.__version__
S1D_VERSION = s1d.STRUCTURE_VERSION
GENERATOR = s1d.GENERATOR
UNIT_TYPES = tuple(s1d.UNIT_TYPES)
STATED_TYPES = tuple(s1d.STATED_TYPES)
FIELDS = tuple(s1d.FIELDS)
ID_LETTER = dict(s1d.ID_LETTER)
build = _patent.build                      # one patent's units and relations (claims, brief summary, detailed description)
texts = _patent.texts                      # text_of(field, sequence) over one patent's own texts
stated_units = _stated.stated_units        # the stated units alone (brief summary and detailed description)
check = _check.check                       # the quote check: nothing leaves unless its quote is the text at its locator
links_from_index = _links.links_from_index  # names_same_part from the structure file's term-to-patent index
df_max_for = _links.df_max_for
patents_in = _links.patents_in
link_key = _parts.link_key                 # a part without leading ordinal words, as links compare parts
DF_SHARE = _links.DF_SHARE                 # 0.2 percent of the patents in the file
DF_SHARE_FALLBACK = _links.DF_SHARE_FALLBACK   # 0.02 percent
POSTINGS_BUDGET = _links.POSTINGS_BUDGET   # index rows one lookup reads at most
SHARED_LISTED_MAX = _links.SHARED_LISTED_MAX
connect_structure = _structdb.connect      # the structure file, read only (mode=ro, query_only)
stated_from_store = _structdb.stated_from_store
CUES = tuple(_structdb.CUES)
SECTIONS = tuple(_structdb.SECTIONS)

__all__ = ["S1DMissing", "s1d", "VERSION", "S1D_VERSION", "GENERATOR", "UNIT_TYPES", "STATED_TYPES", "FIELDS", "ID_LETTER",
           "build", "texts", "stated_units", "check", "links_from_index", "df_max_for", "patents_in", "link_key", "DF_SHARE",
           "DF_SHARE_FALLBACK", "POSTINGS_BUDGET", "SHARED_LISTED_MAX", "connect_structure", "stated_from_store", "CUES",
           "SECTIONS", "PACKAGE", "WHEEL", "S1D_VERSION_DOCUMENTED"]
