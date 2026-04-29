#!/bin/sh
# Approve advertised routes for a headscale node by ID.
# Requires APPROVE_NODE_ID env var.
set -eu
CONFIG=/var/lib/headscale/config.yaml
NODE_ID="${APPROVE_NODE_ID?APPROVE_NODE_ID is required}"
ROUTES="${APPROVE_ROUTES:-0.0.0.0/0,::/0}"

headscale serve --config "$CONFIG" &
SERVER_PID=$!
for _ in $(seq 1 30); do
  headscale nodes list --config "$CONFIG" >/dev/null 2>&1 && break
  sleep 2
done
headscale nodes approve-routes \
  --config "$CONFIG" \
  --identifier "$NODE_ID" \
  --routes "$ROUTES" \
  --force \
  --output json
STATUS=$?
kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null
exit "$STATUS"
