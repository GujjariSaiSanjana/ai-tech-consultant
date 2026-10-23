#!/bin/sh
# Render start command: create or upgrade the schema, then serve the API and UI.
set -e
python -m brain.migrate
exec python -m brain.api
