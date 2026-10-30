"""Optional, separately deployed BET usage-statistics service."""

from .app import create_app

__all__ = ["create_app"]
