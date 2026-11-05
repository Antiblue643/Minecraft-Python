
from __future__ import annotations

import ipaddress
import socket


def local_ip_addresses() -> list[str]:
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass
    result: list[str] = []
    for text in candidates:
        try:
            parsed = ipaddress.ip_address(text)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            continue
        if str(parsed) not in result:
            result.append(str(parsed))
    return result


def normalize_ip_text(address) -> str:
    raw = str(address or "").strip()
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    return str(parsed)


def strip_host_brackets(host) -> str:
    raw = str(host or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        return raw[1:-1]
    return raw


def format_host_port(host, port: int) -> str:
    raw = strip_host_brackets(host)
    normalized = normalize_ip_text(raw)
    try:
        parsed = ipaddress.ip_address(normalized)
    except ValueError:
        return f"{raw}:{int(port)}"
    if isinstance(parsed, ipaddress.IPv6Address):
        return f"[{parsed}]:{int(port)}"
    return f"{parsed}:{int(port)}"
