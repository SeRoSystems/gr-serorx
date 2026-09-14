#!/usr/bin/env bash
# Builds the Debian package into output/, installs it and runs the QA tests against the installed module.
# Runs as root inside the CI container after ci/apt.sh.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/gr-serorx"
cmake -S . -B build-ci-deb -DCMAKE_INSTALL_PREFIX=/usr -DGR_PYTHON_DIR=lib/python3/dist-packages
cmake --build build-ci-deb
(cd build-ci-deb && cpack)
mkdir -p "$root/output"
cp build-ci-deb/gr-serorx_*_all.deb "$root/output/"
apt-get install -y "$root"/output/gr-serorx_*_all.deb
python3 "$root/ci/run_qa.py"
