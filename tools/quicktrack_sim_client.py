#!/usr/bin/env python3
# Copyright (c) 2020 Wi-Fi Alliance
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# UDP client that mimics the QuickTrack test tool control plane to probe a DUT
# running this repository's control app. See --help for safety warnings.

"""Simulate QuickTrack tool: send QuickTrack API over UDP, read ACK + RESPONSE."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Commands.shared_enums import QuickTrackResponseTLV  # noqa: E402
from quicktrack_api_message.quicktrack_api_message import (  # noqa: E402
    QuickTrackAPIMessage,
    QuickTrackMessageType,
)

# Low-risk probes: empty TLV payload on DUT (see Commands/shared_commands.py).
_SAFE_MESSAGE_TYPES = (
    QuickTrackMessageType.GET_CONTROL_APP_VERSION,
    QuickTrackMessageType.GET_IP_ADDR,
    QuickTrackMessageType.GET_MAC_ADDR,
)

_EXCLUDED_FROM_ALL = frozenset(
    {QuickTrackMessageType.CMD_ACK, QuickTrackMessageType.CMD_RESPONSE}
)


def _tlv_value(params: Optional[Dict[Any, Any]], key: Any) -> Optional[str]:
    if not params:
        return None
    v = params.get(key)
    if v is None:
        return None
    if isinstance(v, list):
        v = v[-1]
    return str(v).strip() if v is not None else None


def _decode_datagram(data: bytes) -> QuickTrackAPIMessage:
    msg = QuickTrackAPIMessage()
    msg.decode_bytes(data)
    return msg


def send_and_receive(
    host: str,
    port: int,
    message_type: QuickTrackMessageType,
    message_id: int,
    message_params: Optional[Dict[Any, str]] = None,
    timeout_sec: float = 5.0,
) -> Tuple[QuickTrackAPIMessage, QuickTrackAPIMessage]:
    import socket

    payload = QuickTrackAPIMessage(message_type, message_params or {})
    payload.set_message_id(message_id)
    raw = bytes(payload.get_message_bytes())

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_sec)
    try:
        sock.sendto(raw, (host, port))
        ack_data, _ = sock.recvfrom(4096)
        resp_data, _ = sock.recvfrom(4096)
    finally:
        sock.close()

    ack = _decode_datagram(ack_data)
    resp = _decode_datagram(resp_data)

    if ack.message_type != QuickTrackMessageType.CMD_ACK:
        raise RuntimeError(
            f"Expected CMD_ACK, got {ack.message_type!r} (id={ack.message_id})"
        )
    if resp.message_type != QuickTrackMessageType.CMD_RESPONSE:
        raise RuntimeError(
            f"Expected CMD_RESPONSE, got {resp.message_type!r} (id={resp.message_id})"
        )
    if ack.message_id != message_id or resp.message_id != message_id:
        raise RuntimeError(
            f"message_id mismatch: sent {message_id}, ack={ack.message_id} resp={resp.message_id}"
        )
    return ack, resp


def _summarize_response(resp: QuickTrackAPIMessage) -> Tuple[Optional[str], Optional[str]]:
    params = resp.message_params or {}
    return (
        _tlv_value(params, QuickTrackResponseTLV.STATUS),
        _tlv_value(params, QuickTrackResponseTLV.MESSAGE),
    )


def _print_row(
    name: str,
    type_hex: str,
    ack_ok: bool,
    status: Optional[str],
    message: Optional[str],
) -> None:
    msg_short = (message or "").replace("\n", " ")
    if len(msg_short) > 120:
        msg_short = msg_short[:117] + "..."
    print(
        f"{name}\t{type_hex}\t{ack_ok}\t{status}\t{msg_short}",
    )


def _all_probe_message_types() -> Tuple[QuickTrackMessageType, ...]:
    return tuple(
        m
        for m in QuickTrackMessageType
        if m not in _EXCLUDED_FROM_ALL
    )


def _parse_command_name(name: str) -> QuickTrackMessageType:
    try:
        return QuickTrackMessageType[name]
    except KeyError:
        valid = ", ".join(sorted(m.name for m in _all_probe_message_types()))
        raise SystemExit(f"Unknown command {name!r}. Valid names include:\n{valid}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Send QuickTrack binary API over UDP (like the official test tool). "
            "The DUT must run app.py with matching --ip/--port. "
            "WARNING: commands other than --safe-probe can change Wi-Fi/AP/client state."
        )
    )
    parser.add_argument("--host", required=True, help="DUT IP address")
    parser.add_argument("--port", type=int, default=9004, help="UDP port (default 9004)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Socket timeout in seconds for each receive (default 5)",
    )
    mx = parser.add_mutually_exclusive_group()
    mx.add_argument(
        "--safe-probe",
        action="store_true",
        help="Only GET_CONTROL_APP_VERSION, GET_IP_ADDR, GET_MAC_ADDR (default if no other mode)",
    )
    mx.add_argument(
        "--command",
        metavar="NAME",
        help="Single QuickTrackMessageType name, e.g. GET_CONTROL_APP_VERSION",
    )
    mx.add_argument(
        "--all-commands",
        action="store_true",
        help="Send every command type (DANGEROUS); requires --i-understand-risk",
    )
    parser.add_argument(
        "--i-understand-risk",
        action="store_true",
        help="Acknowledge that --all-commands may disrupt the DUT",
    )
    args = parser.parse_args()

    if args.all_commands and not args.i_understand_risk:
        parser.error("--all-commands requires --i-understand-risk")

    if args.all_commands:
        to_run = list(_all_probe_message_types())
    elif args.command is not None:
        to_run = [_parse_command_name(args.command)]
    else:
        # Default and --safe-probe: low-risk probes only
        to_run = list(_SAFE_MESSAGE_TYPES)

    print("name\ttype_hex\tack_ok\tstatus\tmessage")
    overall_ok = True
    for i, mt in enumerate(to_run, start=1):
        try:
            ack, resp = send_and_receive(
                args.host,
                args.port,
                mt,
                message_id=i,
                message_params={},
                timeout_sec=args.timeout,
            )
            status, message = _summarize_response(resp)
            ack_ok = ack.message_type == QuickTrackMessageType.CMD_ACK
            if status is not None and status != "0":
                overall_ok = False
            _print_row(
                mt.name,
                f"0x{mt.value:04X}",
                ack_ok,
                status,
                message,
            )
        except Exception as ex:  # noqa: BLE001 — CLI tool reports all failures
            overall_ok = False
            _print_row(mt.name, f"0x{mt.value:04X}", False, None, f"ERROR: {ex}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
