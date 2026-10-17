"""geo3way: geometry-aware diff and three-way merge for vector spatial data."""
from .diff import apply, datasets_equal, diff, patch_bytes
from .geometry import validity_errors
from .merge import Conflict, MergeResult, merge

__version__ = "0.1.0"
__all__ = ["Conflict", "MergeResult", "apply", "datasets_equal", "diff", "merge",
           "patch_bytes", "validity_errors"]
