import sys
import types
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

custom_components_pkg = types.ModuleType("custom_components")
custom_components_pkg.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components_pkg)

jellyfin_pkg = types.ModuleType("custom_components.jellyfin")
jellyfin_pkg.__path__ = [str(ROOT / "custom_components" / "jellyfin")]
sys.modules.setdefault("custom_components.jellyfin", jellyfin_pkg)

client_manager_pkg = types.ModuleType("custom_components.jellyfin.client_manager")
client_manager_pkg.__path__ = [
    str(ROOT / "custom_components" / "jellyfin" / "client_manager")
]
client_manager_pkg.JellyfinClientManager = object
sys.modules.setdefault("custom_components.jellyfin.client_manager", client_manager_pkg)

ha_module = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
components_module = sys.modules.setdefault(
    "homeassistant.components", types.ModuleType("homeassistant.components")
)
sensor_module = sys.modules.setdefault(
    "homeassistant.components.sensor", types.ModuleType("homeassistant.components.sensor")
)


class _SensorEntity:
    pass


class _SensorStateClass:
    MEASUREMENT = "measurement"


sensor_module.SensorEntity = _SensorEntity
sensor_module.SensorStateClass = _SensorStateClass
components_module.sensor = sensor_module

config_entries_module = sys.modules.setdefault(
    "homeassistant.config_entries", types.ModuleType("homeassistant.config_entries")
)
config_entries_module.ConfigEntry = object

const_module = sys.modules.setdefault(
    "homeassistant.const", types.ModuleType("homeassistant.const")
)
const_module.CONF_URL = "url"
const_module.DEVICE_DEFAULT_NAME = "Jellyfin"
const_module.STATE_OFF = "off"
const_module.STATE_ON = "on"

core_module = sys.modules.setdefault(
    "homeassistant.core", types.ModuleType("homeassistant.core")
)
core_module.HomeAssistant = object

helpers_module = sys.modules.setdefault(
    "homeassistant.helpers", types.ModuleType("homeassistant.helpers")
)
entity_module = sys.modules.setdefault(
    "homeassistant.helpers.entity", types.ModuleType("homeassistant.helpers.entity")
)
entity_module.Entity = object
helpers_module.entity = entity_module
ha_module.components = components_module
ha_module.config_entries = config_entries_module
ha_module.const = const_module
ha_module.core = core_module
ha_module.helpers = helpers_module

sys.path.append(str(ROOT))

from custom_components.jellyfin.client_manager.sessions import SessionsMixin  # noqa: E402
from custom_components.jellyfin.const import STATE_IDLE, STATE_PAUSED  # noqa: E402
from custom_components.jellyfin.const import DOMAIN  # noqa: E402
from custom_components.jellyfin.models import SessionInfoDto  # noqa: E402
from custom_components.jellyfin.sensor import JellyfinItemCountSensor  # noqa: E402


def _session(**overrides) -> SessionInfoDto:
    data = {
        "Id": "session-1",
        "UserId": "user-1",
        "UserName": "Scott",
        "Client": "Jellyfin Web",
        "DeviceId": "device-1",
        "DeviceName": "Firefox",
        "LastActivityDate": "2026-04-27T12:00:00.000Z",
        "LastPlaybackCheckIn": "2026-04-27T11:59:00.000Z",
        "IsActive": True,
        "SupportsMediaControl": True,
        "SupportsRemoteControl": True,
        "HasCustomDeviceName": False,
    }
    data.update(overrides)
    return SessionInfoDto.model_validate(data)


def _manager_with_sessions(sessions: list[SessionInfoDto]) -> SessionsMixin:
    manager = SessionsMixin.__new__(SessionsMixin)
    manager._sessions = sessions
    return manager


def test_connected_sessions_include_active_session_metadata():
    active_idle = _session()
    inactive = _session(
        Id="session-2",
        UserId="user-2",
        UserName="Alex",
        DeviceId="device-2",
        DeviceName="Phone",
        IsActive=False,
    )
    active_playing = _session(
        Id="session-3",
        UserId="user-3",
        UserName="Casey",
        DeviceId="device-3",
        DeviceName="TV",
        NowPlayingItem={"Id": "item-1", "Type": "Movie", "Name": "A Movie"},
        PlayState={
            "IsPaused": True,
            "CanSeek": True,
            "IsMuted": False,
            "RepeatMode": "RepeatNone",
            "PlaybackOrder": "Default",
            "PositionTicks": 123,
        },
    )
    manager = _manager_with_sessions([active_idle, inactive, active_playing])

    assert manager.connected_session_count == 2
    assert manager.connected_sessions == [
        {
            "session_id": "session-1",
            "username": "Scott",
            "user_id": "user-1",
            "client": "Jellyfin Web",
            "device_id": "device-1",
            "device_name": "Firefox",
            "is_active": True,
            "playback_status": STATE_IDLE,
            "last_activity_date": "2026-04-27T12:00:00.000Z",
            "last_playback_check_in": "2026-04-27T11:59:00.000Z",
            "supports_media_control": True,
            "supports_remote_control": True,
            "has_custom_device_name": False,
            "item_id": None,
            "item_name": None,
            "item_type": None,
            "state": None,
        },
        {
            "session_id": "session-3",
            "username": "Casey",
            "user_id": "user-3",
            "client": "Jellyfin Web",
            "device_id": "device-3",
            "device_name": "TV",
            "is_active": True,
            "playback_status": STATE_PAUSED,
            "last_activity_date": "2026-04-27T12:00:00.000Z",
            "last_playback_check_in": "2026-04-27T11:59:00.000Z",
            "supports_media_control": True,
            "supports_remote_control": True,
            "has_custom_device_name": False,
            "item_id": "item-1",
            "item_name": "A Movie",
            "item_type": "Movie",
            "state": {
                "IsPaused": True,
                "CanSeek": True,
                "IsMuted": False,
                "RepeatMode": "RepeatNone",
                "PlaybackOrder": "Default",
                "PositionTicks": 123,
                "VolumeLevel": None,
                "AudioStreamIndex": None,
                "SubtitleStreamIndex": None,
                "MediaSourceId": None,
                "PlayMethod": None,
            },
        },
    ]


def test_playing_sessions_keep_existing_names_and_add_metadata():
    idle = _session()
    playing = _session(
        Id="session-2",
        UserName="Alex",
        DeviceName="Living Room",
        NowPlayingItem={"Id": "item-2", "Type": "Episode", "Name": "Pilot"},
        PlayState={
            "IsPaused": False,
            "CanSeek": True,
            "IsMuted": False,
            "RepeatMode": "RepeatNone",
            "PlaybackOrder": "Default",
        },
    )
    manager = _manager_with_sessions([idle, playing])

    assert manager.playing_session_count == 1
    assert manager.playing_sessions[0]["username"] == "Alex"
    assert manager.playing_sessions[0]["device_name"] == "Living Room"
    assert manager.playing_sessions[0]["item_name"] == "Pilot"
    assert manager.playing_sessions[0]["item_type"] == "Episode"
    assert manager.playing_sessions[0]["state"]["IsPaused"] is False


def test_connected_session_sensor_exposes_connected_session_metadata():
    class _Manager:
        connected_sessions = [
            {
                "username": "Scott",
                "client": "Jellyfin Web",
                "device_name": "Firefox",
            }
        ]
        playing_sessions = []

    sensor = JellyfinItemCountSensor(
        _Manager(), "connected_session", lambda manager: 1
    )

    assert sensor.extra_state_attributes == {
        "sessions": _Manager.connected_sessions,
        "usernames": ["Scott"],
    }


def _play_state(play_method: str) -> dict:
    return {
        "IsPaused": False,
        "CanSeek": True,
        "IsMuted": False,
        "RepeatMode": "RepeatNone",
        "PlaybackOrder": "Default",
        "PlayMethod": play_method,
    }


def test_transcoding_sessions_filter_by_play_method():
    direct = _session(
        Id="s-direct",
        NowPlayingItem={"Id": "i1", "Type": "Movie", "Name": "Direct"},
        PlayState=_play_state("DirectPlay"),
    )
    transcoding = _session(
        Id="s-trans",
        UserName="Alex",
        NowPlayingItem={"Id": "i2", "Type": "Movie", "Name": "Trans"},
        PlayState=_play_state("Transcode"),
    )
    idle = _session(Id="s-idle")  # no PlayState
    manager = _manager_with_sessions([direct, transcoding, idle])

    assert manager.transcoding_session_count == 1
    assert [s["session_id"] for s in manager.transcoding_sessions] == ["s-trans"]
    assert manager.transcoding_sessions[0]["username"] == "Alex"


def test_transcoding_count_defaults_to_zero_without_sessions():
    manager = _manager_with_sessions(None)

    assert manager.transcoding_session_count == 0
    assert manager.transcoding_sessions == []


def test_transcoding_sensor_exposes_transcoding_metadata():
    class _Manager:
        transcoding_sessions = [
            {"username": "Scott", "client": "Jellyfin Web", "device_name": "Firefox"}
        ]

    sensor = JellyfinItemCountSensor(
        _Manager(), "transcoding_session", lambda manager: 1
    )

    assert sensor.extra_state_attributes == {
        "sessions": _Manager.transcoding_sessions,
        "usernames": ["Scott"],
    }


def test_count_sensor_registers_for_websocket_driven_updates():
    class _Manager:
        host = "http://jellyfin"

    sensor = JellyfinItemCountSensor(_Manager(), "connected_session", lambda manager: 1)
    sensor.hass = types.SimpleNamespace(
        data={DOMAIN: {_Manager.host: {"sensor": {"entities": []}}}}
    )

    asyncio.run(sensor.async_added_to_hass())
    assert sensor.hass.data[DOMAIN][_Manager.host]["sensor"]["entities"] == [sensor]

    asyncio.run(sensor.async_will_remove_from_hass())
    assert sensor.hass.data[DOMAIN][_Manager.host]["sensor"]["entities"] == []
