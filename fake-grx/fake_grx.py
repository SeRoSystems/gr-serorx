"""Fake GRX receiver with a tunable channel and fixed 1030 and 1090 channels: TunableChanneld, Samplestreamingd, Monitord and Receiverd with synthetic IQ.

Limits follow the GRX 3X: the LO tunes 325 to 3800 MHz on both inputs, the sample rate 2.083 to 61.44 MSps,
values outside fail with ABORTED and a sysfs message, the bandwidth is clamped to 200 kHz to 20 MHz.
Block size and the signal are placeholders.
"""
import argparse
import collections
import math
import queue
import signal
import threading
import time
from concurrent import futures

import grpc
import numpy as np
from google.protobuf import empty_pb2

from gnuradio.serorx.adsb import crc, modes
from gnuradio.serorx.grx_client import HARDWARE_KEY, IMAGE_KEY, MODEL_KEY
from gnuradio.serorx.grx_decode import SPECS, STREAMS
from gnuradio.serorx.proto import (Common_pb2, Monitord_pb2, Monitord_pb2_grpc, Receiverd_pb2, Receiverd_pb2_grpc,
                                   Samplestreamingd_pb2, Samplestreamingd_pb2_grpc, TunableChanneld_pb2,
                                   TunableChanneld_pb2_grpc)

BLOCK_SAMPLES = 524288
RX_PORTS = {0: "Wide", 1: "Narrow (LNA)"}
LO_RANGE = (325_000_000, 3_800_000_000)
GAIN_RANGE = (0, 70)
SAMPLE_RATE_RANGE = (2_083_334, 61_440_000)
BANDWIDTH_RANGE = (200_000, 20_000_000)
SYSFS = ("Cannot write to sysfs file (/sys/devices/platform/axi/ff050000.spi/spi_master/spi2/spi2.2/iio:device4/{}): "
         "Invalid argument")
CALIBRATION_DB = -100.0  # at gain 0
STALL_SECONDS = 0.2
POLL_SECONDS = 0.05
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
FRAME_AMPLITUDE = (400.0, 3000.0)
MARGINAL_BIT_PROBABILITY = 0.3
# Items per block, measured on a GRX 2120X at 12 MSps (43.7 ms per block). The streams that receiver
# does not report carry a placeholder rate.
RATES = {
    "modes_downlink": 39.0,
    "modeac_downlink": 13.0,
    "modes_uplink": 11.0,
    "mode123ac_interrogations": 0.7,
    "mode4_interrogations": 0.2,
    "mode4_replies": 0.2,
    "mode5_interrogations": 0.2,
    "mode5_replies": 0.2,
    "dme_tacan": 1.0,
    "isolated_pulses": 1.0,
    "uat_adsb": 1.0,
    "uat_uplink": 0.2,
}
# Blocks between the item and the block delivered with it. Measured on a 2120X: every stream but
# Mode A/C reaches the client 3 to 269 ms before the block carrying its samples, because the decoder
# reads the live signal while the sample path buffers. Mode A/C arrives 32 to 196 ms after its samples,
# the device bins it over 100 ms.
DECODE_OFFSET = collections.defaultdict(lambda: 1, modeac_downlink=-2)

MODEL = "grx3x"
HARDWARE = "fake"
IMAGE = "0000.00.00"
SERIAL = "00:00:5e:00:53:01"
FREQ_STEP = 1000

# Receiverd: every stream publishes RATES items per block, each in its own slot of the block.
SIGNAL_LEVEL = -70.0
NOISE_LEVEL = -95.0
LEVELS = {"level_signal": SIGNAL_LEVEL, "level_noise": NOISE_LEVEL}
UPLINK_FRAME = bytes.fromhex("5D4840D6000000")
UAT_ADSB_PAYLOAD = bytes(range(18))
UAT_UPLINK_PAYLOAD = bytes(432)
MODEAC_CODE = 1234
PULSE_DURATION = 500.0
DECODE_TYPES = {
    "modes_downlink": (Receiverd_pb2.ModeSDownlinkFrame, Receiverd_pb2.ModeSDownlinkFrameWithStreamInfo),
    "modes_uplink": (Receiverd_pb2.ModeSUplinkFrame, Receiverd_pb2.ModeSUplinkFrameWithStreamInfo),
    "modeac_downlink": (Receiverd_pb2.ModeACDownlinkBinnedFrame, Receiverd_pb2.ModeACDownlinkBinnedFrameWithStreamInfo),
    "uat_adsb": (Receiverd_pb2.UATADSBMessage, Receiverd_pb2.UATADSBMessageWithStreamInfo),
    "uat_uplink": (Receiverd_pb2.UATGroundUplinkMessage, Receiverd_pb2.UATGroundUplinkMessageWithStreamInfo),
    "dme_tacan": (Receiverd_pb2.DMETACANPulsePair, Receiverd_pb2.DMETACANPulsePairWithStreamInfo),
    "isolated_pulses": (Receiverd_pb2.IsolatedPulse, Receiverd_pb2.IsolatedPulseWithStreamInfo),
    "mode4_interrogations": (Receiverd_pb2.Mode4Interrogation, Receiverd_pb2.Mode4InterrogationWithStreamInfo),
    "mode4_replies": (Receiverd_pb2.Mode4Reply, Receiverd_pb2.Mode4ReplyWithStreamInfo),
    "mode5_interrogations": (Receiverd_pb2.Mode5Interrogation, Receiverd_pb2.Mode5InterrogationWithStreamInfo),
    "mode5_replies": (Receiverd_pb2.Mode5Reply, Receiverd_pb2.Mode5ReplyWithStreamInfo),
    "mode123ac_interrogations": (Receiverd_pb2.Mode123ACInterrogation,
                                 Receiverd_pb2.Mode123ACInterrogationWithStreamInfo),
}
VALID = Receiverd_pb2.ModeSConfirmationFlags(address_tracked=True, message_valid=True)
# One capability per stream, so every stream the fake serves is reported as available.
ALL_CAPABILITIES = tuple(spec.capabilities[0] for spec in STREAMS)
DECODE_FIELDS = {
    "modes_downlink": lambda ts: dict(LEVELS, confirmation_flags=VALID),
    "modes_uplink": lambda ts: dict(LEVELS, payload=UPLINK_FRAME, confirmation_flags=VALID),
    "modeac_downlink": lambda ts: {"code": MODEAC_CODE, "receptions": [
        Receiverd_pb2.ModeACDownlinkBinnedFrame.Reception(timestamp=ts, signal_level=SIGNAL_LEVEL)]},
    "uat_adsb": lambda ts: dict(LEVELS, payload=UAT_ADSB_PAYLOAD),
    "uat_uplink": lambda ts: dict(LEVELS, payload=UAT_UPLINK_PAYLOAD),
    "dme_tacan": lambda ts: dict(LEVELS, spacing=Receiverd_pb2.DMETACANPulsePair.SPACING_12_US,
                                 band=Common_pb2.BAND_1030_MHZ),
    "isolated_pulses": lambda ts: dict(LEVELS, duration=PULSE_DURATION, band=Common_pb2.BAND_1090_MHZ),
    "mode4_interrogations": lambda ts: dict(LEVELS, sls_level=-3.0),
    "mode4_replies": lambda ts: dict(LEVELS),
    "mode5_interrogations": lambda ts: dict(LEVELS),
    "mode5_replies": lambda ts: dict(LEVELS),
    "mode123ac_interrogations": lambda ts: dict(LEVELS, mode=Receiverd_pb2.Mode123ACInterrogation.MODE_A,
                                                all_call=True, s1_level=float("nan"), sls_level=-6.0),
}

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
        self.subscribers = []
        self.timing_base = Receiverd_pb2.GPS_TOW
        self.capabilities = ALL_CAPABILITIES

    def snapshot(self):
        with self.lock:
            return {"rx_port": self.rx_port, "center_frequency": self.center_frequency,
                    "sample_rate": self.sample_rate, "bandwidth": self.bandwidth, "gain": self.gain}

    def subscribe(self, name):
        """A queue of (timestamp, fields) for one Receiverd stream."""
        items = queue.Queue()
        with self.lock:
            self.subscribers.append((name, items))
        return items

    def unsubscribe(self, items):
        with self.lock:
            self.subscribers = [entry for entry in self.subscribers if entry[1] is not items]

    def publish(self, name, timestamp, fields):
        with self.lock:
            targets = [items for subscribed, items in self.subscribers if subscribed == name]
        for items in targets:
            items.put((timestamp, fields))


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
    def __init__(self, state, realtime, signal=None):
        self.state = state
        self.realtime = realtime
        self.signal = signal
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

    def _center(self, key):
        if key == TUNABLE:
            with self.state.lock:
                return self.state.center_frequency
        return FIXED.get(key, 0)

    @staticmethod
    def _counts(carry):
        """Items per stream for one block. The fractional part carries over, so the rate holds on average."""
        counts = {}
        for name, rate in RATES.items():
            carry[name] += rate
            counts[name] = int(carry[name])
            carry[name] -= counts[name]
        return counts

    @staticmethod
    def _frame_plan(count, block, rate, rng):
        """(sample, payload, text) per Mode S frame of one block, one per slot so none overlap."""
        spu = rate // 1_000_000
        slot = BLOCK_SAMPLES // max(count, 1)
        plan = []
        for index in range(count):
            text = ADSB_FRAMES[(block * count + index) % len(ADSB_FRAMES)]
            length = modes.frame_length(spu, 4 * len(text))
            at = index * slot + int(rng.integers(0, max(slot - length, 1)))
            plan.append((at, bytes.fromhex(text), text))
        return plan

    @staticmethod
    def _inject(iq, plan, rate, rng):
        """Adds every planned frame to the samples. Some carry one marginal bit (pulse 37 %, gap 35 %)."""
        spu = rate // 1_000_000
        for at, _, text in plan:
            level = float(rng.uniform(*FRAME_AMPLITUDE))
            frame = modes.synthesize(text, spu, level)
            if rng.random() < MARGINAL_BIT_PROBABILITY:
                bits = len(frame) // spu - modes.PREAMBLE_US
                s0 = (modes.PREAMBLE_US + int(rng.integers(0, bits))) * spu
                frame[s0:s0 + spu] = np.where(frame[s0:s0 + spu] > 0, 0.37 * level, 0.35 * level)
            iq[at:at + len(frame), 0] += frame

    def _publish_decodes(self, start_ns, rate, block, plan, counts):
        """Publishes the items sent while block leaves, each in its own slot of the block it belongs to.

        DECODE_OFFSET puts that block ahead of or behind the one being delivered, so the items reach the
        client before or after their samples as they do on the device. Only capable streams publish.
        """
        capable = set(self.state.capabilities)
        for name, count in counts.items():
            if not capable.intersection(SPECS[name].capabilities):
                continue
            target = block + DECODE_OFFSET[name]
            if target < 0:
                continue
            base = start_ns + target * BLOCK_SAMPLES * 1_000_000_000 // rate
            if name == "modes_downlink":
                for at, payload, _ in plan:
                    item_ns = base + at * 1_000_000_000 // rate
                    fields = DECODE_FIELDS[name](item_ns)
                    fields["payload"] = payload
                    self.state.publish(name, item_ns, fields)
                continue
            slot = BLOCK_SAMPLES // max(count, 1)
            for index in range(count):
                item_ns = base + (index * slot + slot // 2) * 1_000_000_000 // rate
                self.state.publish(name, item_ns, DECODE_FIELDS[name](item_ns))

    def StartStream(self, request, context):
        key = _key(request.radio_identification)
        tunable = key == TUNABLE
        frames = key == (Common_pb2.BAND_1090_MHZ, 0)
        _, rate, gain = self._properties(request.radio_identification, context)
        generation = self.state.generation
        amplitude = min(1000.0 * 10 ** (gain / 20.0), MAX_AMPLITUDE)
        step = 2 * math.pi / TONE_DIVISOR
        block_period = BLOCK_SAMPLES / rate
        index = np.arange(BLOCK_SAMPLES)
        rng = np.random.default_rng(0)
        start = time.monotonic()
        start_ns = int((start - self.t0) * 1e9)
        block = 0
        lost = 0
        sent = 0
        carry = {name: 0.0 for name in RATES}
        pending = []
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
                # A block leaves at the end of its own span, as it does on the device: its last sample
                # has to exist before it can be sent.
                late = time.monotonic() - (start + (block + 1) * block_period)
                if late > SLOW_CONSUMER_SECONDS:
                    skip += int(late / block_period)
                elif late < 0:
                    time.sleep(-late)
            block += skip
            lost += skip
            counts = self._counts(carry)
            # The frames of the next block are planned and published now, one block before the samples
            # that carry them leave. They are planned for every channel, so the decoder streams carry
            # them wherever the client samples, as they do on the device. Only the 1090 channel puts
            # them into its samples, one block later.
            ahead = self._frame_plan(counts["modes_downlink"], block + 1, rate, rng)
            plan, pending = pending, ahead
            iq = rng.normal(0.0, NOISE_SIGMA, (BLOCK_SAMPLES, 2))
            extra = None if self.signal is None else self.signal(key, block, rate, self._center(key), rng)
            if extra is not None:
                iq += extra
            elif frames:
                self._inject(iq, plan, rate, rng)
            else:
                phase = ((block * BLOCK_SAMPLES + index) % TONE_DIVISOR) * step
                iq[:, 0] += amplitude * np.cos(phase)
                iq[:, 1] += amplitude * np.sin(phase)
            samples = np.clip(np.rint(iq), -32768, 32767).astype("<i2").tobytes()
            # The whole product, so the rounding to whole nanoseconds does not accumulate over the blocks.
            timestamp = start_ns + block * BLOCK_SAMPLES * 1_000_000_000 // rate
            self._publish_decodes(start_ns, rate, block, ahead, counts)
            yield Samplestreamingd_pb2.StartStreamReply(block_timestamp=timestamp, samples=samples, lost_blocks=lost)
            block += 1
            sent += 1


class _Monitord(Monitord_pb2_grpc.MonitordServicer):
    def GetSystemInformation(self, request, context):
        return Monitord_pb2.SystemInformation(
            system_time=int(time.time()), serial_number=SERIAL,
            version_information={MODEL_KEY: MODEL, HARDWARE_KEY: HARDWARE, IMAGE_KEY: IMAGE})


class _Receiverd(Receiverd_pb2_grpc.ReceiverdServicer):
    """The twelve decoder streams, served from what the sample stream publishes."""

    def __init__(self, state):
        self.state = state

    def _stream(self, name, context, keep=None):
        """Relays one stream. keep(fields) filters the items, as the format lists do on the device."""
        inner, reply = DECODE_TYPES[name]
        container = SPECS[name].container
        items = self.state.subscribe(name)
        try:
            while context.is_active():
                try:
                    timestamp, fields = items.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    continue
                if keep is not None and not keep(fields):
                    continue
                item = inner(timestamp=timestamp, timing_base=self.state.timing_base,
                             timing_sync_source=Receiverd_pb2.GNSS, **fields)
                yield reply(**{container: item})
        finally:
            self.state.unsubscribe(items)

    @staticmethod
    def _format_filter(formats):
        """Keeps the items whose first payload byte carries one of the requested formats."""
        wanted = frozenset(formats)
        return lambda fields: bool(fields.get("payload")) and (fields["payload"][0] >> 3) in wanted

    @staticmethod
    def _formats(formats, context):
        if not formats:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "format list empty")

    def GetStatistics(self, request, context):
        capabilities = Receiverd_pb2.ReceptionCapabilities(capabilities=self.state.capabilities)
        return Receiverd_pb2.Statistics(reception_capabilities=capabilities)

    def GetModeSDownlinkFrames(self, request, context):
        self._formats(request.downlink_formats, context)
        return self._stream("modes_downlink", context, self._format_filter(request.downlink_formats))

    def GetModeSUplinkFrames(self, request, context):
        self._formats(request.uplink_formats, context)
        return self._stream("modes_uplink", context, self._format_filter(request.uplink_formats))

    def GetModeACDownlinkFrames(self, request, context):
        return self._stream("modeac_downlink", context)

    def GetUATADSBMessages(self, request, context):
        self._formats(request.payload_type_codes, context)
        return self._stream("uat_adsb", context)

    def GetUATGroundUplinkMessages(self, request, context):
        return self._stream("uat_uplink", context)

    def GetDMETACANPulsePairs(self, request, context):
        return self._stream("dme_tacan", context)

    def GetIsolatedPulses(self, request, context):
        return self._stream("isolated_pulses", context)

    def GetMode4Interrogations(self, request, context):
        return self._stream("mode4_interrogations", context)

    def GetMode4Replies(self, request, context):
        return self._stream("mode4_replies", context)

    def GetMode5Interrogations(self, request, context):
        return self._stream("mode5_interrogations", context)

    def GetMode5Replies(self, request, context):
        return self._stream("mode5_replies", context)

    def GetMode123ACInterrogations(self, request, context):
        return self._stream("mode123ac_interrogations", context)


class FakeGrx:
    """The four services on 127.0.0.1, one port each. Port 0 picks a free port, readable after start().

    signal is an optional generator called per block as signal(key, block, rate, center, rng). It returns an
    (BLOCK_SAMPLES, 2) float array added to the noise, or None for the built in content of that channel.
    """

    def __init__(self, control_port=0, stream_port=0, monitor_port=0, decode_port=0, realtime=True, signal=None):
        self.control_port = control_port
        self.stream_port = stream_port
        self.monitor_port = monitor_port
        self.decode_port = decode_port
        self.realtime = realtime
        self.signal = signal
        self.state = _State()
        self._servers = []

    def _serve(self, add_servicer, servicer, port, workers=8):
        # grpc binds a second server to a port already in use and splits the connections between the two, so
        # SO_REUSEPORT is off. add_insecure_port then returns 0 instead of raising.
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers), options=[("grpc.so_reuseport", 0)])
        add_servicer(servicer, server)
        self._servers.append(server)
        bound = server.add_insecure_port(f"127.0.0.1:{port}")
        if bound == 0:
            self.stop()
            raise OSError(f"port {port} in use")
        return bound

    def start(self):
        self._servers = []
        self.control_port = self._serve(TunableChanneld_pb2_grpc.add_TunableChanneldServicer_to_server,
                                        _TunableChanneld(self.state), self.control_port)
        self.stream_port = self._serve(Samplestreamingd_pb2_grpc.add_SamplestreamingdServicer_to_server,
                                       _Samplestreamingd(self.state, self.realtime, self.signal), self.stream_port)
        self.monitor_port = self._serve(Monitord_pb2_grpc.add_MonitordServicer_to_server, _Monitord(),
                                        self.monitor_port)
        # One worker per stream: every open stream holds its worker for the life of the RPC.
        self.decode_port = self._serve(Receiverd_pb2_grpc.add_ReceiverdServicer_to_server, _Receiverd(self.state),
                                       self.decode_port, workers=len(STREAMS) + 2)
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

    def set_capabilities(self, capabilities):
        """The reception capabilities GetStatistics reports. A stream outside them stays silent on the device."""
        self.state.capabilities = tuple(capabilities)

    def set_timing_base(self, base):
        """The time base every published item carries. NO_BASE and SYSTEM_TIME cannot be placed on a sample."""
        self.state.timing_base = base

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
    parser = argparse.ArgumentParser(
        description="Fake GRX receiver: TunableChanneld, Samplestreamingd, Monitord and Receiverd.")
    parser.add_argument("--control-port", type=int, default=5309)
    parser.add_argument("--stream-port", type=int, default=5308)
    parser.add_argument("--monitor-port", type=int, default=5305)
    parser.add_argument("--decode-port", type=int, default=5303)
    parser.add_argument("--burst", action="store_true", help="send blocks as fast as the client reads")
    args = parser.parse_args()
    fake = FakeGrx(args.control_port, args.stream_port, args.monitor_port, args.decode_port,
                   realtime=not args.burst).start()
    print(f"fake GRX on 127.0.0.1: control {fake.control_port}, stream {fake.stream_port}, "
          f"monitor {fake.monitor_port}, decode {fake.decode_port}. Ctrl+C stops it", flush=True)
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    # A wait with a timeout lets Ctrl+C through on Windows, where an untimed wait is not interruptible.
    while not done.wait(0.5):
        pass
    fake.stop()


if __name__ == "__main__":
    main()
