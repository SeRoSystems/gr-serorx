# CI steps

The pipelines call these scripts, each as root in an `ubuntu:26.04` container. Every script works
from any directory and writes the packages to `output/` at the repository root.

| Script | Purpose |
| --- | --- |
| `apt.sh` | build and test dependencies from apt |
| `test.sh` | configure, build, ctest, compile every example flowgraph with grcc |
| `deb.sh` | Debian package into `output/`, install it, QA tests against the installed module |
| `conda.sh` | conda package into `output/noarch/` with rattler-build |
| `conda_test.sh` | install that package into a fresh micromamba environment, QA tests against it |
| `check_version.sh` | compare a tag with the versions in `CMakeLists.txt` and `recipe.yaml` |
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

`docker run` takes the same arguments. Add `-e APT_MIRROR=http://de.archive.ubuntu.com/ubuntu/`
(or another nearby mirror) to speed up the download. The conda jobs need `curl`, `ca-certificates`
and `bzip2` in the container:

```bash
podman run --rm -v "$PWD":/src -w /src docker.io/library/ubuntu:26.04 bash -c \
    "apt-get update && apt-get install -y curl ca-certificates bzip2 && bash ci/conda.sh && bash ci/conda_test.sh"
```
