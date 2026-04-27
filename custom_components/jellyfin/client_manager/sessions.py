"""Session and device tracking mixin for Jellyfin client manager."""

from __future__ import annotations

import collections.abc
import logging
import traceback

from ..const import STATE_IDLE, STATE_PAUSED, STATE_PLAYING
from ..device import JellyfinDevice
from ..helpers import autolog
from ..models import SessionInfoDto

_LOGGER = logging.getLogger(__name__)


class SessionsMixin:
    """Mixin that tracks active Jellyfin sessions/devices."""

    def __init__(self) -> None:
        self._sessions: list[SessionInfoDto] | None = None
        self._devices: dict[str, JellyfinDevice] = {}
        self._new_devices_callbacks: list[collections.abc.Callable[[object], None]] = []
        self._stale_devices_callbacks: list[collections.abc.Callable[[object], None]] = []
        self._update_callbacks: list[
            tuple[collections.abc.Callable[[object], None], str]
        ] = []
        super().__init__()

    def update_device_list(self) -> None:
        autolog(">>>")
        if self._sessions is None:
            _LOGGER.error("Error updating Jellyfin devices.")
            return
        try:
            new_devices: list[JellyfinDevice] = []
            active_devices: list[str] = []
            dev_update = False
            for session in self._sessions:
                if not session.HasCustomDeviceName:
                    continue
                device_name = session.DeviceName
                if not device_name:
                    _LOGGER.warning(
                        "Session has HasCustomDeviceName=true but DeviceName is null/empty."
                        " UserId=%s, DeviceId=%s",
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
                    if not self._devices[dev_key].is_active:
                        dev_update = True
                    do_update = self.update_check(self._devices[dev_key], session)
                    self._devices[dev_key].update_session(session)
                    self._devices[dev_key].set_active(True)
                    if dev_update:
                        self._do_new_devices_callback(0)
                        dev_update = False
                    if do_update:
                        self._do_update_callback(dev_key)
            for dev_id in list(self._devices):
                if dev_id not in active_devices and self._devices[dev_id].is_active:
                    self._devices[dev_id].set_active(False)
                    self._do_update_callback(dev_id)
                    self._do_stale_devices_callback(dev_id)
            if new_devices:
                self._do_new_devices_callback(0)
        except Exception:  # pragma: no cover - defensive logging
            _LOGGER.critical(traceback.format_exc())
            raise

    def update_check(self, existing: JellyfinDevice, new_session: SessionInfoDto) -> bool:
        autolog(">>>")
        old_state = existing.state
        if new_session.NowPlayingItem is not None:
            if new_session.PlayState is not None and new_session.PlayState.IsPaused:
                new_state = STATE_PAUSED
            else:
                new_state = STATE_PLAYING
        else:
            new_state = STATE_IDLE
        if old_state == STATE_PLAYING or new_state == STATE_PLAYING:
            return True
        if old_state != new_state:
            return True
        return False

    @staticmethod
    def _session_state(session: SessionInfoDto) -> str:
        if session.NowPlayingItem is None:
            return STATE_IDLE
        if session.PlayState is not None and session.PlayState.IsPaused:
            return STATE_PAUSED
        return STATE_PLAYING

    @classmethod
    def _session_summary(cls, session: SessionInfoDto) -> dict[str, object]:
        now_playing = session.NowPlayingItem
        return {
            "session_id": session.Id,
            "username": session.UserName,
            "user_id": session.UserId,
            "client": session.Client,
            "device_id": session.DeviceId,
            "device_name": session.DeviceName,
            "is_active": session.IsActive,
            "playback_status": cls._session_state(session),
            "last_activity_date": session.LastActivityDate,
            "last_playback_check_in": session.LastPlaybackCheckIn,
            "supports_media_control": session.SupportsMediaControl,
            "supports_remote_control": session.SupportsRemoteControl,
            "has_custom_device_name": session.HasCustomDeviceName,
            "item_id": now_playing.Id if now_playing else None,
            "item_name": now_playing.Name if now_playing else None,
            "item_type": now_playing.Type if now_playing else None,
            "state": session.PlayState.model_dump() if session.PlayState else None,
        }

    @property
    def connected_session_count(self) -> int:
        if self._sessions is None:
            return 0
        return sum(1 for session in self._sessions if session.IsActive)

    @property
    def connected_sessions(self) -> list[dict[str, object]]:
        if self._sessions is None:
            return []
        return [
            self._session_summary(session)
            for session in self._sessions
            if session.IsActive
        ]

    @property
    def playing_session_count(self) -> int:
        if self._sessions is None:
            return 0
        return sum(1 for session in self._sessions if session.NowPlayingItem is not None)

    @property
    def playing_sessions(self) -> list[dict[str, object]]:
        if self._sessions is None:
            return []
        return [
            self._session_summary(session)
            for session in self._sessions
            if session.NowPlayingItem is not None
        ]

    @property
    def devices(self) -> collections.abc.Mapping[str, JellyfinDevice]:
        return self._devices

    def add_new_devices_callback(
        self, callback: collections.abc.Callable[[object], None]
    ) -> None:
        self._new_devices_callbacks.append(callback)
        _LOGGER.debug("Added new devices callback to %s", callback)

    def add_stale_devices_callback(
        self, callback: collections.abc.Callable[[object], None]
    ) -> None:
        self._stale_devices_callbacks.append(callback)
        _LOGGER.debug("Added stale devices callback to %s", callback)

    def add_update_callback(
        self, callback: collections.abc.Callable[[object], None], device: str
    ) -> None:
        self._update_callbacks.append((callback, device))
        _LOGGER.debug("Added update callback to %s on %s", callback, device)

    def remove_update_callback(
        self, callback: collections.abc.Callable[[object], None], device: str
    ) -> None:
        if (callback, device) in self._update_callbacks:
            self._update_callbacks.remove((callback, device))
            _LOGGER.debug("Removed update callback %s for %s", callback, device)

    def _do_new_devices_callback(self, msg: object) -> None:
        for callback in self._new_devices_callbacks:
            _LOGGER.debug("Devices callback %s", callback)
            self._event_loop.call_soon(callback, msg)

    def _do_stale_devices_callback(self, msg: object) -> None:
        for callback in self._stale_devices_callbacks:
            _LOGGER.debug("Stale Devices callback %s", callback)
            self._event_loop.call_soon(callback, msg)

    def _do_update_callback(self, msg: object) -> None:
        for callback, device in self._update_callbacks:
            if device == msg:
                _LOGGER.debug("Update callback %s for device %s by %s", callback, device, msg)
                self._event_loop.call_soon(callback, msg)
