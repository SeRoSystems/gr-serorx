#!/usr/bin/env bash
# Builds the conda package with rattler-build into output/noarch/.
# Downloads the pinned rattler-build binary when none is on PATH.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
version=${RATTLER_BUILD_VERSION:-v0.75.0}
if ! command -v rattler-build >/dev/null; then
    mkdir -p "$HOME/.local/bin"
    curl -fsSL -o "$HOME/.local/bin/rattler-build" \
        "https://github.com/prefix-dev/rattler-build/releases/download/$version/rattler-build-x86_64-unknown-linux-musl"
    chmod +x "$HOME/.local/bin/rattler-build"
    export PATH="$HOME/.local/bin:$PATH"
fi
rattler-build build --recipe "$root/gr-serorx/recipe/recipe.yaml" -c conda-forge --output-dir "$root/output"
