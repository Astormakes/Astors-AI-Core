ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-aiohttp pulseaudio-utils

WORKDIR /app
COPY aac_satellite.py /app/
COPY devices /app/devices
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
