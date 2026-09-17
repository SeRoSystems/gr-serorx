#!/usr/bin/env bash
# Installs the build and test dependencies from apt. Runs as root inside the CI container.
# APT_MIRROR, when set, becomes the preferred mirror with archive.ubuntu.com as failover, the scheme
# GitHub's runner images use (http://azure.archive.ubuntu.com/ubuntu/ there, archive.ubuntu.com is slow from Azure).
# 24.04 and newer keep the sources in ubuntu.sources, 22.04 in sources.list.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ -n "${APT_MIRROR:-}" ]; then
    printf '%s\tpriority:1\nhttp://archive.ubuntu.com/ubuntu/\tpriority:2\n' "$APT_MIRROR" > /etc/apt/apt-mirrors.txt
    sources=/etc/apt/sources.list.d/ubuntu.sources
    [ -f "$sources" ] || sources=/etc/apt/sources.list
    sed -i 's|http://archive.ubuntu.com/ubuntu/|mirror+file:/etc/apt/apt-mirrors.txt|' "$sources"
    grep -n "mirror+file" "$sources"
fi
apt-get update
apt-get install -y --no-install-recommends \
    gnuradio-dev cmake make g++ \
    python3-grpcio python3-protobuf python3-numpy python3-pyqt5 \
    ca-certificates curl

# 22.04 ships protobuf 3.12, below the 3.20 the committed stubs need, and grpcio 1.30, whose C core
# spins instead of creating a channel on a current kernel. pip installs a usable version of each.
pip_if_old() {  # import name, floor, pip name
    version=$(python3 -c "import $1; print($1.__version__)")
    echo "$3 $version"
    if [ "$(printf '%s\n%s\n' "$2" "$version" | sort -V | head -1)" != "$2" ]; then
        apt-get install -y --no-install-recommends python3-pip
        pip3 install "$3>=$2"
    fi
}

pip_if_old google.protobuf 3.20 protobuf
pip_if_old grpc 1.51 grpcio
