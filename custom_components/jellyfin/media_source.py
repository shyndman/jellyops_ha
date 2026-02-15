from __future__ import annotations

import logging

from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from homeassistant.components.media_player import BrowseError
from homeassistant.components.media_source.const import MEDIA_MIME_TYPES, URI_SCHEME
from homeassistant.components.media_player.const import MediaType, MediaClass

from homeassistant.const import (  # pylint: disable=import-error
    CONF_URL,
)

from .client_manager import JellyfinClientManager
from .const import DOMAIN
from .helpers import autolog
from .view import get_proxy_image_url
PLAYABLE_MEDIA_TYPES = [
    MediaType.ALBUM,
    MediaType.ARTIST,
    MediaType.TRACK,
]

CONTAINER_TYPES_SPECIFIC_MEDIA_CLASS = {
    MediaType.ALBUM: MediaClass.ALBUM,
    MediaType.ARTIST: MediaClass.ARTIST,
    MediaType.PLAYLIST: MediaClass.PLAYLIST,
    MediaType.SEASON: MediaClass.SEASON,
    MediaType.TVSHOW: MediaClass.TV_SHOW,
}

CHILD_TYPE_MEDIA_CLASS = {
    MediaType.SEASON: MediaClass.SEASON,
    MediaType.ALBUM: MediaClass.ALBUM,
    MediaType.ARTIST: MediaClass.ARTIST,
    MediaType.MOVIE: MediaClass.MOVIE,
    MediaType.PLAYLIST: MediaClass.PLAYLIST,
    MediaType.TRACK: MediaClass.TRACK,
    MediaType.TVSHOW: MediaClass.TV_SHOW,
    MediaType.CHANNEL: MediaClass.CHANNEL,
    MediaType.EPISODE: MediaClass.EPISODE,
}

IDENTIFIER_SPLIT = "~~"

_LOGGER = logging.getLogger(__name__)

class UnknownMediaType(BrowseError):
    """Unknown media type."""

async def async_get_media_source(hass: HomeAssistant) -> JellyfinSource:
    """Set up Jellyfin media source."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    jelly_cm: JellyfinClientManager = hass.data[DOMAIN][entry.data[CONF_URL]]["manager"]
    return JellyfinSource(hass, jelly_cm)

class JellyfinSource(MediaSource):
    """Media source for Jellyfin"""

    @staticmethod
    def parse_mediasource_identifier(identifier: str):
        prefix = f"{URI_SCHEME}{DOMAIN}/"
        text = identifier
        if identifier.startswith(prefix):
            text = identifier[len(prefix):]
        if IDENTIFIER_SPLIT in text:
            return text.split(IDENTIFIER_SPLIT, 2)

        return "", text

    def __init__(self, hass: HomeAssistant, manager: JellyfinClientManager):
        """Initialize Jellyfin source."""
        super().__init__(DOMAIN)
        self.hass = hass
        self.jelly_cm = manager

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable item."""
        autolog("<<<")

        if not item or not item.identifier:
            raise BrowseError("No media item identifier provided")

        media_content_type, media_content_id = self.parse_mediasource_identifier(item.identifier)
        url, mime_type, _ = await self.jelly_cm.get_stream_url(media_content_id, media_content_type)
        if url is None or mime_type is None:
            raise BrowseError(f"Could not resolve stream URL for {media_content_id}")
        return PlayMedia(url, mime_type)

    async def async_browse_media(
        self, item: MediaSourceItem, media_types: tuple[str, ...] = MEDIA_MIME_TYPES
    ) -> BrowseMediaSource:
        """Browse media."""
        # Global media source browsing is not supported. Browse through a media player entity.
        raise BrowseError(
            "Jellyfin browsing is only available through media player entities"
        )

def Type2Mediatype(jellyfin_type: str) -> MediaType | MediaClass | None:
    switcher: dict[str, MediaType | MediaClass] = {
        "Movie": MediaType.MOVIE,
        "Series": MediaType.TVSHOW,
        "Season": MediaType.SEASON,
        "Episode": MediaType.EPISODE,
        "Music": MediaType.ALBUM,
        "Audio": MediaType.TRACK,
        "BoxSet": MediaClass.DIRECTORY,
        "Folder": MediaClass.DIRECTORY,
        "CollectionFolder": MediaClass.DIRECTORY,
        "Playlist": MediaClass.DIRECTORY,
        "PlaylistsFolder": MediaClass.DIRECTORY,
        "ManualPlaylistsFolder": MediaClass.DIRECTORY,
        "MusicArtist": MediaType.ARTIST,
        "MusicAlbum": MediaType.ALBUM,
    }
    return switcher.get(jellyfin_type)


def Type2Mimetype(jellyfin_type: str) -> str | MediaType | MediaClass | None:
    switcher: dict[str, str | MediaType | MediaClass] = {
        "Movie": "video/mp4",
        "Series": MediaType.TVSHOW,
        "Season": MediaType.SEASON,
        "Episode": "video/mp4",
        "Music": MediaType.ALBUM,
        "Audio": "audio/mp3",
        "BoxSet": MediaClass.DIRECTORY,
        "Folder": MediaClass.DIRECTORY,
        "CollectionFolder": MediaClass.DIRECTORY,
        "Playlist": MediaClass.DIRECTORY,
        "PlaylistsFolder": MediaClass.DIRECTORY,
        "ManualPlaylistsFolder": MediaClass.DIRECTORY,
        "MusicArtist": MediaType.ARTIST,
        "MusicAlbum": MediaType.ALBUM,
    }
    return switcher.get(jellyfin_type)


def Type2Mediaclass(jellyfin_type: str) -> MediaClass | None:
    switcher: dict[str, MediaClass] = {
        "Movie": MediaClass.MOVIE,
        "Series": MediaClass.TV_SHOW,
        "Season": MediaClass.SEASON,
        "Episode": MediaClass.EPISODE,
        "Music": MediaClass.DIRECTORY,
        "BoxSet": MediaClass.DIRECTORY,
        "Folder": MediaClass.DIRECTORY,
        "CollectionFolder": MediaClass.DIRECTORY,
        "Playlist": MediaClass.DIRECTORY,
        "PlaylistsFolder": MediaClass.DIRECTORY,
        "ManualPlaylistsFolder": MediaClass.DIRECTORY,
        "MusicArtist": MediaClass.ARTIST,
        "MusicAlbum": MediaClass.ALBUM,
        "Audio": MediaClass.TRACK,
    }
    return switcher.get(jellyfin_type)


def IsPlayable(jellyfin_type: str, canPlayList: bool) -> bool | None:
    switcher: dict[str, bool] = {
        "Movie": True,
        "Series": canPlayList,
        "Season": canPlayList,
        "Episode": True,
        "Music": False,
        "BoxSet": canPlayList,
        "Folder": False,
        "CollectionFolder": False,
        "Playlist": canPlayList,
        "PlaylistsFolder": False,
        "ManualPlaylistsFolder": False,
        "MusicArtist": canPlayList,
        "MusicAlbum": canPlayList,
        "Audio": True,
    }
    return switcher.get(jellyfin_type)


def get_proxied_thumbnail_url(jelly_cm: JellyfinClientManager, media_id: str) -> str:
    """Get a proxied thumbnail URL for a media item.

    Caches the actual Jellyfin URL and returns a proxy URL that Home Assistant
    can serve to browsers that may not have direct access to the Jellyfin server.
    """
    # Get the actual Jellyfin artwork URL and cache it
    artwork_url = jelly_cm.get_artwork_url(media_id)
    jelly_cm.thumbnail_cache[media_id] = artwork_url

    # Return the proxy URL that routes through Home Assistant
    return get_proxy_image_url(jelly_cm.entry_id, media_id)


async def async_library_items(
    jelly_cm: JellyfinClientManager,
    media_content_type_in: str | None = None,
    media_content_id_in: str | None = None,
    canPlayList: bool = True,
    *,
    user_id: str,
) -> BrowseMediaSource:
    """
    Create response payload to describe contents of a specific library.

    Used by async_browse_media.
    """
    _LOGGER.debug(f'>> async_library_items: {media_content_id_in} / {canPlayList}')

    library_info = None
    query = None

    if media_content_type_in is None or media_content_id_in is None:
        media_content_type = None
        media_content_id = None
    else:
        media_content_type, media_content_id = JellyfinSource.parse_mediasource_identifier(media_content_id_in)
    _LOGGER.debug(f'-- async_library_items: {media_content_type} / {media_content_id}')

    if media_content_type in [None, "library"]:
        library_info = BrowseMediaSource(
            domain=DOMAIN,
            identifier=f'library{IDENTIFIER_SPLIT}library',
            media_class=MediaClass.DIRECTORY,
            media_content_type="library",
            title="Media Library",
            can_play=False,
            can_expand=True,
            children=[],
        )
    elif media_content_type in [MediaClass.DIRECTORY, MediaType.ARTIST, MediaType.ALBUM, MediaType.PLAYLIST, MediaType.TVSHOW, MediaType.SEASON, MediaType.CHANNEL]:
        assert media_content_id is not None  # Guaranteed by previous branch
        query = {
            "ParentId": media_content_id,
            "sortBy": "SortName",
            "sortOrder": "Ascending"
        }

        parent_item = await jelly_cm.get_item(media_content_id)
        item_type = str(parent_item["Type"])
        library_info = BrowseMediaSource(
            domain=DOMAIN,
            identifier=f'{media_content_type}{IDENTIFIER_SPLIT}{media_content_id}',
            media_class=media_content_type,
            media_content_type=media_content_type,
            title=str(parent_item["Name"]),
            can_play=IsPlayable(item_type, canPlayList),
            can_expand=True,
            thumbnail=get_proxied_thumbnail_url(jelly_cm, media_content_id),
            children=[],
        )
    else:
        assert media_content_id is not None  # Guaranteed by previous branch
        query = {
            "Id": media_content_id
        }
        library_info = BrowseMediaSource(
            domain=DOMAIN,
            identifier=f'{media_content_type}{IDENTIFIER_SPLIT}{media_content_id}',
            media_class=MediaClass.DIRECTORY,
            media_content_type=media_content_type,
            title="",
            can_play=True,
            can_expand=False,
            thumbnail=get_proxied_thumbnail_url(jelly_cm, media_content_id),
            children=[],
        )
    _LOGGER.debug('-- async_library_items: 1')

    assert library_info is not None  # Always set in one of the branches above
    children: list[BrowseMediaSource] = list(library_info.children) if library_info.children else []
    items = await jelly_cm.get_items(user_id, query)
    for item in items:
        item_type = str(item["Type"])
        item_id = str(item["Id"])
        item_name = str(item["Name"])
        if media_content_type in [None, "library", MediaClass.DIRECTORY, MediaType.ARTIST, MediaType.ALBUM, MediaType.PLAYLIST, MediaType.TVSHOW, MediaType.SEASON, MediaType.CHANNEL]:
            if item["IsFolder"]:
                library_info.children_media_class = MediaClass.DIRECTORY
                children.append(BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f'{Type2Mediatype(item_type)}{IDENTIFIER_SPLIT}{item_id}',
                    media_class=Type2Mediaclass(item_type),
                    media_content_type=Type2Mimetype(item_type),
                    title=item_name,
                    can_play=IsPlayable(item_type, canPlayList),
                    can_expand=True,
                    children=[],
                    thumbnail=get_proxied_thumbnail_url(jelly_cm, item_id)
                ))
            else:
                library_info.children_media_class = Type2Mediaclass(item_type)
                children.append(BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f'{Type2Mediatype(item_type)}{IDENTIFIER_SPLIT}{item_id}',
                    media_class=Type2Mediaclass(item_type),
                    media_content_type=Type2Mimetype(item_type),
                    title=item_name,
                    can_play=IsPlayable(item_type, canPlayList),
                    can_expand=False,
                    children=[],
                    thumbnail=get_proxied_thumbnail_url(jelly_cm, item_id)
                ))
        else:
            library_info.domain=DOMAIN
            library_info.identifier=f'{Type2Mediatype(item_type)}{IDENTIFIER_SPLIT}{item_id}',
            library_info.title = item_name
            library_info.media_content_type = Type2Mimetype(item_type)
            library_info.media_class = Type2Mediaclass(item_type)
            library_info.can_expand = False
            library_info.can_play=IsPlayable(item_type, canPlayList),
            break

    library_info.children = children
    _LOGGER.debug(f'<< async_library_items {library_info.as_dict()}')
    return library_info
