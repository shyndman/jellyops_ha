# Change: Switch Authentication from Username/Password to API Key

## Why

The current username/password authentication requires the integration to store user credentials. API key authentication is simpler, more secure (keys can be revoked without changing passwords), and aligns with how other Home Assistant integrations authenticate with media servers.

## What Changes

- **Config flow**: Replace username/password fields with a single API key field
- **Authentication**: Use `client.authenticate()` with API key directly instead of `client.auth.login()`
- **Device identity**: Remove device ID/name configuration (not needed for API key auth)
- **Validation**: Validate the API key during setup and show an error on failure

## Impact

- Affected specs: authentication (new)
- Affected code:
  - `custom_components/jellyfin/const.py`
  - `custom_components/jellyfin/config_flow.py`
  - `custom_components/jellyfin/__init__.py`
  - `custom_components/jellyfin/strings.json`
  - `custom_components/jellyfin/translations/*.json`
