"""Backups decide the version number (owner, 2026-10-24): tests/backups/ keeps one backup, made by the first release of
the current major version: storage.json (every aac2.* store, from a test setup with made-up data) and setup.json (a
Save file). If the code loads it and runs a turn, a change is a minor or patch one; if not, it is a major version:
raise the major number, then `AAC_SAVE_BACKUP=1 pytest tests/test_backups.py` replaces the backup with the new one.
"""

from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import shutil

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import model_answers, reply, setup_aac

BACKUPS = Path(__file__).parent / "backups"
MANIFEST = Path(__file__).parent.parent / "custom_components" / "aac" / "manifest.json"
VERSION = json.loads(MANIFEST.read_text())["version"]
SAVED = next((p for p in sorted(BACKUPS.iterdir()) if p.is_dir()), None) if BACKUPS.is_dir() else None
NEW_MAJOR = SAVED is None or VERSION.split(".")[0] != SAVED.name.split(".")[0]


async def test_the_backup_is_of_this_major_version(hass: HomeAssistant, mock_entry, aioclient_mock, hass_storage):
    """One backup, of this major version (a new major version replaces it: AAC_SAVE_BACKUP=1)."""
    if not NEW_MAJOR:
        return
    if not os.environ.get("AAC_SAVE_BACKUP"):
        pytest.fail(f"{VERSION} is a new major version: AAC_SAVE_BACKUP=1 pytest tests/test_backups.py replaces the backup "
                    f"of {SAVED.name if SAVED else 'none'}")
    from custom_components.aac.defaults import async_voice_chain

    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "pc", "name": "PC"}]
    core.data["voice_chain"] = False
    await async_voice_chain(core)
    d = core.domain
    model_answers(aioclient_mock, reply("It is noon."))
    await d.cards["chat"].send("what time is it?")
    await d.cards["lists"].call("add_items", {"list": "Shopping", "items": ["Oat milk", "Coffee"]})
    await d.cards["calendar"].call("add", {"summary": "Dentist", "start": "2026-11-02T10:00"})
    await d.save_now()
    core.save()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))  # the delayed saves happen
    await hass.async_block_till_done()
    if SAVED:
        shutil.rmtree(SAVED)  # only the newest major version's backup is kept
    folder = BACKUPS / VERSION
    folder.mkdir(parents=True)
    (folder / "storage.json").write_text(json.dumps({k: v for k, v in hass_storage.items() if k.startswith("aac2")}, indent=1))
    (folder / "setup.json").write_text(json.dumps(d.export(), indent=1))


@pytest.mark.skipif(NEW_MAJOR, reason="a new major version: its backup is made first")
async def test_the_backup_still_loads(hass: HomeAssistant, mock_entry, aioclient_mock, hass_storage):
    """The backup of this major version: every card comes back with its data, a turn runs, its setup file loads.
    If this fails, the change is a major version."""
    hass_storage.update(json.loads((SAVED / "storage.json").read_text()))
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    setup = json.loads((SAVED / "setup.json").read_text())
    assert set(setup["cards"]) <= set(d.cards), f"cards of {SAVED.name} that do not load: {set(setup['cards']) - set(d.cards)}"
    chat = d.cards["chat"]
    assert any(m["text"] == "It is noon." for c in chat.chats() for m in chat.get(c["id"])["messages"])
    assert (await d.cards["lists"].call("read_list", {"list": "Shopping"}))["items"][0]["text"] == "Oat milk"
    assert any(e["summary"] == "Dentist" for e in await d.cards["calendar"].call("calendar", {"start": "2026-11-01", "end": "2026-11-05"}))
    model_answers(aioclient_mock, reply("Still here."))
    _, answer = await chat.send("still there?")
    assert answer == "Still here."
    await d.import_setup(setup)
    assert set(setup["cards"]) <= set(d.cards)
