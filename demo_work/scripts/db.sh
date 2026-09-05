#!/usr/bin/env bash
# Stub for the script nodes in demo_work/pipelines/provision.py.
# Conductor resolves a script `command` against the emitted workflow file's
# directory, which is demo_work/build/ — hence the ../scripts/ path in the
# pipeline, not scripts/.
set -euo pipefail
action="${1:?usage: db.sh <drop|migrate|seed> <environment>}"
environment="${2:?missing environment}"
case "$action" in
  drop)    printf '{"dropped": "%s"}\n' "$environment" ;;
  migrate) printf '{"revision": "0001_initial"}\n' ;;
  seed)    printf '{"status": "seeded %s"}\n' "$environment" ;;
  *) echo "unknown action: $action" >&2; exit 64 ;;
esac
