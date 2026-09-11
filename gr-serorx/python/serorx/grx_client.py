"""Wrappers over the GRX gRPC services TunableChanneld, Samplestreamingd and Monitord. No GNU Radio dependency."""
import errno
import socket
from dataclasses import dataclass

import grpc
from google.protobuf import empty_pb2

from .proto import (Common_pb2, Monitord_pb2_grpc, Samplestreamingd_pb2, Samplestreamingd_pb2_grpc,
                    TunableChanneld_pb2, TunableChanneld_pb2_grpc)

BANDS = {
    "tunable": Common_pb2.BAND_TUNABLE,
    "1030": Common_pb2.BAND_1030_MHZ,
    "1090": Common_pb2.BAND_1090_MHZ,
    "978": Common_pb2.BAND_978_MHZ,
}
BAND_NAMES = {value: name for name, value in BANDS.items()}
PROBE_TIMEOUT = 3.0
UNROUTABLE_ERRNOS = frozenset(getattr(errno, name) for name in (
    "EHOSTUNREACH", "ENETUNREACH", "ENETDOWN", "WSAEHOSTUNREACH", "WSAENETUNREACH", "WSAENETDOWN") if hasattr(errno, name))
MODEL_KEY = "receiver-os.device-type"
HARDWARE_KEY = "receiver-os.hardware-revision"
IMAGE_KEY = "receiver-os.image-version"


def radio_id(band, index=0):
    """RadioIdentification for a band name from BANDS and a per-band index."""
    if band not in BANDS:
        raise ValueError(f"Channel {band!r} not in {sorted(BANDS)}")
    return Common_pb2.RadioIdentification(band=BANDS[band], per_band_index=int(index))


def channel_label(band, index=0):
    """Console form of a channel: '1090 (index 0)'."""
    return f"{band} (index {int(index)})"


class GrxError(RuntimeError):
    """A failed RPC. code is the grpc.StatusCode, details the device message."""

    def __init__(self, endpoint, code, details):
        super().__init__(f"{endpoint}: {code.name}: {details}")
        self.endpoint = endpoint
        self.code = code
        self.details = details


def reachability(err, timeout=PROBE_TIMEOUT):
    """Classifies a GrxError that never reached the service.

    'timeout' (no answer within the deadline), 'refused' (host answers, port closed), 'unresolved'
    (name lookup failed), 'unroutable' (no route or network down), 'failed' (the port is open or the
    endpoint is malformed, and the RPC failed anyway). None when the service answered.

    An UNAVAILABLE error is classified by a plain TCP connection to the endpoint: gRPC words its socket
    errors in the language of the OS, the exception class and errno of the probe do not depend on it.
    """
    if err.code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return "timeout"
    if err.code != grpc.StatusCode.UNAVAILABLE:
        return None
    return _probe(err.endpoint, timeout)


def _probe(endpoint, timeout):
    host, _, port = endpoint.rpartition(":")
    if not host or not port.isdigit():
        return "failed"
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
    except socket.gaierror:
        return "unresolved"
    except ConnectionRefusedError:
        return "refused"
    except TimeoutError:
        return "timeout"
    except OSError as err:
        return "unroutable" if err.errno in UNROUTABLE_ERRNOS else "failed"
    return "failed"


@dataclass(frozen=True)
class StreamProperties:
    center_freq: int
    samp_rate: int
    calibration_db: float


@dataclass(frozen=True)
class SystemInfo:
    """Monitord.GetSystemInformation. model, hardware and image come from version_information, verbatim."""
    model: str
    hardware: str
    image: str
    serial: str
    versions: dict

    def describe(self):
        parts = [self.model or "model unknown", f"serial {self.serial or 'unknown'}"]
        if self.hardware:
            parts.append(f"hardware {self.hardware}")
        if self.image:
            parts.append(f"image {self.image}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Block:
    timestamp: int
    samples: bytes
    lost_blocks: int


class _Client:
    def __init__(self, host, port, timeout):
        self.endpoint = f"{host}:{port}"
        self.timeout = timeout
        self._channel = grpc.insecure_channel(self.endpoint)

    def _call(self, method, request):
        try:
            return method(request, timeout=self.timeout)
        except grpc.RpcError as err:
            raise GrxError(self.endpoint, err.code(), err.details()) from None

    def close(self):
        self._channel.close()


class GrxControl(_Client):
    """TunableChanneld, port 5309."""

    def __init__(self, host, port=5309, timeout=5.0):
        super().__init__(host, port, timeout)
        self._stub = TunableChanneld_pb2_grpc.TunableChanneldStub(self._channel)

    def channels(self):
        reply = self._call(self._stub.GetChannels, empty_pb2.Empty())
        return [(BAND_NAMES.get(c.band, str(c.band)), c.per_band_index) for c in reply.channels]

    def rx_ports(self, radio):
        reply = self._call(self._stub.GetRXPorts, TunableChanneld_pb2.GetRXPortsRequest(channel=radio))
        return dict(reply.rx_port_to_label)

    def get_rx_port(self, radio):
        return self._call(self._stub.GetRXPort, TunableChanneld_pb2.GetRXPortRequest(channel=radio)).rx_port

    def set_rx_port(self, radio, port_id):
        self._call(self._stub.SetRXPort, TunableChanneld_pb2.SetRXPortRequest(channel=radio, rx_port=int(port_id)))

    def get_center_freq(self, radio):
        return self._call(self._stub.GetCenterFrequency,
                          TunableChanneld_pb2.GetCenterFrequencyRequest(channel=radio)).center_frequency_hz

    def set_center_freq(self, radio, hz):
        self._call(self._stub.SetCenterFrequency,
                   TunableChanneld_pb2.SetCenterFrequencyRequest(channel=radio, center_frequency_hz=int(hz)))

    def get_bandwidth(self, radio):
        return self._call(self._stub.GetAnalogBandwidth,
                          TunableChanneld_pb2.GetAnalogBandwidthRequest(channel=radio)).analog_bandwidth_hz

    def set_bandwidth(self, radio, hz):
        self._call(self._stub.SetAnalogBandwidth,
                   TunableChanneld_pb2.SetAnalogBandwidthRequest(channel=radio, analog_bandwidth_hz=int(hz)))

    def get_samp_rate(self, radio):
        return self._call(self._stub.GetSampleRate,
                          TunableChanneld_pb2.GetSampleRateRequest(channel=radio)).sample_rate_sps

    def set_samp_rate(self, radio, sps):
        self._call(self._stub.SetSampleRate,
                   TunableChanneld_pb2.SetSampleRateRequest(channel=radio, sample_rate_sps=int(sps)))

    def get_gain(self, radio):
        return self._call(self._stub.GetHardwareGain,
                          TunableChanneld_pb2.GetHardwareGainRequest(channel=radio)).hardware_gain_db

    def set_gain(self, radio, db):
        self._call(self._stub.SetHardwareGain,
                   TunableChanneld_pb2.SetHardwareGainRequest(channel=radio, hardware_gain_db=int(db)))


class BlockIterator:
    """Iterates StartStream replies as Block. cancel() ends the RPC."""

    def __init__(self, call, endpoint):
        self._call = call
        self._endpoint = endpoint

    def __iter__(self):
        return self

    def __next__(self):
        try:
            reply = next(self._call)
        except grpc.RpcError as err:
            raise GrxError(self._endpoint, err.code(), err.details()) from None
        return Block(reply.block_timestamp, reply.samples, reply.lost_blocks)

    def cancel(self):
        self._call.cancel()


class GrxStream(_Client):
    """Samplestreamingd, port 5308."""

    def __init__(self, host, port=5308, timeout=5.0):
        super().__init__(host, port, timeout)
        self._stub = Samplestreamingd_pb2_grpc.SamplestreamingdStub(self._channel)

    def properties(self, radio):
        reply = self._call(self._stub.GetStreamProperties,
                           Samplestreamingd_pb2.GetStreamPropertiesRequest(radio_identification=radio))
        return StreamProperties(reply.center_frequency, reply.sample_rate, reply.calibration_value)

    def channels(self):
        """Every channel that streams, as (band name, per_band_index) in band order.

        Found by probing GetStreamProperties: each band from index 0 until INVALID_ARGUMENT.
        """
        found = []
        for name, band in sorted(BANDS.items(), key=lambda item: item[1]):
            index = 0
            while True:
                try:
                    self.properties(Common_pb2.RadioIdentification(band=band, per_band_index=index))
                except GrxError as err:
                    if err.code == grpc.StatusCode.INVALID_ARGUMENT:
                        break
                    raise
                found.append((name, index))
                index += 1
        return found

    def start(self, radio, requested_blocks=0):
        request = Samplestreamingd_pb2.StartStreamRequest(radio_identification=radio, requested_blocks=requested_blocks)
        return BlockIterator(self._stub.StartStream(request), self.endpoint)


class GrxMonitor(_Client):
    """Monitord, port 5305."""

    def __init__(self, host, port=5305, timeout=5.0):
        super().__init__(host, port, timeout)
        self._stub = Monitord_pb2_grpc.MonitordStub(self._channel)

    def system_info(self):
        reply = self._call(self._stub.GetSystemInformation, empty_pb2.Empty())
        versions = dict(reply.version_information)
        return SystemInfo(versions.get(MODEL_KEY, ""), versions.get(HARDWARE_KEY, ""), versions.get(IMAGE_KEY, ""),
                          reply.serial_number, versions)
