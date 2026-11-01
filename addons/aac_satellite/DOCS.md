# AAC satellite

Makes a microphone and speaker that are attached to the Home Assistant machine (USB, or Bluetooth through a
USB dongle) available to AAC. It streams the microphone continuously to AAC; AAC detects speech, turns it into
text and answers when you say its name (no wake word needed). The audio only lives in memory.

1. Install AAC first: when it starts, it makes a local-only user "AAC satellite" for this app and writes its access
   to `config/aac/satellite.json`.
2. Start this app. It picks the microphone/speaker of a known device by itself (see below), otherwise HA's default
   audio device; the options `mic` / `speaker` take a name from the app's log.

## Device quirks
Some devices need a nudge; their plugins live in `devices/`:
- **Plantronics/Poly BT600 dongle with a Calisto speaker**: the dongle only opens the Bluetooth audio after an
  "off-hook" HID command. The app sends it at start, after a replug and when the microphone delivers only silence,
  and hangs up when it stops.

## Echo cancellation
The option `echo_cancel` switches on PulseAudio's echo canceller between the microphone and the speaker (WebRTC, as in
video calls, where PulseAudio has it; Home Assistant's audio system has Speex): AAC's own voice is taken out of what the microphone hears. Then AAC can keep listening while it
speaks (a stop word works mid-answer): set the Microphone card in AAC to "keep listening". Speakerphones often cancel
their echo in hardware already. The microphone is always streamed; AAC's Microphone card decides what to do with it
while AAC speaks.

## What the speaker plays (`reference`)
The option `reference` also sends what the speaker plays (PulseAudio's monitor of it) as a second channel, sample by
sample next to the microphone. AAC's Microphone card can then subtract it with a delay, a gain and an invert (its
"Waveforms…" view lines them up): for a microphone and speaker in one housing, where the speaker's sound reaches the
microphone directly. It does not need `echo_cancel`.

## Hunting a hardware fault (`debug`)
The option `debug` keeps a record of what the hardware does, next to the app's work (`debug.py`): every loud stretch
on the raw microphone (before echo cancellation) or on the speaker's PulseAudio monitor (when, how loud, how
noise-like, whether AAC was playing), saved as a stereo WAV (left the microphone, right the monitor) in `/share/aac_satellite_debug/`;
PulseAudio's card, sink and source events; the levels every 5 minutes; and what a device reports (the BT600's HID
reports). Everything goes into the app's log.
