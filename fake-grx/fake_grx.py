"""Fake GRX receiver with a tunable channel and fixed 1030 and 1090 channels: TunableChanneld, Samplestreamingd and Monitord with synthetic IQ.

Limits follow the GRX 3X: the LO tunes 325 to 3800 MHz on both inputs, the sample rate 2.083 to 61.44 MSps,
values outside fail with ABORTED and a sysfs message, the bandwidth is clamped to 200 kHz to 20 MHz.
Block size and the signal are placeholders.
"""
import argparse
import math
import signal
import threading
import time
from concurrent import futures

import grpc
import numpy as np
from google.protobuf import empty_pb2

from gnuradio.serorx.adsb import crc, modes
from gnuradio.serorx.grx_client import HARDWARE_KEY, IMAGE_KEY, MODEL_KEY
from gnuradio.serorx.proto import (Common_pb2, Monitord_pb2, Monitord_pb2_grpc, Samplestreamingd_pb2, Samplestreamingd_pb2_grpc,
                                   TunableChanneld_pb2, TunableChanneld_pb2_grpc)

BLOCK_SAMPLES = 8192
RX_PORTS = {0: "Wide", 1: "Narrow (LNA)"}
LO_RANGE = (325_000_000, 3_800_000_000)
GAIN_RANGE = (0, 70)
SAMPLE_RATE_RANGE = (2_083_334, 61_440_000)
BANDWIDTH_RANGE = (200_000, 20_000_000)
SYSFS = ("Cannot write to sysfs file (/sys/devices/platform/axi/ff050000.spi/spi_master/spi2/spi2.2/iio:device4/{}): "
         "Invalid argument")
CALIBRATION_DB = -100.0  # at gain 0
STALL_SECONDS = 0.2
NOISE_SIGMA = 50.0
TONE_DIVISOR = 8
SLOW_CONSUMER_SECONDS = 1.0
MAX_AMPLITUDE = 30000.0
# A DF4 altitude reply carrying 25000 ft, parity overlaid with the address of the first frame.
# It follows that frame in the cycle, so the decoder has the address when the reply arrives.
REPLY_BODY = bytes.fromhex("20001030")
ADSB_REPLY = (REPLY_BODY + (crc.checksum(REPLY_BODY + b"\x00" * 3, 56)
                            ^ 0x4840D6).to_bytes(3, "big")).hex().upper()
ADSB_FRAMES = ("8D4840D6202CC371C32CE0576098", "8D40621D58C382D690C8AC2863A7",
               "8D40621D58C386435CC412692AD6", "8D485020994409940838175B284F", ADSB_REPLY)
FRAME_EVERY = 40
FRAME_AMPLITUDE = (400.0, 3000.0)
MARGINAL_BIT_PROBABILITY = 0.3

MODEL = "grx3x"
HARDWARE = "fake"
IMAGE = "0000.00.00"
SERIAL = "00:00:5e:00:53:01"
FREQ_STEP = 1000

TUNABLE = (Common_pb2.BAND_TUNABLE, 0)
FIXED = {
    (Common_pb2.BAND_1030_MHZ, 0): 1_030_000_000,
    (Common_pb2.BAND_1090_MHZ, 0): 1_090_000_000,
}


def _key(radio):
    return (radio.band, radio.per_band_index)


class _State:
    """Settings of the tunable channel. A change of generation ends open streams."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rx_port = 0
        self.center_frequency = 1_090_000_000
        self.sample_rate = 12_000_000
        self.bandwidth = 12_000_000
        self.gain = 0
        self.generation = 0
        self.pending_lost = 0
        self.paused = threading.Event()

    def snapshot(self):
        with self.lock:
            return {"rx_port": self.rx_port, "center_frequency": self.center_frequency,
                    "sample_rate": self.sample_rate, "bandwidth": self.bandwidth, "gain": self.gain}


class _TunableChanneld(TunableChanneld_pb2_grpc.TunableChanneldServicer):
    def __init__(self, state):
        self.state = state

    def _check(self, channel, context):
        if _key(channel) != TUNABLE:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "channel not available or not tunable")

    def GetChannels(self, request, context):
        radio = Common_pb2.RadioIdentification(band=TUNABLE[0], per_band_index=TUNABLE[1])
        return TunableChanneld_pb2.GetChannelsReply(channels=[radio])

    def GetRXPorts(self, request, context):
        self._check(request.channel, context)
        return TunableChanneld_pb2.GetRXPortsReply(rx_port_to_label=RX_PORTS)

    def GetRXPort(self, request, context):
        self._check(request.channel, context)
        return TunableChanneld_pb2.GetRXPortReply(rx_port=self.state.rx_port)

    def SetRXPort(self, request, context):
        self._check(request.channel, context)
        if request.rx_port not in RX_PORTS:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"rx_port {request.rx_port} not in {sorted(RX_PORTS)}")
        with self.state.lock:
            self.state.rx_port = request.rx_port
        return empty_pb2.Empty()

    def GetCenterFrequency(self, request, context):
        self._check(request.channel, context)
        return TunableChanneld_pb2.GetCenterFrequencyReply(center_frequency_hz=self.state.center_frequency)

    def SetCenterFrequency(self, request, context):
        self._check(request.channel, context)
        low, high = LO_RANGE
        if not low <= request.center_frequency_hz <= high:
            context.abort(grpc.StatusCode.ABORTED, SYSFS.format("out_altvoltage0_RX_LO_frequency"))
        with self.state.lock:
            # Placeholder for the tuning step of the device (gotcha 22): the frequency is rounded down.
            self.state.center_frequency = request.center_frequency_hz // FREQ_STEP * FREQ_STEP
        return empty_pb2.Empty()

    def GetAnalogBandwidth(self, request, context):
        self._check(request.channel, context)
        return TunableChanneld_pb2.GetAnalogBandwidthReply(analog_bandwidth_hz=self.state.bandwidth)

    def SetAnalogBandwidth(self, request, context):
        self._check(request.channel, context)
        low, high = BANDWIDTH_RANGE
        with self.state.lock:
            self.state.bandwidth = min(max(request.analog_bandwidth_hz, low), high)
        return empty_pb2.Empty()

    def GetSampleRate(self, request, context):
        self._check(request.channel, context)
        return TunableChanneld_pb2.GetSampleRateReply(sample_rate_sps=self.state.sample_rate)

    def SetSampleRate(self, request, context):
        self._check(request.channel, context)
        low, high = SAMPLE_RATE_RANGE
        if not low <= request.sample_rate_sps <= high:
            context.abort(grpc.StatusCode.ABORTED, SYSFS.format("in_voltage_sampling_frequency"))
        with self.state.lock:
            self.state.sample_rate = request.sample_rate_sps
            self.state.generation += 1
        time.sleep(STALL_SECONDS)
        return empty_pb2.Empty()

    def GetHardwareGain(self, request, context):
        self._check(request.channel, context)
        return TunableChanneld_pb2.GetHardwareGainReply(hardware_gain_db=self.state.gain)

    def SetHardwareGain(self, request, context):
        self._check(request.channel, context)
        low, high = GAIN_RANGE
        if not low <= request.hardware_gain_db <= high:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                          f"hardware_gain_db {request.hardware_gain_db} outside {low}..{high}")
        with self.state.lock:
            self.state.gain = request.hardware_gain_db
        return empty_pb2.Empty()


class _Samplestreamingd(Samplestreamingd_pb2_grpc.SamplestreamingdServicer):
    def __init__(self, state, realtime):
        self.state = state
        self.realtime = realtime
        self.t0 = time.monotonic()

    def _properties(self, radio, context):
        key = _key(radio)
        if key == TUNABLE:
            with self.state.lock:
                return self.state.center_frequency, self.state.sample_rate, self.state.gain
        if key in FIXED:
            return FIXED[key], 12_000_000, 0
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "channel not available")

    def GetStreamProperties(self, request, context):
        center, rate, gain = self._properties(request.radio_identification, context)
        # The 3X reports a calibration that falls with the gain (gotcha 23).
        return Samplestreamingd_pb2.StreamProperties(center_frequency=center, sample_rate=rate,
                                                     calibration_value=CALIBRATION_DB - gain)

    def StartStream(self, request, context):
        tunable = _key(request.radio_identification) == TUNABLE
        frames = _key(request.radio_identification) == (Common_pb2.BAND_1090_MHZ, 0)
        _, rate, gain = self._properties(request.radio_identification, context)
        generation = self.state.generation
        amplitude = min(1000.0 * 10 ** (gain / 20.0), MAX_AMPLITUDE)
        step = 2 * math.pi / TONE_DIVISOR
        block_period = BLOCK_SAMPLES / rate
        period_ns = BLOCK_SAMPLES * 1_000_000_000 // rate
        index = np.arange(BLOCK_SAMPLES)
        rng = np.random.default_rng(0)
        start = time.monotonic()
        start_ns = int((start - self.t0) * 1e9)
        block = 0
        lost = 0
        sent = 0
        while context.is_active():
            while self.state.paused.is_set() and context.is_active():
                time.sleep(0.05)
            if request.requested_blocks and sent >= request.requested_blocks:
                return
            if tunable and self.state.generation != generation:
                return
            with self.state.lock:
                skip = self.state.pending_lost
                self.state.pending_lost = 0
            if self.realtime:
                late = time.monotonic() - (start + block * block_period)
                if late > SLOW_CONSUMER_SECONDS:
                    skip += int(late / block_period)
                elif late < 0:
                    time.sleep(-late)
            block += skip
            lost += skip
            iq = rng.normal(0.0, NOISE_SIGMA, (BLOCK_SAMPLES, 2))
            if frames:
                # The 1090 channel carries one ADS-B frame every FRAME_EVERY blocks, cycling ADSB_FRAMES, with a
                # random amplitude. Some frames carry one marginal bit (pulse 37 %, gap 35 %) for the correction stage.
                if block % FRAME_EVERY == 0:
                    spu = rate // 1_000_000
                    level = float(rng.uniform(*FRAME_AMPLITUDE))
                    frame = modes.synthesize(ADSB_FRAMES[(block // FRAME_EVERY) % len(ADSB_FRAMES)], spu, level)
                    if rng.random() < MARGINAL_BIT_PROBABILITY:
                        bits = len(frame) // spu - modes.PREAMBLE_US
                        s0 = (modes.PREAMBLE_US + int(rng.integers(0, bits))) * spu
                        frame[s0:s0 + spu] = np.where(frame[s0:s0 + spu] > 0, 0.37 * level, 0.35 * level)
                    at = int(rng.integers(0, BLOCK_SAMPLES - len(frame)))
                    iq[at:at + len(frame), 0] += frame
            else:
                phase = ((block * BLOCK_SAMPLES + index) % TONE_DIVISOR) * step
                iq[:, 0] += amplitude * np.cos(phase)
                iq[:, 1] += amplitude * np.sin(phase)
            samples = np.clip(np.rint(iq), -32768, 32767).astype("<i2").tobytes()
            timestamp = start_ns + block * period_ns
            yield Samplestreamingd_pb2.StartStreamReply(block_timestamp=timestamp, samples=samples, lost_blocks=lost)
            block += 1
            sent += 1


class _Monitord(Monitord_pb2_grpc.MonitordServicer):
    def GetSystemInformation(self, request, context):
        return Monitord_pb2.SystemInformation(
            system_time=int(time.time()), serial_number=SERIAL,
            version_information={MODEL_KEY: MODEL, HARDWARE_KEY: HARDWARE, IMAGE_KEY: IMAGE})


class FakeGrx:
    """The three services on 127.0.0.1, one port each. Port 0 picks a free port, readable after start()."""

    def __init__(self, control_port=0, stream_port=0, monitor_port=0, realtime=True):
        self.control_port = control_port
        self.stream_port = stream_port
        self.monitor_port = monitor_port
        self.realtime = realtime
        self.state = _State()
        self._servers = []

    def _serve(self, add_servicer, servicer, port):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        add_servicer(servicer, server)
        self._servers.append(server)
        return server.add_insecure_port(f"127.0.0.1:{port}")

    def start(self):
        self._servers = []
        self.control_port = self._serve(TunableChanneld_pb2_grpc.add_TunableChanneldServicer_to_server,
                                        _TunableChanneld(self.state), self.control_port)
        self.stream_port = self._serve(Samplestreamingd_pb2_grpc.add_SamplestreamingdServicer_to_server,
                                       _Samplestreamingd(self.state, self.realtime), self.stream_port)
        self.monitor_port = self._serve(Monitord_pb2_grpc.add_MonitordServicer_to_server, _Monitord(),
                                        self.monitor_port)
        for server in self._servers:
            server.start()
        return self

    def stop(self):
        for event in [server.stop(grace=0.5) for server in self._servers]:
            event.wait()
        self._servers = []

    def pause_stream(self):
        """Open streams stay open and send nothing until resume_stream()."""
        self.state.paused.set()

    def resume_stream(self):
        self.state.paused.clear()

    def inject_lost_blocks(self, count):
        with self.state.lock:
            self.state.pending_lost += count

    @property
    def settings(self):
        return self.state.snapshot()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main():
    parser = argparse.ArgumentParser(description="Fake GRX receiver: TunableChanneld, Samplestreamingd and Monitord.")
    parser.add_argument("--control-port", type=int, default=5309)
    parser.add_argument("--stream-port", type=int, default=5308)
    parser.add_argument("--monitor-port", type=int, default=5305)
    parser.add_argument("--burst", action="store_true", help="send blocks as fast as the client reads")
    args = parser.parse_args()
    fake = FakeGrx(args.control_port, args.stream_port, args.monitor_port, realtime=not args.burst).start()
    print(f"fake GRX on 127.0.0.1: control {fake.control_port}, stream {fake.stream_port}, "
          f"monitor {fake.monitor_port}. Ctrl+C stops it", flush=True)
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    # A wait with a timeout lets Ctrl+C through on Windows, where an untimed wait is not interruptible.
    while not done.wait(0.5):
        pass
    fake.stop()


if __name__ == "__main__":
    main()
