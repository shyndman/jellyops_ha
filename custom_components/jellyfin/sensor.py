"""Support to interface with the Jellyfin API."""
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_URL,
    DEVICE_DEFAULT_NAME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from .client_manager import JellyfinClientManager
from .const import DOMAIN
from .helpers import autolog

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

PLATFORM = "sensor"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: "AddEntitiesCallback",
) -> None:

    _jelly: JellyfinClientManager = hass.data[DOMAIN][config_entry.data.get(CONF_URL)]["manager"]
    async_add_entities(
        [
            JellyfinSensor(_jelly),
            JellyfinItemCountSensor(_jelly, "movie", lambda m: m.movie_count),
            JellyfinItemCountSensor(_jelly, "episode", lambda m: m.episode_count),
            JellyfinItemCountSensor(_jelly, "series", lambda m: m.series_count),
            JellyfinItemCountSensor(_jelly, "connected_session", lambda m: m.connected_session_count),
            JellyfinItemCountSensor(_jelly, "playing_session", lambda m: m.playing_session_count),
        ],
        True,
    )
    

class JellyfinSensor(Entity):
    """Representation of an Jellyfin device."""

    def __init__(self, jelly_cm: JellyfinClientManager):
        """Initialize the Jellyfin device."""
        _LOGGER.debug("New Jellyfin Sensor initialized")
        self.jelly_cm = jelly_cm
        self._available = True

    async def async_added_to_hass(self) -> None:
        autolog("<<<")
        self.hass.data[DOMAIN][self.jelly_cm.host][PLATFORM]["entities"].append(self)

    async def async_will_remove_from_hass(self) -> None:
        autolog("<<<")
        self.hass.data[DOMAIN][self.jelly_cm.host][PLATFORM]["entities"].remove(self)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.jelly_cm.is_available

    @property
    def unique_id(self) -> str | None:
        """Return the id of this jellyfin server."""
        info = self.jelly_cm.info
        if info is None:
            return None
        return info.Id

    @property
    def device_info(self) -> dict[str, object] | None:
        """Return device information about this entity."""
        info = self.jelly_cm.info
        if info is None:
            return None
        return {
            "identifiers": {
                # Unique identifiers within a specific domain
                (DOMAIN, self.jelly_cm.server_url)
            },
            "manufacturer": "Jellyfin",
            "model": f"Jellyfin {info.Version}".rstrip(),
            "name": info.ServerName,
            "configuration_url": self.jelly_cm.server_url,
        }

    @property
    def name(self) -> str:
        """Return the name of the device."""
        info = self.jelly_cm.info
        if info is None:
            return DEVICE_DEFAULT_NAME
        return f"Jellyfin {info.ServerName}" or DEVICE_DEFAULT_NAME

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state."""
        return False

    @property
    def state(self) -> str:
        """Return the state of the device."""
        return STATE_ON if self.jelly_cm.is_available else STATE_OFF

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return the state attributes."""
        info = self.jelly_cm.info
        if info is None:
            return None
        extra_attr: dict[str, object] = {
            "os": info.OperatingSystem,
            "update_available": info.HasUpdateAvailable,
            "version": info.Version,
        }
        if self.jelly_cm.data:
            extra_attr["data"] = self.jelly_cm.data
        if self.jelly_cm.yamc:
            extra_attr["yamc"] = self.jelly_cm.yamc

        return extra_attr

    async def async_update(self) -> None:
        """Synchronise state from the server."""
        autolog("<<<")
        await self.jelly_cm.update_data()

    async def async_trigger_scan(self) -> None:
        _LOGGER.info("Library scan triggered")
        await self.jelly_cm.trigger_scan()

    async def async_delete_item(self, id: str) -> None:
        _LOGGER.debug("async_delete_item triggered")
        await self.jelly_cm.delete_item(id)
        self.async_schedule_update_ha_state()

    async def async_search_item(self, search_term: str) -> None:
        _LOGGER.debug("async_search_item triggered: %s", search_term)
        await self.jelly_cm.search_item(search_term)
        self.async_schedule_update_ha_state()

    async def async_yamc_setpage(self, page: int) -> None:
        _LOGGER.debug("YAMC setpage: %d", page)

        await self.jelly_cm.yamc_set_page(page)
        self.async_schedule_update_ha_state()

    async def async_yamc_setplaylist(self, playlist: str) -> None:
        _LOGGER.debug("YAMC setplaylist: %s", playlist)

        await self.jelly_cm.yamc_set_playlist(playlist)
        self.async_schedule_update_ha_state()


class JellyfinItemCountSensor(SensorEntity):
    """Sensor for Jellyfin library item counts (movies, episodes, series)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        jelly_cm: JellyfinClientManager,
        item_type: str,
        count_getter: Callable[[JellyfinClientManager], int | None],
    ) -> None:
        """Initialize the count sensor."""
        self.jelly_cm = jelly_cm
        self._item_type = item_type
        self._count_getter = count_getter

    async def async_added_to_hass(self) -> None:
        autolog("<<<")
        self.hass.data[DOMAIN][self.jelly_cm.host][PLATFORM]["entities"].append(self)

    async def async_will_remove_from_hass(self) -> None:
        autolog("<<<")
        self.hass.data[DOMAIN][self.jelly_cm.host][PLATFORM]["entities"].remove(self)

    @property
    def unique_id(self) -> str | None:
        """Return unique ID for this sensor."""
        info = self.jelly_cm.info
        if info is None:
            return None
        return f"{info.Id}_{self._item_type}_count"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        info = self.jelly_cm.info
        server_name = info.ServerName if info else "Jellyfin"
        return f"{server_name} {self._item_type.title()} Count"

    @property
    def native_value(self) -> int | None:
        """Return the count value."""
        return self._count_getter(self.jelly_cm)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.jelly_cm.is_available

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state."""
        return False

    @property
    def device_info(self) -> dict[str, object]:
        """Return device information to link to the Jellyfin server device."""
        return {
            "identifiers": {(DOMAIN, self.jelly_cm.server_url)},
        }

    async def async_update(self) -> None:
        """Update the sensor (piggybacks on JellyfinSensor's update)."""
        pass

    @staticmethod
    def _session_attributes(sessions: list[dict[str, object]]) -> dict[str, object]:
        usernames = [
            session.get("username") for session in sessions if session.get("username")
        ]
        return {
            "sessions": sessions,
            "usernames": usernames,
        }

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Attach session metadata for session sensors."""
        if self._item_type == "connected_session":
            return self._session_attributes(self.jelly_cm.connected_sessions)
        if self._item_type != "playing_session":
            return None
        return self._session_attributes(self.jelly_cm.playing_sessions)


