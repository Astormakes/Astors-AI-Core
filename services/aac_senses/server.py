"""AAC senses: a small HTTP service that tags sounds, makes voice prints and hears moods, for AAC's audio stages.

Runs on any machine in AAC's Compute (CPU is enough). Endpoints (16-bit mono WAV in the body):
  GET  /v1/models            → {"data": [{"id": "aac-senses"}]}  (so AAC sees it online)
  POST /v1/audio/tags        → {"labels": [["Speech", 0.81], ["Music", 0.42], …]}  (AudioSet, top 10)
  POST /v1/audio/voiceprint  → {"embedding": [… 512 floats …]}  (compare with cosine similarity)
  POST /v1/audio/mood        → {"moods": [["neutral", 0.71], ["happy", 0.2], …]}  (how the voice sounds)

Models (downloaded on first start): MIT/ast-finetuned-audioset-10-10-0.4593 (sound tagger),
microsoft/wavlm-base-plus-sv (speaker verification x-vector), superb/wav2vec2-base-superb-er (speech
emotion: neutral, happy, angry, sad; from the sound of the voice, not the words; any other audio
classification model can be named with --mood-model).

    python server.py --port 8083 [--api-key-file FILE] [--threads 4] [--no-tagger] [--no-voices] [--no-mood]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
from pathlib import Path
import wave

from aiohttp import web
import numpy as np
import torch
from transformers import (
    ASTFeatureExtractor,
    ASTForAudioClassification,
    AutoFeatureExtractor,
    AutoModelForAudioClassification,
    WavLMForXVector,
)

LOG = logging.getLogger("aac_senses")
RATE = 16000
TAGGER = "MIT/ast-finetuned-audioset-10-10-0.4593"
VOICES = "microsoft/wavlm-base-plus-sv"
MOOD = "superb/wav2vec2-base-superb-er"
MOOD_NAMES = {"neu": "neutral", "hap": "happy", "ang": "angry", "sad": "sad", "exc": "excited", "fru": "frustrated",
              "sur": "surprised", "fea": "fearful", "dis": "disgusted", "cal": "calm"}


def read_wav(data: bytes) -> np.ndarray:
    """16-bit WAV → float32 mono at 16 kHz (other rates are resampled linearly)."""
    with wave.open(io.BytesIO(data)) as w:
        rate, channels, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(audio):
        audio = np.interp(np.linspace(0, len(audio) - 1, int(len(audio) * RATE / rate)), np.arange(len(audio)), audio)
    return audio.astype(np.float32)


class Senses:
    """The models, loaded once; calls run in a thread, one at a time."""

    def __init__(self, tagger: bool = True, voices: bool = True, mood: str | None = MOOD) -> None:
        self.tagger = self.voices = self.mood = None
        if tagger:
            LOG.info("Loading %s", TAGGER)
            self.tag_fx = ASTFeatureExtractor.from_pretrained(TAGGER)
            self.tagger = ASTForAudioClassification.from_pretrained(TAGGER).eval()
        if voices:
            LOG.info("Loading %s", VOICES)
            self.voice_fx = AutoFeatureExtractor.from_pretrained(VOICES)
            self.voices = WavLMForXVector.from_pretrained(VOICES).eval()
        if mood:
            LOG.info("Loading %s", mood)
            self.mood_fx = AutoFeatureExtractor.from_pretrained(mood)
            self.mood = AutoModelForAudioClassification.from_pretrained(mood).eval()
        self.lock = asyncio.Lock()

    @torch.inference_mode()
    def tags(self, audio: np.ndarray, top: int = 10) -> list[list]:
        inputs = self.tag_fx(audio, sampling_rate=RATE, return_tensors="pt")
        probs = torch.sigmoid(self.tagger(**inputs).logits[0])
        best = torch.topk(probs, top)
        labels = self.tagger.config.id2label
        return [[labels[int(i)], round(float(p), 3)] for p, i in zip(best.values, best.indices, strict=True)]

    @torch.inference_mode()
    def voiceprint(self, audio: np.ndarray) -> list[float]:
        inputs = self.voice_fx(audio, sampling_rate=RATE, return_tensors="pt")
        emb = torch.nn.functional.normalize(self.voices(**inputs).embeddings, dim=-1)[0]
        return [round(float(x), 5) for x in emb]

    @torch.inference_mode()
    def moods(self, audio: np.ndarray) -> list[list]:
        inputs = self.mood_fx(audio, sampling_rate=RATE, return_tensors="pt")
        probs = torch.softmax(self.mood(**inputs).logits[0], dim=-1)
        labels = self.mood.config.id2label
        found = [[MOOD_NAMES.get(labels[i].lower()[:3], labels[i].lower()), round(float(p), 3)] for i, p in enumerate(probs)]
        return sorted(found, key=lambda x: x[1], reverse=True)


def make_app(senses: Senses, api_key: str | None) -> web.Application:
    @web.middleware
    async def auth(request: web.Request, handler):
        if api_key and request.headers.get("Authorization") != f"Bearer {api_key}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def models(_request: web.Request) -> web.Response:
        return web.json_response({"object": "list", "data": [{"id": "aac-senses", "object": "model"}]})

    async def run(request: web.Request, fn) -> web.Response:
        try:
            audio = read_wav(await request.read())
        except (wave.Error, EOFError, ValueError) as err:
            return web.json_response({"error": f"not a WAV: {err}"}, status=400)
        if len(audio) < RATE // 4:
            return web.json_response({"error": "too short (min 0.25 s)"}, status=400)
        async with senses.lock:
            return web.json_response(await asyncio.to_thread(fn, audio))

    async def tags(request: web.Request) -> web.Response:
        if senses.tagger is None:
            return web.json_response({"error": "the sound tagger is not loaded (--no-tagger)"}, status=404)
        return await run(request, lambda a: {"labels": senses.tags(a)})

    async def voiceprint(request: web.Request) -> web.Response:
        if senses.voices is None:
            return web.json_response({"error": "voice prints are not loaded (--no-voices)"}, status=404)
        return await run(request, lambda a: {"embedding": senses.voiceprint(a)})

    async def mood(request: web.Request) -> web.Response:
        if senses.mood is None:
            return web.json_response({"error": "the mood model is not loaded (--no-mood)"}, status=404)
        return await run(request, lambda a: {"moods": senses.moods(a)})

    app = web.Application(middlewares=[auth], client_max_size=32 * 1024 * 1024)
    app.add_routes([web.get("/v1/models", models), web.post("/v1/audio/tags", tags),
                    web.post("/v1/audio/voiceprint", voiceprint), web.post("/v1/audio/mood", mood)])
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8083)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--no-tagger", action="store_true", help="do not load the sound tagger")
    parser.add_argument("--no-voices", action="store_true", help="do not load the voice print model")
    parser.add_argument("--no-mood", action="store_true", help="do not load the mood model")
    parser.add_argument("--mood-model", default=MOOD, help="a Hugging Face audio classification model for moods")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    torch.set_num_threads(args.threads)
    key = args.api_key_file.read_text().strip() if args.api_key_file else None
    web.run_app(make_app(Senses(not args.no_tagger, not args.no_voices, None if args.no_mood else args.mood_model), key), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
