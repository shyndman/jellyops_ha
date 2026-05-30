"""Connection and API helpers for the Jellyfin client manager."""

from __future__ import annotations

import collections.abc
import json
import logging
import time
import uuid
from typing import Any, TypedDict, TYPE_CHECKING, cast

from homeassistant.exceptions import ConfigEntryNotReady
from jellyfin_apiclient_python import JellyfinClient

from ..const import DOMAIN, USER_APP_NAME, CLIENT_VERSION
from ..helpers import autolog
from ..models import JellyfinEntryData, SessionInfoDto, SystemInfo
from ..url import normalize_server_url

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class _SessionsEventData(TypedDict):
    """Typed dict for Jellyfin WebSocket session payloads."""

    value: list[dict[str, Any]]


class ConnectionMixin:
    """Mixin providing connection/authentication facilities."""

    hass: HomeAssistant
    config: JellyfinEntryData
    callback: collections.abc.Callable[[JellyfinClient, str, object], None]
    jf_client: JellyfinClient | None
    is_stopping: bool
    _info: SystemInfo | None
    server_url: str

    def __init__(self) -> None:
        self.callback = lambda client, event_name, data: None
        self.jf_client = None
        self.is_stopping = True
        self._event_loop = self.hass.loop
        self._info = None
        self.server_url = ""
        super().__init__()

    @property
    def _client(self) -> JellyfinClient:
        if self.jf_client is None:
            raise RuntimeError("JellyfinClient not initialized - call login() first")
        return self.jf_client

    @staticmethod
    def expo(max_value: int | None = None) -> collections.abc.Generator[int, None, None]:
        n = 0
        while True:
            value = 2**n
            if max_value is None or value < max_value:
                yield value
                n += 1
            else:
                yield max_value

    @staticmethod
    def clean_none_dict_values(obj: object) -> object:
        if not isinstance(obj, collections.abc.Iterable) or isinstance(obj, str):
            return obj
        queue = [obj]
        while queue:
            item = queue.pop()
            if isinstance(item, collections.abc.Mapping):
                mutable = isinstance(item, collections.abc.MutableMapping)
                remove: list[str] = []
                for key, value in item.items():
                    if value is None and mutable:
                        remove.append(key)
                    elif isinstance(value, str):
                        continue
                    elif isinstance(value, collections.abc.Iterable):
                        queue.append(value)
                if mutable:
                    for key in remove:
                        item.pop(key)
            elif isinstance(item, collections.abc.Iterable):
                for value in item:
                    if value is None or isinstance(value, str):
                        continue
                    if isinstance(value, collections.abc.Iterable):
                        queue.append(value)
        return obj

    async def connect(self) -> None:
        autolog(">>>")
        is_logged_in = await self.hass.async_add_executor_job(self.login)
        if is_logged_in:
            _LOGGER.info("Successfully added server.")
            return
        raise ConfigEntryNotReady

    @staticmethod
    def client_factory(verify_ssl: bool, device_id: str) -> JellyfinClient:
        client = JellyfinClient(allow_multiple_clients=True)
        client.config.data["app.default"] = True
        client.config.data["app.name"] = USER_APP_NAME
        client.config.data["app.version"] = CLIENT_VERSION
        client.config.data["app.device_id"] = device_id
        client.config.data["auth.ssl"] = verify_ssl
        return client

    def login(self) -> bool:
        autolog(">>>")
        try:
            self.server_url = normalize_server_url(self.config.url)
        except ValueError:
            _LOGGER.error("Invalid Jellyfin URL: %s", self.config.url)
            return False
        device_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.server_url))
        self.jf_client = self.client_factory(self.config.verify_ssl, device_id)
        try:
            self._client.authenticate(
                {
                    "Servers": [
                        {"AccessToken": self.config.api_key, "address": self.server_url}
                    ]
                },
                discover=False,
            )
            if self.config.library_user_id:
                self._client.config.data["auth.user_id"] = self.config.library_user_id
            info = self._client.jellyfin.get_system_info()
        except Exception:
            _LOGGER.error("Unable to authenticate with Jellyfin.", exc_info=True)
            return False
        return info is not None

    async def start(self) -> None:
        autolog(">>>")

        def event(event_name: str, data: object) -> None:
            _LOGGER.debug(
                "Event payload JSON: %s",
                json.dumps(
                    {"event_name": event_name, "payload": data},
                    default=str,
                    ensure_ascii=False,
                ),
            )
            if event_name == "WebSocketConnect":
                self._client.wsc.send("SessionsStart", "0,1500")
            elif event_name == "WebSocketDisconnect":
                timeout_gen = self.expo(100)
                while not self.is_stopping:
                    timeout = next(timeout_gen)
                    _LOGGER.warning(
                        "No connection to server. Next try in %s second(s)", timeout
                    )
                    self._client.stop()
                    time.sleep(timeout)
                    if self.login():
                        self._client.callback = event
                        self._client.callback_ws = event
                        self._client.start(True)
                        break
            elif event_name in ("LibraryChanged", "UserDataChanged"):
                autolog("LibraryChanged: trigger update")
                # A library change means the item counts on the server changed, so
                # force_refresh re-runs update_data() (which also refreshes the
                # system info backing the update-available binary sensor). These
                # events are infrequent.
                self._refresh_entities(("sensor", "binary_sensor"), force_refresh=True)
            elif event_name == "Sessions":
                cleaned = cast(_SessionsEventData, self.clean_none_dict_values(data))
                raw = cleaned["value"]
                _LOGGER.debug("Sessions (WebSocket): %s", raw)
                self._sessions = [SessionInfoDto.model_validate(s) for s in raw]
                _LOGGER.debug(
                    "Sessions counts from WebSocket: total=%s active=%s playing=%s transcoding=%s",
                    len(self._sessions),
                    self.connected_session_count,
                    self.playing_session_count,
                    self.transcoding_session_count,
                )
                self.update_device_list()
                autolog("Sessions: trigger update")
                # Session data is already refreshed in self._sessions above, so the
                # sensors only need to re-publish state. Avoid force_refresh here: it
                # would re-run update_data()'s library HTTP queries on every Sessions
                # push (~every 1.5s).
                self._refresh_entities(("sensor",))
            else:
                self.callback(self._client, event_name, data)

        self._client.callback = event
        self._client.callback_ws = event
        await self.hass.async_add_executor_job(self._client.start, True)
        self.is_stopping = False
        raw_info = await self.hass.async_add_executor_job(self._client.jellyfin._get, "System/Info")
        self._info = SystemInfo.model_validate(raw_info)
        raw_sessions = cast(
            list[dict[str, Any]],
            self.clean_none_dict_values(
                await self.hass.async_add_executor_job(self._client.jellyfin._get, "Sessions")
            ),
        )
        _LOGGER.debug("Sessions (initial fetch): %s", raw_sessions)
        self._sessions = [SessionInfoDto.model_validate(s) for s in raw_sessions]
        await self.update_data()

    async def refresh_system_info(self) -> None:
        """Re-fetch System/Info so derived sensors (e.g. update available) stay current."""
        raw_info = await self.hass.async_add_executor_job(
            self._client.jellyfin._get, "System/Info"
        )
        self._info = SystemInfo.model_validate(raw_info)

    def _refresh_entities(
        self, platforms: tuple[str, ...], force_refresh: bool = False
    ) -> None:
        """Schedule a state refresh for the given platforms' entities.

        Runs on the websocket client's thread, so a snapshot of each entity
        list is iterated to avoid racing with adds/removes on the loop thread.
        """
        host_data = self.hass.data[DOMAIN][self.host]
        for platform in platforms:
            bucket = host_data.get(platform)
            if not bucket:
                continue
            for entity in list(bucket["entities"]):
                entity.schedule_update_ha_state(force_refresh=force_refresh)

    async def stop(self) -> None:
        autolog("<<<")
        self.is_stopping = True
        await self.hass.async_add_executor_job(self._client.stop)

    def get_server_url(self) -> str:
        return self._client.config.data["auth.server"]

    def get_auth_token(self) -> str:
        return self._client.config.data["auth.token"]

    async def get_item(self, item_id: str) -> dict[str, object]:
        return await self.hass.async_add_executor_job(self._client.jellyfin.get_item, item_id)

    async def get_items(
        self, user_id: str, query: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
        self._client.config.data["auth.user_id"] = user_id
        response = await self.hass.async_add_executor_job(
            self._client.jellyfin.users, "/Items", "GET", query
        )
        return response["Items"]

    async def set_playstate(
        self, session_id: str, state: str, params: dict[str, object]
    ) -> None:
        await self.hass.async_add_executor_job(
            self._client.jellyfin.post_session, session_id, f"Playing/{state}", params
        )

    async def play_media(self, session_id: str, media_id: str) -> None:
        params = {"playCommand": "PlayNow", "itemIds": media_id}
        await self.hass.async_add_executor_job(
            self._client.jellyfin.post_session, session_id, "Playing", params
        )

    async def view_media(self, session_id: str, media_id: str) -> None:
        item = await self.hass.async_add_executor_job(self._client.jellyfin.get_item, media_id)
        _LOGGER.debug("view_media: %s", item)
        params = {"itemId": media_id, "itemType": item["Type"], "itemName": item["Name"]}
        await self.hass.async_add_executor_job(
            self._client.jellyfin.post_session, session_id, "Viewing", params
        )

    @property
    def info(self) -> SystemInfo | None:
        if self.is_stopping:
            return None
        return self._info

    @property
    def api(self) -> object:
        return self._client.jellyfin

    @property
    def is_available(self) -> bool:
        return not self.is_stopping
