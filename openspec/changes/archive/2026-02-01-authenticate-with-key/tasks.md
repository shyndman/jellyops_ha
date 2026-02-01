# Tasks: authenticate-with-key

## 1. Add API Key Constant

- [x] 1.1 Open `custom_components/jellyfin/const.py`
- [x] 1.2 Add a new constant: `CONF_API_KEY = "api_key"`
- [x] 1.3 This constant will be used as the key in config entry data storage

## 2. Update Config Flow Schema

- [x] 2.1 Open `custom_components/jellyfin/config_flow.py`
- [x] 2.2 Remove imports for `CONF_USERNAME`, `CONF_PASSWORD`, `CONF_CLIENT_ID` from `homeassistant.const`
- [x] 2.3 Add import for `CONF_API_KEY` from `.const`
- [x] 2.4 In `async_step_user()`, replace the `data_schema` dict:
  - Remove: `vol.Required(CONF_USERNAME): str`
  - Remove: `vol.Optional(CONF_PASSWORD, default=""): str`
  - Add: `vol.Required(CONF_API_KEY): str`
- [x] 2.5 Remove instance variables `self._username` and `self._password` from `__init__` if present
- [x] 2.6 Update the `user_input` handling:
  - Remove: `self._username = user_input[CONF_USERNAME]`
  - Remove: `self._password = user_input[CONF_PASSWORD]`
  - Add: `self._api_key = user_input[CONF_API_KEY]`

## 3. Validate API Key on Submit

- [x] 3.1 Add a helper method to test the API key connection using `JellyfinClient`
- [x] 3.2 Normalize the URL with a shared helper before testing
- [x] 3.3 In `async_step_user()`, call the helper via `async_add_executor_job`
- [x] 3.4 On failure, return the same form with `errors={"base": "cannot_connect"}`

## 4. Update Config Entry Data Storage

- [x] 4.1 In `async_step_user()`, update `async_create_entry()` data dict:
  - Remove: `CONF_USERNAME: self._username`
  - Remove: `CONF_PASSWORD: self._password`
  - Remove: `CONF_CLIENT_ID: str(uuid.uuid4())`
  - Add: `CONF_API_KEY: self._api_key`
- [x] 4.2 Remove the `import uuid` at the top of the file

## 5. Update Options Flow

- [x] 5.1 In `JellyfinOptionsFlowHandler.__init__()`:
  - Remove: `self._username = ...` line
  - Remove: `self._password = ...` line
  - Add: `self._api_key = config_entry.data.get(CONF_API_KEY, "")`
- [x] 5.2 In `async_step_user()` data_schema:
  - Remove: `vol.Required(CONF_USERNAME, default=self._username): str`
  - Remove: `vol.Required(CONF_PASSWORD, default=self._password): str`
  - Add: `vol.Required(CONF_API_KEY, default=self._api_key): str`
- [x] 5.3 Update user_input handling:
  - Remove: `self._username = user_input[CONF_USERNAME]`
  - Remove: `self._password = user_input[CONF_PASSWORD]`
  - Add: `self._api_key = user_input[CONF_API_KEY]`
- [x] 5.4 Update `async_create_entry()` data dict:
  - Remove: `CONF_USERNAME: self._username`
  - Remove: `CONF_PASSWORD: self._password`
  - Add: `CONF_API_KEY: self._api_key`

## 6. Remove Client ID Usage

- [x] 6.1 Open `custom_components/jellyfin/__init__.py`
- [x] 6.2 Remove imports for `CONF_USERNAME`, `CONF_PASSWORD`, `CONF_CLIENT_ID` from `homeassistant.const`
- [x] 6.3 Add import for `CONF_API_KEY` from `.const`
- [x] 6.4 Remove `client.config.app(...)` and any use of `CONF_CLIENT_ID`
- [x] 6.5 Remove device filtering based on `CONF_CLIENT_ID`

## 7. Normalize URL and Update Login

- [x] 7.1 Add a shared URL helper using `urllib.parse` with default ports 80/443
- [x] 7.2 Use the helper to normalize the server URL in `login()`
- [x] 7.3 Authenticate with API key via `client.authenticate(...)`
- [x] 7.4 Validate by calling `get_system_info()` and return failure on error

## 8. Update UI Strings

- [x] 8.1 Open `custom_components/jellyfin/strings.json`
- [x] 8.2 In `config.step.user.data`:
  - Remove: `"username": "..."` line
  - Remove: `"password": "..."` line
  - Add: `"api_key": "API Key"`
- [x] 8.3 In `options.step.user.data`:
  - Remove: `"username": "..."` line
  - Remove: `"password": "..."` line
  - Add: `"api_key": "API Key"`

## 9. Update Translations

- [x] 9.1 Open `custom_components/jellyfin/translations/en.json` and make same changes as strings.json
- [x] 9.2 Open `custom_components/jellyfin/translations/de.json` and update:
  - Remove username/password entries
  - Add: `"api_key": "API-Schlüssel"`
- [x] 9.3 Open `custom_components/jellyfin/translations/fr.json` and update:
  - Remove username/password entries
  - Add: `"api_key": "Clé API"`

## 10. Validation

- [x] 10.1 Remove the integration from Home Assistant if previously configured
- [x] 10.2 Restart Home Assistant
- [x] 10.3 Add the integration via UI - verify API key field appears
- [x] 10.4 Test with a valid API key - verify connection succeeds
- [x] 10.5 Test with an invalid API key - verify appropriate error shown
- [x] 10.6 Verify media player entities appear after successful configuration
