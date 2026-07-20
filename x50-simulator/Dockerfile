ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 \
    android-tools \
    bash \
    jq \
    curl

WORKDIR /app

COPY app /app

RUN chmod +x /app/run.sh

CMD [ "/app/run.sh" ]
