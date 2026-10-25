FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
WORKDIR /opt/zdx
COPY . .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels open-pyxel \
    && rm -rf /wheels
RUN useradd --system --home /var/lib/zdx zdx && mkdir -p /var/lib/zdx && chown zdx:zdx /var/lib/zdx
USER zdx
EXPOSE 8765
ENTRYPOINT ["zdx"]
CMD ["--help"]
