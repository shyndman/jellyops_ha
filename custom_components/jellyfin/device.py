"""Jellyfin device/session wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import STATE_IDLE, STATE_OFF, STATE_PAUSED, STATE_PLAYING
from .models import SessionInfoDto

if TYPE_CHECKING:
    from .client_manager import JellyfinClientManager

__all__ = ["JellyfinDevice"]


class JellyfinDevice:
    """Represents properties of a Jellyfin Device."""
    def __init__(
        self,
        session: SessionInfoDto,
        jf_manager: "JellyfinClientManager",
        device_key: str,
    ) -> None:
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

    async def get_items(
        self, query: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
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
