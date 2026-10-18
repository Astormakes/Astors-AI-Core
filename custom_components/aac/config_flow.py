"""Config flow for AAC – Astor's AI Core."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DOMAIN,
    LOGGER,
)
from .provider import (
    OpenAICompatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
)


async def _async_fetch_models(hass: HomeAssistant, data: dict[str, Any]) -> list[str]:
    provider = OpenAICompatProvider(
        async_get_clientsession(hass), data[CONF_BASE_URL], data.get(CONF_API_KEY)
    )
    return await provider.list_models()


class AACConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up AAC: server first, then the model."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._models: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the model server."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._models = await _async_fetch_models(self.hass, user_input)
            except ProviderAuthError:
                errors["base"] = "invalid_auth"
            except ProviderConnectionError:
                errors["base"] = "cannot_connect"
            except ProviderError:
                LOGGER.exception("Unexpected answer from model server")
                errors["base"] = "unknown"
            else:
                if not self._models:
                    errors["base"] = "no_models"
                else:
                    self._data = user_input
                    return await self.async_step_model()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_BASE_URL): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.URL)
                        ),
                        vol.Optional(CONF_API_KEY): TextSelector(
                            TextSelectorConfig(type=TextSelectorType.PASSWORD)
                        ),
                    }
                ),
                user_input or {CONF_BASE_URL: DEFAULT_BASE_URL},
            ),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the models the server offers."""
        if user_input is not None:
            data = {**self._data, **user_input}
            self._async_abort_entries_match(
                {CONF_BASE_URL: data[CONF_BASE_URL], CONF_MODEL: data[CONF_MODEL]}
            )
            return self.async_create_entry(
                title=f"{DEFAULT_NAME} ({data[CONF_MODEL]})",
                data=data,
                options={
                    CONF_PROMPT: DEFAULT_PROMPT,
                    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
                },
            )

        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODEL, default=self._models[0]): SelectSelector(
                        SelectSelectorConfig(options=self._models, custom_value=True)
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return AACOptionsFlow()


class AACOptionsFlow(OptionsFlow):
    """Prompt, HA control and sampling settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)
            return self.async_create_entry(data=user_input)

        apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PROMPT, default=options.get(CONF_PROMPT, DEFAULT_PROMPT)
                ): TemplateSelector(),
                vol.Optional(
                    CONF_LLM_HASS_API, default=options.get(CONF_LLM_HASS_API, [])
                ): SelectSelector(SelectSelectorConfig(options=apis, multiple=True)),
                vol.Optional(
                    CONF_TEMPERATURE,
                    default=options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                ): NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05)),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                ): NumberSelector(NumberSelectorConfig(min=16, max=32768, step=1)),
                vol.Optional(
                    CONF_THINKING, default=options.get(CONF_THINKING, DEFAULT_THINKING)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
