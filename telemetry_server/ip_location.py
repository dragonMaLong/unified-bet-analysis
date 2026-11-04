"""Best-effort, local-only IP location lookup for authenticated statistics pages."""
from __future__ import annotations

import ipaddress
from pathlib import Path


UNKNOWN_LOCATION = "未知"
MAX_LOOKUPS = 200
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


def _address_and_label(value):
    if not isinstance(value, str) or "%" in value:
        return None, UNKNOWN_LOCATION
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None, UNKNOWN_LOCATION
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_loopback:
        return None, "本机地址"
    if address.is_link_local:
        return None, "链路本地地址"
    if any(address in network for network in _PRIVATE_NETWORKS if address.version == network.version):
        return None, "内网地址"
    if address.is_multicast or not address.is_global:
        return None, "保留或特殊地址"
    return address, UNKNOWN_LOCATION


def _location_text(region):
    # Current ip2region v4/v6 databases contain Country|Province|City|ISP|ISO.
    if not isinstance(region, str) or len(region) > 1024:
        return UNKNOWN_LOCATION
    fields = region.split("|")
    if len(fields) != 5:
        return UNKNOWN_LOCATION
    parts = []
    for field in fields[:3]:
        field = field.strip()
        if field.casefold() in {"", "0", "unknown", "未知", "-"}:
            continue
        if any(ord(character) < 32 for character in field):
            return UNKNOWN_LOCATION
        if field not in parts:
            parts.append(field)
    return " · ".join(parts) or UNKNOWN_LOCATION


def _open_searcher(directory, family):
    # Lazy imports allow normal intake and admin access without the optional
    # database dependency. No package or data downloads occur during a request.
    from ip2region import searcher, util

    version = util.IPv4 if family == 4 else util.IPv6
    path = Path(directory) / f"ip2region_v{family}.xdb"
    # The library's *_from_file helpers do not close files on parse errors.
    # Keep validation and vector reads in our own context manager instead.
    with path.open("rb") as source:
        util.verify(source)
        header = util.load_header(source)
        if util.version_from_header(header) is not version:
            raise ValueError("IP database address family mismatch")
        vector = util.load_vector_index(source)
        if len(vector) != util.VectorIndexLength:
            raise ValueError("Incomplete IP database index")
    return searcher.new_with_vector_index(version, str(path), vector)


def enrich_ip_locations(rows, directory):
    """Add location labels in place, retaining no IP cache beyond this request.

    Open at most one Searcher per address family. Missing or damaged databases,
    missing dependencies and failed queries leave a useful page with unknown
    locations. This function is never part of accepting client event batches.
    """
    readers = {}
    labels = {}
    try:
        for index, row in enumerate(rows):
            row["location"] = UNKNOWN_LOCATION
            if index >= MAX_LOOKUPS:
                continue
            address, label = _address_and_label(row.get("ip"))
            if address is None:
                row["location"] = label
                continue
            if not directory:
                continue
            key = str(address)
            if key in labels:
                row["location"] = labels[key]
                continue
            family = address.version
            if family not in readers:
                try:
                    readers[family] = _open_searcher(directory, family)
                except Exception:
                    readers[family] = None
            reader = readers[family]
            if reader is not None:
                try:
                    label = _location_text(reader.search(key))
                except Exception:
                    label = UNKNOWN_LOCATION
            labels[key] = label
            row["location"] = label
    finally:
        for reader in readers.values():
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
