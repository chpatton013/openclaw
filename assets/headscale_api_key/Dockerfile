# headscale is statically compiled (CGO_ENABLED=0), so it runs on musl-based Alpine.
# Alpine provides /bin/sh which lets us run the serve+apikeys create script.
FROM ghcr.io/juanfont/headscale:0.26.1 AS headscale
FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=headscale /ko-app/headscale /usr/local/bin/headscale
COPY create-api-key.sh /usr/local/bin/create-api-key
RUN chmod +x /usr/local/bin/create-api-key
ENTRYPOINT ["/usr/local/bin/create-api-key"]
