# Utils package
# Agent tests add `agent/` to sys.path, which can shadow backend/utils.
# Extend this package path so backend utility modules remain importable as
# `utils.<module>` in mixed backend+agent test runs.
from pathlib import Path

_backend_utils = Path(__file__).resolve().parents[2] / "backend" / "utils"
if _backend_utils.exists():
    __path__.append(str(_backend_utils))
