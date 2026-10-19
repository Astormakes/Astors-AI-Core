"""Config flow and migration tests."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aac.const import DOMAIN
from custom_components.aac.store import STORAGE_KEY
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE_URL, MODEL


async def test_one_click(hass: HomeAssistant, mock_panel):
    """Adding AAC asks nothing, creates the entry and the panel."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    await hass.async_block_till_done()
    assert mock_panel["register"].call_args.kwargs["frontend_url_path"] == "aac"


async def test_single_instance(hass: HomeAssistant, mock_entry):
    """Only one AAC."""
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_migrate_v1(hass: HomeAssistant, hass_storage):
    """A v0.1 entry (server/model in the entry) moves into the panel store."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"AAC ({MODEL})",
        version=1,
        data={"base_url": BASE_URL, "api_key": "secret", "model": MODEL},
        options={"prompt": "Old prompt", "llm_hass_api": ["assist"], "temperature": 0.3},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data == {}
    assert entry.title == "AAC"
    store = entry.runtime_data.store
    assert store.models[0]["base_url"] == BASE_URL
    assert store.models[0]["api_key"] == "secret"
    assert store.pools["master"] == [store.models[0]["id"]]
    assert store.master["prompt"] == "Old prompt"
    assert store.master["temperature"] == 0.3
    assert hass_storage[STORAGE_KEY]["data"]["models"][0]["model"] == MODEL


async def test_old_store_keys_migrate(hass: HomeAssistant, hass_storage, mock_entry):
    """0.4 stored models as "workers" and the master's model as "worker"."""
    from .conftest import MODEL_CFG, setup_aac

    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"workers": [dict(MODEL_CFG)], "master": {"worker": MODEL_CFG["id"]}},
    }
    await setup_aac(hass, mock_entry)
    store = mock_entry.runtime_data.store
    assert store.models[0]["id"] == MODEL_CFG["id"]
    assert store.pools["master"] == [MODEL_CFG["id"]]
