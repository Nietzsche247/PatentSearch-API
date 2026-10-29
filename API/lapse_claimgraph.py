"""The one module through which the fork reaches claimgraph, PatentRef's claim parser. Every other module takes
claimgraph's names from here: the claim tree (API/lapse_structure.py), the structured mode of /api/v1/lapse/similar
(API/lapse_similar_structured.py), the claim links and the patent brief (API/lapse_permalinks.py, API/lapse_brief.py), the
claim counts (API/claim_counts.py, API/lapse_structure_counts.py, lapse_tools/claims_counts_snapshot.py), the triage table
(API/lapse_triage.py) and the extraction trial's engine (lapse/extract/units.py in the patentref clone).

claimgraph is a private package and is not part of this repository: release Z took out the copy release X had put at the
fork root, and the release deploy installs claimgraph's wheel into the API's virtual environment. When the package is not
installed, when the claimgraph found is not the installed package (a stray folder of that name, for example), or when it
builds another structure version than the one the API documents, importing this module raises ClaimgraphMissing, which
says what to install, so the API stops at startup with that message instead of failing on a request.
"""
from importlib import metadata
from pathlib import Path

STRUCTURE_VERSION_DOCUMENTED = "2"   # the claim tree's shape as openapi.json and the docs pages describe it
PACKAGE = "claimgraph 0.2.0"
WHEEL = "claimgraph-0.2.0-py3-none-any.whl"


class ClaimgraphMissing(ImportError):
    """claimgraph is not installed in this Python environment, or not the package and structure version the API needs."""


def _missing(why):
    return ClaimgraphMissing(
        f"PatentRef's claim parser is not available: {why}. claimgraph is a private package that is not part of this "
        f"repository: install its wheel ({PACKAGE}) into the API's virtual environment with that environment's pip "
        f"(pip install --no-deps --no-index {WHEEL}), as the release deploy does, then reload the API.")


try:
    import claimgraph
except ImportError as e:
    raise _missing(f"import claimgraph failed ({e.__class__.__name__}: {e})") from e

try:
    _dist = metadata.distribution("claimgraph")
except metadata.PackageNotFoundError:
    raise _missing(f"the claimgraph at {getattr(claimgraph, '__file__', None)} is not an installed package") from None
_INSTALLED_AT = Path(str(_dist.locate_file("claimgraph/__init__.py"))).resolve()
if Path(str(getattr(claimgraph, "__file__", "") or "")).resolve() != _INSTALLED_AT:
    raise _missing(f"the claimgraph imported from {getattr(claimgraph, '__file__', None)} is not the installed package at "
                   f"{_INSTALLED_AT}")
if claimgraph.STRUCTURE_VERSION != STRUCTURE_VERSION_DOCUMENTED:
    raise _missing(f"claimgraph {claimgraph.__version__} builds structure version {claimgraph.STRUCTURE_VERSION}, and this API "
                   f"documents structure version {STRUCTURE_VERSION_DOCUMENTED}")

STRUCTURE_VERSION = claimgraph.STRUCTURE_VERSION
__version__ = claimgraph.__version__
VERSION = __version__
GENERATOR = f"claimgraph {__version__}"
ClaimRow = claimgraph.ClaimRow
build_patent = claimgraph.build_patent
validate_patent = claimgraph.validate_patent
check_patent = validate_patent      # the triage table's name: every span of a built tree against its claim text; [] when all hold
find_mentions = claimgraph.terms.find_mentions
normalize_key = claimgraph.terms.normalize_key

__all__ = ["ClaimgraphMissing", "claimgraph", "STRUCTURE_VERSION", "VERSION", "__version__", "GENERATOR", "ClaimRow",
           "build_patent", "validate_patent", "check_patent", "find_mentions", "normalize_key"]
