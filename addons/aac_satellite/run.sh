#!/usr/bin/with-contenv bashio
# AAC satellite as a Home Assistant app: stream HA's microphone to AAC, play AAC's answers.

CONFIG=/homeassistant/aac/satellite.json
until [ -f "$CONFIG" ]; do
  bashio::log.warning "Waiting for ${CONFIG}: AAC writes it when it starts (is AAC installed?)"
  sleep 30
done

ARGS=(--config-file "$CONFIG" --id ha_app --name "$(bashio::config 'name')")
bashio::config.has_value 'area' && ARGS+=(--area "$(bashio::config 'area')")
bashio::config.has_value 'mic' && ARGS+=(--mic "$(bashio::config 'mic')")
bashio::config.has_value 'speaker' && ARGS+=(--speaker "$(bashio::config 'speaker')")
bashio::config.true 'echo_cancel' && ARGS+=(--echo-cancel)

bashio::log.info "Microphones and speakers HA's audio system offers:"
pactl list sources short 2>/dev/null | grep -v '\.monitor' | cut -f2
pactl list sinks short 2>/dev/null | cut -f2

exec python3 -u /app/aac_satellite.py "${ARGS[@]}"
