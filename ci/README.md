# GitHub Actions CI

Every push runs the QA tests (`.github/workflows/ci.yml`, steps in `ci/`).
A tag `vX.Y.Z` also builds the Debian and conda packages, runs the QA tests
against the installed conda package on Windows and attaches both packages to
a GitHub release. A tag `vX.Y.Z-rc1` makes a pre-release. The tag has to match
the version in `gr-serorx/CMakeLists.txt` and `gr-serorx/recipe/recipe.yaml`.

`ci/apt.sh` reads `APT_MIRROR`. The workflow sets it to `http://azure.archive.ubuntu.com/ubuntu/`,
the mirror GitHub's runner hosts use, with `archive.ubuntu.com` as failover.

## Running the container jobs locally

```bash
podman run --rm -v "$PWD":/src -w /src docker.io/library/ubuntu:26.04 \
    bash -c "bash ci/apt.sh && bash ci/test.sh && bash ci/deb.sh"
```

`docker run` takes the same arguments. Add `-e APT_MIRROR=http://de.archive.ubuntu.com/ubuntu/`
(or another nearby mirror) to speed up the download.
