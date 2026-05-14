from qtop.models import JobState, format_bytes, parse_memory


class TestJobState:
    def test_known_codes_map(self):
        assert JobState.from_sge("r") is JobState.RUNNING
        assert JobState.from_sge("qw") is JobState.WAITING
        assert JobState.from_sge("Eqw") is JobState.ERROR
        assert JobState.from_sge("hqw") is JobState.HELD
        assert JobState.from_sge("t") is JobState.TRANSFERRING
        assert JobState.from_sge("s") is JobState.SUSPENDED
        assert JobState.from_sge("dr") is JobState.DELETING

    def test_unknown_code_returns_unknown_no_exception(self):
        assert JobState.from_sge("xxx") is JobState.UNKNOWN

    def test_empty_or_none(self):
        assert JobState.from_sge(None) is JobState.UNKNOWN
        assert JobState.from_sge("") is JobState.UNKNOWN


class TestParseMemory:
    def test_gigabyte_decimal(self):
        # 8.5 GiB exactly in 1024-base
        assert parse_memory("8.500G") == int(8.5 * 1024 ** 3)

    def test_megabyte(self):
        assert parse_memory("512M") == 512 * 1024 ** 2

    def test_kilobyte(self):
        assert parse_memory("1024K") == 1024 * 1024

    def test_binary_suffix_with_i(self):
        # "8Gi" and "8G" should be the same in SGE-land
        assert parse_memory("8Gi") == parse_memory("8G")

    def test_na_returns_none(self):
        assert parse_memory("N/A") is None
        assert parse_memory("NONE") is None
        assert parse_memory("-") is None
        assert parse_memory("") is None
        assert parse_memory(None) is None

    def test_bare_int_is_bytes(self):
        assert parse_memory("4096") == 4096
        assert parse_memory(4096) == 4096

    def test_unparseable_returns_none(self):
        assert parse_memory("hello") is None


class TestFormatBytes:
    def test_roundtrip_ish(self):
        assert format_bytes(8 * 1024 ** 3) == "8.0G"
        assert format_bytes(512 * 1024 ** 2) == "512.0M"
        assert format_bytes(1024) == "1.0K"
        assert format_bytes(None) == "-"
