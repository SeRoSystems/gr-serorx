# Contributing

Report problems or ask questions on
[GitHub Issues](https://github.com/SeRoSystems/gr-serorx/issues).

## Tests

```bash
cd gr-serorx && mkdir -p build && cd build
cmake -DCMAKE_INSTALL_PREFIX=/usr/local -DSERORX_TEST_PYTHON=/path/to/venv/bin/python ..
make && ctest --output-on-failure
```

The test interpreter needs `grpcio`. QA tests import `fake_grx` from
`fake-grx/`, which sits next to `gr-serorx/` in this repository.

## Protobuf stubs

`python/serorx/proto/*_pb2*.py` are generated and committed, not written
by hand. Regenerate them with `python/serorx/proto/generate.sh`, which
needs `grpcio-tools==1.51.1` in its own environment (see the script). Do
not edit the generated files directly.
