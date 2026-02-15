"""Composite Jellyfin client manager built from specialized mixins."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..models import JellyfinEntryData
from .connection import ConnectionMixin
from .library import LibraryMixin
from .sessions import SessionsMixin

__all__ = ["JellyfinClientManager"]


class JellyfinClientManager(ConnectionMixin, LibraryMixin, SessionsMixin):
    """Coordinate Jellyfin connections, library data, and session tracking."""

    hass: HomeAssistant
    config: JellyfinEntryData
    entry_id: str
    host: str

    def __init__(self, hass: HomeAssistant, config: JellyfinEntryData) -> None:
        self.hass = hass
        self.config = config
        self.entry_id = ""
        self.host = config.url
        super().__init__()
