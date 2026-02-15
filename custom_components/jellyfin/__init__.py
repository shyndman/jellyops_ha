"""The jellyfin component."""

import asyncio
import collections.abc
import json
import logging
import time
import traceback
import uuid
from datetime import timedelta
from collections.abc import Mapping
from typing import Any, TypedDict, cast

import dateutil.parser as dt
import homeassistant.helpers.config_validation as cv  # pylint: disable=import-error
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (  # pylint: disable=import-error
    ATTR_ENTITY_ID,
    ATTR_ID,
    CONF_URL,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.dispatcher import (  # pylint: disable=import-error
    async_dispatcher_send,
)
from jellyfin_apiclient_python import JellyfinClient

from .const import (
    ATTR_PAGE,
    ATTR_PLAYLIST,
    ATTR_SEARCH_TERM,
    CLIENT_VERSION,
    DOMAIN,
    PLAYABLE_ITEM_TYPES,
    PLAYLISTS,
    SERVICE_BROWSE,
    SERVICE_DELETE,
    SERVICE_SCAN,
    SERVICE_SEARCH,
    SERVICE_YAMC_SETPAGE,
    SERVICE_YAMC_SETPLAYLIST,
    SIGNAL_STATE_UPDATED,
    STATE_IDLE,
    STATE_OFF,
    STATE_PAUSED,
)
from .view import JellyfinImageView

# Re-import after view import to continue the const imports
from .const import (
    STATE_PLAYING,
    USER_APP_NAME,
    YAMC_PAGE_SIZE,
)
from .models import (
    BaseItemDtoQueryResult,
    JellyfinEntryData,
    MediaSourceInfo,
    PlaybackInfoResponse,
    SessionInfoDto,
    SystemInfo,
    UpcomingCardDefaults,
    UpcomingCardItem,
    UpcomingCardPayload,
    YamcCardDefaults,
    YamcCardItem,
    YamcCardPayload,
)
from .url import normalize_server_url

_LOGGER = logging.getLogger(__name__)


class _SessionsEventData(TypedDict):
    """WebSocket event data for Sessions events."""

    value: list[dict[str, Any]]


PLATFORMS = ["sensor", "media_player"]
_update_unlistener: collections.abc.Callable[[], None] | None = None
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=30)

SERVICE_SCHEMA = vol.Schema({})

SCAN_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    }
)
YAMC_SETPAGE_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_PAGE): vol.All(vol.Coerce(int)),
    }
)
YAMC_SETPLAYLIST_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_PLAYLIST): cv.string}
)
DELETE_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_ID): cv.string}
)
SEARCH_SERVICE_SCHEMA = SERVICE_SCHEMA.extend(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_SEARCH_TERM): cv.string,
    }
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


def autolog(message: str) -> None:
    "Automatically log the current function details."
    import inspect

    # Get the previous frame in the stack, otherwise it would
    # be this function!!!
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        _LOGGER.debug("%s: <unknown frame>", message)
        return
    func = frame.f_back.f_code
    # Dump the message + the name of this function to the log.
    _LOGGER.debug(
        "%s: %s in %s:%i",
        message, func.co_name, func.co_filename, func.co_firstlineno,
    )


async def async_setup(hass: HomeAssistant, config: Mapping[str, object]) -> bool:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    # Register the image proxy view for media browser thumbnails
    hass.http.register_view(JellyfinImageView())

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    autolog("<<<")

    global _update_unlistener
    if _update_unlistener:
        _update_unlistener()

    if not config_entry.unique_id:
        hass.config_entries.async_update_entry(
            config_entry, unique_id=config_entry.title
        )

    # Merge entry data and options, then validate as JellyfinEntryData
    config_dict: dict[str, object] = dict(config_entry.data)
    config_dict.update(config_entry.options)
    if config_entry.options:
        hass.config_entries.async_update_entry(config_entry, data=config_dict, options={})

    config = JellyfinEntryData.model_validate(config_dict)

    _update_unlistener = config_entry.add_update_listener(_update_listener)

    hass.data[DOMAIN][config.url] = {
        "entry_id": config_entry.entry_id,
    }
    _jelly = JellyfinClientManager(hass, config)
    _jelly.entry_id = config_entry.entry_id
    try:
        await _jelly.connect()
        hass.data[DOMAIN][config.url]["manager"] = _jelly
    except Exception:
        _LOGGER.error("Cannot connect to Jellyfin server.")
        raise ConfigEntryNotReady

    async def async_service_handler(service: object) -> None:
        """Map services to methods"""
        service_name = getattr(service, "service", None)
        service_data = getattr(service, "data", {})
        method = SERVICE_TO_METHOD.get(service_name) if service_name else None
        if method is None:
            _LOGGER.warning("Unknown service: %s", service_name)
            return

        method_name = method["method"]
        params = {
            key: value for key, value in service_data.items() if key != "entity_id"
        }

        entity_id = service_data.get(ATTR_ENTITY_ID)

        for sensor in hass.data[DOMAIN][config.url]["sensor"]["entities"]:
            if sensor.entity_id == entity_id:
                await getattr(sensor, method_name)(**params)

        for media_player in hass.data[DOMAIN][config.url]["media_player"][
            "entities"
        ]:
            if media_player.entity_id == entity_id:
                await getattr(media_player, method_name)(**params)

    for my_service in SERVICE_TO_METHOD:
        schema = SERVICE_TO_METHOD[my_service].get("schema", SERVICE_SCHEMA)
        hass.services.async_register(
            DOMAIN, my_service, async_service_handler, schema=schema
        )

    # Start the client and fetch server info BEFORE setting up entity platforms.
    # This ensures _info is available when entities access device_info during registration.
    await _jelly.start()

    for platform in PLATFORMS:
        hass.data[DOMAIN][config.url][platform] = {}
        hass.data[DOMAIN][config.url][platform]["entities"] = []
        await hass.config_entries.async_forward_entry_setups(config_entry, [platform])

    async_dispatcher_send(hass, SIGNAL_STATE_UPDATED)

    async def stop_jellyfin(event: object) -> None:
        """Stop Jellyfin connection."""
        await _jelly.stop()

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

    _jelly: JellyfinClientManager = hass.data[DOMAIN][config_entry.data.get(CONF_URL)][
        "manager"
    ]
    await _jelly.stop()

    return unload_ok


async def _update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update listener."""
    _LOGGER.debug("reload triggered")
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    entreg = entity_registry.async_get(hass)
    if entity_registry.async_entries_for_device(entreg, device_entry.id):
        return False
    return True


class JellyfinDevice:
    """Represents properties of a Jellyfin Device."""

    def __init__(
        self, session: SessionInfoDto, jf_manager: "JellyfinClientManager", device_key: str
    ):
        """Initialize Jellyfin device object."""
        self.jf_manager = jf_manager
        self.is_active = True
        self._device_key = device_key
        self.session = session

    @property
    def device_key(self) -> str:
        """Return the stable device key ({DeviceName}.{UserId})."""
        return self._device_key

    def update_session(self, session: SessionInfoDto) -> None:
        """Update session object."""
        self.session = session

    def set_active(self, active: bool) -> None:
        """Mark device as on/off."""
        self.is_active = active

    @property
    def session_id(self) -> str:
        """Return current session Id."""
        if self.session.Id is None:
            raise ValueError("Session.Id is unexpectedly None")
        return self.session.Id

    @property
    def unique_id(self) -> str:
        """Return device id."""
        if self.session.DeviceId is None:
            raise ValueError("Session.DeviceId is unexpectedly None")
        return self.session.DeviceId

    @property
    def name(self) -> str:
        """Return device name."""
        if self.session.DeviceName is None:
            raise ValueError("Session.DeviceName is unexpectedly None")
        return self.session.DeviceName

    @property
    def client(self) -> str | None:
        """Return client name."""
        return self.session.Client

    @property
    def username(self) -> str | None:
        """Return username."""
        return self.session.UserName

    @property
    def user_id(self) -> str:
        """Return the user ID for this session."""
        return self.session.UserId

    @property
    def media_title(self) -> str | None:
        """Return title currently playing."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.Name

    @property
    def media_season(self) -> int | None:
        """Season of current playing media (TV Show only)."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.ParentIndexNumber

    @property
    def media_series_title(self) -> str | None:
        """The title of the series of current playing media (TV Show only)."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.SeriesName

    @property
    def media_episode(self) -> int | None:
        """Episode of current playing media (TV Show only)."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.IndexNumber

    @property
    def media_album_name(self) -> str | None:
        """Album name of current playing media (Music track only)."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.Album

    @property
    def media_artist(self) -> str | list[str] | None:
        """Artist of current playing media (Music track only)."""
        if self.session.NowPlayingItem is None:
            return None
        artists = self.session.NowPlayingItem.Artists
        if artists is None:
            return None
        if len(artists) > 1:
            return artists[0]
        return artists

    @property
    def media_album_artist(self) -> str | None:
        """Album artist of current playing media (Music track only)."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.AlbumArtist

    @property
    def media_id(self) -> str | None:
        """Return id of currently playing media."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.Id

    @property
    def media_type(self) -> str | None:
        """Return type currently playing."""
        if self.session.NowPlayingItem is None:
            return None
        return self.session.NowPlayingItem.Type

    @property
    def media_image_url(self) -> str | None:
        """Image url of current playing media."""
        if not self.is_nowplaying:
            return None
        now_playing = self.session.NowPlayingItem
        if now_playing is None or now_playing.image_tags is None:
            return None

        image_tags = now_playing.image_tags
        if image_tags.Thumb is not None:
            image_type = "Thumb"
        elif image_tags.Primary is not None:
            image_type = "Primary"
        else:
            return None

        return self.jf_manager.api.artwork(self.media_id, image_type, 500)

    @property
    def media_position(self) -> float | None:
        """Return position currently playing."""
        if self.session.PlayState is None:
            return None
        position_ticks = self.session.PlayState.PositionTicks
        if position_ticks is None:
            return None
        return position_ticks / 10000000

    @property
    def media_runtime(self) -> float | None:
        """Return total runtime length."""
        if self.session.NowPlayingItem is None:
            return None
        runtime_ticks = self.session.NowPlayingItem.RunTimeTicks
        if runtime_ticks is None:
            return None
        return runtime_ticks / 10000000

    @property
    def media_percent_played(self) -> float | None:
        """Return media percent played."""
        position = self.media_position
        runtime = self.media_runtime
        if position is None or runtime is None:
            return None
        return (position / runtime) * 100

    @property
    def state(self) -> str:
        """Return current playstate of the device."""
        if not self.is_active:
            return STATE_OFF
        if self.session.NowPlayingItem is None:
            return STATE_IDLE
        if self.session.PlayState is not None and self.session.PlayState.IsPaused:
            return STATE_PAUSED
        return STATE_PLAYING

    @property
    def is_nowplaying(self) -> bool:
        """Return true if an item is currently active."""
        return self.state not in (STATE_IDLE, STATE_OFF)

    @property
    def supports_remote_control(self) -> bool:
        """Return remote control status."""
        return self.session.SupportsRemoteControl

    async def get_item(self, id: str):
        return await self.jf_manager.get_item(id)

    async def get_items(self, query: dict[str, object] | None = None) -> list[dict[str, object]]:
        return await self.jf_manager.get_items(self.user_id, query)

    async def get_artwork(self, media_id: str) -> tuple[str | None, str | None]:
        return await self.jf_manager.get_artwork(media_id)

    def get_artwork_url(self, media_id: str, type: str = "Primary") -> str:
        return self.jf_manager.get_artwork_url(media_id, type)

    async def set_playstate(self, state: str, pos: float = 0) -> None:
        """Send media commands to server."""
        params: dict[str, object] = {}
        if state == "Seek":
            params["seekPositionTicks"] = int(pos * 10000000)

        await self.jf_manager.set_playstate(self.session_id, state, params)

    def media_play(self):
        """Send play command to device."""
        return self.set_playstate("Unpause")

    def media_pause(self):
        """Send pause command to device."""
        return self.set_playstate("Pause")

    def media_stop(self):
        """Send stop command to device."""
        return self.set_playstate("Stop")

    def media_next(self):
        """Send next track command to device."""
        return self.set_playstate("NextTrack")

    def media_previous(self):
        """Send previous track command to device."""
        return self.set_playstate("PreviousTrack")

    async def seek(self, position: float):
        """Send seek command to device."""
        await self.set_playstate("Seek", position)

    async def play_media(self, media_id: str) -> None:
        await self.jf_manager.play_media(self.session_id, media_id)

    async def browse_item(self, media_id: str) -> None:
        await self.jf_manager.view_media(self.session_id, media_id)


class JellyfinClientManager:
    hass: HomeAssistant
    callback: collections.abc.Callable[[JellyfinClient, str, object], None]
    jf_client: JellyfinClient | None
    is_stopping: bool
    _event_loop: asyncio.AbstractEventLoop
    host: str
    _info: SystemInfo | None
    config: JellyfinEntryData
    server_url: str
    _yamc_cur_page: int
    _last_playlist: str
    _last_search: str
    thumbnail_cache: dict[str, str]
    entry_id: str

    def __init__(self, hass: HomeAssistant, config: JellyfinEntryData) -> None:
        self.hass = hass
        self.callback = lambda client, event_name, data: None
        self.jf_client = None
        self.is_stopping = True
        self._event_loop = hass.loop

        self.host = config.url
        self._info = None
        self._data: BaseItemDtoQueryResult | None = None
        self._yamc: BaseItemDtoQueryResult | None = None
        self._yamc_cur_page = 1
        self._last_playlist = ""
        self._last_search = ""
        self._yamc_streams: dict[str, dict[str, str | None]] = {}

        self.config = config
        self.server_url = ""

        # Cache for thumbnail URLs (media_id -> jellyfin_image_url)
        # Used by the image proxy view to fetch images on behalf of the browser
        self.thumbnail_cache = {}

        # Library item counts
        self._movie_count: int | None = None
        self._episode_count: int | None = None
        self._series_count: int | None = None

        self._sessions: list[SessionInfoDto] | None = None
        self._devices: dict[str, JellyfinDevice] = {}

        # Callbacks
        self._new_devices_callbacks: list[collections.abc.Callable[[object], None]] = []
        self._stale_devices_callbacks: list[collections.abc.Callable[[object], None]] = []
        self._update_callbacks: list[tuple[collections.abc.Callable[[object], None], str]] = []

    @property
    def _client(self) -> JellyfinClient:
        """Return the Jellyfin client, raising if not initialized."""
        if self.jf_client is None:
            raise RuntimeError("JellyfinClient not initialized - call login() first")
        return self.jf_client

    @staticmethod
    def expo(max_value: int | None = None) -> collections.abc.Generator[int]:
        n = 0
        while True:
            a = 2**n
            if max_value is None or a < max_value:
                yield a
                n += 1
            else:
                yield max_value

    @staticmethod
    def clean_none_dict_values(obj: object) -> object:
        """
        Recursively remove keys with a value of None
        """
        if not isinstance(obj, collections.abc.Iterable) or isinstance(obj, str):
            return obj

        queue = [obj]

        while queue:
            item = queue.pop()

            if isinstance(item, collections.abc.Mapping):
                mutable = isinstance(item, collections.abc.MutableMapping)
                remove = []

                for key, value in item.items():
                    if value is None and mutable:
                        remove.append(key)

                    elif isinstance(value, str):
                        continue

                    elif isinstance(value, collections.abc.Iterable):
                        queue.append(value)

                if mutable:
                    # Remove keys with None value
                    for key in remove:
                        item.pop(key)

            elif isinstance(item, collections.abc.Iterable):
                for value in item:
                    if value is None or isinstance(value, str):
                        continue
                    elif isinstance(value, collections.abc.Iterable):
                        queue.append(value)

        return obj

    async def connect(self):
        autolog(">>>")

        is_logged_in = await self.hass.async_add_executor_job(self.login)

        if is_logged_in:
            _LOGGER.info("Successfully added server.")
        else:
            raise ConfigEntryNotReady

    @staticmethod
    def client_factory(verify_ssl: bool, device_id: str):
        client = JellyfinClient(allow_multiple_clients=True)
        client.config.data["app.default"] = True
        client.config.data["app.name"] = USER_APP_NAME
        client.config.data["app.version"] = CLIENT_VERSION
        client.config.data["app.device_id"] = device_id
        client.config.data["auth.ssl"] = verify_ssl
        return client

    def login(self):
        autolog(">>>")

        try:
            self.server_url = normalize_server_url(self.config.url)
        except ValueError:
            _LOGGER.error("Invalid Jellyfin URL: %s", self.config.url)
            return False

        # Generate a deterministic device_id from the server URL
        device_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.server_url))
        self.jf_client = self.client_factory(self.config.verify_ssl, device_id)
        try:
            self._client.authenticate(
                {
                    "Servers": [
                        {
                            "AccessToken": self.config.api_key,
                            "address": self.server_url,
                        }
                    ]
                },
                discover=False,
            )
            # Set auth.user_id so {UserId} template substitution works in API calls
            if self.config.library_user_id:
                self._client.config.data["auth.user_id"] = self.config.library_user_id
            info = self._client.jellyfin.get_system_info()
        except Exception:
            _LOGGER.error("Unable to authenticate with Jellyfin.", exc_info=True)
            return False

        return info is not None

    async def start(self):
        autolog(">>>")

        def event(event_name: str, data: object) -> None:
            _LOGGER.debug("Event: %s", event_name)
            if event_name == "WebSocketConnect":
                self._client.wsc.send("SessionsStart", "0,1500")
            elif event_name == "WebSocketDisconnect":
                timeout_gen = self.expo(100)
                while not self.is_stopping:
                    timeout = next(timeout_gen)
                    _LOGGER.warning(
                        "No connection to server. Next try in {0} second(s)".format(
                            timeout
                        )
                    )
                    self._client.stop()
                    time.sleep(timeout)
                    if self.login():
                        self._client.callback = event
                        self._client.callback_ws = event
                        self._client.start(True)
                        break
            elif event_name in ("LibraryChanged", "UserDataChanged"):
            elif event_name in ("LibraryChanged", "UserDataChanged"):
                    autolog("LibraryChanged: trigger update")
                    sensor.schedule_update_ha_state(force_refresh=True)
            elif event_name == "Sessions":
                cleaned = cast(_SessionsEventData, self.clean_none_dict_values(data))
                raw = cleaned["value"]
                _LOGGER.debug("Sessions (WebSocket): %s", raw)
                self._sessions = [SessionInfoDto.model_validate(s) for s in raw]
                self.update_device_list()
                for sensor in self.hass.data[DOMAIN][self.host]["sensor"]["entities"]:
                    autolog("Sessions: trigger update")
                    sensor.schedule_update_ha_state(force_refresh=True)
            else:
                self.callback(self._client, event_name, data)

        self._client.callback = event
        self._client.callback_ws = event

        await self.hass.async_add_executor_job(self._client.start, True)
        self.is_stopping = False

        raw_info = await self.hass.async_add_executor_job(
            self._client.jellyfin._get, "System/Info"
        )
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

    async def stop(self):
        autolog("<<<")

        self.is_stopping = True
        await self.hass.async_add_executor_job(self._client.stop)

    async def _get_item_count(self, item_type: str) -> int:
        """Fetch the total count of items of a given type."""
        query = {
            "includeItemTypes": item_type,
            "recursive": "true",
            "limit": 0,
            "enableTotalRecordCount": "true",
        }
        raw = await self.hass.async_add_executor_job(
            self._client.jellyfin.items, "", "GET", query
        )
        result = BaseItemDtoQueryResult.model_validate(raw)
        return result.TotalRecordCount

    async def update_data(self):
        autolog("<<<")
        user_id = self.config.library_user_id

        # Fetch library item counts
        self._movie_count = await self._get_item_count("Movie")
        self._episode_count = await self._get_item_count("Episode")
        self._series_count = await self._get_item_count("Series")

        if self.config.generate_upcoming:
            if not user_id:
                _LOGGER.warning(
                    "Upcoming media enabled but no Jellyfin user configured; skipping update."
                )
                self._data = None
            else:
                raw_upcoming = await self.hass.async_add_executor_job(
                    self._client.jellyfin.shows,
                    "/NextUp",
                    {
                        "Limit": YAMC_PAGE_SIZE,
                        "UserId": user_id,
                        "fields": "DateCreated,Studios,Genres",
                        "excludeItemTypes": "Folder",
                    },
                )
                self._data = BaseItemDtoQueryResult.model_validate(raw_upcoming)

        if self.config.generate_yamc:
            if not user_id:
                _LOGGER.warning(
                    "YAMC data enabled but no Jellyfin user configured; skipping update."
                )
                self._yamc = None
                self._yamc_streams = {}
            else:
                query = {
                    "startIndex": (self._yamc_cur_page - 1) * YAMC_PAGE_SIZE,
                    "limit": YAMC_PAGE_SIZE,
                    "userId": user_id,
                    "recursive": "true",
                    "fields": "DateCreated,Studios,Genres,Taglines,ProviderIds,Ratings,MediaStreams",
                    "collapseBoxSetItems": "false",
                    "excludeItemTypes": "Folder",
                }

                if not self._last_playlist:
                    self._last_playlist = "latest_movies"

                if self._last_search:
                    query["searchTerm"] = self._last_search
                elif self._last_playlist:
                    for pl in PLAYLISTS:
                        if pl["name"] == self._last_playlist:
                            query.update(pl["query"])

                if self._last_playlist == "nextup":
                    raw_yamc = await self.hass.async_add_executor_job(
                        self._client.jellyfin.shows, "/NextUp", query
                    )
                else:
                    raw_yamc = await self.hass.async_add_executor_job(
                        self._client.jellyfin.items, "", "GET", query
                    )

                self._yamc = BaseItemDtoQueryResult.model_validate(raw_yamc)
                self._yamc_streams = {}

                for item in self._yamc.Items:
                    # Only fetch stream URLs for directly playable types
                    if item.Type in PLAYABLE_ITEM_TYPES:
                        stream_url, _, info = await self.get_stream_url(item.Id, item.Type)
                        self._yamc_streams[item.Id] = {
                            "stream_url": stream_url,
                            "info": info,
                        }

    def update_device_list(self):
        """Update device list."""
        autolog(">>>")
        if self._sessions is None:
            _LOGGER.error("Error updating Jellyfin devices.")
            return

        try:
            new_devices: list[JellyfinDevice] = []
            active_devices: list[str] = []
            dev_update = False
            for session in self._sessions:
                # Skip devices without custom names (e.g., web browsers with
                # timestamp-based DeviceIds)
                if not session.HasCustomDeviceName:
                    continue

                # Guard against null DeviceName (schema allows it, shouldn't
                # happen when HasCustomDeviceName=true)
                device_name = session.DeviceName
                if not device_name:
                    _LOGGER.warning(
                        "Session has HasCustomDeviceName=true but DeviceName is "
                        "null/empty. UserId=%s, DeviceId=%s",
                        session.UserId,
                        session.DeviceId,
                    )
                    continue

                dev_key = f"{session.UserId}{device_name}"

                if session.NowPlayingItem is not None:
                    _LOGGER.debug(
                        "Session msg on %s of type: %s",
                        dev_key,
                        session.NowPlayingItem.Type,
                    )

                active_devices.append(dev_key)
                if dev_key not in self._devices:
                    _LOGGER.debug(
                        "New Jellyfin DeviceID: %s. Adding to device list.", dev_key
                    )
                    new = JellyfinDevice(session, self, dev_key)
                    self._devices[dev_key] = new
                    new_devices.append(new)
                else:
                    # Before we send in new data check for changes to state
                    # to decide if we need to fire the update callback
                    if not self._devices[dev_key].is_active:
                        # Device wasn't active on the last update
                        # We need to fire a device callback to let subs now
                        dev_update = True

                    do_update = self.update_check(self._devices[dev_key], session)
                    self._devices[dev_key].update_session(session)
                    self._devices[dev_key].set_active(True)
                    if dev_update:
                        self._do_new_devices_callback(0)
                        dev_update = False
                    if do_update:
                        self._do_update_callback(dev_key)

            # Need to check for new inactive devices and flag
            for dev_id in self._devices:
                if dev_id not in active_devices:
                    # Device no longer active
                    if self._devices[dev_id].is_active:
                        self._devices[dev_id].set_active(False)
                        self._do_update_callback(dev_id)
                        self._do_stale_devices_callback(dev_id)

            # Call device callback if new devices were found.
            if new_devices:
                self._do_new_devices_callback(0)
        except Exception:
            _LOGGER.critical(traceback.format_exc())
            raise

    def update_check(self, existing: JellyfinDevice, new_session: SessionInfoDto) -> bool:
        """Check device state to see if we need to fire the callback.

        Returns True if either state is 'Playing', or on any state transition.
        Returns False if both states are: 'Paused', 'Idle', or 'Off'.
        """
        autolog(">>>")

        old_state = existing.state

        # Determine new state from session
        if new_session.NowPlayingItem is not None:
            if new_session.PlayState is not None and new_session.PlayState.IsPaused:
                new_state = STATE_PAUSED
            else:
                new_state = STATE_PLAYING
        else:
            new_state = STATE_IDLE

        if old_state == STATE_PLAYING or new_state == STATE_PLAYING:
            return True
        elif old_state != new_state:
            return True
        else:
            return False

    @property
    def info(self) -> SystemInfo | None:
        if self.is_stopping:
            return None

        return self._info

    @property
    def movie_count(self) -> int | None:
        """Total number of movies in the library."""
        return self._movie_count

    @property
    def episode_count(self) -> int | None:
        """Total number of episodes in the library."""
        return self._episode_count

    @property
    def series_count(self) -> int | None:
        """Total number of series in the library."""
        return self._series_count

    @property
    def connected_session_count(self) -> int:
        """Number of active sessions."""
        if self._sessions is None:
            return 0
        return sum(1 for s in self._sessions if s.IsActive)

    @property
    def playing_session_count(self) -> int:
        """Number of sessions with media loaded."""
        if self._sessions is None:
            return 0
        return sum(1 for s in self._sessions if s.NowPlayingItem is not None)

    @property
    def playing_sessions(self) -> list[dict[str, object]]:
        """Active sessions with playback metadata."""
        if self._sessions is None:
            return []
        sessions: list[dict[str, object]] = []
        for session in self._sessions:
            if session.NowPlayingItem is None:
                continue
            sessions.append(
                {
                    "username": session.UserName,
                    "device_name": session.DeviceName,
                    "item_name": session.NowPlayingItem.Name,
                    "state": session.PlayState.model_dump() if session.PlayState else None,
                }
            )
        return sessions


    @property
    def data(self):
        """Upcoming card data"""
        if not self.config.generate_upcoming or self.is_stopping:
            return None

        payload: UpcomingCardPayload = [
            UpcomingCardDefaults(
                title_default="$title",
                line1_default="$episode",
                line2_default="$release",
                line3_default="$rating - $runtime",
                line4_default="$number - $studio",
                icon="mdi:arrow-down-bold-circle",
            )
        ]

        if self._data is None or not self._data.Items:
            return payload

        for item in self._data.Items:
            title = item.SeriesName or item.Name
            episode = item.Name
            if not title or not episode:
                raise ValueError(
                    f"Upcoming item missing required fields: Id={item.Id}, Title={title}, Episode={episode}"
                )

            studios = ",".join(o.Name for o in item.Studios or [] if o.Name) or None
            genres = ",".join(item.Genres) if item.Genres else None
            runtime_minutes = (
                int(item.RunTimeTicks / 10000000 / 60) if item.RunTimeTicks else None
            )
            number = None
            if item.ParentIndexNumber is not None and item.IndexNumber is not None:
                number = f"S{item.ParentIndexNumber}E{item.IndexNumber}"

            payload.append(
                UpcomingCardItem(
                    title=title,
                    episode=episode,
                    flag=False,
                    airdate=item.DateCreated,
                    number=number,
                    runtime=runtime_minutes,
                    studio=studios,
                    release=dt.parse(item.PremiereDate).__format__("%d/%m/%Y")
                    if item.PremiereDate
                    else None,
                    poster=self.get_artwork_url(item.Id),
                    fanart=self.get_artwork_url(item.Id, "Backdrop"),
                    genres=genres,
                    rating=None,
                    stream_url=None,
                    info_url=None,
                )
            )

        return payload

    @property
    def yamc(self):
        """Upcoming card data"""
        if not self.config.generate_yamc or self.is_stopping:
            return None

        payload: YamcCardPayload = [
            YamcCardDefaults(
                title_default="$title",
                line1_default="$tagline",
                line2_default="$empty",
                line3_default="$release - $genres",
                line4_default="$runtime - $rating - $info",
                line5_default="$date",
                text_link_default="$info_url",
                link_default="$stream_url",
            )
        ]

        if self._yamc is None or not self._yamc.Items:
            return payload

        for item in self._yamc.Items:
            user_data = item.UserData
            base_flag = bool(user_data and user_data.Played)
            progress = 0.0
            if user_data and user_data.PlayedPercentage is not None:
                progress = user_data.PlayedPercentage
            elif base_flag:
                progress = 100.0

            rating = None
            if item.CommunityRating is not None:
                rating = "\N{BLACK STAR} {}".format(round(item.CommunityRating, 1))
            elif item.CriticRating is not None:
                rating = "\N{BLACK STAR} {}".format(round(item.CriticRating / 10, 1))

            studios = ",".join(o.Name for o in item.Studios or [] if o.Name) or None
            genres = ",".join(item.Genres) if item.Genres else None
            stream_meta = self._yamc_streams.get(item.Id, {})
            stream_url = stream_meta.get("stream_url")
            stream_info = stream_meta.get("info")
            number = None
            if item.ParentIndexNumber is not None and item.IndexNumber is not None:
                number = f"S{item.ParentIndexNumber}E{item.IndexNumber}"

            info_url = None
            if item.ProviderIds:
                if item.Type == "Movie" and "Imdb" in item.ProviderIds:
                    info_url = f"https://trakt.tv/search/imdb/{item.ProviderIds['Imdb']}?id_type=movie"
                elif item.Type == "Series" and "Imdb" in item.ProviderIds:
                    info_url = f"https://trakt.tv/search/imdb/{item.ProviderIds['Imdb']}?id_type=series"
                elif item.Type == "Episode" and "Imdb" in item.ProviderIds:
                    info_url = f"https://trakt.tv/search/imdb/{item.ProviderIds['Imdb']}?id_type=episode"
                elif (
                    item.Type == "MusicAlbum" and "MusicBrainzAlbum" in item.ProviderIds
                ):
                    info_url = f"https://musicbrainz.org/album/{item.ProviderIds['MusicBrainzAlbum']}"
                elif (
                    item.Type == "MusicArtist"
                    and "MusicBrainzArtist" in item.ProviderIds
                ):
                    info_url = f"https://musicbrainz.org/artist/{item.ProviderIds['MusicBrainzArtist']}"

            title = item.Name or item.SeriesName
            if not title:
                raise ValueError(f"YAMC item missing title: Id={item.Id}")

            episode_value: str | None = None
            tagline_value: str | None = None
            flag_value = base_flag
            release_value: str | None = None
            fanart_type = "Primary"

            if item.Type == "Movie":
                episode_value = None
                tagline_value = item.Taglines[0] if item.Taglines else ""
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%Y")
                    if item.PremiereDate
                    else None
                )
                fanart_type = "Backdrop"
            elif item.Type == "Series":
                episode_value = item.Name
                tagline_value = item.Name
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%d/%m/%Y")
                    if item.PremiereDate
                    else None
                )
                fanart_type = "Backdrop"
            elif item.Type == "Episode":
                episode_value = item.Name
                tagline_value = item.Name
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%d/%m/%Y")
                    if item.PremiereDate
                    else None
                )
                fanart_type = "Primary"
            elif item.Type == "MusicAlbum":
                episode_value = None
                tagline_value = ",".join(item.Artists) if item.Artists else None
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%Y")
                    if item.PremiereDate
                    else None
                )
                flag_value = False
                progress = 0.0
            elif item.Type == "MusicArtist":
                episode_value = None
                tagline_value = ",".join(item.Artists) if item.Artists else None
                release_value = (
                    dt.parse(item.DateCreated).__format__("%Y")
                    if item.DateCreated
                    else None
                )
                flag_value = False
                progress = 0.0
            else:
                episode_value = item.Name
                tagline_value = item.Name
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%d/%m/%Y")
                    if item.PremiereDate
                    else None
                )
                flag_value = False
                progress = 0.0

            payload.append(
                YamcCardItem(
                    id=item.Id,
                    type=item.Type,
                    title=title,
                    episode=episode_value,
                    tagline=tagline_value,
                    flag=flag_value,
                    airdate=item.DateCreated,
                    number=number,
                    runtime=int(item.RunTimeTicks / 10000000 / 60)
                    if item.RunTimeTicks
                    else None,
                    studio=studios,
                    release=release_value,
                    poster=self.get_artwork_url(item.Id),
                    fanart=self.get_artwork_url(item.Id, fanart_type),
                    genres=genres,
                    progress=progress,
                    rating=rating,
                    info=stream_info,
                    stream_url=stream_url,
                    info_url=info_url,
                )
            )

        attrs = {}
        attrs["last_search"] = self._last_search
        attrs["last_playlist"] = self._last_playlist
        attrs["playlists"] = json.dumps(PLAYLISTS)
        attrs["total_items"] = min(50, self._yamc.TotalRecordCount)
        attrs["page"] = self._yamc_cur_page
        attrs["page_size"] = YAMC_PAGE_SIZE
        attrs["data"] = json.dumps(payload)

        return attrs

    async def trigger_scan(self):
        await self.hass.async_add_executor_job(
            self._client.jellyfin._post, "Library/Refresh"
        )

    async def delete_item(self, id: str) -> None:
        await self.hass.async_add_executor_job(
            self._client.jellyfin.items, f"/{id}", "DELETE"
        )
        await self.update_data()

    async def search_item(self, search_term: str) -> None:
        self._yamc_cur_page = 1
        self._last_search = search_term
        await self.update_data()

    async def yamc_set_page(self, page: int) -> None:
        self._yamc_cur_page = page
        await self.update_data()

    async def yamc_set_playlist(self, playlist: str) -> None:
        self._last_search = ""
        self._last_playlist = playlist
        await self.update_data()

    def get_server_url(self) -> str:
        return self._client.config.data["auth.server"]

    def get_auth_token(self) -> str:
        return self._client.config.data["auth.token"]

    async def get_item(self, id: str) -> dict[str, object]:
        return await self.hass.async_add_executor_job(
            self._client.jellyfin.get_item, id
        )

    async def get_items(
        self, user_id: str, query: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
        # Temporarily set auth.user_id for {UserId} template substitution
        self._client.config.data["auth.user_id"] = user_id
        response = await self.hass.async_add_executor_job(
            self._client.jellyfin.users, "/Items", "GET", query
        )
        # _LOGGER.debug("get_items: %s | %s", str(query), str(response))
        return response["Items"]

    async def set_playstate(self, session_id: str, state: str, params: dict[str, object]) -> None:
        await self.hass.async_add_executor_job(
            self._client.jellyfin.post_session,
            session_id,
            "Playing/%s" % state,
            params,
        )

    async def play_media(self, session_id: str, media_id: str) -> None:
        params = {"playCommand": "PlayNow", "itemIds": media_id}
        await self.hass.async_add_executor_job(
            self._client.jellyfin.post_session, session_id, "Playing", params
        )

    async def view_media(self, session_id: str, media_id: str) -> None:
        item = await self.hass.async_add_executor_job(
            self._client.jellyfin.get_item, media_id
        )
        _LOGGER.debug("view_media: %s", str(item))

        params = {
            "itemId": media_id,
            "itemType": item["Type"],
            "itemName": item["Name"],
        }
        await self.hass.async_add_executor_job(
            self._client.jellyfin.post_session, session_id, "Viewing", params
        )

    async def get_artwork(
        self, media_id: str, artwork_type: str = "Primary"
    ) -> tuple[str | None, str | None]:
        query = {"format": "PNG", "maxWidth": 500, "maxHeight": 500}
        image = await self.hass.async_add_executor_job(
            self._client.jellyfin.items,
            "GET",
            "%s/Images/%s" % (media_id, artwork_type),
            query,
        )
        if image is not None:
            return (image, "image/png")

        return (None, None)

    def get_artwork_url(self, media_id: str, artwork_type: str = "Primary") -> str:
        return self._client.jellyfin.artwork(media_id, artwork_type, 500)

    async def get_play_info(self, media_id: str, profile: object) -> object:
        return await self.hass.async_add_executor_job(
            self._client.jellyfin.get_play_info, media_id, profile
        )

    async def get_stream_url(
        self, media_id: str, media_content_type: str
    ) -> tuple[str | None, str | None, str | None]:
        profile = {
            "Name": USER_APP_NAME,
            "MaxStreamingBitrate": 25000 * 1000,
            "MusicStreamingTranscodingBitrate": 1920000,
            "TimelineOffsetSeconds": 5,
            "TranscodingProfiles": [
                {
                    "Type": "Audio",
                    "Container": "mp3",
                    "Protocol": "http",
                    "AudioCodec": "mp3",
                    "MaxAudioChannels": "2",
                },
                {
                    "Type": "Video",
                    "Container": "mp4",
                    "Protocol": "http",
                    "AudioCodec": "aac,mp3,opus,flac,vorbis",
                    "VideoCodec": "h264,mpeg4,mpeg2video",
                    "MaxAudioChannels": "6",
                },
                {"Container": "jpeg", "Type": "Photo"},
            ],
            "DirectPlayProfiles": [
                {"Type": "Audio", "Container": "mp3", "AudioCodec": "mp3"},
                {"Type": "Audio", "Container": "m4a,m4b", "AudioCodec": "aac"},
                {
                    "Type": "Video",
                    "Container": "mp4,m4v",
                    "AudioCodec": "aac,mp3,opus,flac,vorbis",
                    "VideoCodec": "h264,mpeg4,mpeg2video",
                    "MaxAudioChannels": "6",
                },
            ],
            "ResponseProfiles": [],
            "ContainerProfiles": [],
            "CodecProfiles": [],
            "SubtitleProfiles": [
                {"Format": "srt", "Method": "External"},
                {"Format": "srt", "Method": "Embed"},
                {"Format": "ass", "Method": "External"},
                {"Format": "ass", "Method": "Embed"},
                {"Format": "sub", "Method": "Embed"},
                {"Format": "sub", "Method": "External"},
                {"Format": "ssa", "Method": "Embed"},
                {"Format": "ssa", "Method": "External"},
                {"Format": "smi", "Method": "Embed"},
                {"Format": "smi", "Method": "External"},
                # Jellyfin currently refuses to serve these subtitle types as external.
                {"Format": "pgssub", "Method": "Embed"},
                # {
                #    "Format": "pgssub",
                #    "Method": "External"
                # },
                {"Format": "dvdsub", "Method": "Embed"},
                # {
                #    "Format": "dvdsub",
                #    "Method": "External"
                # },
                {"Format": "pgs", "Method": "Embed"},
                # {
                #    "Format": "pgs",
                #    "Method": "External"
                # }
            ],
        }

        raw_playback_info = await self.get_play_info(media_id, profile)
        _LOGGER.debug("playbackinfo: %s", str(raw_playback_info))
        if raw_playback_info is None:
            _LOGGER.error(f"No playback info for item id {media_id}")
            return (None, None, None)

        playback_info = PlaybackInfoResponse.model_validate(raw_playback_info)
        if playback_info.MediaSources is None or not playback_info.MediaSources:
            _LOGGER.error(f"No media sources for item id {media_id}")
            return (None, None, None)

        selected: MediaSourceInfo | None = None
        weight_selected = 0.0
        for media_source in playback_info.MediaSources:
            weight = (1 if media_source.SupportsDirectStream else 0) * 50000 + (
                media_source.Bitrate or 0
            ) / 1000
            if weight > weight_selected:
                weight_selected = weight
                selected = media_source

        if selected is None:
            return (None, None, None)

        url = ""
        mimetype = "none/none"
        info = "Not playable"
        if selected.SupportsDirectStream:
            if selected.Container is None or selected.Id is None:
                raise ValueError("DirectStream source missing Container or Id")
            if media_content_type in ("Audio", "track"):
                mimetype = "audio/" + selected.Container
                url = (
                    self.get_server_url()
                    + "/Audio/%s/stream?static=true&MediaSourceId=%s&api_key=%s"
                    % (media_id, selected.Id, self.get_auth_token())
                )
            else:
                mimetype = "video/" + selected.Container
                url = (
                    self.get_server_url()
                    + "/Videos/%s/stream?static=true&MediaSourceId=%s&api_key=%s"
                    % (media_id, selected.Id, self.get_auth_token())
                )

        elif selected.SupportsTranscoding:
            if selected.TranscodingUrl is None:
                raise ValueError("Transcoding source missing TranscodingUrl")
            url = self.get_server_url() + selected.TranscodingUrl
            container = selected.TranscodingContainer or selected.Container
            if container is None:
                raise ValueError("Transcoding source missing Container")
            if media_content_type in ("Audio", "track"):
                mimetype = "audio/" + container
            else:
                mimetype = "video/" + container

        if selected.MediaStreams is not None:
            if media_content_type in ("Audio", "track"):
                for stream in selected.MediaStreams:
                    if stream.Type == "Audio":
                        info = f"{stream.Codec} {stream.SampleRate}Hz"
                        break
            else:
                for stream in selected.MediaStreams:
                    if stream.Type == "Video":
                        info = f"{stream.Width}x{stream.Height} {stream.Codec}"
                        break

        _LOGGER.debug("stream info: %s - url: %s", info, url)
        return (url, mimetype, info)

    @property
    def api(self):
        """Return the api."""
        return self._client.jellyfin

    @property
    def devices(self) -> Mapping[str, JellyfinDevice]:
        """Return devices dictionary."""
        return self._devices

    @property
    def is_available(self):
        return not self.is_stopping

    # Callbacks

    def add_new_devices_callback(
        self, callback: collections.abc.Callable[[object], None]
    ) -> None:
        """Register as callback for when new devices are added."""
        self._new_devices_callbacks.append(callback)
        _LOGGER.debug("Added new devices callback to %s", callback)

    def _do_new_devices_callback(self, msg: object) -> None:
        """Call registered callback functions."""
        for callback in self._new_devices_callbacks:
            _LOGGER.debug("Devices callback %s", callback)
            self._event_loop.call_soon(callback, msg)

    def add_stale_devices_callback(
        self, callback: collections.abc.Callable[[object], None]
    ) -> None:
        """Register as callback for when stale devices exist."""
        self._stale_devices_callbacks.append(callback)
        _LOGGER.debug("Added stale devices callback to %s", callback)

    def _do_stale_devices_callback(self, msg: object) -> None:
        """Call registered callback functions."""
        for callback in self._stale_devices_callbacks:
            _LOGGER.debug("Stale Devices callback %s", callback)
            self._event_loop.call_soon(callback, msg)

    def add_update_callback(
        self, callback: collections.abc.Callable[[object], None], device: str
    ) -> None:
        """Register as callback for when a matching device changes."""
        self._update_callbacks.append((callback, device))
        _LOGGER.debug("Added update callback to %s on %s", callback, device)

    def remove_update_callback(
        self, callback: collections.abc.Callable[[object], None], device: str
    ) -> None:
        """Remove a registered update callback."""
        if (callback, device) in self._update_callbacks:
            self._update_callbacks.remove((callback, device))
            _LOGGER.debug("Removed update callback %s for %s", callback, device)

    def _do_update_callback(self, msg: object) -> None:
        """Call registered callback functions."""
        for callback, device in self._update_callbacks:
            if device == msg:
                _LOGGER.debug(
                    "Update callback %s for device %s by %s", callback, device, msg
                )
                self._event_loop.call_soon(callback, msg)
