"""The jellyfin component."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client_manager import JellyfinClientManager
from .const import DOMAIN, SIGNAL_STATE_UPDATED
from .helpers import autolog
from .models import JellyfinEntryData
from .services import async_register_services
from .view import JellyfinImageView

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "media_player"]
_update_unlistener: Callable[[], None] | None = None


async def async_setup(hass: HomeAssistant, config: Mapping[str, object]) -> bool:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    hass.http.register_view(JellyfinImageView())
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    autolog("<<<")
    global _update_unlistener
    if _update_unlistener:
        _update_unlistener()

    if not config_entry.unique_id:
        hass.config_entries.async_update_entry(config_entry, unique_id=config_entry.title)

    config_dict: dict[str, object] = dict(config_entry.data)
    config_dict.update(config_entry.options)
    if config_entry.options:
        hass.config_entries.async_update_entry(config_entry, data=config_dict, options={})

    config = JellyfinEntryData.model_validate(config_dict)
    _update_unlistener = config_entry.add_update_listener(_update_listener)

    hass.data[DOMAIN][config.url] = {"entry_id": config_entry.entry_id}
    manager = JellyfinClientManager(hass, config)
    manager.entry_id = config_entry.entry_id
    try:
        await manager.connect()
        hass.data[DOMAIN][config.url]["manager"] = manager
    except Exception:  # pragma: no cover - HA handles retry/backoff
        _LOGGER.error("Cannot connect to Jellyfin server.")
        raise ConfigEntryNotReady from None

    await manager.start()

    for platform in PLATFORMS:
        hass.data[DOMAIN][config.url][platform] = {"entities": []}
        await hass.config_entries.async_forward_entry_setups(config_entry, [platform])

    async_dispatcher_send(hass, SIGNAL_STATE_UPDATED)

    async def stop_jellyfin(event: object) -> None:
        await manager.stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_jellyfin)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.info("Unloading jellyfin")
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(config_entry, component)
                for component in PLATFORMS
            ]
        )
    )

    manager: JellyfinClientManager = hass.data[DOMAIN][config_entry.data.get(CONF_URL)]["manager"]
    await manager.stop()
    return unload_ok


async def _update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    _LOGGER.debug("reload triggered")
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    entreg = entity_registry.async_get(hass)
    if entity_registry.async_entries_for_device(entreg, device_entry.id):
        return False
    return True
