#!/usr/bin/env sh
# One image, two roles.
#
#   docker run agentmesh api        -> the FastAPI service
#   docker run agentmesh worker     -> a queue consumer
#   docker run agentmesh <cmd...>   -> anything else, executed as-is
set -eu

role="${1:-api}"

case "$role" in
    api)
        shift || true
        exec agentmesh serve "$@"
        ;;
    worker)
        shift || true
        exec agentmesh worker "$@"
        ;;
    *)
        exec "$@"
        ;;
esac

