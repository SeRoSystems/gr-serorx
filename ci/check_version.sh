#!/usr/bin/env bash
# Exits 1 when the tag disagrees with the versions in CMakeLists.txt and recipe.yaml.
# Usage: ci/check_version.sh v0.5.0 or v0.5.0-rc1 (the part after the hyphen is ignored).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tag=${1#v}
tag=${tag%%-*}
cmake_file=$root/gr-serorx/CMakeLists.txt
field() { grep -oP "^set\($1\s+\K\d+" "$cmake_file"; }
cmake_version="$(field VERSION_MAJOR).$(field VERSION_API).$(field VERSION_ABI)"
recipe_version=$(grep -oP '^\s*version:\s*"\K[0-9.]+' "$root/gr-serorx/recipe/recipe.yaml")
if [ "$tag" != "$cmake_version" ] || [ "$tag" != "$recipe_version" ]; then
    echo "tag $1 is version $tag, CMakeLists.txt has $cmake_version, recipe.yaml has $recipe_version" >&2
    exit 1
fi
echo "version $tag"
