"""Local host-process execution (not a security sandbox)."""
from .local import LocalExecutionBackend, local_status

__all__ = ["LocalExecutionBackend", "local_status"]
