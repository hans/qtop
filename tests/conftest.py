from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def load_fixture(fixtures_dir):
    def _load(name: str) -> str:
        return (fixtures_dir / name).read_text()
    return _load
