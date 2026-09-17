"""Compatibility re-export for Trajecta's managed host-process service."""

from server.src.tools.personal.process import ProcessManager

ProcessTools = ProcessManager

__all__ = ["ProcessManager", "ProcessTools"]
