"""Binary sensors for Jellyfin Operations."""
import logging
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from .client_manager import JellyfinClientManager
from .const import DOMAIN
from .helpers import autolog

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

PLATFORM = "binary_sensor"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: "AddEntitiesCallback",
) -> None:
    _jelly: JellyfinClientManager = hass.data[DOMAIN][config_entry.data.get(CONF_URL)]["manager"]
    async_add_entities([JellyfinUpdateAvailableSensor(_jelly)], True)


class JellyfinUpdateAvailableSensor(BinarySensorEntity):
    """Reports whether a Jellyfin server update is available."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE

    def __init__(self, jelly_cm: JellyfinClientManager) -> None:
        self.jelly_cm = jelly_cm

    async def async_added_to_hass(self) -> None:
        autolog("<<<")
        self.hass.data[DOMAIN][self.jelly_cm.host][PLATFORM]["entities"].append(self)

    async def async_will_remove_from_hass(self) -> None:
        autolog("<<<")
        self.hass.data[DOMAIN][self.jelly_cm.host][PLATFORM]["entities"].remove(self)

    @property
    def unique_id(self) -> str | None:
        info = self.jelly_cm.info
        if info is None:
            return None
        return f"{info.Id}_update_available"

    @property
    def name(self) -> str:
        info = self.jelly_cm.info
        server_name = info.ServerName if info else "Jellyfin"
        return f"{server_name} Update Available"

    @property
    def is_on(self) -> bool | None:
        info = self.jelly_cm.info
        if info is None:
            return None
        return bool(info.HasUpdateAvailable)

    @property
    def available(self) -> bool:
        return self.jelly_cm.is_available

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def device_info(self) -> dict[str, object]:
        return {
            "identifiers": {(DOMAIN, self.jelly_cm.server_url)},
        }

    async def async_update(self) -> None:
        """State is read live from the manager; refresh is driven externally."""
        pass
