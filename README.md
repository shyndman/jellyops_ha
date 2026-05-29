# Jellyfin Operations

A Home Assistant integration for Jellyfin server administrators. Where the core
Jellyfin integration serves the media consumer, this one serves the operator:
session/now-playing monitoring, library item counts, and management actions
(library scans, item search, item deletion).

Forked version to work with 2025.1 version of Home Assistant

All thanks and rights goes to the author of the integration. 

## Installation:

- Go to HACS
- Press the three dots in the upper right corner
- Press Custom repositories
- In the Repository field, enter `shyndman/jellyfin_ha`
- In the Category field, select `Integration`
- Search for added integration in HACS and install it
- Configure your Jellyfin server
- After a restart, you will have media_player and sensor entities.

---

## Features

### Entities

- 1 media_player entity per device
- 1 sensor per server
- Supports the "upcoming-media-card" custom card

### Media Browser

- Browse medias and start playback from within Home Assistant

### Media Source

- Browse and stream to a cast device (e.g. Chromecast)

## Reference

- The Jellyfin OpenAPI description lives at `docs/reference/openapi.json`.

### Services

- `trigger_scan`: Trigger a server media scan
- `browse`: Show a media info on a device
- `delete`: Delete a media
- `search`: Search for media (for compatible fontends)

### Upcoming Media Card

###### Sample for ui-lovelace.yaml:

```
- type: custom:upcoming-media-card
  entity: sensor.jellyfin_media_server
  title: Latest Media
```

More configuration options can be found in the [upcoming-media-card](https://github.com/custom-cards/upcoming-media-card#options) repo.

---

#### [View Changelog](changelog/changelog.md)
