"""Shared helper utilities for the Jellyfin integration."""

from __future__ import annotations

import inspect
import logging

_LOGGER = logging.getLogger(__name__)


def autolog(message: str) -> None:
    """Automatically log the current function details."""
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        _LOGGER.debug("%s: <unknown frame>", message)
        return
    func = frame.f_back.f_code
    _LOGGER.debug("%s: %s in %s:%i", message, func.co_name, func.co_filename, func.co_firstlineno)
