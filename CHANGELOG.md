# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.5.0

- `GRX Source` (`serorx.grx_source`): streams IQ from one channel (1030,
  1090, 978 or the tunable daughterboard) of a SeRo GRX receiver over
  gRPC, with configuration and runtime control of center frequency,
  sample rate, hardware gain, RX input and analog bandwidth on the
  tunable channel.
- ADS-B decoding chain in `[SeRo RX]/ADS-B`: four stages from a magnitude
  stream to decoded fields (`modes_preamble`, `modes_slicer`,
  `modes_frame_check`, `adsb_fields`), plus the combined `adsb_decoder`
  block.
- `QT GUI Message Log` (`serorx.qt_msg_log`): a Qt text box message sink.
- `fake-grx/`: a fake receiver serving synthetic IQ over the same gRPC
  services, for running the examples and the QA tests without hardware.
- Debian and conda packages, both built from this repository.
