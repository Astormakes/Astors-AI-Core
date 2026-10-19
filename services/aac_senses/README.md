# AAC senses

A small HTTP service for AAC's audio stages: a **sound tagger** (AudioSet, ~500 classes: music, laughter,
a door, a motorbike …) and **voice prints** (who is talking). CPU is enough.

```
python3 -m venv venv
./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
./venv/bin/pip install transformers numpy aiohttp
./venv/bin/python server.py --port 8083 --threads 4     # first start downloads ~1 GB of models
```

Then in AAC: Compute → Add model (base URL `http://<machine>:8083/v1`, model `aac-senses`) and put it into
the voice pool's **Sound tagger** and **Voice prints** roles (Audio pipeline page). Modules: Sounds, Voices.

Endpoints: `GET /v1/models`, `POST /v1/audio/tags`, `POST /v1/audio/voiceprint` (16-bit mono WAV body).
Models: `MIT/ast-finetuned-audioset-10-10-0.4593`, `microsoft/wavlm-base-plus-sv`. ~0.8 s per call on a
modern desktop CPU (4 threads); an old server CPU needs a lighter tagger.
