#!/bin/sh
# Render start command. The free plan's disk is ephemeral, so reseed the demo inbox
# (offline, simulated history) on every start, then serve the app.
set -e
npm run seed
exec npm start
