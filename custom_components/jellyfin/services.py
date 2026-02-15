"""Home Assistant service registration for the Jellyfin integration."""

from __future__ import annotations

import logging
from typing import TypedDict

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID, ATTR_ID
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    ATTR_PAGE,
    ATTR_PLAYLIST,
    ATTR_SEARCH_TERM,
    DOMAIN,
    SERVICE_BROWSE,
    SERVICE_DELETE,
    SERVICE_SCAN,
    SERVICE_SEARCH,
    SERVICE_YAMC_SETPAGE,
    SERVICE_YAMC_SETPLAYLIST,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ("sensor", "media_player")

SERVICE_SCHEMA = vol.Schema({})

SCAN_SERVICE_SCHEMA = SERVICE_SCHEMA.extend({vol.Required(ATTR_ENTITY_ID): cv.entity_id})
YAMC_SETPAGE_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_PAGE): vol.Coerce(int)}
)
YAMC_SETPLAYLIST_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_PLAYLIST): cv.string}
)
DELETE_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_ID): cv.string}
)
SEARCH_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_SEARCH_TERM): cv.string}
)
BROWSE_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_ID): cv.string}
)


class _ServiceDef(TypedDict):
    method: str
    schema: vol.Schema


SERVICE_TO_METHOD: dict[str, _ServiceDef] = {
    SERVICE_SCAN: {"method": "async_trigger_scan", "schema": SCAN_SERVICE_SCHEMA},
    SERVICE_BROWSE: {"method": "async_browse_item", "schema": BROWSE_SERVICE_SCHEMA},
    SERVICE_DELETE: {"method": "async_delete_item", "schema": DELETE_SERVICE_SCHEMA},
    SERVICE_SEARCH: {"method": "async_search_item", "schema": SEARCH_SERVICE_SCHEMA},
    SERVICE_YAMC_SETPAGE: {
        "method": "async_yamc_setpage",
        "schema": YAMC_SETPAGE_SERVICE_SCHEMA,
    },
    SERVICE_YAMC_SETPLAYLIST: {
        "method": "async_yamc_setplaylist",
        "schema": YAMC_SETPLAYLIST_SERVICE_SCHEMA,
    },
}


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration-wide services once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("_services_registered"):
        return
    domain_data["_services_registered"] = True

    async def async_service_handler(call: ServiceCall) -> None:
        service_name = call.service
        service_def = SERVICE_TO_METHOD.get(service_name)
        if service_def is None:
            _LOGGER.warning("Unknown service: %s", service_name)
            return
        entity_id = call.data.get(ATTR_ENTITY_ID)
        if not entity_id:
            _LOGGER.warning("Service %s missing entity_id", service_name)
            return
        params = {key: value for key, value in call.data.items() if key != ATTR_ENTITY_ID}
        for server_data in hass.data.get(DOMAIN, {}).values():
            if not isinstance(server_data, dict):
                continue
            for platform in PLATFORMS:
                entities = server_data.get(platform, {}).get("entities", [])
                for entity in entities:
                    if entity.entity_id == entity_id:
                        await getattr(entity, service_def["method"])(**params)
                        return
        _LOGGER.warning("Entity %s not found for service %s", entity_id, service_name)

    for service_name, definition in SERVICE_TO_METHOD.items():
        schema = definition.get("schema", SERVICE_SCHEMA)
        hass.services.async_register(DOMAIN, service_name, async_service_handler, schema=schema)
