# Fake GRX receiver

`fake_grx.py` serves four gRPC services of a GRX receiver on localhost with synthetic IQ: TunableChanneld (control), Samplestreamingd (stream), Monitord (model and serial) and Receiverd (decoder output). It exists for the QA tests of `gr-serorx` and for running the example flowgraphs without hardware. It is not an authoritative reference for the receiver API.

## Tests

Every QA test in `gr-serorx/python/serorx/qa/` starts its own `FakeGrx` on free ports and stops it at the end. The tests `import fake_grx`, so the CMake option `SERORX_FAKE_DIR` (default `../fake-grx` relative to `gr-serorx/`) puts this directory on their `PYTHONPATH`. CI needs this directory next to the module or that option pointed at a copy.

- `FakeGrx(control_port=0, stream_port=0, monitor_port=0, decode_port=0, realtime=False)`: port 0 picks a free port, `realtime=False` streams as fast as the client reads.
- `realtime=True` paces blocks at the configured sample rate and counts blocks a slow client missed as `lost_blocks`, like the device.
- `inject_lost_blocks(n)`, `pause_stream()`, `set_timing_base(base)`, `settings` and `state` steer and inspect it from a test.

## Behaviour

- Channels: tunable (index 0), 1030 (index 0), 1090 (index 0). The 1090 channel carries one reference ADS-B frame every 40 blocks at a random amplitude, some with one marginal bit.
- Limits follow the GRX 3X: LO 325 to 3800 MHz on both inputs, sample rate 2.083 to 61.44 MSps, both refused with `ABORTED` and a sysfs message. Bandwidth is clamped to 200 kHz to 20 MHz. The center frequency is rounded down to 1 kHz.
- Receiverd: the sample stream publishes one item of every decoder stream every 40 blocks, the i-th at sample `(i + 1) * 512` of the block. The Mode S downlink item carries the frame the 1090 channel put into the samples, timestamped at the sample it starts on. Every item carries GPS time of week until `set_timing_base` says otherwise. An empty format list is refused with `INVALID_ARGUMENT`.
- A sample rate change ends open streams, as the device interrupts its data flow.
- Stream properties answer at once. The device recomputes them on a 10 s clock, which the fake does not model.

## Running it for the examples

The module must be importable by the interpreter (installed, or `PYTHONPATH=gr-serorx/build/test_modules`):

```bash
python3 fake-grx/fake_grx.py             # ports 5309, 5308, 5305 and 5303, Ctrl+C to stop
python3 fake-grx/fake_grx.py --burst     # blocks as fast as the client reads
```
