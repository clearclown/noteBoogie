# Book Navigator gateway (reinhardt-web) — build context is the REPO ROOT
# (protos/ lives beside gateway/):  docker build -f gateway/Dockerfile .
FROM rust:1.96-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    protobuf-compiler pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY protos/ protos/
COPY gateway/ gateway/
WORKDIR /build/gateway
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /build/gateway/target/release/gateway /usr/local/bin/gateway
# DATA_FOLDER must match the API container's layout so relative audio paths
# and figure images resolve (shared volume mounted at /app/data).
ENV DATA_FOLDER=/app/data \
    GATEWAY_BIND_ADDR=0.0.0.0:8088
EXPOSE 8088
CMD ["gateway"]
