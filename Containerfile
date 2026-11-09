FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 CHESS_SCID_DIR=/imports/scid CHESS_STOCKFISH_PATH=/usr/local/bin/stockfish
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-flask ca-certificates curl patch \
    build-essential tcl-dev tk-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages --no-cache-dir python-chess
RUN curl -fL 'https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-linux-x86-64-universal.tar.gz' -o /tmp/stockfish19.tar.gz \
    && echo '9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f  /tmp/stockfish19.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/stockfish19.tar.gz -C /tmp \
    && install -m 0755 /tmp/stockfish/stockfish-linux-x86-64-universal /usr/local/bin/stockfish \
    && printf 'uci\nquit\n' | /usr/local/bin/stockfish | grep -m1 'id name Stockfish 19' \
    && rm -rf /tmp/stockfish /tmp/stockfish19.tar.gz

# Scid vs PC 4.27 (tcscid is the project's official console Tcl interpreter).
COPY scid-native.patch /tmp/scid-native.patch
RUN curl -fL 'https://sourceforge.net/projects/scidvspc/files/source/scid_vs_pc-4.27.tgz/download' -o /tmp/scid.tgz \
    && echo '696375c38e9d396ed53000acf21baf52c002b64de08084ba059e7504cf64f953  /tmp/scid.tgz' | sha256sum -c - \
    && mkdir /tmp/scid && tar -xzf /tmp/scid.tgz -C /tmp/scid --strip-components=1 \
    && cd /tmp/scid && patch -p1 < /tmp/scid-native.patch \
    && ./configure && make -j2 tcscid \
    && install -m 0755 tcscid /usr/local/bin/tcscid \
    && rm -rf /tmp/scid /tmp/scid.tgz /tmp/scid-native.patch

COPY app.py extensions.py scid_backend.py /app/
COPY templates/ /app/templates/
COPY static/ /app/static/
COPY tests/ /app/tests/
RUN mkdir -p /app/data/pgn /app/repertoire /app/training /imports/scid \
    && printf 'quit\n' | /usr/local/bin/stockfish >/dev/null
EXPOSE 8765
CMD ["python3", "app.py"]
