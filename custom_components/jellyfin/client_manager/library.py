"""Library, artwork, and stream helpers for Jellyfin client manager."""

from __future__ import annotations

import json
import logging
from typing import Any

import dateutil.parser as dt

from ..const import PLAYABLE_ITEM_TYPES, PLAYLISTS, USER_APP_NAME, YAMC_PAGE_SIZE
from ..helpers import autolog
from ..models import (
    BaseItemDtoQueryResult,
    MediaSourceInfo,
    PlaybackInfoResponse,
    UpcomingCardDefaults,
    UpcomingCardItem,
    UpcomingCardPayload,
    YamcCardDefaults,
    YamcCardItem,
    YamcCardPayload,
)

_LOGGER = logging.getLogger(__name__)


class LibraryMixin:
    """Mixin encapsulating Jellyfin library operations."""

    _movie_count: int | None
    _episode_count: int | None
    _series_count: int | None

    def __init__(self) -> None:
        self._data: BaseItemDtoQueryResult | None = None
        self._yamc: BaseItemDtoQueryResult | None = None
        self._yamc_cur_page = 1
        self._last_playlist = ""
        self._last_search = ""
        self._yamc_streams: dict[str, dict[str, str | None]] = {}
        self.thumbnail_cache: dict[str, str] = {}
        self._movie_count = None
        self._episode_count = None
        self._series_count = None
        super().__init__()

    async def _get_item_count(self, item_type: str) -> int:
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

    async def update_data(self) -> None:
        autolog("<<<")
        user_id = self.config.library_user_id
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
                query: dict[str, Any] = {
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
                    for playlist in PLAYLISTS:
                        if playlist["name"] == self._last_playlist:
                            query.update(playlist["query"])
                            break
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
                    if item.Type in PLAYABLE_ITEM_TYPES:
                        stream_url, _, info = await self.get_stream_url(item.Id, item.Type)
                        self._yamc_streams[item.Id] = {"stream_url": stream_url, "info": info}

    @property
    def movie_count(self) -> int | None:
        return self._movie_count

    @property
    def episode_count(self) -> int | None:
        return self._episode_count

    @property
    def series_count(self) -> int | None:
        return self._series_count

    @property
    def data(self) -> UpcomingCardPayload | None:
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
                    "Upcoming item missing required fields: Id=%s, Title=%s, Episode=%s"
                    % (item.Id, title, episode)
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
    def yamc(self) -> dict[str, Any] | None:
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
            return {
                "last_search": self._last_search,
                "last_playlist": self._last_playlist,
                "playlists": json.dumps(PLAYLISTS),
                "total_items": 0,
                "page": self._yamc_cur_page,
                "page_size": YAMC_PAGE_SIZE,
                "data": json.dumps(payload),
            }
        for item in self._yamc.Items:
            user_data = item.UserData
            base_flag = bool(user_data and user_data.Played)
            progress = (
                user_data.PlayedPercentage
                if user_data and user_data.PlayedPercentage is not None
                else 100.0 if base_flag else 0.0
            )
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
                elif item.Type == "MusicAlbum" and "MusicBrainzAlbum" in item.ProviderIds:
                    info_url = f"https://musicbrainz.org/album/{item.ProviderIds['MusicBrainzAlbum']}"
                elif item.Type == "MusicArtist" and "MusicBrainzArtist" in item.ProviderIds:
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
                tagline_value = item.Taglines[0] if item.Taglines else ""
                release_value = dt.parse(item.PremiereDate).__format__("%Y") if item.PremiereDate else None
                fanart_type = "Backdrop"
                episode_value = None
            elif item.Type == "Series":
                episode_value = item.Name
                tagline_value = item.Name
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%d/%m/%Y") if item.PremiereDate else None
                )
                fanart_type = "Backdrop"
            elif item.Type == "Episode":
                episode_value = item.Name
                tagline_value = item.Name
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%d/%m/%Y") if item.PremiereDate else None
                )
                fanart_type = "Primary"
            elif item.Type == "MusicAlbum":
                tagline_value = ",".join(item.Artists) if item.Artists else None
                release_value = dt.parse(item.PremiereDate).__format__("%Y") if item.PremiereDate else None
                flag_value = False
                progress = 0.0
                episode_value = None
                fanart_type = "Backdrop"
            elif item.Type == "MusicArtist":
                tagline_value = ",".join(item.Artists) if item.Artists else None
                release_value = dt.parse(item.DateCreated).__format__("%Y") if item.DateCreated else None
                flag_value = False
                progress = 0.0
                episode_value = None
            else:
                episode_value = item.Name
                tagline_value = item.Name
                release_value = (
                    dt.parse(item.PremiereDate).__format__("%d/%m/%Y") if item.PremiereDate else None
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
        attrs = {
            "last_search": self._last_search,
            "last_playlist": self._last_playlist,
            "playlists": json.dumps(PLAYLISTS),
            "total_items": min(50, self._yamc.TotalRecordCount),
            "page": self._yamc_cur_page,
            "page_size": YAMC_PAGE_SIZE,
            "data": json.dumps(payload),
        }
        return attrs

    async def trigger_scan(self) -> None:
        await self.hass.async_add_executor_job(
            self._client.jellyfin._post, "Library/Refresh"
        )

    async def delete_item(self, item_id: str) -> None:
        await self.hass.async_add_executor_job(
            self._client.jellyfin.items, f"/{item_id}", "DELETE"
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

    async def get_artwork(
        self, media_id: str, artwork_type: str = "Primary"
    ) -> tuple[str | None, str | None]:
        query = {"format": "PNG", "maxWidth": 500, "maxHeight": 500}
        image = await self.hass.async_add_executor_job(
            self._client.jellyfin.items,
            "GET",
            f"{media_id}/Images/{artwork_type}",
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
                {"Type": "Audio", "Container": "mp3", "Protocol": "http", "AudioCodec": "mp3", "MaxAudioChannels": "2"},
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
                {"Format": "pgssub", "Method": "Embed"},
                {"Format": "dvdsub", "Method": "Embed"},
                {"Format": "pgs", "Method": "Embed"},
            ],
        }
        raw_playback_info = await self.get_play_info(media_id, profile)
        _LOGGER.debug("playbackinfo: %s", raw_playback_info)
        if raw_playback_info is None:
            _LOGGER.error("No playback info for item id %s", media_id)
            return (None, None, None)
        playback_info = PlaybackInfoResponse.model_validate(raw_playback_info)
        if playback_info.MediaSources is None or not playback_info.MediaSources:
            _LOGGER.error("No media sources for item id %s", media_id)
            return (None, None, None)
        selected: MediaSourceInfo | None = None
        weight_selected = 0.0
        for media_source in playback_info.MediaSources:
            weight = (1 if media_source.SupportsDirectStream else 0) * 50000 + (
                (media_source.Bitrate or 0) / 1000
            )
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
            mimetype = ("audio/" if media_content_type in ("Audio", "track") else "video/") + container
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
