"""Backups decide the version number (owner, 2026-10-24): versions are Z.Y.X; X counts changes the backup survives,
Y goes up when a backup no longer loads, Z is a rebuild. tests/backups/ keeps one backup, made by the first release of
the current Z.Y: storage.json (every aac2.* store, from a test setup with made-up data) and setup.json (a Save file).
If the code loads it and runs a turn, raise X; if not, raise Y, then `AAC_SAVE_BACKUP=1 pytest tests/test_backups.py`
replaces the backup with the new one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from homeassistant.core import HomeAssistant

from .conftest import model_answers, reply, setup_aac

BACKUPS = Path(__file__).parent / "backups"
MANIFEST = Path(__file__).parent.parent / "custom_components" / "aac" / "manifest.json"
VERSION = json.loads(MANIFEST.read_text())["version"]
SAVED = next((p for p in sorted(BACKUPS.iterdir()) if p.is_dir()), None) if BACKUPS.is_dir() else None
NEW_MAJOR = SAVED is None or VERSION.split(".")[:2] != SAVED.name.split(".")[:2]  # a new Z.Y: its backup is made first


async def test_the_backup_is_of_this_major_version(hass: HomeAssistant, mock_entry, aioclient_mock, hass_storage):
    """One backup, of this Z.Y (a new one replaces it: AAC_SAVE_BACKUP=1)."""
    if not NEW_MAJOR:
        return
    if not os.environ.get("AAC_SAVE_BACKUP"):
        pytest.fail(f"{VERSION} starts a new Z.Y: AAC_SAVE_BACKUP=1 pytest tests/test_backups.py replaces the backup "
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
    chat = d.cards["chat"]  # an old install: its chats were quiet for long
    for c in chat.data["chats"].values():
        c["created"] = c["updated"] = "2026-01-01T12:00:00+00:00"
    await chat.save()
    await d.save_now()
    await core.save_now()
    if SAVED:
        shutil.rmtree(SAVED)  # only the backup of the newest Z.Y is kept
    folder = BACKUPS / VERSION
    folder.mkdir(parents=True)
    (folder / "storage.json").write_text(json.dumps({k: v for k, v in hass_storage.items() if k.startswith("aac2")}, indent=1))
    (folder / "setup.json").write_text(json.dumps(d.export(), indent=1))


@pytest.mark.skipif(NEW_MAJOR, reason="a new Z.Y: its backup is made first")
async def test_the_backup_still_loads(hass: HomeAssistant, mock_entry, aioclient_mock, hass_storage):
    """The backup of this Z.Y: every card comes back with its data, a turn runs, its setup file loads. If this fails,
    the change raises Y."""
    hass_storage.update(json.loads((SAVED / "storage.json").read_text()))
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    assert [k["id"] for k in core.audio.settings.get("known", [])] == ["pc"]  # the core's own store came back
    setup = json.loads((SAVED / "setup.json").read_text())
    names = lambda: sorted(c.name for c in d.cards.values())  # noqa: E731 - by name: an id that did not fit its name moved
    saved = sorted(spec["title"] for spec in setup["cards"].values())
    assert not set(saved) - set(names()), f"cards of {SAVED.name} that do not load: {set(saved) - set(names())}"
    chat = d.cards["chat"]
    assert any(m["text"] == "It is noon." for c in chat.chats() for m in chat.get(c["id"])["messages"])
    assert (await d.cards["lists"].call("read_list", {"list": "Shopping"}))["items"][0]["text"] == "Oat milk"
    assert any(e["summary"] == "Dentist" for e in await d.cards["calendar"].call("calendar", {"start": "2026-11-01", "end": "2026-11-05"}))
    model_answers(aioclient_mock, reply("Still here."))
    _, answer = await chat.send("still there?")
    assert answer == "Still here."
    await d.import_setup(setup)
    assert names() == saved
