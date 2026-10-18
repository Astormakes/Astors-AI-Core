"""Config and options flow tests."""

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.aac.const import CONF_BASE_URL, CONF_MODEL, DOMAIN
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE_URL, MODEL

MODELS = {"object": "list", "data": [{"id": MODEL}, {"id": "other"}]}


async def test_full_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Server -> model -> entry."""
    aioclient_mock.get(f"{BASE_URL}/models", json=MODELS)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_KEY: "secret"}
    )
    assert result["step_id"] == "model"
    assert aioclient_mock.mock_calls[0][3] == {"Authorization": "Bearer secret"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/models", json=MODELS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: MODEL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BASE_URL: BASE_URL,
        CONF_API_KEY: "secret",
        CONF_MODEL: MODEL,
    }
    assert result["options"][CONF_LLM_HASS_API] == ["assist"]


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """Unreachable server shows an error."""
    aioclient_mock.get(f"{BASE_URL}/models", exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_auth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    """401 shows invalid_auth."""
    aioclient_mock.get(f"{BASE_URL}/models", status=401, text="no")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: BASE_URL, CONF_API_KEY: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options(hass: HomeAssistant, aioclient_mock, mock_entry):
    """Options can be changed and empty HA API is dropped."""
    aioclient_mock.get(f"{BASE_URL}/models", json=MODELS)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "prompt": "New prompt",
            CONF_LLM_HASS_API: [],
            "temperature": 0.2,
            "max_tokens": 256,
            "thinking": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options["prompt"] == "New prompt"
    assert CONF_LLM_HASS_API not in mock_entry.options
