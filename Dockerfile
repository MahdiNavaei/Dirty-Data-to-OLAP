# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.11-slim-trixie@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
ARG NODE_IMAGE=node:22.14.0-bookworm-slim@sha256:1c18d9ab3af4585870b92e4dbc5cac5a0dc77dd13df1a5905cea89fc720eb05b
ARG NGINX_IMAGE=nginx:1.27.4-alpine@sha256:4ff102c5d78d254a6f0da062b3cf39eaf07f01eec0927fd21e219d0af8bc0591
ARG UV_IMAGE=docker.io/astral/uv@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5
ARG DESBORDANTE_REPOSITORY=https://github.com/Desbordante/desbordante-core.git
ARG DESBORDANTE_REVISION=b211961f3f272ed8815ef1ffbda90573b11e1116

FROM ${UV_IMAGE} AS uv-bin

FROM ${PYTHON_IMAGE} AS python-dependencies

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_HTTP_TIMEOUT=120
ENV UV_HTTP_RETRIES=10
WORKDIR /app

COPY --from=uv-bin /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv --system-site-packages "${VIRTUAL_ENV}"

COPY pyproject.toml uv.lock .python-version README.md ./
# hll is a locked sdist whose build-system asks for setuptools<77. The pinned
# Python base contains its immutable system setuptools bootstrap; use it only
# for this known legacy sdist and keep all runtime dependencies lock-synced.
RUN uv sync --locked --python "${VIRTUAL_ENV}/bin/python" --no-dev --no-install-project \
    --no-build-isolation-package hll \
    --extra api --extra sql --extra files --extra profiling

COPY src ./src
COPY tools ./tools
COPY config ./config
COPY policies ./policies
COPY docs/architecture ./docs/architecture
RUN uv sync --locked --python "${VIRTUAL_ENV}/bin/python" --no-dev \
    --no-build-isolation-package hll \
    --no-build-isolation-package dirty-data-to-olap \
    --extra api --extra sql --extra files --extra profiling
RUN python tools/generate_step29_openapi.py --output /app/frontend-openapi.json

FROM ${PYTHON_IMAGE} AS desbordante-provider-build

ARG DESBORDANTE_REPOSITORY
ARG DESBORDANTE_REVISION
WORKDIR /tmp

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cmake \
        g++ \
        git \
        libboost-container-dev \
        libboost-graph-dev \
        libboost-thread-dev \
        libicu-dev \
        make \
        ninja-build \
    && rm -rf /var/lib/apt/lists/*

RUN git init /tmp/desbordante \
    && git -C /tmp/desbordante remote add origin "${DESBORDANTE_REPOSITORY}" \
    && git -C /tmp/desbordante fetch --depth 1 origin "${DESBORDANTE_REVISION}" \
    && git -C /tmp/desbordante checkout --detach FETCH_HEAD \
    && test "$(git -C /tmp/desbordante rev-parse HEAD)" = "${DESBORDANTE_REVISION}"

# Debian Trixie ships Boost 1.83 while this pinned upstream revision declares
# 1.85 as its minimum. The build uses only the compatible released API; keep
# the upstream commit identity fixed and record this toolchain adaptation in
# the Step30 receipt rather than silently depending on a host Boost install.
RUN sed -i 's/find_package(Boost 1.85.0/find_package(Boost 1.83.0/' \
        /tmp/desbordante/cmake/desbordante_deps.cmake \
    && cmake -S /tmp/desbordante -B /tmp/desbordante/build \
        -G Ninja \
        -D DESBORDANTE_BUILD_TESTS=OFF \
        -D DESBORDANTE_BUILD_BENCHMARKS=OFF \
        -D DESBORDANTE_BINDINGS=INSTALL \
        -D DESBORDANTE_FETCH_DATASETS=OFF \
        -D CMAKE_BUILD_TYPE=Release \
        -D CMAKE_INSTALL_PREFIX=/tmp/provider-install \
    && CMAKE_BUILD_PARALLEL_LEVEL=2 cmake --build /tmp/desbordante/build \
    && cmake --install /tmp/desbordante/build \
    && test -f /tmp/provider-install/desbordante*.so

FROM ${PYTHON_IMAGE} AS backend

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libboost-container1.83.0 \
        libboost-graph1.83.0 \
        libboost-thread1.83.0 \
        libicu76 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-dependencies /opt/venv /opt/venv
COPY --from=desbordante-provider-build /tmp/provider-install/desbordante*.so /opt/venv/lib/python3.11/site-packages/

COPY --from=python-dependencies /app/src ./src
COPY --from=python-dependencies /app/config ./config
COPY --from=python-dependencies /app/policies ./policies
COPY --from=python-dependencies /app/docs/architecture ./docs/architecture
COPY tools/run_step29_local.py ./tools/run_step29_local.py

RUN useradd --system --uid 10001 --create-home --home-dir /home/ddo ddo \
    && mkdir -p /var/lib/dirty-data-to-olap \
    && chown -R ddo:ddo /var/lib/dirty-data-to-olap
USER ddo

EXPOSE 8765
VOLUME ["/var/lib/dirty-data-to-olap"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=12 \
    CMD python -c "from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8765/api/v1/health', timeout=3); raise SystemExit(0 if response.status == 200 else 1)"
ENTRYPOINT ["python", "tools/run_step29_local.py"]
CMD ["--root", "/var/lib/dirty-data-to-olap", "--graph-root", "/app", "--host", "0.0.0.0", "--port", "8765"]

FROM ${NODE_IMAGE} AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
COPY --from=python-dependencies /app/frontend-openapi.json ./openapi.json

COPY --from=python-dependencies /opt/venv /opt/venv
COPY --from=python-dependencies /app/src /app/src
COPY --from=python-dependencies /app/tools /app/tools
COPY --from=python-dependencies /app/config /app/config
COPY --from=python-dependencies /app/policies /app/policies
COPY --from=python-dependencies /app/docs/architecture /app/docs/architecture
ENV PATH="/opt/venv/bin:${PATH}"
ENV VITE_API_BASE_URL=""
RUN npm run build

FROM ${NGINX_IMAGE} AS frontend

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/frontend/dist /usr/share/nginx/html
RUN addgroup -S ddo \
    && adduser -S -D -H -u 10001 -G ddo ddo \
    && chown -R ddo:ddo /var/cache/nginx /var/log/nginx /etc/nginx/conf.d /usr/share/nginx/html \
    && touch /var/run/nginx.pid \
    && chown ddo:ddo /var/run/nginx.pid
USER ddo
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=8 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
