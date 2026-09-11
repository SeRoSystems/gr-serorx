#!/usr/bin/env bash
# Installs the build and test dependencies from apt. Runs as root inside the CI container.
# APT_MIRROR, when set, becomes the preferred mirror with archive.ubuntu.com as failover, the scheme
# GitHub's runner images use (http://azure.archive.ubuntu.com/ubuntu/ there, archive.ubuntu.com is slow from Azure).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ -n "${APT_MIRROR:-}" ]; then
    printf '%s\tpriority:1\nhttp://archive.ubuntu.com/ubuntu/\tpriority:2\n' "$APT_MIRROR" > /etc/apt/apt-mirrors.txt
    sed -i 's|http://archive.ubuntu.com/ubuntu/|mirror+file:/etc/apt/apt-mirrors.txt|' /etc/apt/sources.list.d/ubuntu.sources
    grep -n "^URIs" /etc/apt/sources.list.d/ubuntu.sources
fi
apt-get update
apt-get install -y --no-install-recommends \
    gnuradio-dev cmake make g++ \
    python3-grpcio python3-protobuf python3-numpy python3-pyqt5 \
    ca-certificates curl
