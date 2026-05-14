import pytest

from qtop import cli


class TestArgParsing:
    def test_user_and_all_are_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--user", "alice", "--all"])

    def test_all_resolves_to_star(self):
        args = cli.build_parser().parse_args(["--all"])
        assert cli.resolve_user(args) == "*"

    def test_user_explicit(self):
        args = cli.build_parser().parse_args(["--user", "alice"])
        assert cli.resolve_user(args) == "alice"

    def test_default_user_from_env(self, monkeypatch):
        monkeypatch.setenv("USER", "bob")
        args = cli.build_parser().parse_args([])
        assert cli.resolve_user(args) == "bob"

    def test_default_interval_is_ten(self):
        args = cli.build_parser().parse_args([])
        assert args.interval == 10.0

    def test_demo_flag_default_false(self):
        args = cli.build_parser().parse_args([])
        assert args.demo is False


class TestMain:
    def test_qstat_missing_exits_non_zero(self, monkeypatch, capsys):
        monkeypatch.setattr("qtop.cli.qstat_available", lambda: False)
        rc = cli.main(["--user", "alice"])
        assert rc == 1
        assert "qstat" in capsys.readouterr().err

    def test_demo_path_does_not_check_qstat(self, monkeypatch):
        """--demo should construct a DemoClient and not require qstat."""
        monkeypatch.setattr("qtop.cli.qstat_available", lambda: False)
        # Stub QtopApp so we don't actually launch the TUI in tests
        captured = {}

        class FakeApp:
            def __init__(self, **kw):
                captured.update(kw)
            def run(self):
                captured["ran"] = True

        # The app module imports lazily inside main(); patch via sys.modules
        import qtop.app
        monkeypatch.setattr(qtop.app, "QtopApp", FakeApp)
        rc = cli.main(["--demo", "--user", "alice"])
        assert rc == 0
        assert captured["ran"] is True
        assert captured["demo"] is True
        assert captured["user"] == "alice"
        from qtop.client import DemoClient
        assert isinstance(captured["client"], DemoClient)
