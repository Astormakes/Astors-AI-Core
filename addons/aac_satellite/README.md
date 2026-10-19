# AAC satellite

Streams one microphone to AAC and plays AAC's spoken answers. Works with
PulseAudio/PipeWire (`parec`/`paplay`) or ALSA (`arecord`/`aplay`); the only
Python dependency is `aiohttp`.

```bash
python -m venv ~/.aac-sat && ~/.aac-sat/bin/pip install aiohttp
~/.aac-sat/bin/python aac_satellite.py --list                 # microphones and speakers
~/.aac-sat/bin/python aac_satellite.py --url http://homeassistant.local:8123 \
    --token-file ~/.aac_token --name PC --area Office [--mic <name>] [--speaker <name>]
```

`~/.aac_token` holds a Home Assistant long-lived access token (your profile →
Security). The satellite reconnects on its own; AAC shows it under
SETTINGS → Audio pipeline.

Testing without speaking: `--input-file talk.wav` streams a recording as the
microphone (needs ffmpeg), `--output-dir answers/` saves the answers instead of
playing them.

Protocol: [spec/audio-source.md](../../spec/audio-source.md). As a Home Assistant app: see DOCS.md.
