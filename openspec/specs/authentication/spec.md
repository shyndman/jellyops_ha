# authentication Specification

## Purpose
TBD - created by archiving change authenticate-with-key. Update Purpose after archive.
## Requirements
### Requirement: API Key Authentication

The integration SHALL authenticate with the Jellyfin server using an API key instead of username/password credentials.

#### Scenario: Successful authentication with valid API key
- **WHEN** user provides a valid Jellyfin API key and server URL
- **THEN** the integration authenticates successfully
- **AND** creates a connection to the Jellyfin server

#### Scenario: Failed authentication with invalid API key
- **WHEN** user provides an invalid API key
- **THEN** the integration fails to authenticate
- **AND** displays an appropriate error message

### Requirement: API Key Configuration

The config flow SHALL collect an API key from the user instead of username and password.

#### Scenario: Config flow shows API key field
- **WHEN** user adds the Jellyfin integration
- **THEN** the configuration form displays an "API Key" field
- **AND** does NOT display username or password fields

#### Scenario: Options flow shows API key field
- **WHEN** user edits the Jellyfin integration options
- **THEN** the options form displays the current API key
- **AND** allows the user to update it

