# gr-serorx

GNU Radio 3.10 module for the SeRo Systems GRX receivers. Main block: **GRX Source** (`serorx.grx_source`), streams IQ samples from a receiver channel over gRPC (1090, 1030, 978 or tunable).

## Block parameters

General tab:

| Parameter | Meaning | Default |
| --------- | ------- | ------- |
| Channel | `tunable`, `1030`, `1090` or `978`. Fixed channels ignore the tuning parameters. | `tunable` |
| Receiver address | IP or hostname of the receiver | |
| Center frequency (Hz) | tunable channel only. Wide 325 to 3800 MHz, Narrow 700 to 1100 MHz | 1090e6 |
| Sample rate (Sps) | tunable channel only, 12MSps recommended, 2.083 to 61.44 MSps accepted | 12e6 |
| Hardware gain (dB) | tunable channel only, 0 to 70 dB | 0 |
| RX input | `wide` (325-3800 MHz) or `narrow` (700-1100 MHz, LNA) | `wide` |
| Analog bandwidth (Hz) | 200 kHz to 20 MHz, 0 leaves the device setting unchanged | 0 |

Advanced tab:

| Parameter | Meaning | Default |
| --------- | ------- | ------- |
| Channel index | `per_band_index` of the channel | 0 |
| Output | `fc32`: complex float scaled to ±1. `sc16`: raw int16 pairs. | `fc32` |
| Buffer (s) | queue between the network reader and the flowgraph | 0.5 |
| Control port | TunableChanneld | 5309 |
| Stream port | Samplestreamingd | 5308 |
| Monitor port | Monitord, model and serial | 5305 |
| RPC timeout (s) | unary control calls | 5 |
| Check value ranges | GRC refuses values outside the ranges above. `No` passes any value to the receiver, which rejects what it cannot do. | Yes |

GRC marks the block red for a missing value or a value outside a range and names the reason in the properties dialog

```Console
Assertion "# Hardware gain not between 0 dB and 70 dB
not check_ranges or band != "'tunable'" or gain is None or gain <= 70" failed.
```

A rejected value or an unreachable receiver stops the flowgraph from starting and shows the device message.

## Console output

The block writes to the GRC console (bottom left).

```Console
receiver grx3x, serial fc:0f:e7:00:00:00, hardware hw2-ad9363-1030-hdr-ad9363-1090-hdr-ad9363-tunable, image 2026.07.28
channels: 1030 (index 0), 1090 (index 0), tunable (index 0)
channel tunable (index 0): receiver reports 688.00 MHz, 12.00 MSps, hardware gain 30 dB, analog bandwidth 6.00 MHz, RX input Wide, calibration -115.47 dB
center frequency requested 1090.00 MHz, receiver reports 1090.00 MHz (-2 Hz)
center frequency 950.00 MHz
calibration -145.47 dB
lost 46 blocks in last 5 s: 46 reported by receiver, 0 dropped by full buffer
stream stopped: 458 blocks delivered, 916 lost (66.7 %): 916 reported by receiver, 0 dropped by full buffer
```

- Receiver: the Monitord fields `receiver-os.device-type`, `serial_number`, `receiver-os.hardware-revision` and `receiver-os.image-version`, each verbatim. An unreachable Monitord gives a warning and the flowgraph continues.
- Channels: every channel Samplestreamingd serves, found by probing each band and index. A missing channel fails the flowgraph with this list.
- Channel: every value is read back from the receiver after configuration. A requested value the receiver did not adopt gets a warning with both numbers. Every setter reads back the same way and logs the value or the mismatch.
- Calibration line: the GRX 3X recomputes its stream properties every 10 s, so a gain or input change reaches them 0 to 10 s later. The block polls them every second and logs and tags a changed calibration. The value falls by 1 dB per dB of gain, so the calibration in the channel line can still be the previous one.
- Loss lines come at most once per 5 s. "Reported by receiver" is the device's `lost_blocks` counter (network or receiver side). "Dropped by full buffer" means the flowgraph consumed slower than the stream.
- The summary at stop gives the loss percentage over the run.

## Failures

A failure at start is logged as an error and raised, so it is also the last line of the traceback in the GRC console.

| Situation | Message |
| --------- | ------- |
| No answer from the address | `host '192.0.2.1' could not be reached (no answer within 5 s), check address and receiver connection` |
| Name does not resolve | `host 'grx-lab' does not resolve, check address` |
| Host answers, streaming port closed | `host '192.0.2.1' answers, but port 5308 (Samplestreamingd) is closed, check address and firewall` |
| Host answers, control port closed | `host '192.0.2.1' answers, but port 5309 (TunableChanneld) is closed, check address and firewall. Tunable channel cannot be configured` |
| Channel missing | `channel 978 (index 0) is not available. Available channels: 1030 (index 0), 1090 (index 0), tunable (index 0)` |
| Value rejected by receiver | `center frequency 100.00 MHz rejected by receiver: ABORTED: Cannot write to sysfs file (out_altvoltage0_RX_LO_frequency): Invalid argument. Selected RX input (Wide) supports 325 to 3800 MHz` |
| Value outside the protocol's `uint32` fields | `hardware gain -5 dB rejected: Value out of range: -5. Receiver supports 0 to 70 dB` |
| Value clamped by the receiver | a warning, `analog bandwidth requested 50.00 MHz, receiver reports 20.00 MHz`, and the flowgraph runs |
| RX input label unknown | `RX input 'wide' not found, receiver offers UNKNOWN, Wide, Narrow` |
| Monitord port closed | a warning, `host '192.0.2.1' answers, but port 5305 (Monitord) is closed, check address and firewall. Model and serial unknown`, and the flowgraph runs |
| grpcio or protobuf missing for the interpreter GRC uses | `gr-serorx needs the grpcio and protobuf packages for /usr/bin/python3: sudo apt install python3-grpcio python3-protobuf (Debian), conda install grpcio protobuf (radioconda)` |
| protobuf older than 3.20 | `protobuf 3.19.6 is too old for gr-serorx, 3.20 or newer is needed` |
| Blocks missing from the GRC palette after install | GRC lists new blocks only after a restart. A GNU Radio installed outside `/usr` or `/usr/local` needs `GRC_BLOCKS_PATH=<prefix>/share/gnuradio/grc/blocks` |

While the flowgraph runs:

| Situation | Message |
| --------- | ------- |
| Stream breaks (link down, receiver reboot) | `stream from '192.0.2.1' lost (not reachable), reconnecting in 1 s`, backoff doubling to 10 s, then `stream from '192.0.2.1' resumed` |
| Stream open, nothing arrives | `no samples from '192.0.2.1' for 5.0 s`, repeated every 30 s |
| Setter while the receiver is gone | `hardware gain not set: host '192.0.2.1' could not be reached (no answer within 5 s)`, value unchanged |
| Setter value rejected | `sample rate 1.00 MSps rejected by receiver: ABORTED: Cannot write to sysfs file (in_voltage_sampling_frequency): Invalid argument. Receiver supports 2.083 to 61.44 MSps`, value unchanged |
| Sample rate not adopted | a warning, `sample rate requested 5.00 MSps, receiver reports 12.00 MSps`, the reported rate is used |
| Stream properties lag behind a rate change | a warning, `stream properties report 12.00 MSps, expected 5.00 MSps`, the stream restarts anyway |
| Flowgraph slower than the stream | `flowgraph consumes slower than 12.00 MSps, samples dropped`, once per run, then counted in the loss lines |

## Runtime control

Setters `set_center_freq`, `set_samp_rate`, `set_gain`, `set_rx_port`, `set_bandwidth` work from GRC variables and sliders. A sample rate change restarts the stream: the queued samples of the old rate are discarded and the first sample of the new stream carries `rx_rate`, `rx_freq`, `grx_calibration_db`, `rx_time` and `grx_timestamp`.

## Stream tags

| Key | Value | When |
| --- | ----- | ---- |
| `rx_rate` | double | start, after a sample rate change |
| `rx_freq` | double | start, after a center frequency change (the value the receiver reports) |
| `rx_time` | (uint64 seconds, double fraction), GPS time of week | start, after every gap, on every new stream (reconnect, sample rate change) |
| `grx_timestamp` | uint64, raw `block_timestamp` in ns of GPS time of week | start, after every gap, on every new stream (reconnect, sample rate change) |
| `grx_lost_blocks` | uint64 | after a gap: device counter delta plus locally dropped blocks |
| `grx_calibration_db` | double | start, and whenever the value the receiver reports changes (polled every second). `level_dBm = value + 10*log10(I^2 + Q^2)` |

## ADS-B decoding

For quickstart on 1090 MHz analysis, a basic decoding chain is provided. Four blocks in the GRC category `[SeRo RX]/ADS-B` (Python package `gnuradio.serorx.adsb`) take a magnitude stream (Complex to Mag, an even number of MSps) and decode to ADS-B lines. PDUs (a metadata dict and a data vector) connect them, so each stage is a message block that plugs into the stock PDU blocks. Text leaves the chain as byte PDUs. A message of a wrong type is dropped with a warning.

`examples/adsb_stages_demo.grc` implements the chain on the 1090 channel.

| Block | In | Out |
| ----- | -- | --- |
| **Mode S Preamble Detector** (`modes_preamble`) | magnitude stream | `windows`: one PDU per preamble candidate, the 120 µs frame window as float32, with `offset`, `spu`, `samp_rate` and `time` (from `rx_time` tags) in the metadata |
| **Mode S PPM Slicer** (`modes_slicer`) | `windows` | `bits`: the 14 bytes with a `confidence` per bit and the `window` samples in the metadata. `confidence`: the confidences signed by the bit value, for a QT GUI Time Sink in message mode |
| **Mode S Frame Check** (`modes_frame_check`) | `bits` | `frames`: frames with an accepted DF (default 17,18) and a valid CRC-24, after flipping the least confident bits one at a time when allowed. `hex`: the same as hex text in byte PDUs. `windows`: the samples of each accepted frame, for display |
| **ADS-B Fields** (`adsb_fields`) | `frames` (PDU with the 14 bytes or 28 hex characters, or a hex string) | `lines`: one text line per frame as a byte PDU. `fields`: a dict with df, icao, tc, frame (the 14 bytes), time and the decoded fields |

**ADS-B Decoder** (`adsb_decoder`) is the four in one block with a `frames` output port. The preamble stage and the decoder block follow the sample rate variable at runtime through `set_samp_rate`.

## QT GUI Message Log

`serorx.qt_msg_log` shows every incoming message as one line in a read-only scrolling text box placed by its GUI Hint. Strings and byte PDUs appear as text, other PMTs in printed form. Lines beyond Max lines are dropped from the top.

```Log
02:02:35.427 DF17 ICAO=406900 TC=4 callsign=EXS8GN
02:02:35.465 DF17 ICAO=408033 TC=11 altitude=36000
02:02:52.446 DF17 ICAO=4081A9 TC=11 altitude=34000 latitude=49.81234 longitude=6.78912
```

## Build from source

You can find the latest source code at [GitHub](https://github.com/SeRoSystems/gr-serorx).

Requirements: GNU Radio 3.10 with its cmake files (`gnuradio-dev` on Debian), cmake, and `grpcio`, `protobuf`, `numpy` importable by the interpreter that runs flowgraphs.

```bash
sudo apt install gnuradio-dev cmake make g++ python3-grpcio python3-protobuf python3-numpy python3-pyqt5
```

```bash
mkdir -p build && cd build
cmake -DCMAKE_INSTALL_PREFIX=/usr/local ..
make
sudo make install
```

`sudo make uninstall` removes the installed files.

The QA tests in `python/serorx/qa/` need `grpcio` in the test interpreter and the fake receiver from `fake-grx/` next to this module (see its README). The examples run against the fake with `host = 'localhost'`. GNU Radio's cmake forces the interpreter it was built with, so a different one is given explicitly:

```bash
cmake -DCMAKE_INSTALL_PREFIX=/usr/local -DSERORX_TEST_PYTHON=/path/to/venv/bin/python ..
make && ctest --output-on-failure
```

## Packages

Tagged commits build both packages in CI and attach them to the release.

Debian package for apt GNU Radio:

```bash
mkdir -p build-deb && cd build-deb
cmake -DCMAKE_INSTALL_PREFIX=/usr -DGR_PYTHON_DIR=lib/python3/dist-packages ..
make && cpack
sudo apt install ./gr-serorx_<version>_all.deb
```

Conda package for radioconda (Windows, Linux, macOS), built with [rattler-build](https://github.com/prefix-dev/rattler-build):

```bash
rattler-build build --recipe recipe/recipe.yaml -c conda-forge --output-dir out
conda install ./out/noarch/gr-serorx-<version>-*.conda
```

## Stubs

`python/serorx/proto/*_pb2*.py` are generated from the vendor protos by `python/serorx/proto/generate.sh` with grpcio-tools 1.51.1 (protoc 3.21.6) and committed. That protoc version keeps the stubs compatible with protobuf runtimes from 3.21 (Debian) to the current PyPI and conda-forge releases.
