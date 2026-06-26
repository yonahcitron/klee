"""frontier — parser-directed symbolic execution on KLEE, as one pipeline.

pFuzzer's loop with KLEE's solver: grow a concrete prefix; let a small symbolic
window + length explore the next bytes; classify each terminated path by native
survival replay; bank valids and re-queue promising prefixes. A "version" (run9
validity, run14 keyword climbing, run18 sweep, run15 unified) is just a point in
a small knob space — see ``config.PROFILES``.

Public API: ``Config``, ``resolve``, ``validate``, ``Pipeline``.
"""

__version__ = "1.0.0"

from .config import Config, resolve, validate  # noqa: E402,F401
from .pipeline import Pipeline  # noqa: E402,F401
