import importlib.util
import ipaddress
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from telemetry_server.ip_location import _location_text, enrich_ip_locations


class IPLocationTests(unittest.TestCase):
    def test_local_and_special_addresses_do_not_open_databases(self):
        examples = {
            "10.1.2.3": "内网地址", "172.16.0.2": "内网地址",
            "192.168.1.1": "内网地址", "fc00::10": "内网地址",
            "127.0.0.1": "本机地址", "::1": "本机地址",
            "::ffff:192.168.0.1": "内网地址",
            "169.254.1.1": "链路本地地址", "fe80::1": "链路本地地址",
            "203.0.113.7": "保留或特殊地址", "2001:db8::1": "保留或特殊地址",
            "100.64.0.1": "保留或特殊地址", "224.0.0.1": "保留或特殊地址",
            "ff02::1": "保留或特殊地址", "0.0.0.0": "保留或特殊地址",
            "not-an-ip": "未知", "fe80::1%eth0": "未知", None: "未知",
        }
        rows = [{"ip": ip} for ip in examples]
        with patch("telemetry_server.ip_location._open_searcher") as open_reader:
            enrich_ip_locations(rows, "unused")
            open_reader.assert_not_called()
        self.assertEqual([row["location"] for row in rows], list(examples.values()))

    def test_locations_omit_provider_placeholder_and_repeated_city(self):
        cases = {
            "中国|上海|上海|电信|CN": "中国 · 上海",
            "中国|江苏省|南京市|联通|CN": "中国 · 江苏省 · 南京市",
            "美国|0|0|Example ISP|US": "美国",
            "0|0|0|Example ISP|0": "未知",
            "中国| unknown |未知|Example ISP|CN": "中国",
            "中国|江苏|南京\n|Example ISP|CN": "中国 · 江苏 · 南京",
            "中国|江\n苏|南京|Example ISP|CN": "未知",
            "unexpected": "未知",
            "": "未知",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_location_text(raw), expected)

    def test_one_reader_per_family_with_request_scoped_deduplication(self):
        v4 = Mock()
        v4.search.return_value = "中国|上海|上海|电信|CN"
        v6 = Mock()
        v6.search.return_value = "美国|加利福尼亚|圣何塞|Example ISP|US"
        rows = [{"ip": "1.2.3.4", "version": "1.3.0"},
                {"ip": "::ffff:1.2.3.4", "version": "1.2.2"},
                {"ip": "2606:4700:4700::1111"}, {"ip": "8.8.8.8"}]
        with patch("telemetry_server.ip_location._open_searcher", side_effect=[v4, v6]) as open_reader:
            enrich_ip_locations(rows, "database-dir")
        self.assertEqual(open_reader.call_args_list[0].args, ("database-dir", 4))
        self.assertEqual(open_reader.call_args_list[1].args, ("database-dir", 6))
        self.assertEqual(v4.search.call_count, 2)
        v6.search.assert_called_once_with("2606:4700:4700::1111")
        v4.close.assert_called_once()
        v6.close.assert_called_once()
        self.assertEqual(rows[0]["location"], "中国 · 上海")
        self.assertEqual(rows[1]["location"], rows[0]["location"])
        self.assertEqual(rows[2]["location"], "美国 · 加利福尼亚 · 圣何塞")
        self.assertEqual(rows[1]["ip"], "::ffff:1.2.3.4")
        self.assertEqual(rows[1]["version"], "1.2.2")

    def test_missing_or_damaged_database_falls_back_once_per_family(self):
        for failure in (FileNotFoundError(), ValueError(), ImportError(), PermissionError()):
            with self.subTest(failure=type(failure).__name__):
                rows = [{"ip": "1.2.3.4"}, {"ip": "8.8.8.8"}, {"ip": "2606:4700:4700::1111"}]
                with patch("telemetry_server.ip_location._open_searcher", side_effect=failure) as open_reader:
                    enrich_ip_locations(rows, "unavailable")
                self.assertEqual(open_reader.call_count, 2)
                self.assertTrue(all(row["location"] == "未知" for row in rows))

    def test_query_and_close_failure_do_not_break_other_locations(self):
        reader = Mock()
        reader.search.side_effect = [UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid"), "中国|0|0|0|CN"]
        reader.close.side_effect = OSError()
        rows = [{"ip": "1.2.3.4"}, {"ip": "8.8.8.8"}]
        with patch("telemetry_server.ip_location._open_searcher", return_value=reader):
            enrich_ip_locations(rows, "database-dir")
        self.assertEqual([row["location"] for row in rows], ["未知", "中国"])
        reader.close.assert_called_once()

    def test_ip_cache_does_not_survive_a_request(self):
        readers = [Mock(), Mock()]
        readers[0].search.return_value = "中国|上海|上海|0|CN"
        readers[1].search.return_value = "中国|北京|北京|0|CN"
        rows = [{"ip": "1.2.3.4"}]
        with patch("telemetry_server.ip_location._open_searcher", side_effect=readers) as open_reader:
            enrich_ip_locations(rows, "database-dir")
            self.assertEqual(rows[0]["location"], "中国 · 上海")
            enrich_ip_locations(rows, "database-dir")
            self.assertEqual(rows[0]["location"], "中国 · 北京")
            self.assertEqual(open_reader.call_count, 2)

    def test_lookup_budget_is_bounded(self):
        reader = Mock()
        reader.search.return_value = "中国|0|0|0|CN"
        rows = [{"ip": str(ipaddress.IPv4Address(int(ipaddress.IPv4Address("1.0.0.1")) + i))}
                for i in range(205)]
        with patch("telemetry_server.ip_location._open_searcher", return_value=reader):
            enrich_ip_locations(rows, "database-dir")
        self.assertEqual(reader.search.call_count, 200)
        self.assertEqual(rows[199]["location"], "中国")
        self.assertEqual(rows[200]["location"], "未知")

    def test_dependency_absent_and_directory_unconfigured_are_safe(self):
        with patch.dict(sys.modules, {"ip2region": None}):
            rows = [{"ip": "1.2.3.4"}]
            enrich_ip_locations(rows, "database-dir")
            self.assertEqual(rows[0]["location"], "未知")
        with patch("telemetry_server.ip_location._open_searcher") as open_reader:
            enrich_ip_locations(rows, None)
            self.assertEqual(rows[0]["location"], "未知")
            open_reader.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("ip2region"), "optional py-ip2region dependency is not installed")
class IPLocationDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def write_database(self, family, region):
        # A small XDB 3.0 fixture: one inclusive address range and all vector
        # entries pointing to it. Test the real library, without real user IPs.
        raw_region = region.encode("utf8")
        data_offset = 256 + 524288
        index_offset = data_offset + len(raw_region)
        header = struct.pack("<HHIIIHH", 3, 0, 0, index_offset, index_offset, family, 4)
        header += b"\0" * (256 - len(header))
        vector = struct.pack("<II", index_offset, index_offset) * 65536
        address_length = 4 if family == 4 else 16
        entry = b"\0" * address_length + b"\xff" * address_length + struct.pack("<HI", len(raw_region), data_offset)
        path = self.directory / f"ip2region_v{family}.xdb"
        path.write_bytes(header + vector + raw_region + entry)
        return path

    def test_real_library_reads_both_address_families_and_releases_files(self):
        v4 = self.write_database(4, "中国|上海|上海|Example ISP|CN")
        v6 = self.write_database(6, "美国|0|0|Example ISP|US")
        rows = [{"ip": "1.2.3.4"}, {"ip": "2606:4700:4700::1111"}]
        enrich_ip_locations(rows, self.directory)
        self.assertEqual([row["location"] for row in rows], ["中国 · 上海", "美国"])
        # On Windows these unlinks also catch leaked handles.
        v4.unlink()
        v6.unlink()

    def test_truncated_and_wrong_family_databases_are_unknown_and_closed(self):
        path = self.directory / "ip2region_v4.xdb"
        for contents in (b"broken", b"\0" * 256):
            path.write_bytes(contents)
            rows = [{"ip": "1.2.3.4"}]
            enrich_ip_locations(rows, self.directory)
            self.assertEqual(rows[0]["location"], "未知")
            path.unlink()
        self.write_database(6, "美国|0|0|Example ISP|US").rename(path)
        enrich_ip_locations(rows, self.directory)
        self.assertEqual(rows[0]["location"], "未知")
        path.unlink()


if __name__ == "__main__":
    unittest.main()
