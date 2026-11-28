import errno
import socket
import struct

import pytest

from owned_tcp_diag import HEADER, NO_COOKIE, SocketIdentity, assess, decode_reply, exact_request, observe


IDENTITY = SocketIdentity(19, "127.0.0.1", 8000, "127.0.0.2", 42195, 0x1234567812345678, 975)


def response(**changes):
    return {
        "family": socket.AF_INET, "state": "ESTABLISHED", "local_address": IDENTITY.local_address,
        "local_port": IDENTITY.local_port, "peer_address": IDENTITY.peer_address, "peer_port": IDENTITY.peer_port,
        "cookie": IDENTITY.cookie, "inode": IDENTITY.inode, "interface": 0, "expires_ms": 2000,
        "raw_receive_queue": 7, "raw_send_queue": 11, "skmem": {"rmem_alloc": 101}, "meminfo": None,
        **changes,
    }


def call(*replies):
    queue = iter(replies)
    cookies = []

    def read(identity, cookie):
        assert identity == IDENTITY
        cookies.append(cookie)
        reply = next(queue)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    clock = iter((100, 101))
    return observe(read, IDENTITY, now=lambda: next(clock)), cookies


def test_only_exact_non_dump_wire_operation():
    packet = exact_request(IDENTITY, sequence=65, cookie=IDENTITY.cookie)
    assert HEADER.unpack_from(packet) == (72, 20, 1, 65, 0)
    assert struct.unpack_from("!HH", packet, 24) == (IDENTITY.local_port, IDENTITY.peer_port)
    assert struct.unpack_from("=II", packet, 64) == (0x12345678, 0x12345678)


@pytest.mark.parametrize(
    "reply,status",
    [
        ({"state": "LISTEN", "peer_port": 0}, "listener_reply"),
        ({"peer_port": 42196}, "different_tuple"),
        ({"cookie": 777}, "identity_changed"),
        ({"cookie": 777, "state": "TIME_WAIT", "inode": 0}, "time_wait_identity_changed"),
        ({"inode": 957}, "identity_uncertain"),
        ({}, "identity_uncertain"),
    ],
)
def test_enoent_of_cookie_is_not_absence_when_tuple_still_answers(reply, status):
    row, cookies = call(OSError(errno.ENOENT, ""), response(**reply))
    assert row["status"] == status
    assert "skmem_bytes" not in row and "send_queue_bytes" not in row
    assert cookies == [IDENTITY.cookie, NO_COOKIE]


def test_two_exact_enoent_responses_are_a_sample_only():
    row, cookies = call(OSError(errno.ENOENT, ""), OSError(errno.ENOENT, ""))
    assert row["status"] == "absent"
    assert cookies == [IDENTITY.cookie, NO_COOKIE]
    assert "send_queue_bytes" not in row


def test_access_error_on_tuple_cross_check_is_preserved():
    row, _ = call(OSError(errno.ENOENT, ""), PermissionError(errno.EPERM, ""))
    assert row["status"] == "access_error"
    assert row["errno"] == errno.EPERM
    assert row["cookie_errno"] == errno.ENOENT


def test_time_wait_wire_zero_fields_are_never_published_as_buffer_measurements():
    packet = exact_request(IDENTITY, sequence=8, cookie=IDENTITY.cookie)
    # inet_diag_msg starts with the four-byte family/state/timer/retrans header.
    msg = bytes((socket.AF_INET, 6, 3, 0)) + packet[24:72] + struct.pack("=IIIII", 42000, 0, 0, 0, 0)
    reply = HEADER.pack(len(msg) + HEADER.size, 20, 0, 8, 0) + msg
    row, cookies = call(decode_reply(reply, 8))
    assert row["status"] == "time_wait" and row["expiry_ms"] == 42000
    assert row["send_queue_bytes"] is None and row["receive_queue_bytes"] is None
    assert row["skmem_bytes"] is None and row["meminfo_bytes"] is None
    assert cookies == [IDENTITY.cookie]


def test_live_positive_exposes_present_values_and_unsupported_memory():
    row, _ = call(response(skmem=None))
    assert row["status"] == "present"
    assert row["send_queue_bytes"] == 11 and row["receive_queue_bytes"] == 7
    assert row["skmem_bytes"] is None


def test_fd_detached_exact_fin_socket_remains_owned():
    row, _ = call(response(state="FIN_WAIT_2", inode=0))
    assert row["state"] == "FIN_WAIT_2" and row["status"] == "present" and row["fd_association"] == "detached"
    assert call(response(state="FIN_WAIT_2", inode=850))[0]["status"] == "identity_uncertain"
    assert call(response(inode=0))[0]["status"] == "identity_uncertain"
    assert call(response(state="FIN_WAIT_2", inode=0, cookie=123))[0]["status"] == "identity_changed"


def test_distinct_kernel_accounting_units_are_preserved_without_adding():
    row, _ = call(response(skmem={"rmem_alloc": 0, "rcvbuf": 262144, "sndbuf": 16384, "drops": 3, "wmem_queued": 10}))
    assert row["skmem_bytes"] == {"rmem_alloc": 0, "wmem_queued": 10}
    assert row["skmem_capacity_bytes"] == {"rcvbuf": 262144, "sndbuf": 16384}
    assert row["skmem_drops_packets"] == 3
    control, _ = call(response(state="TIME_WAIT", inode=0, skmem={"sndbuf": 0, "drops": 0}))
    assert control["skmem_capacity_bytes"] is None and control["skmem_drops_packets"] is None


def sample(status, start, end=None):
    return {"status": status, "start_ns": start, "end_ns": start + 1 if end is None else end}


ORIGIN = {"lower_ns": 30, "upper_ns": 50, "kind": "same-clock-client-transition"}
BUDGET = 1


def test_absence_needs_same_generation_positive_control_and_deadline_confirmation():
    pre = sample("present", 10)
    gone = sample("absent", 75)
    late = sample("absent", 1_000_060)
    assert assess([gone, late], ORIGIN, BUDGET)["status"] == "positive_control_unavailable"
    assert assess([pre, gone], ORIGIN, BUDGET)["status"] == "deadline_observation_inconclusive"
    observed = assess([pre, gone, late], ORIGIN, BUDGET)
    assert observed["status"] == "absent_observed_within_window"
    assert observed["first_absent_after_origin"] == gone


def test_at_deadline_time_wait_is_retained_control_block():
    samples = [sample("present", 10), sample("time_wait", 200), sample("time_wait", 1_000_100)]
    assert assess(samples, ORIGIN, BUDGET)["status"] == "control_block_at_or_after_deadline"


@pytest.mark.parametrize("status", ["identity_changed", "access_error", "listener_reply", "time_wait_identity_changed"])
def test_failure_in_window_cannot_be_promoted_even_after_absence(status):
    samples = [sample("present", 10), sample(status, 70), sample("absent", 90), sample("absent", 1_000_100)]
    observed = assess(samples, ORIGIN, BUDGET)
    assert observed["status"] == "observation_uncertain"
    assert observed["failure_kinds_in_window"] == [status]


def test_uncertain_clock_crossing_prevents_before_deadline_inference():
    samples = [sample("present", 10), sample("absent", 1_000_020, 1_000_040), sample("absent", 1_000_100)]
    assert assess(samples, ORIGIN, BUDGET)["status"] == "deadline_observation_inconclusive"
