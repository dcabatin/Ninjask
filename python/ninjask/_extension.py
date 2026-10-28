"""Which compiled module provides Ninjask's classes (`Dex`, `Battle`, `Tracker`).

Normally it is `ninjask._core`, built from this repository's crate. A project
can instead compile the engine into an extension module of its own, next to
classes of its own that take Ninjask's objects (README, "Embedding in another
extension"). It then names that module under the entry-point group
`ninjask.extension`, and this package uses that module's classes, so that a
process has one `Battle` class, not two incompatible ones.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import entry_points
from types import ModuleType

GROUP = "ninjask.extension"
CLASSES = ("Dex", "Battle", "Tracker")


def load() -> ModuleType:
    points = sorted(entry_points(group=GROUP), key=lambda e: e.value)
    if len({e.value for e in points}) > 1:
        names = ", ".join(f"{e.name} = {e.value}" for e in points)
        raise ImportError(f"several modules are declared to provide Ninjask's classes ({GROUP}): {names}")
    if points:
        try:
            module = points[0].load()
        except ImportError as e:
            raise ImportError(
                f"{points[0].value}, declared to provide Ninjask's classes ({GROUP}), "
                "does not import; rebuild the project that declares it"
            ) from e
    else:
        try:
            module = import_module("ninjask._core")
        except ImportError as e:
            raise ImportError(
                "Ninjask's compiled module (ninjask._core) is not built: "
                "run `pip install -e .` (or `maturin develop --release`) in the Ninjask checkout"
            ) from e
    missing = [c for c in CLASSES if not hasattr(module, c)]
    if missing:
        raise ImportError(f"{module.__name__} lacks Ninjask's {', '.join(missing)}")
    return module


module = load()
