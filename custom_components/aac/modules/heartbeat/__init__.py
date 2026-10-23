"""Heartbeat module: every few minutes a check-in request to the master (spec/streams.md).

The first module on the stream bus (owner, 2026-10-21: "the heartbeat should be a module"). It is a
source node: a clock checks every minute whether a domain's interval has passed (outside its quiet
hours) and sends a message from its card through the graph; the model node queues it (low priority, pointless
once the next heartbeat is due, one waiting at a time) and opens a chat of its own for a finding.
Tested instructions (Qwen3-8B and Qwen3.6-35B): alarms reported 12/12, quiet home silent; stating the
NOTHING rule several times made both models swallow real alarms.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ...const import DOMAIN
from ...broker import async_send
from ...streams import Node
from ...capabilities import in_quiet_hours
from .. import ModuleContext

NODE = "heartbeat"
_CTX = f"{DOMAIN}_heartbeat_ctx"


def request(ctx: ModuleContext, domain_id: str, now: bool = False) -> dict[str, Any]:
    """The check-in a domain's settings ask for."""
    interval = max(1, int(ctx.setting(domain_id, "interval") or 30))
    builtin = ctx.module.manifest["settings"]["instructions"]["placeholder"]
    return {
        "trigger": "heartbeat", "title": "Heartbeat", "text": "Check-in: anything I should know right now?",
        "intro": "Heartbeat: a scheduled check-in, nobody asked you anything.",
        "context": ["time", "home", "open_loops"],
        "instructions": (ctx.setting(domain_id, "instructions") or "").strip() or builtin,
        "tier": ctx.setting(domain_id, "tier") or "fast", "tools": bool(ctx.setting(domain_id, "tools")),
        "priority": "low", "due_minutes": interval, "tags": ["heartbeat"], "coalesce": True,
        "chat": "own", "chat_prefix": "hb", "now": now,
    }


async def async_queue(hass: HomeAssistant, domain_id: str | None = None) -> None:
    """One heartbeat through the graph and the queue (what the clock does when the interval passed)."""
    ctx: ModuleContext = hass.data[_CTX]
    data = ctx.entry.runtime_data
    domain = data.domain(domain_id) if domain_id else data.domain()
    await async_send(hass, ctx.entry, domain, request(ctx, domain.id), NODE)


async def async_run_now(hass: HomeAssistant, domain_id: str | None = None):
    """One heartbeat at once, without the queue ("Run once now"; also while the module is off): the run,
    or None (no master model, or one is already running)."""
    ctx: ModuleContext = hass.data[_CTX]
    data = ctx.entry.runtime_data
    domain = data.domain(domain_id) if domain_id else data.domain()
    # From the heartbeat's own card, so it works while the module is off in this domain (its wires stay)
    return await async_send(hass, ctx.entry, domain, request(ctx, domain.id, now=True), NODE)


async def async_setup_module(ctx: ModuleContext) -> None:
    """The node, its clock, the "Run once now" command and the page."""
    ctx.add_node(Node(id=NODE, title="Heartbeat", outputs={"out": "text"}, role="source",
                      description="Every few minutes (outside quiet hours) a check-in: anything worth telling you right now? "
                                  "Low priority; a finding opens a chat of its own, silence stays silent."))
    ctx.hass.data[_CTX] = ctx
    started = dt_util.utcnow()
    last: dict[str, datetime] = {}

    async def tick(now: datetime) -> None:
        for domain_id in ctx.domains:
            if not ctx.enabled(domain_id):
                continue
            interval = timedelta(minutes=max(1, int(ctx.setting(domain_id, "interval") or 30)))
            if now - last.get(domain_id, started) < interval:
                continue
            last[domain_id] = now
            if in_quiet_hours(dt_util.as_local(now), ctx.setting(domain_id, "quiet_start") or "", ctx.setting(domain_id, "quiet_end") or ""):
                continue
            await async_queue(ctx.hass, domain_id)

    ctx.entry.async_on_unload(async_track_time_interval(ctx.hass, tick, timedelta(minutes=1), name="AAC heartbeat"))
    ctx.add_ws_commands(ws_run, ws_info)
    ctx.add_panel_page("runs", "Heartbeat", "aac-heartbeat-page", "page.js")


def _ctx_and_domain(hass: HomeAssistant, connection, msg) -> tuple[ModuleContext, str] | None:
    ctx = hass.data.get(_CTX)
    if ctx is None or not hass.config_entries.async_loaded_entries(DOMAIN):
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return None
    dom = ctx.entry.runtime_data.domain(msg.get("domain")) or ctx.entry.runtime_data.domain()
    return ctx, dom.id


@websocket_api.websocket_command({vol.Required("type"): "aac/heartbeat/run", vol.Optional("domain"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_run(hass: HomeAssistant, connection, msg) -> None:
    """Run one heartbeat now (ignores quiet hours and the module's on/off switch)."""
    if not (found := _ctx_and_domain(hass, connection, msg)):
        return
    run = await async_run_now(hass, found[1])
    if run is None:
        connection.send_error(msg["id"], "skipped", "No master model, or a heartbeat is already running")
        return
    connection.send_result(msg["id"], {"run_id": run.id})


@websocket_api.websocket_command({vol.Required("type"): "aac/heartbeat/info", vol.Optional("domain"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_info(hass: HomeAssistant, connection, msg) -> None:
    """Settings and the last heartbeats of a domain, for the page."""
    if not (found := _ctx_and_domain(hass, connection, msg)):
        return
    ctx, domain_id = found
    dom = ctx.entry.runtime_data.domain(domain_id)
    chats = {c["id"] for c in dom.chats.list()}
    runs = [r for r in reversed(dom.runs.runs) if r.trigger == "heartbeat"][:30]
    connection.send_result(msg["id"], {
        "enabled": ctx.enabled(domain_id),
        **{k: ctx.setting(domain_id, k) for k in ("interval", "quiet_start", "quiet_end", "tier", "tools")},
        "runs": [{"id": r.id, "status": r.status, "started": r.started, "result": r.result,
                  "chat_id": r.chat_id if r.chat_id in chats else None, "chat_deleted": bool(r.chat_id) and r.chat_id not in chats}
                 for r in runs],
    })
