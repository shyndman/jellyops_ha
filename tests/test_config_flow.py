import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeParsedDate:
    def __init__(self, raw: str):
        self._raw = raw

    def __format__(self, _format_spec: str) -> str:
        return self._raw


_parser_module = types.SimpleNamespace(parse=lambda value: _FakeParsedDate(value))
sys.modules.setdefault("dateutil", types.SimpleNamespace(parser=_parser_module))
sys.modules["dateutil.parser"] = _parser_module

_vol_module = types.ModuleType("voluptuous")


class _FakeSchema(dict):
    def extend(self, extra):
        merged = dict(self)
        merged.update(extra)
        return _FakeSchema(merged)


_vol_module.Schema = lambda data=None: _FakeSchema(data or {})
_vol_module.Required = lambda key, default=None: key
_vol_module.Optional = lambda key, default=None: key
_vol_module.All = lambda *validators: (lambda value: value)
_vol_module.Coerce = lambda _type: (lambda value: value)
_vol_module.UNDEFINED = object()
sys.modules.setdefault("voluptuous", _vol_module)


class _FakeJellyfinClient:
    def __init__(self, *args, **kwargs):
        self.config = types.SimpleNamespace(data={})
        self.jellyfin = types.SimpleNamespace()

    def authenticate(self, *args, **kwargs):
        return None


_jf_module = types.ModuleType("jellyfin_apiclient_python")
_jf_module.JellyfinClient = _FakeJellyfinClient
sys.modules.setdefault("jellyfin_apiclient_python", _jf_module)

ha_module = types.ModuleType("homeassistant")


class _BaseFlow:
    def async_show_form(self, step_id, data_schema, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(self, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    async def async_set_unique_id(self, *args, **kwargs):
        return None

    def _abort_if_unique_id_configured(self):
        return None

    def async_abort(self, reason):
        return {"type": "abort", "reason": reason}


class _ConfigFlow(_BaseFlow):
    pass


class _OptionsFlow(_BaseFlow):
    pass


config_entries_module = types.ModuleType("homeassistant.config_entries")
class _HandlerRegistry:
    def register(self, _domain):
        def decorator(handler):
            return handler
        return decorator


config_entries_module.ConfigFlow = _ConfigFlow
config_entries_module.OptionsFlow = _OptionsFlow
config_entries_module.ConfigFlowResult = dict  # used only as a return-type annotation
config_entries_module.CONN_CLASS_LOCAL_PUSH = "local_push"
config_entries_module.HANDLERS = _HandlerRegistry()

exceptions_module = types.ModuleType("homeassistant.exceptions")


class HomeAssistantError(Exception):
    pass


exceptions_module.HomeAssistantError = HomeAssistantError

core_module = types.ModuleType("homeassistant.core")


def callback(func):
    return func


core_module.callback = callback

selector_module = types.ModuleType("homeassistant.helpers.selector")
selector_module.selector = lambda value: value

helpers_module = types.ModuleType("homeassistant.helpers")
helpers_module.selector = selector_module

const_module = types.ModuleType("homeassistant.const")
const_module.CONF_URL = "url"
const_module.CONF_VERIFY_SSL = "verify_ssl"

ha_module.config_entries = config_entries_module
ha_module.exceptions = exceptions_module
ha_module.core = core_module
ha_module.const = const_module
ha_module.helpers = helpers_module

sys.modules.setdefault("homeassistant", ha_module)
sys.modules.setdefault("homeassistant.config_entries", config_entries_module)
sys.modules.setdefault("homeassistant.exceptions", exceptions_module)
sys.modules.setdefault("homeassistant.core", core_module)
sys.modules.setdefault("homeassistant.const", const_module)
sys.modules.setdefault("homeassistant.helpers", helpers_module)
sys.modules.setdefault("homeassistant.helpers.selector", selector_module)

ROOT = Path(__file__).resolve().parents[1]

custom_components_pkg = types.ModuleType("custom_components")
custom_components_pkg.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components_pkg)

jellyfin_pkg = types.ModuleType("custom_components.jellyfin")
jellyfin_pkg.__path__ = [str(ROOT / "custom_components" / "jellyfin")]
sys.modules.setdefault("custom_components.jellyfin", jellyfin_pkg)

sys.path.append(str(ROOT))

from custom_components.jellyfin.config_flow import JellyfinFlowBase, JellyfinFlowHandler  # noqa: E402
from custom_components.jellyfin.const import (  # noqa: E402
    CONF_API_KEY,
    CONF_GENERATE_UPCOMING,
    CONF_GENERATE_YAMC,
    CONF_LIBRARY_USER_ID,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL  # noqa: E402


def _build_flow() -> JellyfinFlowHandler:
    flow = JellyfinFlowHandler()
    hass = MagicMock()

    async def async_add_executor_job(func, *args):
        return func(*args)

    hass.async_add_executor_job = async_add_executor_job
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    hass.loop = loop
    flow.hass = hass
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(
        side_effect=lambda title, data: {"type": "create_entry", "title": title, "data": data}
    )
    return flow


def test_flow_without_optional_features_creates_entry():
    flow = _build_flow()
    user_input = {
        CONF_URL: "http://server",
        CONF_API_KEY: "token",
        CONF_VERIFY_SSL: True,
        CONF_GENERATE_UPCOMING: False,
        CONF_GENERATE_YAMC: False,
    }

    async def _run():
        with patch.object(JellyfinFlowBase, "_authenticate_client", return_value=MagicMock()):
            return await flow.async_step_user(user_input=user_input)

    result = asyncio.run(_run())

    assert result["type"] == "create_entry"
    assert result["data"][CONF_LIBRARY_USER_ID] is None
    assert result["data"][CONF_GENERATE_UPCOMING] is False
    assert result["data"][CONF_GENERATE_YAMC] is False


def test_flow_with_optional_features_requires_user_selection():
    flow = _build_flow()
    user_input = {
        CONF_URL: "http://server",
        CONF_API_KEY: "token",
        CONF_VERIFY_SSL: False,
        CONF_GENERATE_UPCOMING: True,
        CONF_GENERATE_YAMC: True,
    }

    async def _run_first():
        with patch.object(
            JellyfinFlowBase, "_authenticate_client", return_value=MagicMock()
        ), patch.object(
            JellyfinFlowBase,
            "_async_get_user_options",
            AsyncMock(return_value=[{"label": "User A", "value": "abc"}]),
        ):
            return await flow.async_step_user(user_input=user_input)

    result = asyncio.run(_run_first())

    assert result["type"] == "form"
    assert result["step_id"] == "select_user"

    result2 = asyncio.run(flow.async_step_select_user({CONF_LIBRARY_USER_ID: "abc"}))
    assert result2["type"] == "create_entry"
    assert result2["data"][CONF_LIBRARY_USER_ID] == "abc"
    assert result2["data"][CONF_GENERATE_UPCOMING] is True
    assert result2["data"][CONF_GENERATE_YAMC] is True
