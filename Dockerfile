# pocket-dev の実行コンテナ
# Debian(glibc) なので manylinux の pip wheel・.deb がそのまま使える。
# コンテナ内は root なので apt install が無条件に通る（Termux の制約が無い）。
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        git curl wget ca-certificates openssh-client \
        build-essential pkg-config \
        sudo procps psmisc less nano vim-tiny tmux ripgrep jq unzip zip \
        locales tini \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TERM=xterm-256color \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# エージェント本体（作業ファイルは /workspace にボリュームとして置く）
WORKDIR /opt/pocket-dev
COPY agent ./agent
COPY web ./web

EXPOSE 8765

# コンテナ内の 0.0.0.0 待受は安全。外部への露出は docker のポート公開範囲で制御する
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "agent.server", "--root", "/workspace", "--bind", "0.0.0.0"]
