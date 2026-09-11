#!/usr/bin/env bash
# Configures and builds the module, runs the QA tests and compiles every example flowgraph with grcc.
# SERORX_TEST_PYTHON selects the QA interpreter (default: the interpreter GNU Radio was built with).
set -euo pipefail
cd "$(dirname "$0")/../gr-serorx"
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr/local \
    ${SERORX_TEST_PYTHON:+-DSERORX_TEST_PYTHON=$SERORX_TEST_PYTHON}
cmake --build build
ctest --test-dir build --output-on-failure
out=$(mktemp -d)
for grc in examples/*.grc; do
    GR_CONF_GRC_GLOBAL_BLOCKS_PATH=/usr/share/gnuradio/grc/blocks GRC_BLOCKS_PATH=$PWD/grc \
        PYTHONPATH=$PWD/build/test_modules grcc "$grc" -o "$out"
done
ls "$out"
