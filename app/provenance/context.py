"""Module-level accessor for the audit logger, avoiding deep parameter threading."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.provenance.audit import AuditLogger

_audit_logger: AuditLogger | None = None


def set_audit_logger(logger: AuditLogger | None) -> None:
    """Set the global audit logger (called once at startup)."""
    global _audit_logger
    _audit_logger = logger


def get_audit_logger() -> AuditLogger | None:
    """Get the audit logger. Returns None if provenance is disabled."""
    return _audit_logger
