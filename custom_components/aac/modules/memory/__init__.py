"""Memory module: AAC's long-term memory, the default provider of the core's memory slot (spec/memory.md).

Moved out of the core in 0.47 (owner, 2026-10-21: "everything not core becomes a module, even memory").
The core only asks the slot (slots.py): what to hand the model along with a message, what to keep after
one. Everything else is here: one SQLite file per domain (`aac/memory[.<domain>].db`, unchanged), the
tools remember / recall / lookup_facts / forget, the write path after each chat message (extractor.py),
its plans in the open loops of every automatic run, and the page. Its embedding model is the pool role
"memory" (none = keywords only). Switched off in a domain: no memory there at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import DOMAIN
from ...provider import OpenAICompatProvider, ProviderConnectionError
from ...streams import Node
from .. import REQUEST_PORT, ModuleContext
from .api import AACMemoryAPI
from .extractor import async_extract
from .store import KINDS, SOURCES, MemoryStore, memory_path, summarize

if TYPE_CHECKING:
    from ...domains import Domain

_CTX = f"{DOMAIN}_memory_ctx"


class MemoryProvider:
    """The memory slot, served from one MemoryStore per domain."""

    def __init__(self, ctx: ModuleContext) -> None:
        """Initialize."""
        self.ctx = ctx
        self.stores: dict[str, MemoryStore] = {}

    def store(self, domain_id: str | None = None) -> MemoryStore:
        """A domain's memory (None = the first domain)."""
        return self.stores.get(domain_id or "") or next(iter(self.stores.values()))

    async def async_context(self, domain_id: str, text: str) -> str | None:
        """Matching memories, handed over with the message: small models rarely call recall on their own."""
        memory = self.store(domain_id)
        if not (found := await memory.async_context(text)):
            return None
        hint = "Memories that may help (from your long-term memory):\n" + "\n".join(
            f"- {m.text} ({m.source}{'' if m.confirmed else ', unconfirmed'}, {m.created[:10]})" for m in found)
        if series := await memory.async_series_for(found):
            hint += "\nExact values (structured facts):\n" + "\n".join(f"- {line}" for line in series)
        return hint

    async def async_after_message(self, domain_id: str, text: str, stored_now: list[str],
                                  provider: OpenAICompatProvider, model: str) -> None:
        """The write path: keep what was worth keeping (the owner can switch it off)."""
        if self.ctx.setting(domain_id, "extract"):
            await async_extract(self.store(domain_id), provider, model, text, stored_now)

    async def async_model_changed(self) -> None:
        """A new embedding model: every domain's memories get new vectors (from their text)."""
        for memory in self.stores.values():
            await memory.async_reembed()

    async def async_remove_domain(self, domain: Domain) -> None:
        """The domain's memory file goes with it."""
        if (memory := self.stores.pop(domain.id, None)) is None:
            return
        path = memory._path  # noqa: SLF001
        await memory.async_close()
        for suffix in ("", "-wal", "-shm"):
            await self.ctx.hass.async_add_executor_job(lambda p=path.with_name(path.name + suffix): p.unlink(missing_ok=True))


async def async_setup_module(ctx: ModuleContext) -> None:
    """Every domain's memory, the slot, the tools, the open loops and the page."""
    hass = ctx.hass
    pool = ctx.entry.runtime_data.pool

    async def embed(texts: list[str]):
        if (picked := pool.endpoint("memory")) is None:
            return None  # keywords only
        model_id, endpoint = picked
        provider = OpenAICompatProvider(async_get_clientsession(hass), endpoint.base_url, endpoint.api_key, connect_timeout=3)
        try:
            vectors = await provider.embed(endpoint.model, texts)
        except ProviderConnectionError as err:
            pool.mark_down(model_id, str(err))
            raise
        pool.mark_up(model_id)
        return vectors, endpoint.model

    memory = MemoryProvider(ctx)
    for dom in ctx.domains.values():
        store = memory.stores[dom.id] = MemoryStore(hass, memory_path(hass, dom.suffix), embed)
        await store.async_open()
        ctx.entry.async_on_unload(store.async_close)
    hass.data[_CTX] = memory
    ctx.provide_slot("memory", memory)

    async def plans(domain_id: str) -> list[str]:
        return [f"- plan: {m.text} (since {m.created[:10]})" for m in await memory.store(domain_id).async_plans()]

    ctx.add_open_loops(plans)

    # In the request chain (spec/v1.md): recalls go along with the message; the answer feeds the write path
    async def recall(request) -> None:
        if request.words and (hint := await memory.async_context(request.domain.id, request.words)):
            request.context.append(hint)

    async def keep(domain_id: str, port: str, item: dict) -> None:
        request = item["request"]
        if request.kind == "chat" and request.words:  # heartbeats and triggers: nobody said anything
            await memory.async_after_message(domain_id, request.words, item.get("stored_now") or [],
                                             item["provider"], item["model"])

    ctx.add_node(Node(id="memory", title="Memory recall", inputs=REQUEST_PORT, outputs=REQUEST_PORT, adds=["context"],
                      order=20, annotate=recall))
    ctx.add_node(Node(id="memory_keeper", title="Memory keeper", inputs={"answers": "model.answer"}, run=keep))
    ctx.add_llm_api(lambda dom, api_id, name: AACMemoryAPI(hass=hass, id=api_id, name=name, memory=memory.stores[dom.id]))
    ctx.add_ws_commands(ws_memory_list, ws_memory_save, ws_memory_delete, ws_memory_reembed, ws_facts)
    ctx.add_panel_page("browse", "Memory", "aac-memory-page", "page.js")


def memory_of(hass: HomeAssistant, domain_id: str | None = None) -> MemoryStore:
    """A domain's memory store (None = the first domain)."""
    return hass.data[_CTX].store(domain_id)


def _memory(hass: HomeAssistant, connection, msg) -> MemoryStore | None:
    if hass.data.get(_CTX) is None or not hass.config_entries.async_loaded_entries(DOMAIN):
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return None
    return memory_of(hass, msg.get("domain"))


@websocket_api.websocket_command({vol.Required("type"): "aac/memory/list", vol.Optional("domain"): vol.Any(str, None),
                                  vol.Optional("query"): str, vol.Optional("unconfirmed", default=False): bool})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_memory_list(hass: HomeAssistant, connection, msg) -> None:
    """Memories: newest first, or the best matches for a query."""
    if (memory := _memory(hass, connection, msg)) is None:
        return
    items = await memory.async_recall(msg["query"], limit=50) if msg.get("query") else await memory.async_list(unconfirmed=msg["unconfirmed"])
    connection.send_result(msg["id"], {"memories": [m.as_dict() for m in items], "stats": await memory.async_count()})


@websocket_api.websocket_command({
    vol.Required("type"): "aac/memory/save", vol.Optional("domain"): vol.Any(str, None),
    vol.Optional("memory_id"): str,
    vol.Optional("text"): vol.All(str, vol.Length(min=1)),
    vol.Optional("kind"): vol.In(list(KINDS)),
    vol.Optional("source"): vol.In(list(SOURCES)),
    vol.Optional("confirmed"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_memory_save(hass: HomeAssistant, connection, msg) -> None:
    """Add a memory (no id) or change one."""
    if (memory := _memory(hass, connection, msg)) is None:
        return
    if "memory_id" not in msg:
        if not msg.get("text"):
            connection.send_error(msg["id"], "invalid", "Text is needed")
            return
        mem = await memory.async_remember(msg["text"], msg.get("kind", "note"), msg.get("source", "user said"))
    else:
        fields = {k: msg[k] for k in ("text", "kind", "source", "confirmed") if k in msg}
        if (mem := await memory.async_update(msg["memory_id"], **fields)) is None:
            connection.send_error(msg["id"], "not_found", "Memory not found")
            return
    connection.send_result(msg["id"], mem.as_dict())


@websocket_api.websocket_command({vol.Required("type"): "aac/memory/delete", vol.Optional("domain"): vol.Any(str, None),
                                  vol.Required("memory_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_memory_delete(hass: HomeAssistant, connection, msg) -> None:
    """Forget a memory."""
    if (memory := _memory(hass, connection, msg)) is not None:
        await memory.async_delete(msg["memory_id"])
        connection.send_result(msg["id"])


@websocket_api.websocket_command({vol.Required("type"): "aac/memory/reembed", vol.Optional("domain"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_memory_reembed(hass: HomeAssistant, connection, msg) -> None:
    """Give all memories vectors from the current embedding model (waits until done)."""
    if (memory := _memory(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], {"embedded": await memory.async_reembed()})


@websocket_api.websocket_command({vol.Required("type"): "aac/facts", vol.Optional("domain"): vol.Any(str, None),
                                  vol.Optional("subject"): str, vol.Optional("attribute"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_facts(hass: HomeAssistant, connection, msg) -> None:
    """Structured facts: the overview, or one series with its values."""
    if (memory := _memory(hass, connection, msg)) is None:
        return
    if msg.get("subject") and msg.get("attribute"):
        series = await memory.async_facts(msg["subject"], msg["attribute"])
        connection.send_result(msg["id"], {"series": series, "summary": summarize(series)})
    else:
        keys = await memory.async_fact_keys()
        for key in keys:
            key["summary"] = summarize(await memory.async_facts(key["subject"], key["attribute"]))
        connection.send_result(msg["id"], {"keys": keys})
