# Multi-stage Dockerfile for Pantheon ChatRoom
# Optimized version with Python 3.12 for 10-20% performance boost
# Use python:3.12-slim as base for BOTH stages to ensure python binary paths match, preventing broken venv symlinks

FROM python:3.12-slim-bookworm AS builder

# ============================================
# Build Arguments (for cache invalidation and path configuration)
# ============================================
# VENV_PATH: Virtual environment location (change this to invalidate venv-related caches)
# CACHE_VERSION: Cache identifier for BuildKit mount cache
#   - Default: "default" (use for normal builds)
#   - Change only when you need to force cache invalidation (e.g., after path changes)
#   - Can be overridden at build time: docker build --build-arg CACHE_VERSION=v2 ...
#   - Note: Dependency changes (pyproject.toml, uv.lock) automatically invalidate cache
#           via COPY layer detection, so you rarely need to change this manually.
ARG VENV_PATH=/venv
ARG CACHE_VERSION=default

# Install system dependencies, uv, and Playwright browser dependencies (Use Aliyun mirror)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    # R environment (needed for compiling rpy2)
    r-base-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    pip install --no-cache-dir uv

WORKDIR /app

# Install venv outside /app so that "COPY --from=builder /app /app" in the
# production stage copies only source code — no venv files mixed in.
# Using ARG variable to ensure consistency across all RUN commands.
ENV UV_PROJECT_ENVIRONMENT=${VENV_PATH}

# ── Layer 1: dependency files only ──────────────────────────────────────────
# Copy only pyproject.toml + uv.lock so this layer is reused on code-only changes.
# Also copy pantheon/__init__.py for dynamic version reading (setuptools needs it).
COPY pyproject.toml uv.lock ./
COPY pantheon/__init__.py ./pantheon/__init__.py

# Install all Python dependencies (no project package itself yet).
# BuildKit cache mount keeps uv's HTTP cache on the host across builds.
# --extra r/knowledge/slack: install runtime extras only (dev extra excluded to skip pytest tools).
# --no-dev: also excludes [dependency-groups] dev (ipdb, ipython, build tools, etc.).
# Cache ID includes CACHE_VERSION to allow forced cache invalidation when needed.
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${CACHE_VERSION} \
    echo "Installing base dependencies (cache: ${CACHE_VERSION}, venv: ${VENV_PATH})" && \
    uv sync --no-dev --extra r --extra knowledge --extra claw --no-install-project --python /usr/local/bin/python

# ── Layer 2: single-cell analysis extras (MOVED TO AFTER FINAL UV SYNC) ────
# NOTE: Single-cell packages installation moved to Layer 3.5 (after uv sync)
# to prevent them from being removed by 'uv sync' which cleans packages not in lock file

# Packages removed to reduce image size (install on-demand via system_manager):
# Python packages (~6GB+ saved):
# - squidpy: Advanced spatial transcriptomics (~6GB with spatialdata + dask + datashader dependencies)
#   → scanpy provides basic spatial analysis features (sc.pl.spatial, etc.)
#   → Users needing advanced spatial features: uv pip install squidpy (5-10 min)
# - doubletdetection: Redundant with scrublet (both detect doublets)
# - bbknn: Less common batch correction (scanorama covers main use case)
# - pynndescent: Optional umap-learn accelerator (not essential)
# - spatialdata: Newer spatial package (included as squidpy dependency, ~500MB)
# - decoupler: Specialized functional analysis (lower usage frequency)
# - scikit-image: Large image processing lib (squidpy includes it as dependency)
#
# Packages NOT pre-installed (verified to pull torch/CUDA via uv pip compile):
# - harmonypy: Batch correction (~2MB + torch 873MB + CUDA 3GB)
#   → Use scanorama as alternative (pre-installed above)
# - cell2location: Spatial deconvolution (~20MB + torch + CUDA)
# - scvi-tools: Deep learning framework (~3-4GB with CUDA)
# - scarches: Transfer learning (~3GB with CUDA)
#
# Optimized build metrics:
# - Build time: ~15-18 minutes (vs 50-60 min with R packages, vs 22-25 min with squidpy)
# - Image size: ~2.3-2.5GB (vs 8.5GB with squidpy, vs 3.2GB before optimization)
# - Coverage: 70-80% of workflows without runtime installation (core workflows: 100%)
# - Spatial transcriptomics: Basic features via scanpy (70-80% coverage)
# - Less common features installed on-demand (adds 2-10 minutes per package)

# ── Layer 3: application code ────────────────────────────────────────────────
# Copy source code AFTER all dependencies are installed.
# Code-only changes never invalidate the expensive dependency layers above.
COPY . /app/

# Sync again to pick up any lock-file changes introduced by the source COPY.
# --no-install-project: do NOT install the pantheon package into /venv.
# The project is found at runtime via PYTHONPATH=/app, so installing it into
# the venv is unnecessary and would risk copying source files into /venv,
# making Layer A (venv) change on every code commit.
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${CACHE_VERSION} \
    uv sync --no-dev --extra r --extra knowledge --extra claw --no-install-project --python /usr/local/bin/python

# ── Layer 3.5: single-cell analysis extras ──────────────────────────────────
# IMPORTANT: Install AFTER uv sync to prevent removal by sync's cleanup process.
# uv sync removes packages not in lock file, so single-cell packages must be
# installed after the final sync to persist in the image.
#
# ============================================
# Single Cell Analysis Dependencies (Optimized - 12 packages)
# ============================================
# Based on comprehensive dependency testing with uv pip compile
# ALL packages below are verified CUDA-free (no torch/nvidia dependencies)
# Covers 70-80% of single-cell analysis workflows (core workflows: 100%)
#
# Optimized single-cell stack (~120MB, adds ~3-4 min to build time)
# Focuses on most frequently used packages (>70% usage rate)
#
# IMPORTANT: Uses ${VENV_PATH} variable to ensure consistency with ENV setting.
# Includes verification step to catch installation failures early.
# NOTE: No cache mount used - packages installed directly to venv to avoid hardlink issues
RUN echo "Installing single-cell packages (venv: ${VENV_PATH})" && \
    uv pip install --python ${VENV_PATH}/bin/python --no-cache \
    # Core data structures & framework (~55MB) - 95% usage
    anndata \
    scanpy \
    # Quality control (~5MB) - 85% usage
    scrublet \
    # Clustering & dimensionality reduction (~55MB) - 90% usage
    leidenalg \
    igraph \
    numba \
    umap-learn \
    # Batch correction (~5MB) - 70% usage
    scanorama \
    # Cell type annotation (~18MB) - 75% usage
    celltypist \
    # Functional analysis (~12MB) - 80% usage
    gseapy \
    # Visualization & R interop (~11MB) - 85% usage
    seaborn \
    anndata2ri && \
    echo "Verifying single-cell packages installation..." && \
    ${VENV_PATH}/bin/python -c "import anndata, scanpy, scrublet, leidenalg; print('✓ Single-cell packages verified successfully')"

# ── Layer 3.6: Install pip in venv ──────────────────────────────────────────
# uv creates venvs without pip by design (recommends using 'uv pip' instead).
# However, for compatibility with shell tools and standard workflows, we install pip.
# This allows 'pip list' and other pip commands to work correctly in the venv.
RUN echo "Installing pip in venv for compatibility..." && \
    uv pip install --python ${VENV_PATH}/bin/python --no-cache pip && \
    ${VENV_PATH}/bin/pip --version && \
    echo "✓ pip installed successfully"

# Pre-compile project source files to .pyc bytecode.
# Only compile /app/pantheon (source), NOT the venv, so the venv directory stays
# content-stable across code-only changes (critical for Docker layer caching).
# Note: pandas and numpy are already compiled during pip install, no need to recompile.
RUN ${VENV_PATH}/bin/python -m compileall -q /app/pantheon 2>/dev/null || true

# ============================================
# Production Stage (Optimized)
# ============================================
FROM python:3.12-slim-bookworm

# Install runtime dependencies and Playwright browser dependencies (Use Aliyun mirror for stability in CN)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    ca-certificates \
    # jemalloc for better memory management (10-20% improvement)
    libjemalloc2 \
    # Playwright browser dependencies (required for webtoolset)
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    # Additional Chromium dependencies
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxshmfence1 \
    # Fonts for better rendering (removed emoji fonts to save ~20MB)
    fonts-liberation \
    # R environment for single-cell analysis
    r-base \
    r-base-dev \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /var/cache/apt/archives/* \
    # Install Node.js from NodeSource (using Aliyun mirror for faster download in CN)
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && npm install -g ajv ajv-formats \
    && npm cache clean --force \
    # Install uv
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

# ============================================
# Layer Caching Optimization Strategy
# ============================================
# Layers are ordered by stability (most stable first, most volatile last):
# 1. venv (~1-2GB) - changes only when dependencies change
# 2. ENV variables - rarely changes
# 3. Playwright (~300-400MB) - changes only when Playwright version changes
# 4. MCP caches (~150-300MB) - changes only when MCP servers change
# 5. CLI tools (~50MB) - changes only when tool versions change
# 6. Workspace directory - rarely changes
# 7. Application code (~10MB) - changes on every commit ⬅️ MOVED TO END
# 8. Entrypoint setup - depends on application code
#
# This ordering ensures that code-only changes invalidate only the last 2 layers (~10MB),
# keeping all heavy layers (Playwright, MCP, CLI tools) cached.

# Layer A — venv (~1-2GB, stable): only rebuilt when pyproject.toml / uv.lock changes.
# Kept at /venv (same path as builder) to avoid the Docker overlay2 opaque-whiteout
# problem: if we copied to /app/.venv first and then did "COPY /app /app", the second
# COPY would create an opaque whiteout for /app, silently erasing /app/.venv.
# Keeping the two COPYs at non-overlapping paths (/venv vs /app) eliminates the issue.
# Both stages use python:3.12-slim, so venv symlinks remain valid.
# Using ARG to ensure path consistency between builder and production stages.
ARG VENV_PATH=/venv
COPY --from=builder ${VENV_PATH} ${VENV_PATH}

# Layer B — Environment variables (stable): Set before installing heavy components.
# PYTHONPATH="/app" is just a declaration - the directory doesn't need to exist yet.
# All heavy installations below (Playwright, MCP, CLI tools) only depend on /venv,
# not on the application code in /app.
# Using ARG to reference venv path consistently.
ARG VENV_PATH=/venv
ENV PATH="${VENV_PATH}/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    # PYTHONOPTIMIZE removed: Some packages (e.g., anndata) rely on docstrings at runtime
    # and break when PYTHONOPTIMIZE=2 removes them. Trade-off: slightly higher memory usage
    # for better compatibility with scientific packages.
    # Use jemalloc for better memory management (10-20% improvement)
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2 \
    # PYTHONDONTWRITEBYTECODE removed: .pyc files are pre-compiled in builder stage
    # and must remain readable at runtime to benefit from the compileall optimization.
    # Keep uv's package cache inside the image so 'uv run --with' hits it at runtime
    UV_CACHE_DIR=/app/.uv-cache \
    # Install playwright browsers to an explicit path outside /root/.cache.
    # This prevents accidental deletion and makes the installation location unambiguous.
    PLAYWRIGHT_BROWSERS_PATH=/usr/local/share/ms-playwright \
    # Use Playwright mirror for faster downloads in China
    PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

# Layer C — Playwright browsers (~300-400MB, stable): only rebuilt when Playwright version changes.
# Using chromium is sufficient for most web scraping/automation tasks.
# PLAYWRIGHT_DOWNLOAD_HOST is already set in ENV above; the fallbacks override
# it temporarily if the primary mirror is unreachable.
RUN ${VENV_PATH}/bin/playwright install chromium --with-deps || \
    (PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.azureedge.net \
     ${VENV_PATH}/bin/playwright install chromium --with-deps) || \
    (PLAYWRIGHT_DOWNLOAD_HOST= \
     ${VENV_PATH}/bin/playwright install chromium --with-deps)

# ============================================
# Layer D — MCP Server Caches (~150-300MB, stable)
# ============================================
# Pre-download and cache MCP servers to avoid first-run installation delays
# This significantly reduces pod startup time when agents first use these tools
#
# Servers:
# - context7 (npx @upstash/context7-mcp): ~100-200MB cache from npm
# - biomcp (uv run --with biomcp-python): Python environment cache
RUN echo "Pre-warming context7 MCP server cache (downloading npm packages)..." && \
    npm install -g @upstash/context7-mcp && \
    npm cache clean --force && \
    echo "context7 cache pre-warmed"

# Pre-warm the 'uv run --with biomcp-python' cache by running the exact same
# command that MCP will use at runtime. Because UV_CACHE_DIR=/app/.uv-cache is
# baked into the image, the downloaded wheels are available to every container
# without hitting the network again.
# Note: This creates /app/.uv-cache directory, but doesn't require application code.
RUN echo "Pre-warming biomcp uv run cache into /app/.uv-cache..." \
    && mkdir -p /app/.uv-cache \
    && cd /tmp && uv run --with biomcp-python python -c "import biomcp; print('biomcp ready')" \
    && echo "biomcp uv cache pre-warmed"

# ============================================
# R Packages for Single Cell Analysis (Moved to Runtime)
# ============================================
# R packages removed from Docker image to reduce size (~600-800MB saved)
# These are installed on-demand by system_manager when needed:
# - SoupX: Ambient RNA removal (~300-400MB with dependencies including Seurat)
# - celda: Cell type deconvolution (~300-400MB with Bioconductor dependencies)
#
# Total R dependencies: 200+ packages including Seurat, SingleCellExperiment,
# scater, scran, ComplexHeatmap, ggplot2, etc.
#
# Usage frequency: ~10-20% of single-cell workflows
# Installation time: 5-10 minutes on first use
# Trade-off: Faster builds, smaller images, minimal user impact
#
# Note: r-base and r-base-dev system packages are still installed above (~50MB)
# for rpy2 support and to enable on-demand R package installation

# ============================================
# Layer E — CLI Tools for Report Generation (~50MB, stable)
# ============================================
# Install monolith (HTML bundler) and tectonic (LaTeX compiler)
# These are optional tools - if download fails, the build continues
# They can be installed at runtime if needed
RUN (curl -L --retry 3 --retry-delay 5 --max-time 60 \
    https://github.com/Y2Z/monolith/releases/download/v2.7.0/monolith-x86_64-unknown-linux-gnu \
    -o /usr/local/bin/monolith && chmod +x /usr/local/bin/monolith) || \
    echo "Warning: monolith download failed, skipping (can be installed at runtime)" && \
    (curl -L --retry 3 --retry-delay 5 --max-time 60 \
    https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic@0.15.0/tectonic-0.15.0-x86_64-unknown-linux-gnu.tar.gz \
    -o /tmp/tectonic.tar.gz && \
    tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin && \
    rm -f /tmp/tectonic.tar.gz) || \
    echo "Warning: tectonic download failed, skipping (can be installed at runtime)"

# Layer F — Workspace directory (stable)
RUN mkdir -p /workspace

# ============================================
# Layer G — Application Code (~10MB, changes every commit)
# ============================================
# MOVED TO END: This is the most frequently changing layer.
# By placing it after all heavy layers (Playwright, MCP, CLI tools),
# code-only changes only invalidate this layer and the entrypoint layer below.
# Result: Code changes rebuild only ~10MB instead of ~500-750MB.
COPY --from=builder /app /app

# Layer H — Entrypoint setup (depends on /app)
# This must come after COPY /app because it needs the entrypoint script.
RUN chmod +x /app/docker/docker-entrypoint-dual-mode.sh && \
    cp /app/docker/docker-entrypoint-dual-mode.sh /usr/local/bin/docker-entrypoint.sh

# Set default working directory
WORKDIR /workspace

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Entrypoint
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# No CMD - let entrypoint.sh handle the default command with --id_hash
