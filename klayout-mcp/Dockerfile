# klayout-mcp/Dockerfile
# Build context = ~/testFolder/KlayoutClaw

# === Stage 1: Builder ===
FROM ubuntu:24.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential \
    qtbase5-dev qttools5-dev libqt5svg5-dev qtmultimedia5-dev \
    libqt5xmlpatterns5-dev qttools5-dev-tools \
    libgit2-dev \
    ruby ruby-dev python3 python3-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN git clone https://github.com/KLayout/klayout.git --branch v0.30.5 --depth 1 src
WORKDIR /build/src
RUN ./build.sh -qt5 -python python3 -ruby ruby

# === Stage 2: Runtime ===
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev \
    build-essential cmake libqhull-dev \
    xvfb curl ca-certificates \
    libqt5core5a libqt5gui5 libqt5network5 libqt5xml5 libqt5svg5 libqt5multimedia5 libqt5printsupport5 \
    libqt5xmlpatterns5 libqt5sql5 libqt5designer5 libqt5help5 \
    libgit2-1.7 \
    libruby \
    && rm -rf /var/lib/apt/lists/*

# uv venv with the same scientific-Python pins as the agent images
COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /uvx /bin/
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHON_PATH="/opt/venv/bin/python"
RUN uv pip install gdstk==0.9.60 numpy==1.26.4 \
    opencv-python-headless==4.11.0.86 scikit-learn==1.6.1 \
    scipy==1.15.3 scikit-image==0.24.0 matplotlib==3.10.3 shapely==2.1.1

# Copy KLayout binaries from builder stage.
# Source build outputs to /build/src/bin-release/.
COPY --from=builder /build/src/bin-release/ /usr/local/lib/klayout/
RUN ln -s /usr/local/lib/klayout/klayout /usr/local/bin/klayout
ENV LD_LIBRARY_PATH="/usr/local/lib/klayout"

# KlayoutClaw source (build context is ~/testFolder/KlayoutClaw)
COPY . /opt/KlayoutClaw

# Worker symlinks → bind-mounted source for hot-reload.
# At runtime, /opt/KlayoutClaw/tools is overridden by a read-only bind mount;
# symlinks resolve through it.
RUN mkdir -p /root/.klayout/pymacros \
    && ln -sf /opt/KlayoutClaw/tools/route_worker.py /root/.klayout/pymacros/route_worker.py \
    && ln -sf /opt/KlayoutClaw/tools/evaluate_worker.py /root/.klayout/pymacros/evaluate_worker.py \
    && ln -sf /opt/KlayoutClaw/tools/ordered_loop.py /root/.klayout/pymacros/ordered_loop.py

COPY klayout-mcp/start-klayout.sh /usr/local/bin/start-klayout.sh
RUN chmod +x /usr/local/bin/start-klayout.sh

ENTRYPOINT ["/usr/local/bin/start-klayout.sh"]
