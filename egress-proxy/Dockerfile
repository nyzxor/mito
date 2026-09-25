# Physics-layer CONNECT proxy (ADR-0005). Built only where cargo is available.
FROM rust:1-bookworm AS build
WORKDIR /src
COPY Cargo.toml ./
COPY src src
RUN cargo build --release

FROM debian:bookworm-slim
RUN useradd --uid 65532 --user-group --create-home mito
COPY --from=build /src/target/release/mito-egress-proxy /usr/local/bin/mito-egress-proxy
USER 65532:65532
ENTRYPOINT ["mito-egress-proxy"]
