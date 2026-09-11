#!/usr/bin/env bash
# Installs the build and test dependencies from apt. Runs as root inside the CI container.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    gnuradio-dev cmake make \
    python3-grpcio python3-protobuf python3-numpy python3-pyqt5 \
    ca-certificates curl
