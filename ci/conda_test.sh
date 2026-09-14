#!/usr/bin/env bash
# Installs the conda package built by ci/conda.sh into a fresh environment and runs the QA tests
# against it. Downloads micromamba when none is on PATH. MAMBA_ROOT_PREFIX holds the environments.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
export MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX:-${TMPDIR:-/tmp}/serorx-micromamba}
if ! command -v micromamba > /dev/null; then
    mkdir -p "$MAMBA_ROOT_PREFIX"
    curl -fsSL https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "$MAMBA_ROOT_PREFIX" bin/micromamba
    export PATH="$MAMBA_ROOT_PREFIX/bin:$PATH"
fi
micromamba create -y -f "$root/ci/conda-test-env.yml"
micromamba install -y -n ci "$root"/output/noarch/gr-serorx-*.conda
micromamba run -n ci python "$root/ci/run_qa.py"
