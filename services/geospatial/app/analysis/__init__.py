"""Analysis plugin registry and operations.

Importing this package triggers registration of all built-in operations.
"""

from app.analysis import ops  # noqa: F401
from app.analysis.registry import (  # noqa: F401
    CATALOG_SCHEMA_VERSION,
    AnalysisOp,
    all_ops,
    catalog,
    get_op,
    register,
)
