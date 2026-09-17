# CI steps

The pipelines call these scripts, each as root in an `ubuntu:26.04` container. Every script works
from any directory and writes the packages to `output/` at the repository root.

The QA tests run a second time in `ubuntu:22.04` (`test-jammy`), the oldest supported release.
There apt carries protobuf 3.12 and grpcio 1.30, so `apt.sh` installs protobuf 3.20 and grpcio 1.51
or newer with pip: the committed stubs need the first, and the 1.30 C core spins instead of creating
a channel on a current kernel. GNU Radio is 3.10.1 here, which does not expose the block logger.

| Script | Purpose |
| --- | --- |
| `apt.sh` | build and test dependencies from apt |
| `test.sh` | configure, build, ctest, compile every example flowgraph with grcc |
| `deb.sh` | Debian package into `output/`, install it, QA tests against the installed module |
| `conda.sh` | conda package into `output/noarch/` with rattler-build |
| `conda_test.sh` | install that package into a fresh micromamba environment, QA tests against it |
| `check_version.sh` | compare a tag with the versions in `CMakeLists.txt` and `recipe.yaml` |
| `changelog.sh` | print the `CHANGELOG.md` entry of a tag as the release description |
| `run_qa.py` | run every QA file in `gr-serorx/python/serorx/qa/` with the current interpreter |
| `conda-test-env.yml` | the environment `conda_test.sh` and the Windows job create |

Variables:

| Name | Read by | Meaning |
| --- | --- | --- |
| `APT_MIRROR` | `apt.sh` | preferred Ubuntu mirror, `archive.ubuntu.com` stays as failover |
| `SERORX_TEST_PYTHON` | `test.sh` | QA interpreter, default the one GNU Radio was built with |
| `RATTLER_BUILD_VERSION` | `conda.sh` | rattler-build release to download, default `v0.75.0` |
| `MAMBA_ROOT_PREFIX` | `conda_test.sh` | where micromamba and its environments land |

## Running a job locally

```bash
podman run --rm -v "$PWD":/src -w /src docker.io/library/ubuntu:26.04 \
    bash -c "bash ci/apt.sh && bash ci/test.sh && bash ci/deb.sh"
```

`docker run` takes the same arguments. `docker.io/library/ubuntu:22.04` in place of the image runs
the `test-jammy` job. Add `-e APT_MIRROR=http://de.archive.ubuntu.com/ubuntu/`
(or another nearby mirror) to speed up the download. The conda jobs need `curl`, `ca-certificates`
and `bzip2` in the container:

```bash
podman run --rm -v "$PWD":/src -w /src docker.io/library/ubuntu:26.04 bash -c \
    "apt-get update && apt-get install -y curl ca-certificates bzip2 && bash ci/conda.sh && bash ci/conda_test.sh"
```
