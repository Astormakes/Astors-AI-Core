"""Log: puts whatever reaches its one input into AAC's log, of any kind: a context (its newest entry), a clip (kept as
audio to listen to), audio starting and stopping, and on a dotted wire what is new in what is provided there. Allowed
to read AAC's log (Permissions), it shows the whole log on its page, gives it to models and provides it on a dotted
wire; otherwise its page shows what it wrote itself."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time
from typing import Any

from custom_components.aac.api import SAMPLE_RATE, Card, Context, new_id, pcm_to_wav, register

VERSION = "2.1.2"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

READ_EVERY = 2  # seconds between looks at what dotted wires provide
QUIET = 1.5  # seconds without audio from a satellite: it stopped
PROVIDED = 50  # the newest entries a dotted wire from 'provides' reads
SHOWN = 300  # entries its page shows at once


@register
class Log(Card):
    """Writes down what reaches it."""

    type = "log"
    title = "Log"
    category = "Data"
    help = ("Puts whatever reaches its input into AAC's log (Settings → Logs): a context (its newest entry and where it "
            "came from), a clip (length, satellite), audio starting and stopping, and on a dotted wire what is new in what "
            "is provided there. Allowed to read AAC's log (Permissions), it shows the whole log on its page, models can "
            "read it, and 'provides' gives it to cards that read.")
    inputs = {"in": "any"}
    outputs = {"provides": "read"}
    ports_help = {"in": "anything: contexts, clips, audio, a dotted wire from what a card provides",
                  "provides": "AAC's newest log entries, for cards that read (dotted; when it may read the log): one entry"}
    settings_schema = {"audio": {"type": "bool", "title": "Keep clips as audio", "default": True,
                                 "help": "Each clip is saved as a WAV file (config/aac/data/<domain>/<card>/) and can be played on its page."},
                       "audio_kept": {"type": "number", "title": "Clips kept", "default": 50, "min": 1,
                                      "help": "Older clips' audio is deleted."}}
    needs = {"log"}
    functions = {"read": {"help": "AAC's log of this domain, newest last: what passed the cards' wires, what went wrong, "
                                  "what was changed.",
                          "args": {"search": {"type": "string", "help": "A word in the entry."},
                                   "card": {"type": "string", "help": "Only what this card wrote (its name)."},
                                   "level": {"type": "string", "help": "Only error, warning, info or flow (what passed the wires)."},
                                   "last": {"type": "number", "help": "How many entries (default 30)."}}}}
    ui = True

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._task: asyncio.Task | None = None
        self._seen: dict[str, set[str]] = {}  # dotted wire → entry ids already written down
        self._audio: dict[str, list[float]] = {}  # satellite → [started, last chunk]

    @classmethod
    def upgrade(cls, settings: dict[str, Any], made_with: str) -> dict[str, Any]:
        settings.pop("keep", None)  # 1.3.0: AAC's log keeps the entries
        return settings

    async def start(self) -> None:
        self._task = self.task(self._watch(), background=True)

    async def unload(self) -> None:
        if self._task:
            self._task.cancel()

    def _write(self, what: str, came_from: str, text: str, context: Context | None = None, **extra: Any) -> None:
        self.log(f"{came_from}: {text}" if came_from else text, what=what, context=context, **extra)

    @staticmethod
    def _entry(e: dict[str, Any]) -> str:
        calls = ", ".join(f"{c['name']}({c.get('args') or {}})" for c in e["data"].get("calls") or [])
        return f"#{e['tag']}: {e['text'] or calls}".strip()

    async def handle(self, context: Context, port: str) -> None:
        newest = context.newest
        what = "snapshot" if context.streaming else "context"
        by = newest["tag"].split(".", 1)[0] if newest else ""
        self._write(what, self._name(by), (self._entry(newest) if newest else "(empty)") + f"  [{len(context.entries)} entries]", context)

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if clip.get("stop"):
            self._write("clip", clip.get("name") or clip.get("source") or "", "stop")
            return
        part = f", phrase {clip['part']}" if clip.get("part") else ""
        said = f": {clip['text']}" if clip.get("text") else ""
        extra = {}
        if self.settings.get("audio", True) and clip.get("pcm"):  # kept to listen to
            extra["audio"] = f"{new_id()}.wav"
            await self._keep_audio(extra["audio"], pcm_to_wav(clip["pcm"], int(clip.get("rate") or SAMPLE_RATE)))
        self._write("clip", clip.get("name") or clip.get("source") or "", f"{clip.get('seconds')} s{part}{said}", **extra)

    async def _keep_audio(self, name: str, wav: bytes) -> None:
        """Save a clip; only the newest ones stay (their log entries lose the file)."""
        await self.save_file(name, wav)
        for old in (await self.own_files(".wav"))[:-max(1, int(self.settings.get("audio_kept") or 50))]:
            await self.remove_file(old)

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        source, now = meta.get("name") or meta.get("source") or "audio", time.monotonic()
        if source not in self._audio:
            self._audio[source] = [now, now]
            self._write("audio", source, "started")
        else:
            self._audio[source][1] = now

    async def _watch(self) -> None:
        """Audio that stopped, and what is new on dotted wires (what a card provides)."""
        while True:
            await asyncio.sleep(READ_EVERY)
            now = time.monotonic()
            for source, (began, last) in list(self._audio.items()):
                if now - last > QUIET:
                    self._audio.pop(source)
                    self._write("audio", source, f"stopped ({round(last - began)} s)")
            for card, port in self.wired_into("in"):
                if card.ports()[1].get(port) != "read":
                    continue
                held = card.held(port)
                key, ids = f"{card.id}.{port}", [e["id"] for e in (held.conversation if held else [])]
                if key not in self._seen:  # the first look: what is there now is the start
                    self._seen[key] = set(ids)
                    continue
                new = [e for e in held.conversation if e["id"] not in self._seen[key]] if held else []
                self._seen[key] = set(ids)
                for e in new[-5:]:
                    self._write("read", f"{card.name}.{port}", self._entry(e))

    def _name(self, card_id: str) -> str:
        return next((c.name for c in self.all_cards() if c.id == card_id), card_id)

    def _card_id(self, name: str) -> str | None:
        """A card named so (or with that id)."""
        word = name.strip().lower()
        return next((c.id for c in self.all_cards() if word in (c.id, c.name.lower())), word or None)

    def _lines(self, entries: list[dict[str, Any]]) -> str:
        """Entries as text for a model: time, level, who, what."""
        def who(e: dict[str, Any]) -> str:
            return self._name(e["card"]) if e.get("card") else f"AAC ({e.get('part')})"
        return "\n".join(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['time']))} {e['level']} {who(e)}"
                         f"{f' {p}' if (p := e.get('port')) else ''} {e['what']}: {e['text']}{f' (×{e['n']})' if e.get('n') else ''}"
                         for e in entries)

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function != "read":
            return await super().call(function, args, context)
        level = str(args.get("level") or "").strip().lower()
        try:
            found = self.read_log(search=str(args.get("search") or ""), card=self._card_id(str(args.get("card") or "")),
                                  levels=[level] if level else None, last=max(1, min(int(args.get("last") or 30), 200)))
        except PermissionError as err:
            return {"error": str(err)}
        return self._lines(found) or "Nothing in the log matches."

    def held(self, port: str) -> Context | None:
        """What a dotted wire from 'provides' reads: AAC's newest log entries as one entry (when it may read the log)."""
        if port != "provides":
            return None
        context = Context()
        try:
            found = self.read_log(levels=["error", "warning", "info"], last=PROVIDED)
        except PermissionError:
            return context
        if found:
            text = self._lines(found)
            entry = context.add(self.tag("text"), text)
            entry["id"] = hashlib.sha1(f"{self.id}|{text}".encode()).hexdigest()[:10]
        return context

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        if action == "entries":  # for its page: the whole log when it may read it, else what it wrote itself
            allowed = self.may("log", "read")
            before = int(args["before"]) if args.get("before") else None
            found = self.read_log(card=None if allowed else self.id, before=before, last=SHOWN)
            names = {c.id: c.name for c in self.all_cards()}
            return {"entries": found, "all": allowed, "names": names}
        if action == "audio":  # one kept clip, to play
            name = str(args.get("file") or "")
            wav = await self.load_file(name) if re.fullmatch(r"[0-9a-f]+\.wav", name) else None
            if wav is None:
                raise ValueError("that clip is no longer kept")
            return base64.b64encode(wav).decode()
        return await super().ui_call(action, args)
