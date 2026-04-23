#!/bin/sh
set -eu
CONFIG=/var/lib/headscale/config.yaml
headscale serve --config "$CONFIG" &
SERVER_PID=$!
for _ in $(seq 1 30); do
  headscale apikeys list --config "$CONFIG" >/dev/null 2>&1 && break
  sleep 2
done
headscale apikeys create --config "$CONFIG" --expiration 0 --output json
STATUS=$?
kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null
exit "$STATUS"
