FROM alpine:3.19

# Install Python 3, ADB (android-tools), dos2unix, bash, and utilities
RUN apk add --no-cache \
    python3 \
    android-tools \
    bash \
    jq \
    curl \
    dos2unix

WORKDIR /app

COPY app /app

# Ensure LF line endings and executable permissions
RUN dos2unix /app/run.sh /app/server.py /app/index.html /app/app.js /app/styles.css && \
    chmod +x /app/run.sh

ENTRYPOINT [ "/app/run.sh" ]
