#!/usr/bin/env bash
# Prints the CHANGELOG.md entry of a tag, for the release description.
# Usage: ci/changelog.sh v0.7.0 or v0.7.0-rc1 (the part after the hyphen is ignored).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tag=${1#v}
version=${tag%%-*}
entry=$(awk -v want="## $version" '
    $0 == want { found = 1; next }
    found && /^## / { exit }
    found { lines[n++] = $0 }
    END {
        while (n > 0 && lines[n - 1] == "") n--
        while (start < n && lines[start] == "") start++
        for (i = start; i < n; i++) print lines[i]
    }
' "$root/CHANGELOG.md")
if [ -z "$entry" ]; then
    echo "CHANGELOG.md has no entry for $version" >&2
    exit 1
fi
printf '%s\n' "$entry"
