# Test dependencies only. No product source, user profile or credentials.
# Build from the repository root for the same architecture as the Docker daemon.
FROM golang:1.26.4-bookworm AS go
FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=go /usr/local/go /usr/local/go
COPY --from=node /usr/local/bin/node /usr/local/bin/node
ENV PATH=/venv/bin:/usr/local/go/bin:$PATH \
    UV_PROJECT_ENVIRONMENT=/venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git xvfb xauth x11-utils x11-xserver-utils \
    build-essential pkg-config python3-dev python3-pip python3-setuptools \
    libx11-dev libxtst-dev libxfixes-dev libxcomposite-dev libxdamage-dev \
    libxrandr-dev libxkbfile-dev libxres-dev libxext-dev libxrender-dev libxpresent-dev \
    libxxhash-dev liblz4-dev libbrotli-dev libx264-dev libvpx-dev libwebp-dev \
    libcairo2-dev libgtk-3-dev libpam0g-dev libsystemd-dev \
    dbus dbus-x11 gir1.2-gtk-3.0 python3-gi python3-gi-cairo python3-cairo \
    python3-pil python3-cryptography python3-lz4 python3-xdg python3-numpy \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv
# Xpra's bookworm binary repository has no arm64 packages. Build the same
# release on either architecture, retaining native capture and H.264 support.
RUN apt-get update && apt-get install -y --no-install-recommends python-gi-dev python3-cairo-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://files.pythonhosted.org/packages/a3/e2/04a57a8150f20b67f5a9eccfa6461682e52d494c72eeac66233998bb751f/xpra-6.5.tar.gz -o /tmp/xpra.tar.gz \
    && echo 'e687142fcbcee1e729270125ef219d99e74e1302d35d887f015fc9765523debb  /tmp/xpra.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/xpra.tar.gz -C /tmp \
    && /usr/bin/python3 -m pip install --break-system-packages 'Cython>=3.1,<3.3' \
    && cd /tmp/xpra-6.5 && NTHREADS=4 /usr/bin/python3 setup.py install \
    && rm -rf /tmp/xpra-6.5 /tmp/xpra.tar.gz
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY pantheon/__init__.py ./pantheon/__init__.py
RUN uv sync --frozen --no-dev --no-install-project \
    && uv pip install --python /venv/bin/python 'pytest>=8,<10' 'pytest-asyncio>=0.25,<2' python-xlib \
    && playwright install chromium --with-deps \
    && /usr/bin/python3 -c 'from xpra.codecs.x264 import encoder; encoder.selftest(full=False); assert any(s.encoding == "h264" for s in encoder.get_specs())'
WORKDIR /tmp
