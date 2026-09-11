# Fake GRX receiver

`fake_grx.py` serves the three gRPC services of a GRX receiver on localhost with synthetic IQ: TunableChanneld (control), Samplestreamingd (stream) and Monitord (model and serial). It exists for the QA tests of `gr-serorx` and for running the example flowgraphs without hardware. It is not an authoritative reference for the receiver API.

## Tests

Every QA test in `gr-serorx/python/serorx/qa/` starts its own `FakeGrx` on free ports and stops it at the end. The tests `import fake_grx`, so the CMake option `SERORX_FAKE_DIR` (default `../fake-grx` relative to `gr-serorx/`) puts this directory on their `PYTHONPATH`. CI needs this directory next to the module or that option pointed at a copy.

- `FakeGrx(control_port=0, stream_port=0, monitor_port=0, realtime=False)`: port 0 picks a free port, `realtime=False` streams as fast as the client reads.
- `realtime=True` paces blocks at the configured sample rate and counts blocks a slow client missed as `lost_blocks`, like the device.
- `inject_lost_blocks(n)`, `pause_stream()`, `settings` and `state` steer and inspect it from a test.

## Behaviour

- Channels: tunable (index 0), 1030 (index 0), 1090 (index 0). The 1090 channel carries one reference ADS-B frame every 40 blocks at a random amplitude, some with one marginal bit.
- Limits follow the GRX 3X: LO 325 to 3800 MHz on both inputs, sample rate 2.083 to 61.44 MSps, both refused with `ABORTED` and a sysfs message. Bandwidth is clamped to 200 kHz to 20 MHz. The center frequency is rounded down to 1 kHz.
- A sample rate change ends open streams, as the device interrupts its data flow.
- Stream properties answer at once. The device recomputes them on a 10 s clock, which the fake does not model.

## Running it for the examples

The module must be importable by the interpreter (installed, or `PYTHONPATH=gr-serorx/build/test_modules`):

```bash
python3 fake-grx/fake_grx.py             # ports 5309, 5308 and 5305, Ctrl+C stops it
python3 fake-grx/fake_grx.py --burst     # blocks as fast as the client reads
```
