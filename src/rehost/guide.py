"""Backward-compatible names for the harness guide helpers.

The implementation lives in :mod:`rehost.knowledge`; this module keeps the
public imports used by existing integrations and saved workflows working.
"""

from .knowledge import END, BEGIN, TEMPLATE, ensure_guide, record_check

ensure = ensure_guide
record = record_check

__all__ = ['TEMPLATE', 'BEGIN', 'END', 'ensure', 'record']
