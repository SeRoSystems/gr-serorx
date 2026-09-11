"""GNU Radio blocks for the SeRo Systems GRX receivers."""
import re
import sys


def _protobuf_too_old(version):
    """True for a protobuf runtime below 3.20, the oldest that loads the committed stubs."""
    match = re.match(r"(\d+)\.(\d+)", version)
    return bool(match) and (int(match.group(1)), int(match.group(2))) < (3, 20)


try:
    import grpc  # noqa: F401
    from google.protobuf import __version__ as _protobuf_version
except ImportError as err:
    raise ImportError(
        f"gr-serorx needs the grpcio and protobuf packages for {sys.executable}: "
        "sudo apt install python3-grpcio python3-protobuf (Debian), conda install grpcio protobuf (radioconda)"
    ) from err
if _protobuf_too_old(_protobuf_version):
    raise ImportError(f"protobuf {_protobuf_version} is too old for gr-serorx: 3.20 or newer is needed")

from .grx_source import grx_source  # noqa: E402
from .qt_msg_log import qt_msg_log  # noqa: E402
