# Changelog

## 0.7.0

- `GRX Source` tags the receiver's own decoding results onto the samples
  they were decoded from. A new decoding tab toggles any of the twelve
  Receiverd streams
- Each decoded item becomes one `grx_<stream>` tag, a dict of its fields
  and its GPS timestamp, on the sample its timestamp names. Every stream
  belongs to one band, so switch on the streams of the band the selected
  channel receives.
- `fake-grx/` serves Receiverd as its fourth service, with block size,
  item rates and publication times taken from a receiver.

## 0.6.0

- Ported readasb (GPL-3.0, see Credits in the README) to python as a
  better decoding chain for the ADS-B blocks.
- Mode S decoding is two blocks: `modes_demod` takes
  the complex baseband stream and emits validated frames, `adsb_fields`
  decodes them to text lines and field dicts. `adsb_decoder` combines
  both. The complex to magintude block is no longer needed.
- Accepted downlink formats are 0, 4, 5, 11, 16, 17, 18, 20 and 21,
  previously only 17 and 18.
- Each preamble candidate is sliced at five sample offsets and the
  reading with the highest score wins. The CRC syndrome repairs one
  flipped bit by default or through a setting none or two.
- Short replies overlay the aircraft address on their parity field. They
  are validated against the addresses of extended squitters heard in the
  last minute, so appear only once its aircraft has been seen.
- The preamble threshold measures against the gaps inside each candidate,
  so a strong frame does not mask a weak one elsewhere in the block.
- `modes_preamble`, `modes_slicer` and `modes_frame_check` are gone.
  Flowgraphs built on them connect to `modes_demod` instead, taking the
  complex stream rather than a magnitude stream.

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

---

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
