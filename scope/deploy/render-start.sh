#!/bin/sh
# Render start command. The free plan's disk is ephemeral, so rebuild the Trailhead demo
# project offline on every start (meetings 1-2, approve, follow-up meeting 3). Projects
# created in the UI afterwards use the real model. A failed seed must not block the web app.
PY=/app/.venv/bin/python
T=data/transcripts/trailhead
seed() { SCOPE_LLM=fake SCOPE_BRAIN=fake "$PY" -m scope.cli "$@"; }

seed run --client "Trailhead Media" \
  --transcript "$T/meeting-01-kickoff.txt" --transcript "$T/meeting-02-deep-dive.txt" \
  && seed approve --project 1 --actor "Demo seed" \
  && seed run --project 1 --transcript "$T/meeting-03-follow-up.txt" \
  || echo "demo seed failed; starting the web app anyway" >&2

exec "$PY" -m scope.web
