"""Copies the module into the conda build prefix. Runs inside rattler-build."""
import os
import pathlib
import shutil

src = pathlib.Path(os.environ["SRC_DIR"])
prefix = pathlib.Path(os.environ["PREFIX"])
site = pathlib.Path(os.environ["SP_DIR"])

package = site / "gnuradio" / "serorx"
shutil.copytree(src / "python" / "serorx", package, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("qa", "__pycache__", "*.proto", "generate.sh",
                                              "CMakeLists.txt", ".gitignore"))
# GRC looks in share/ on Linux and macOS and in Library/share/ on Windows. The unused copy is harmless.
for rel in ("share/gnuradio/grc/blocks", "Library/share/gnuradio/grc/blocks"):
    target = prefix / rel
    target.mkdir(parents=True, exist_ok=True)
    for block in (src / "grc").glob("*.block.yml"):
        shutil.copy(block, target)
for rel in ("share/gnuradio/examples/serorx", "Library/share/gnuradio/examples/serorx"):
    target = prefix / rel
    target.mkdir(parents=True, exist_ok=True)
    for example in (src / "examples").glob("*.grc"):
        shutil.copy(example, target)
