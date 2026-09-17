"""Compatibility re-export for Trajecta's free network tool service."""

from server.src.tools.personal.network import NetworkTools

HTTPTools = NetworkTools

__all__ = ["HTTPTools", "NetworkTools"]
