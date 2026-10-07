import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from make_demo_kb import ZHANG, build                          # noqa: E402

from wxreply.config import AppConfig, ContactBinding, LLMConfig  # noqa: E402


@pytest.fixture()
def demo_kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    build(root)
    return root


@pytest.fixture()
def cfg(tmp_path: Path) -> AppConfig:
    c = AppConfig(data_dir=str(tmp_path / "data"))
    c.llm = LLMConfig(provider="offline")
    c.contacts = [ContactBinding(label="联系人 1", username=ZHANG, enabled=True)]
    return c


@pytest.fixture()
def reader(demo_kb: Path):
    from wxreply.kb import KBReader
    return KBReader(demo_kb)
