FROM python:3.11-slim-bookworm

ARG PAWN_VERSION=3.10.10
ARG PAWN_URL=https://github.com/pawn-lang/compiler/releases/download/v3.10.10/pawnc-3.10.10-linux.tar.gz
ARG PAWN_STDLIB_URL=https://github.com/pawn-lang/pawn-stdlib/archive/refs/heads/master.zip
ARG SAMP_STDLIB_URL=https://github.com/pawn-lang/samp-stdlib/archive/refs/tags/0.3.7-R2-2-1.zip

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PAWN_COMPILER=/usr/local/bin/pawncc \
    PAWN_INCLUDE_DIR=/opt/pawn/include \
    LD_LIBRARY_PATH=/usr/local/lib

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
        tar \
        libc6 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Pawn Compiler и libpawnc.so
RUN set -eu; \
    mkdir -p /tmp/pawn-compiler /opt/pawn/include; \
    curl -fL --retry 4 --retry-delay 2 "$PAWN_URL" -o /tmp/pawn.tar.gz; \
    tar -xzf /tmp/pawn.tar.gz -C /tmp/pawn-compiler; \
    PAWNCC="$(find /tmp/pawn-compiler -type f -name pawncc | head -n 1)"; \
    PAWNLIB="$(find /tmp/pawn-compiler -type f -name libpawnc.so | head -n 1)"; \
    test -n "$PAWNCC"; \
    test -n "$PAWNLIB"; \
    install -m 0755 "$PAWNCC" /usr/local/bin/pawncc; \
    install -m 0644 "$PAWNLIB" /usr/local/lib/libpawnc.so; \
    COMPILER_INCLUDE="$(find /tmp/pawn-compiler -type d -name include | head -n 1 || true)"; \
    if [ -n "$COMPILER_INCLUDE" ]; then cp -a "$COMPILER_INCLUDE"/. /opt/pawn/include/; fi; \
    ldconfig; \
    rm -rf /tmp/pawn-compiler /tmp/pawn.tar.gz

# Стандартная библиотека Pawn
RUN set -eu; \
    mkdir -p /tmp/pawn-stdlib; \
    curl -fL --retry 4 --retry-delay 2 "$PAWN_STDLIB_URL" -o /tmp/pawn-stdlib.zip; \
    unzip -q /tmp/pawn-stdlib.zip -d /tmp/pawn-stdlib; \
    ROOT="$(find /tmp/pawn-stdlib -mindepth 1 -maxdepth 1 -type d | head -n 1)"; \
    cp -a "$ROOT"/. /opt/pawn/include/; \
    rm -rf /tmp/pawn-stdlib /tmp/pawn-stdlib.zip

# Стандартная библиотека SA-MP, включая a_samp.inc
RUN set -eu; \
    mkdir -p /tmp/samp-stdlib; \
    curl -fL --retry 4 --retry-delay 2 "$SAMP_STDLIB_URL" -o /tmp/samp-stdlib.zip; \
    unzip -q /tmp/samp-stdlib.zip -d /tmp/samp-stdlib; \
    ROOT="$(find /tmp/samp-stdlib -mindepth 1 -maxdepth 1 -type d | head -n 1)"; \
    cp -a "$ROOT"/. /opt/pawn/include/; \
    test -f /opt/pawn/include/a_samp.inc; \
    rm -rf /tmp/samp-stdlib /tmp/samp-stdlib.zip

# Проверяем компилятор ещё на этапе сборки.
RUN set -eu; \
    OUTPUT="$(pawncc 2>&1 || true)"; \
    echo "$OUTPUT"; \
    echo "$OUTPUT" | grep -q "Pawn compiler"

# /app на Bothost может перекрываться bind mount, поэтому приложение лежит вне /app.
WORKDIR /usr/src/pawn-bot
COPY main.py /usr/src/pawn-bot/main.py

CMD ["python", "/usr/src/pawn-bot/main.py"]
