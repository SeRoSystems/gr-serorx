#!/usr/bin/env bash
# Regenerates the gRPC stubs with grpcio-tools 1.51.1 (protoc 3.21.6).

set -euo pipefail
cd "$(dirname "$0")"
PY="${PROTOC_PYTHON:-../../../../workspace/protoc-venv/bin/python}"
"$PY" -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. \
    Common.proto TunableChanneld.proto Samplestreamingd.proto Monitord.proto Receiverd.proto
sed -i -E 's/^import (Common|TunableChanneld|Samplestreamingd|Monitord|Receiverd)_pb2 as /from . import \1_pb2 as /' ./*_pb2.py ./*_pb2_grpc.py
