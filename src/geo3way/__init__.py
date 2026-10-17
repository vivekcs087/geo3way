"""geo3way: geometry-aware diff and three-way merge for vector spatial data."""
from .diff import apply, datasets_equal, diff, patch_bytes
from .geometry import validity_errors
from .io import FormatError, load, save
from .merge import Conflict, MergeResult, merge

__version__ = "0.2.0"
__all__ = ["Conflict", "FormatError", "MergeResult", "apply", "datasets_equal", "diff", "load",
           "merge", "merge_files", "patch_bytes", "save", "validity_errors"]


def merge_files(*args, **kwargs):  # type: ignore[no-untyped-def]
    """Merge three files; see :func:`geo3way.cli.merge_files`."""
    from .cli import merge_files as _mf
    return _mf(*args, **kwargs)
