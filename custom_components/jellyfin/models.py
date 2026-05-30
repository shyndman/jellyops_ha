"""Typed representations of Jellyfin API payloads."""
from __future__ import annotations

from typing import Annotated, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NameGuidPair(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Name: str | None = Field(None, description="Display name")
    Id: str | None = Field(None, description="Stable identifier")


class UserItemDataDto(BaseModel):
    model_config = ConfigDict(extra="ignore")

    PlayedPercentage: float | None = Field(None, description="Completion percentage")
    Played: bool | None = Field(None, description="True when item is fully played")


class BaseItemDto(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Id: str
    Type: str
    Name: str | None = None
    SeriesName: str | None = None
    ParentIndexNumber: int | None = None
    IndexNumber: int | None = None
    DateCreated: str | None = None
    PremiereDate: str | None = None
    RunTimeTicks: int | None = None
    Studios: list[NameGuidPair] | None = None
    Genres: list[str] | None = None
    UserData: UserItemDataDto | None = None
    Taglines: list[str] | None = None
    ProviderIds: dict[str, str] | None = None
    Artists: list[str] | None = None
    CommunityRating: float | None = None
    CriticRating: float | None = None
    DateLastMediaAdded: str | None = None
    OfficialRating: str | None = None

    @field_validator("RunTimeTicks")
    @classmethod
    def non_negative_runtime(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("RunTimeTicks must be non-negative")
        return value


class BaseItemDtoQueryResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Items: list[BaseItemDto]
    TotalRecordCount: int
    StartIndex: int | None = None


class SystemInfo(BaseModel):
    """Information about the Jellyfin server."""

    model_config = ConfigDict(extra="ignore")

    Id: str | None = None
    ServerName: str | None = None
    Version: str | None = None
    OperatingSystem: str | None = None  # Deprecated but still returned
    HasUpdateAvailable: bool = False  # Deprecated but still returned
    HasPendingRestart: bool = False
    IsShuttingDown: bool = False
    SupportsLibraryMonitor: bool = False
    WebSocketPortNumber: int | None = None
    LocalAddress: str | None = None
    ProductName: str | None = None
    StartupWizardCompleted: bool | None = None


class ItemCounts(BaseModel):
    """Library item counts returned by /Items/Counts (LibrarySummary)."""

    model_config = ConfigDict(extra="ignore")

    MovieCount: int = 0
    SeriesCount: int = 0
    EpisodeCount: int = 0
    ArtistCount: int = 0
    ProgramCount: int = 0
    TrailerCount: int = 0
    SongCount: int = 0
    AlbumCount: int = 0
    MusicVideoCount: int = 0
    BoxSetCount: int = 0
    BookCount: int = 0
    ItemCount: int = 0


# =============================================================================
# Session Models (WebSocket / Sessions API)
# =============================================================================


class PlayerStateInfo(BaseModel):
    """Playback state for a session."""

    model_config = ConfigDict(extra="ignore")

    # Non-nullable per spec
    IsPaused: bool
    CanSeek: bool
    IsMuted: bool
    RepeatMode: str
    PlaybackOrder: str

    # Nullable per spec
    PositionTicks: int | None = None
    VolumeLevel: int | None = None
    AudioStreamIndex: int | None = None
    SubtitleStreamIndex: int | None = None
    MediaSourceId: str | None = None
    PlayMethod: str | None = None


class ImageTags(BaseModel):
    """Image tag identifiers keyed by image type."""

    model_config = ConfigDict(extra="allow")  # Unknown image types OK

    Primary: str | None = None
    Thumb: str | None = None
    Backdrop: str | None = None
    Banner: str | None = None
    Logo: str | None = None


class NowPlayingItemDto(BaseModel):
    """Media item currently playing in a session."""

    model_config = ConfigDict(extra="ignore")

    # Non-nullable per spec
    Id: str
    Type: str

    # Nullable per spec
    Name: str | None = None
    RunTimeTicks: int | None = None
    IndexNumber: int | None = None
    ParentIndexNumber: int | None = None
    SeriesName: str | None = None
    Album: str | None = None
    Artists: list[str] | None = None
    AlbumArtist: str | None = None
    image_tags: Annotated[ImageTags | None, Field(alias="ImageTags")] = None


class SessionInfoDto(BaseModel):
    """Active session information from Jellyfin server."""

    model_config = ConfigDict(extra="ignore")

    # Non-nullable per spec
    UserId: str
    LastActivityDate: str
    LastPlaybackCheckIn: str
    IsActive: bool
    SupportsMediaControl: bool
    SupportsRemoteControl: bool
    HasCustomDeviceName: bool

    # Nullable per spec
    Id: str | None = None
    UserName: str | None = None
    Client: str | None = None
    DeviceId: str | None = None
    DeviceName: str | None = None
    PlayState: PlayerStateInfo | None = None
    NowPlayingItem: NowPlayingItemDto | None = None


# =============================================================================
# Playback / Stream Models
# =============================================================================


class MediaStream(BaseModel):
    """Audio/video stream within a media source."""

    model_config = ConfigDict(extra="ignore")

    # Non-nullable per spec
    Type: str  # "Audio", "Video", "Subtitle", etc.

    # Nullable per spec
    Codec: str | None = None
    SampleRate: int | None = None
    Width: int | None = None
    Height: int | None = None


class MediaSourceInfo(BaseModel):
    """Media source (file/stream) for playback."""

    model_config = ConfigDict(extra="ignore")

    # Non-nullable per spec
    SupportsDirectStream: bool
    SupportsTranscoding: bool

    # Nullable per spec
    Id: str | None = None
    Container: str | None = None
    Bitrate: int | None = None
    TranscodingUrl: str | None = None
    TranscodingContainer: str | None = None
    MediaStreams: list[MediaStream] | None = None


class PlaybackInfoResponse(BaseModel):
    """Response from playback info endpoint."""

    model_config = ConfigDict(extra="ignore")

    # Nullable per spec
    MediaSources: list[MediaSourceInfo] | None = None
    PlaySessionId: str | None = None
    ErrorCode: str | None = None


# =============================================================================
# Config Entry Model
# =============================================================================


class JellyfinEntryData(BaseModel):
    """Validated configuration data for a Jellyfin integration entry."""

    model_config = ConfigDict(extra="forbid")

    url: str
    api_key: str
    verify_ssl: bool = True
    generate_upcoming: bool = False
    generate_yamc: bool = False
    library_user_id: str | None = None

    @model_validator(mode="after")
    def validate_library_user_required(self) -> Self:
        """Ensure library_user_id is set when upcoming/yamc features are enabled."""
        if (self.generate_upcoming or self.generate_yamc) and not self.library_user_id:
            raise ValueError("library_user_id required when upcoming/yamc enabled")
        return self


class UpcomingCardDefaults(TypedDict):
    title_default: str
    line1_default: str
    line2_default: str
    line3_default: str
    line4_default: str
    icon: str


class UpcomingCardItem(TypedDict):
    title: str
    episode: str
    flag: bool
    airdate: str | None
    number: str | None
    runtime: int | None
    studio: str | None
    release: str | None
    poster: str | None
    fanart: str | None
    genres: str | None
    rating: str | None
    stream_url: str | None
    info_url: str | None


UpcomingCardPayload = list[UpcomingCardDefaults | UpcomingCardItem]


class YamcCardDefaults(TypedDict):
    title_default: str
    line1_default: str
    line2_default: str
    line3_default: str
    line4_default: str
    line5_default: str
    text_link_default: str
    link_default: str


class YamcCardItem(TypedDict):
    id: str
    type: str
    title: str
    episode: str | None
    tagline: str | None
    flag: bool
    airdate: str | None
    number: str | None
    runtime: int | None
    studio: str | None
    release: str | None
    poster: str | None
    fanart: str | None
    genres: str | None
    progress: float | None
    rating: str | None
    info: str | None
    stream_url: str | None
    info_url: str | None


YamcCardPayload = list[YamcCardDefaults | YamcCardItem]
