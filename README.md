# gr-serorx

[![Release](https://img.shields.io/github/v/release/SeRoSystems/gr-serorx)](https://github.com/SeRoSystems/gr-serorx/releases) [![CI](https://github.com/SeRoSystems/gr-serorx/actions/workflows/ci.yml/badge.svg)](https://github.com/SeRoSystems/gr-serorx/actions/workflows/ci.yml)

A GNU Radio out-of-tree module providing a source block that streams
IQ samples from a SeRo GRX receiver. A standard ADS-B decoding
flow is also provided as an example.

![Image of tunable example](flowgraph-tunable-example.png)

## Installation

You can find a conda package for Windows (and other platforms) and
a .deb package for APT-based GNU Radio distributions under Releases.

### Conda (Windows, MacOS)

The package needs a conda environment with GNU Radio 3.10 or newer, such as
[radioconda](https://github.com/radioconda/radioconda-installer).

1. Download `gr-serorx-<version>-<build>.conda` from Releases.
2. Open the environment's prompt (Windows: the radioconda Prompt in the Start
   menu) and install the dependencies and the package:

   ```bash
   conda install -c conda-forge grpcio protobuf
   conda install ./gr-serorx-0.5.0-pyh4616a5c_0.conda
   ```

3. Start GNU Radio Companion. The blocks appear in the category `[SeRo RX]`.

### Debian/Ubuntu (APT-based GNU Radio distributions)

The package depends on the distribution's `gnuradio` (3.10 or newer),
`python3-grpcio`, `python3-protobuf` (3.20 or newer) and `python3-numpy`.
Debian 12 and Ubuntu 24.04 or newer ship these versions.

1. Download `gr-serorx_<version>_all.deb` from Releases.
2. Install it, which pulls the dependencies:

   ```bash
   sudo apt install ./gr-serorx_0.5.0_all.deb
   ```

3. Start GNU Radio Companion. The blocks appear in the category `[SeRo RX]`.

## Usage

The block `GRX Source` connects to a receiver and streams the selected
channel. A basic ADS-B decoding chain is provided as an example. For
more information, refer to the in package README (`gr-serorx/README.md`).

### Examples

You can find examples for the tunable channel and the ADS-B decoding
flow in `gr-serorx/examples`.
