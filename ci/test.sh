#!/usr/bin/env bash
# Configures and builds the module, runs the QA tests and compiles every example flowgraph with grcc.
# SERORX_TEST_PYTHON selects the QA interpreter (default: the interpreter GNU Radio was built with).
# The build directory is build-ci/, so a container run leaves the build/ of the host alone.
set -euo pipefail
cd "$(dirname "$0")/../gr-serorx"
cmake -S . -B build-ci -DCMAKE_INSTALL_PREFIX=/usr/local \
    ${SERORX_TEST_PYTHON:+-DSERORX_TEST_PYTHON=$SERORX_TEST_PYTHON}
cmake --build build-ci
ctest --test-dir build-ci --output-on-failure
out=$(mktemp -d)
for grc in examples/*.grc; do
    GR_CONF_GRC_GLOBAL_BLOCKS_PATH=/usr/share/gnuradio/grc/blocks GRC_BLOCKS_PATH=$PWD/grc \
        PYTHONPATH=$PWD/build-ci/test_modules grcc "$grc" -o "$out"
done
ls "$out"
