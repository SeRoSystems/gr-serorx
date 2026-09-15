"""GNU Radio source block for one channel of a SeRo Systems GRX receiver."""
import collections
import re
import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np
import pmt
from gnuradio import gr

from .grx_client import GrxControl, GrxError, GrxMonitor, GrxStream, channel_label, radio_id, reachability
from .grx_decode import SPECS, STREAM_NAMES, GrxDecode

TAG_RATE = "rx_rate"
TAG_FREQ = "rx_freq"
TAG_TIMESTAMP = "grx_timestamp"
TAG_TIME = "rx_time"
TAG_LOST = "grx_lost_blocks"
TAG_CALIBRATION = "grx_calibration_db"
TAG_DECODE_PREFIX = "grx_"

BYTES_PER_SAMPLE = 4
NS = 1_000_000_000
WEEK_NS = 7 * 24 * 3600 * NS
EARLY_GRACE = 2.0
SCALE = np.float32(1.0 / 32768.0)
RX_PORT_NAMES = ("wide", "narrow")
OUTPUT_TYPES = {"fc32": np.complex64, "sc16": (np.int16, 2)}
POP_TIMEOUT = 0.1
MIN_BUFFER_BYTES = 64 * 8192 * BYTES_PER_SAMPLE
BACKOFF_START = 1.0
BACKOFF_MAX = 10.0
RATE_POLL = 0.05
REPORT_INTERVAL = 5.0
REFRESH_INTERVAL = 1.0
STALL_SECONDS = 5.0
STALL_REPORT = 30.0
SETTING_NAMES = {"set_center_freq": "center frequency", "set_gain": "hardware gain", "set_bandwidth": "analog bandwidth",
                 "set_rx_port": "RX input", "set_samp_rate": "sample rate"}
RANGE_HINTS = {"hardware gain": "Receiver supports 0 to 70 dB", "sample rate": "Receiver supports 2.083 to 61.44 MSps",
               "analog bandwidth": "Receiver supports 200 kHz to 20 MHz"}
HZ_UNITS = ((1e6, "MHz"), (1e3, "kHz"), (1, "Hz"))
SPS_UNITS = ((1e6, "MSps"), (1e3, "kSps"), (1, "Sps"))
RX_RANGES = {"wide": "325 to 3800 MHz", "narrow": "700 to 1100 MHz"}
SYSFS_PATH = re.compile(r"/sys/[^\s)]*/([^\s/)]+)")


def _sample_offset(timestamp, base, samp_rate):
    """Sample index of timestamp in a block starting at base. Negative before it, computed over the GPS week."""
    delta = (timestamp - base) % WEEK_NS
    if delta > WEEK_NS // 2:
        delta -= WEEK_NS
    return delta * samp_rate // NS


def _pmt_value(value):
    if isinstance(value, bool):
        return pmt.from_bool(value)
    if isinstance(value, int):
        return pmt.from_long(value)
    if isinstance(value, float):
        return pmt.from_double(value)
    if isinstance(value, bytes):
        return pmt.init_u8vector(len(value), list(value))
    return pmt.intern(str(value))


def _decode_value(item):
    """The item fields plus its raw timestamp as a PMT dict."""
    value = pmt.dict_add(pmt.make_dict(), pmt.intern("timestamp"), pmt.from_uint64(item.timestamp))
    for key, entry in item.fields.items():
        value = pmt.dict_add(value, pmt.intern(key), _pmt_value(entry))
    return value


@dataclass
class _Entry:
    """One queued block. marks are (sample index, stream name, tag key, tag value) of the decodes it carries."""
    samples: bytes
    tags: list
    timestamp: int
    count: int
    arrived: float
    marks: list = field(default_factory=list)


def _rx_time(timestamp_ns):
    """UHD style rx_time: (uint64 full seconds, double fraction). The device clock is GPS time of week."""
    return pmt.make_tuple(pmt.from_uint64(timestamp_ns // NS), pmt.from_double((timestamp_ns % NS) / NS))


def _scaled(value, units):
    """The value with the largest unit it reaches, two decimals: 1090.00 MHz, 500.00 kHz, 2.08 MSps. No GHz."""
    for scale, unit in units:
        if abs(value) >= scale or scale == 1:
            return f"{value / scale:.2f} {unit}"


def _hz(hz):
    return _scaled(hz, HZ_UNITS)


def _sps(sps):
    return _scaled(sps, SPS_UNITS)


class grx_source(gr.sync_block):
    """Streams IQ from one GRX channel and tunes the tunable channel.

    The constructor connects to the receiver and applies the settings, so a bad value
    or an unreachable receiver fails the flowgraph with the device message.

    Console: receiver model, serial, hardware and image, the channel list, the settings the receiver
    reports, a warning per value it did not adopt, every runtime change, a changed calibration,
    lost blocks (at most one line per REPORT_INTERVAL) and a loss summary at stop.

    Tags: rx_rate, rx_freq (start and change), rx_time and grx_timestamp (start, after every gap and
    on every new stream, GPS time of week in seconds and in nanoseconds), grx_lost_blocks (after a gap),
    grx_calibration_db (start and whenever the value the receiver reports changes). The receiver
    recomputes its stream properties on a 10 s clock, so a calibration change shows up to 10 s later.
    The reader thread polls them every REFRESH_INTERVAL. Centre frequency and sample rate come from
    TunableChanneld, which answers at once.

    decodes names the Receiverd streams to subscribe to, from grx_decode.STREAM_NAMES. Each decoded item
    becomes one grx_<stream> tag, a dict of its fields, on the sample its GPS timestamp names. decode_delay
    is how long a block waits in the queue before it leaves, the window an item has to arrive in.
    """

    def __init__(self, host, band="tunable", channel_index=0, center_freq=1090e6, samp_rate=12e6, gain=0,
                 rx_port="wide", bandwidth=0, output_type="fc32", buffer_seconds=0.5, decodes=(), decode_delay=0.25,
                 control_port=5309, stream_port=5308, monitor_port=5305, decode_port=5303, timeout=5.0):
        if output_type not in OUTPUT_TYPES:
            raise ValueError(f"Output {output_type!r} not in {sorted(OUTPUT_TYPES)}")
        if rx_port not in RX_PORT_NAMES:
            raise ValueError(f"RX input {rx_port!r} not in {RX_PORT_NAMES}")
        unknown = [name for name in decodes if name not in STREAM_NAMES]
        if unknown:
            raise ValueError(f"Decode stream {unknown[0]!r} not in {list(STREAM_NAMES)}")
        gr.sync_block.__init__(self, name="grx_source", in_sig=None, out_sig=[OUTPUT_TYPES[output_type]])
        self._float = output_type == "fc32"
        self._radio = radio_id(band, channel_index)
        self._channel = (band, int(channel_index))
        self._label = channel_label(band, channel_index)
        self._tunable = band == "tunable"
        self._center_freq = int(center_freq)
        self._samp_rate = int(samp_rate)
        self._gain = int(gain)
        self._rx_port = rx_port
        self._bandwidth = int(bandwidth)
        self._timeout = float(timeout)
        self._buffer_seconds = float(buffer_seconds)
        self._host = host
        self._stream_port = stream_port
        self._control_port = control_port
        self._decode_port = decode_port
        self._decode_names = tuple(decodes)
        self._decode_delay = max(float(decode_delay), 0.0) if self._decode_names else 0.0

        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._queue = collections.deque()
        self._queued_bytes = 0
        self._max_bytes = MIN_BUFFER_BYTES
        self._pending_tags = []
        self._start_tags = []
        self._current = None
        self._offset = 0
        self._marks = collections.deque()
        self._reader = None
        self._iter = None
        self._stop_reader = threading.Event()
        self._running = False
        self._lost_device = None
        self._lost_local = 0
        self._resume_pending = False
        self._blocks_in = 0
        self._lost_link = 0
        self._lost_buffer = 0
        self._reported = (0, 0)
        self._report_time = time.monotonic()
        self._refresh_time = time.monotonic()
        self._last_block = time.monotonic()
        self._stall_reported = None
        self._interrupted = False
        self._slow_hinted = False
        self._decode = None
        self._decode_keys = {name: pmt.intern(TAG_DECODE_PREFIX + name) for name in self._decode_names}
        self._decode_threads = []
        self._decode_iters = {}
        self._stop_decoders = threading.Event()
        self._early = []
        self._decode_tagged = collections.Counter()
        self._decode_lost = collections.Counter()
        self._decode_device = {}
        self._decode_shortfall = 0.0
        self._decode_reported = 0
        self._decode_report_time = time.monotonic()

        self._control = GrxControl(host, control_port, self._timeout) if self._tunable else None
        self._stream = GrxStream(host, stream_port, self._timeout)
        self._port_ids = {}
        self._port_labels = {}
        channels = self._discover_channels()
        self._describe_receiver(host, monitor_port)
        self._check_channel(channels)
        if self._tunable:
            self._apply_settings()
            self._props = self._wait_for_rate(self._samp_rate)
        else:
            self._props = self._stream.properties(self._radio)
        reported = self._read_back()
        self._props = replace(self._props, center_freq=reported["center_freq"])
        self._adopt_properties()
        self._log("info", self._settings_line(reported))
        for line in self._mismatches(reported):
            self._log("warn", line)
        self._open_decode(host)

    # console

    def _log(self, level, text):
        getattr(self.logger, level)(text)

    def _connection_problem(self, err, port, service, hint=True):
        """One line for an RPC that never reached the service, None when the service answered."""
        kind = reachability(err, self._timeout)
        if kind is None:
            return None
        if kind == "refused":
            line = f"host {self._host!r} answers, but port {port} ({service}) is closed"
            return line + (", check address and firewall" if hint else "")
        if kind == "unresolved":
            return f"host {self._host!r} does not resolve" + (", check address" if hint else "")
        reason = {"timeout": f"no answer within {self._timeout:g} s", "unroutable": "no route to host"}.get(
            kind, "connection failed")
        line = f"host {self._host!r} could not be reached ({reason})"
        return line + (", check address and receiver connection" if hint else "")

    def _setting_error(self, name, shown, err):
        """One line for a Set* call that failed: value outside the protocol's uint32 fields (ValueError
        from protobuf), receiver gone, or a value the receiver refused with any status."""
        hint = self._range_hint(name)
        suffix = f". {hint}" if hint else ""
        if isinstance(err, ValueError):
            return f"{name} {shown} rejected: {err}{suffix}"
        problem = self._connection_problem(err, self._control_port, "TunableChanneld", hint=False)
        if problem is not None:
            return f"{name} not set: {problem}"
        text = SYSFS_PATH.sub(lambda m: m.group(1), err.details or "")
        if err.code.name != "INVALID_ARGUMENT":
            text = f"{err.code.name}: {text}"
        return f"{name} {shown} rejected by receiver: {text}{suffix}"

    def _range_hint(self, name):
        if name == "center frequency":
            return f"Selected RX input ({self._rx_port.capitalize()}) supports {RX_RANGES[self._rx_port]}"
        return RANGE_HINTS.get(name)

    def _stream_reason(self, err, port=None, service="Samplestreamingd"):
        """Reason for the reconnect line: the closed port with its service, not reachable, or the status name."""
        kind = reachability(err, self._timeout)
        if kind == "refused":
            return f"port {port or self._stream_port} ({service}) closed"
        if kind is not None:
            return "not reachable"
        return err.code.name.lower().replace("_", " ")

    def _fail(self, message):
        """Logs the message, closes the gRPC channels and raises it as the only exception, without the gRPC error underneath."""
        self._log("error", message)
        for client in (self._control, self._stream, self._decode):
            if client is not None:
                client.close()
        raise RuntimeError(message) from None

    def _discover_channels(self):
        """The channels the receiver streams. A receiver that does not answer fails construction."""
        try:
            return self._stream.channels()
        except GrxError as err:
            problem = self._connection_problem(err, self._stream_port, "Samplestreamingd")
            if problem is None:
                raise
            self._fail(problem)

    def _describe_receiver(self, host, port):
        monitor = GrxMonitor(host, port, self._timeout)
        try:
            self._log("info", f"receiver {monitor.system_info().describe()}")
        except GrxError as err:
            problem = self._connection_problem(err, port, "Monitord") or str(err)
            self._log("warn", f"{problem}. Model and serial unknown")
        finally:
            monitor.close()

    def _check_channel(self, channels):
        """Logs the channel list. A missing channel fails construction with that list."""
        listed = ", ".join(channel_label(band, index) for band, index in channels) or "none"
        self._log("info", f"channels: {listed}")
        if self._channel not in channels:
            self._fail(f"channel {self._label} is not available. Available channels: {listed}")

    def _port_name(self, port_id):
        return self._port_labels.get(port_id, str(port_id))

    def _read_back(self):
        """The settings as the receiver reports them. TunableChanneld answers at once, the stream properties lag."""
        reported = {"center_freq": self._props.center_freq, "samp_rate": self._props.samp_rate}
        if self._tunable:
            reported["center_freq"] = self._control.get_center_freq(self._radio)
            reported["gain"] = self._control.get_gain(self._radio)
            reported["bandwidth"] = self._control.get_bandwidth(self._radio)
            reported["rx_port"] = self._control.get_rx_port(self._radio)
        return reported

    def _settings_line(self, reported):
        parts = [_hz(reported["center_freq"]), _sps(reported["samp_rate"])]
        if self._tunable:
            parts.append(f"hardware gain {reported['gain']} dB")
            parts.append(f"analog bandwidth {_hz(reported['bandwidth'])}")
            parts.append(f"RX input {self._port_name(reported['rx_port'])}")
        parts.append(f"calibration {self._props.calibration_db:.2f} dB")
        return f"channel {self._label}: receiver reports " + ", ".join(parts)

    @staticmethod
    def _mismatch(name, requested, reported, fmt=str, unit="Hz"):
        """One line for a value the receiver did not adopt. When both format alike, the difference in unit follows."""
        want, got = fmt(requested), fmt(reported)
        line = f"{name} requested {want}, receiver reports {got}"
        if want == got:
            line += f" ({reported - requested:+d} {unit})"
        return line

    def _mismatches(self, reported):
        """Requested values the receiver did not adopt. _wait_for_rate covers the sample rate."""
        if not self._tunable:
            return []
        lines = []
        if self._center_freq != reported["center_freq"]:
            lines.append(self._mismatch("center frequency", self._center_freq, reported["center_freq"], _hz))
        if self._gain != reported["gain"]:
            lines.append(self._mismatch("hardware gain", self._gain, reported["gain"], lambda db: f"{db} dB"))
        if 0 < self._bandwidth != reported["bandwidth"]:
            lines.append(self._mismatch("analog bandwidth", self._bandwidth, reported["bandwidth"], _hz))
        if self._port_ids[self._rx_port] != reported["rx_port"]:
            lines.append(f"RX input requested {self._port_name(self._port_ids[self._rx_port])}, "
                         f"receiver reports {self._port_name(reported['rx_port'])}")
        return lines

    def _report_loss(self):
        now = time.monotonic()
        if now - self._report_time < REPORT_INTERVAL:
            return
        link = self._lost_link - self._reported[0]
        buffer = self._lost_buffer - self._reported[1]
        if link or buffer:
            self._log("warn", f"lost {link + buffer} blocks in last {now - self._report_time:.0f} s: "
                              f"{link} reported by receiver, {buffer} dropped by full buffer")
        self._reported = (self._lost_link, self._lost_buffer)
        self._report_time = now

    def _refresh_properties(self):
        """Re-reads the calibration from the stream properties and tags a change. Runs on the reader thread.

        Centre frequency and sample rate stay as TunableChanneld reported them: the stream properties
        follow on the receiver's own clock.
        """
        now = time.monotonic()
        if now - self._refresh_time < REFRESH_INTERVAL:
            return
        self._refresh_time = now
        try:
            props = self._stream.properties(self._radio)
        except GrxError as err:
            # A receiver that is gone is reported by the stream loop.
            if reachability(err) is None:
                self._log("warn", f"stream properties not read: {err}")
            return
        old = self._props
        self._props = replace(old, calibration_db=props.calibration_db)
        if props.calibration_db != old.calibration_db:
            self._add_pending(TAG_CALIBRATION, pmt.from_double(float(props.calibration_db)))
            self._log("info", f"calibration {props.calibration_db:.2f} dB")

    def _loss_summary(self):
        total = self._blocks_in + self._lost_link
        if not total:
            return "stream stopped: no blocks received"
        lost = self._lost_link + self._lost_buffer
        return (f"stream stopped: {self._blocks_in - self._lost_buffer} blocks delivered, {lost} lost "
                f"({100.0 * lost / total:.1f} %): {self._lost_link} reported by receiver, "
                f"{self._lost_buffer} dropped by full buffer")

    # decoding

    def _open_decode(self, host):
        """Connects to Receiverd when a stream is selected. An unreachable service is a warning, the tags stay away."""
        if not self._decode_names:
            return
        self._decode = GrxDecode(host, self._decode_port, self._timeout)
        try:
            stats = self._decode.statistics()
        except GrxError as err:
            problem = self._connection_problem(err, self._decode_port, "Receiverd") or str(err)
            self._log("warn", f"{problem}. No decode tags")
            self._decode.close()
            self._decode = None
            self._decode_names = ()
            self._decode_delay = 0.0
            return
        labels = ", ".join(SPECS[name].label for name in self._decode_names)
        self._log("info", f"decoding {labels}, tagged within {self._decode_delay:.2f} s of their samples")
        for line in self._incapable(stats):
            self._log("warn", line)

    def _incapable(self, stats):
        """One line naming the selected streams the receiver does not report, from its reception capabilities."""
        reported = set(stats.reception_capabilities.capabilities)
        if not reported:
            return []
        missing = [SPECS[name].label for name in self._decode_names
                   if not reported.intersection(SPECS[name].capabilities)]
        if not missing:
            return []
        return [f"receiver does not report {', '.join(missing)}, those streams stay silent"]

    def _start_decoding(self):
        if self._decode is None:
            return
        self._stop_decoders.clear()
        self._decode_report_time = time.monotonic()
        self._decode_threads = [threading.Thread(target=self._decode_loop, args=(name,), name=f"grx_{name}",
                                                 daemon=True) for name in self._decode_names]
        for thread in self._decode_threads:
            thread.start()

    def _stop_decoding(self):
        self._stop_decoders.set()
        for name in list(self._decode_iters):
            call = self._decode_iters.get(name)
            if call is not None:
                call.cancel()
        for thread in self._decode_threads:
            thread.join(timeout=self._timeout + BACKOFF_MAX)
        self._decode_threads = []
        with self._lock:
            self._decode_lost["expired"] += len(self._early)
            self._early = []

    def _decode_loop(self, name):
        backoff = BACKOFF_START
        label = SPECS[name].label
        while not self._stop_decoders.is_set():
            try:
                call = self._decode.start(name)
                self._decode_iters[name] = call
                if self._stop_decoders.is_set():
                    call.cancel()
                    break
                for item in call:
                    if self._stop_decoders.is_set():
                        break
                    self._take_decode(item)
                    backoff = BACKOFF_START
            except GrxError as err:
                if self._stop_decoders.is_set():
                    break
                self._log("warn", f"{label} stream from {self._host!r} lost "
                                  f"({self._stream_reason(err, self._decode_port, 'Receiverd')}), "
                                  f"reconnecting in {backoff:.0f} s")
            finally:
                self._decode_iters[name] = None
            if self._stop_decoders.wait(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _take_decode(self, item):
        """Holds one decoded item until the block carrying its sample is queued. Runs on a decode thread."""
        with self._lock:
            previous = self._decode_device.get(item.name)
            if previous is not None and item.dropped > previous:
                self._decode_lost["receiver"] += item.dropped - previous
            self._decode_device[item.name] = item.dropped
            if not item.timed:
                self._decode_lost["untimed"] += 1
                return
            self._early.append((time.monotonic(), item))
            self._match_locked()
        self._report_decodes()

    def _match_locked(self):
        """Places every held item that a queued block covers. The caller holds the lock."""
        if not self._early:
            return
        now = time.monotonic()
        kept = []
        for received, item in self._early:
            placed = self._place_locked(item)
            if placed == "early":
                if now - received > self._decode_delay + EARLY_GRACE:
                    self._decode_lost["expired"] += 1
                else:
                    kept.append((received, item))
                continue
            if placed != "placed":
                self._decode_lost[placed] += 1
        self._early = kept

    def _place_locked(self, item):
        """'placed', 'late' (older than the queue), 'gap' (inside a lost block) or 'early' (its block is not queued)."""
        for index, entry in enumerate(self._queue):
            offset = _sample_offset(item.timestamp, entry.timestamp, self._samp_rate)
            if offset < 0:
                if index == 0:
                    self._decode_shortfall = max(self._decode_shortfall, -offset / self._samp_rate)
                    return "late"
                return "gap"
            if offset < entry.count:
                entry.marks.append((int(offset), item.name, self._decode_keys[item.name], _decode_value(item)))
                return "placed"
        return "early"

    def _report_decodes(self):
        """Warns about items the buffer no longer held, at most one line per REPORT_INTERVAL."""
        now = time.monotonic()
        if now - self._decode_report_time < REPORT_INTERVAL:
            return
        late = self._decode_lost["late"] - self._decode_reported
        if late:
            needed = self._decode_delay + self._decode_shortfall
            self._log("warn", f"{late} decoded items were older than the buffer in the last "
                              f"{now - self._decode_report_time:.0f} s, raise the decode delay above "
                              f"{needed:.2f} s")
        self._decode_reported = self._decode_lost["late"]
        self._decode_shortfall = 0.0
        self._decode_report_time = now

    def _decode_summary(self):
        tagged = sum(self._decode_tagged.values())
        per_stream = ", ".join(f"{SPECS[name].label} {self._decode_tagged[name]}"
                               for name in self._decode_names if self._decode_tagged[name])
        reasons = {"late": "older than the buffer", "gap": "in a lost block", "expired": "without samples",
                   "discarded": "dropped with their blocks", "untimed": "without GPS time",
                   "receiver": "dropped by the receiver"}
        lost = ", ".join(f"{self._decode_lost[key]} {text}" for key, text in reasons.items() if self._decode_lost[key])
        line = f"decoding stopped: {tagged} items tagged"
        if per_stream:
            line += f" ({per_stream})"
        return line + (f", {lost}" if lost else "")

    # configuration

    def _apply_settings(self):
        try:
            ports = self._control.rx_ports(self._radio)
        except GrxError as err:
            problem = self._connection_problem(err, self._control_port, "TunableChanneld")
            if problem is None:
                raise
            self._fail(f"{problem}. Tunable channel cannot be configured")
        self._port_labels = dict(ports)
        self._port_ids = {name: pid for pid, label in ports.items() for name in RX_PORT_NAMES if name in label.lower()}
        if self._rx_port not in self._port_ids:
            self._fail(f"RX input {self._rx_port!r} not found, receiver offers {', '.join(ports.values())}")
        steps = [("RX input", "set_rx_port", self._port_ids[self._rx_port], self._rx_port),
                 ("center frequency", "set_center_freq", self._center_freq, _hz(self._center_freq))]
        if self._bandwidth > 0:
            steps.append(("analog bandwidth", "set_bandwidth", self._bandwidth, _hz(self._bandwidth)))
        steps.append(("hardware gain", "set_gain", self._gain, f"{self._gain} dB"))
        steps.append(("sample rate", "set_samp_rate", self._samp_rate, _sps(self._samp_rate)))
        for name, what, value, shown in steps:
            try:
                getattr(self._control, what)(self._radio, value)
            except (GrxError, ValueError) as err:
                self._fail(self._setting_error(name, shown, err))

    def _adopt_properties(self):
        """Sizes the buffer for the rate and tags the properties on the first sample of the next stream."""
        self._samp_rate = self._props.samp_rate
        self._max_bytes = max(int(self._buffer_seconds * self._samp_rate) * BYTES_PER_SAMPLE, MIN_BUFFER_BYTES)
        with self._lock:
            self._start_tags = [(TAG_RATE, pmt.from_double(float(self._props.samp_rate))),
                                (TAG_FREQ, pmt.from_double(float(self._props.center_freq))),
                                (TAG_CALIBRATION, pmt.from_double(float(self._props.calibration_db)))]

    def _add_pending(self, key, value):
        with self._lock:
            self._pending_tags.append((key, value))

    # scheduler hooks

    def start(self):
        self._running = True
        self._start_decoding()
        self._start_reader()
        return True

    def stop(self):
        self._running = False
        self._halt_reader()
        self._stop_decoding()
        self._log("info", self._loss_summary())
        if self._decode is not None:
            self._log("info", self._decode_summary())
        return True

    # reader thread

    def _start_reader(self):
        self._stop_reader.clear()
        self._report_time = time.monotonic()
        self._refresh_time = time.monotonic()
        self._last_block = time.monotonic()
        self._stall_reported = None
        self._interrupted = False
        self._slow_hinted = False
        self._reader = threading.Thread(target=self._read_loop, name="grx_reader", daemon=True)
        self._reader.start()

    def _halt_reader(self):
        """Stops the reader thread and discards the blocks still queued, so the next stream's tags land on its own samples."""
        self._stop_reader.set()
        it = self._iter
        if it is not None:
            it.cancel()
        if self._reader is not None:
            self._reader.join(timeout=self._timeout + BACKOFF_MAX)
            self._reader = None
        with self._lock:
            self._blocks_in -= len(self._queue)
            self._decode_lost["discarded"] += sum(len(entry.marks) for entry in self._queue)
            self._decode_lost["expired"] += len(self._early)
            self._early = []
            self._queue.clear()
            self._queued_bytes = 0
            self._not_empty.notify_all()

    def _read_loop(self):
        backoff = BACKOFF_START
        while not self._stop_reader.is_set():
            try:
                # The device counter restarts at 0 with every stream, which starts on its own timestamp.
                self._lost_device = None
                self._resume_pending = True
                self._iter = self._stream.start(self._radio)
                if self._stop_reader.is_set():
                    # A halt that ran before the iterator existed could not cancel it.
                    self._iter.cancel()
                    break
                for block in self._iter:
                    if self._stop_reader.is_set():
                        break
                    self._push(block)
                    backoff = BACKOFF_START
                if not self._stop_reader.is_set():
                    self._log("warn", f"stream from {self._host!r} ended, reconnecting in {backoff:.0f} s")
            except GrxError as err:
                if self._stop_reader.is_set():
                    break
                self._log("warn", f"stream from {self._host!r} lost ({self._stream_reason(err)}), "
                                  f"reconnecting in {backoff:.0f} s")
            finally:
                self._iter = None
            self._interrupted = True
            if self._stop_reader.wait(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _push(self, block):
        self._blocks_in += 1
        self._last_block = time.monotonic()
        self._stall_reported = None
        if self._interrupted:
            self._interrupted = False
            self._log("info", f"stream from {self._host!r} resumed")
        if self._lost_device is not None:
            delta = (block.lost_blocks - self._lost_device) & 0xFFFFFFFF
            self._lost_local += delta
            self._lost_link += delta
        self._lost_device = block.lost_blocks
        with self._lock:
            if self._queued_bytes + len(block.samples) > self._max_bytes:
                self._lost_local += 1
                self._lost_buffer += 1
                if not self._slow_hinted:
                    self._slow_hinted = True
                    self._log("warn", f"flowgraph consumes slower than {_sps(self._samp_rate)}, samples dropped")
                self._report_loss()
                return
            tags = []
            if self._lost_local:
                tags.append((TAG_LOST, pmt.from_uint64(self._lost_local)))
                self._lost_local = 0
                self._resume_pending = True
            if self._resume_pending:
                tags.extend(self._start_tags)
                self._start_tags = []
                tags.append((TAG_TIMESTAMP, pmt.from_uint64(block.timestamp)))
                tags.append((TAG_TIME, _rx_time(block.timestamp)))
                self._resume_pending = False
            self._queue.append(_Entry(block.samples, tags, block.timestamp,
                                      len(block.samples) // BYTES_PER_SAMPLE, time.monotonic()))
            self._queued_bytes += len(block.samples)
            self._match_locked()
            self._not_empty.notify()
        self._report_loss()
        self._refresh_properties()

    # output

    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)
        written = 0
        while written < n:
            if self._current is None:
                with self._lock:
                    if not self._ready_locked() and written == 0:
                        self._not_empty.wait(POP_TIMEOUT)
                    if not self._ready_locked():
                        break
                    entry = self._queue.popleft()
                    self._queued_bytes -= len(entry.samples)
                    self._pending_tags.extend(entry.tags)
                    self._marks = collections.deque(sorted(entry.marks, key=lambda mark: mark[0]))
                self._current = entry.samples
                self._offset = 0
            count = min((len(self._current) - self._offset) // BYTES_PER_SAMPLE, n - written)
            if count == 0:
                # Fewer than BYTES_PER_SAMPLE bytes remain: a partial sample is dropped.
                self._current = None
                continue
            raw = np.frombuffer(self._current, dtype="<i2", count=2 * count, offset=self._offset)
            if self._float:
                out[written:written + count] = (raw.astype(np.float32) * SCALE).view(np.complex64)
            else:
                out[written:written + count] = raw.reshape(count, 2)
            self._emit_tags(written)
            self._emit_marks(written, self._offset // BYTES_PER_SAMPLE, count)
            written += count
            self._offset += count * BYTES_PER_SAMPLE
            if self._offset >= len(self._current):
                self._current = None
        if written == 0:
            self._check_stall()
        return written

    def _ready_locked(self):
        """A queued block may leave once it has waited the decode delay, the window a decode has to arrive in."""
        if not self._queue:
            return False
        return not self._decode_delay or time.monotonic() - self._queue[0].arrived >= self._decode_delay

    def _emit_marks(self, written, start, count):
        """Tags the decodes of the samples [start, start + count) of the current block at their own position."""
        while self._marks and self._marks[0][0] < start + count:
            index, name, key, value = self._marks.popleft()
            self.add_item_tag(0, self.nitems_written(0) + written + max(index - start, 0), key, value)
            self._decode_tagged[name] += 1

    def _check_stall(self):
        """Warns when an open stream delivers nothing for STALL_SECONDS, again every STALL_REPORT."""
        if self._iter is None:
            return
        now = time.monotonic()
        silence = now - self._last_block
        if silence < STALL_SECONDS:
            return
        if self._stall_reported is not None and now - self._stall_reported < STALL_REPORT:
            return
        self._stall_reported = now
        self._log("warn", f"no samples from {self._host!r} for {silence:.1f} s")

    def _emit_tags(self, offset):
        with self._lock:
            tags, self._pending_tags = self._pending_tags, []
        for key, value in tags:
            self.add_item_tag(0, self.nitems_written(0) + offset, pmt.intern(key), value)

    # runtime control

    def _tunable_or_warn(self, what):
        if self._tunable:
            return True
        self._log("warn", f"{SETTING_NAMES[what]} ignored: channel is not tunable")
        return False

    def _apply(self, what, value, shown):
        """Calls GrxControl.<what> with value and reads it back with the matching getter.

        Returns the reported value, None after a logged error or on a fixed channel.
        """
        if not self._tunable_or_warn(what):
            return None
        try:
            getattr(self._control, what)(self._radio, value)
            return getattr(self._control, what.replace("set_", "get_", 1))(self._radio)
        except (GrxError, ValueError) as err:
            self._log("error", self._setting_error(SETTING_NAMES[what], shown, err))
            return None

    def _log_applied(self, name, requested, reported, fmt):
        if requested == reported:
            self._log("info", f"{name} {fmt(reported)}")
        else:
            self._log("warn", self._mismatch(name, requested, reported, fmt))

    def set_center_freq(self, hz):
        hz = int(hz)
        got = self._apply("set_center_freq", hz, _hz(hz))
        if got is None:
            return False
        self._center_freq = hz
        self._props = replace(self._props, center_freq=got)
        self._add_pending(TAG_FREQ, pmt.from_double(float(got)))
        self._log_applied("center frequency", hz, got, _hz)
        return True

    def set_gain(self, db):
        db = int(db)
        got = self._apply("set_gain", db, f"{db} dB")
        if got is None:
            return False
        self._gain = db
        self._log_applied("hardware gain", db, got, lambda value: f"{value} dB")
        return True

    def set_bandwidth(self, hz):
        hz = int(hz)
        if hz <= 0:
            self._log("warn", "analog bandwidth ignored: 0 means unchanged")
            return False
        got = self._apply("set_bandwidth", hz, _hz(hz))
        if got is None:
            return False
        self._bandwidth = hz
        self._log_applied("analog bandwidth", hz, got, _hz)
        return True

    def set_rx_port(self, name):
        if not self._tunable_or_warn("set_rx_port"):
            return False
        if name not in self._port_ids:
            self._log("error", f"RX input {name!r} not found, receiver offers {', '.join(self._port_labels.values())}")
            return False
        port_id = self._port_ids[name]
        got = self._apply("set_rx_port", port_id, name)
        if got is None:
            return False
        self._rx_port = name
        self._log_applied("RX input", port_id, got, self._port_name)
        return True

    def set_samp_rate(self, sps):
        sps = int(sps)
        if not self._tunable_or_warn("set_samp_rate"):
            return False
        was_running = self._running
        if was_running:
            self._halt_reader()
        try:
            self._control.set_samp_rate(self._radio, sps)
            self._props = replace(self._wait_for_rate(sps), center_freq=self._props.center_freq)
        except (GrxError, ValueError) as err:
            self._log("error", self._setting_error("sample rate", _sps(sps), err))
            if was_running:
                self._start_reader()
            return False
        self._adopt_properties()
        self._log("info", f"sample rate {_sps(self._props.samp_rate)}")
        if was_running:
            self._start_reader()
        return True

    def _wait_for_rate(self, sps):
        """The stream properties with the rate TunableChanneld reports, once the stream side carries it or after the timeout.

        TunableChanneld answers with the adopted rate at once. The stream properties follow on the
        receiver's own clock and only need to catch up before the stream restarts.
        """
        got = self._control.get_samp_rate(self._radio)
        if got != sps:
            self._log("warn", self._mismatch("sample rate", sps, got, _sps, "Sps"))
        deadline = time.monotonic() + self._timeout
        while True:
            props = self._stream.properties(self._radio)
            if props.samp_rate == got:
                break
            if time.monotonic() >= deadline:
                self._log("warn", f"stream properties report {_sps(props.samp_rate)}, expected {_sps(got)}")
                break
            time.sleep(RATE_POLL)
        return replace(props, samp_rate=got)

    def get_center_freq(self):
        return self._control.get_center_freq(self._radio) if self._tunable else self._props.center_freq

    def get_gain(self):
        return self._control.get_gain(self._radio) if self._tunable else 0

    def get_bandwidth(self):
        return self._control.get_bandwidth(self._radio) if self._tunable else 0

    def get_rx_port(self):
        return self._rx_port

    def get_samp_rate(self):
        return self._samp_rate

    def get_calibration_db(self):
        return self._props.calibration_db
