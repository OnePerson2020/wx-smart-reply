"""配置读写与运行时装配（不依赖图形界面）。"""
import json
from pathlib import Path

from make_demo_kb import GROUP, ZHANG
from wxreply.config import AppConfig, ContactBinding, LLMConfig
from wxreply.engine import ReplyEngine
from wxreply.watcher import build_runtime


def test_save_load_roundtrip(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path / "d"))
    cfg.llm = LLMConfig(provider="openai", base_url="http://x/v1", api_key="k", model="m",
                        temperature=0.5, max_tokens=999)
    cfg.contacts = [ContactBinding(label="联系人 1", username=ZHANG, enabled=True, note="同事"),
                    ContactBinding(label="联系人 2", username=GROUP, enabled=False,
                                   tones=[{"name": "稳", "hint": "稳"}])]
    cfg.decrypted_dir = "/tmp/kb"
    cfg.poll_interval_sec = 7.5
    p = cfg.save()

    back = AppConfig.load(p)
    assert back.llm.model == "m"
    assert back.llm.max_tokens == 999
    assert len(back.contacts) == 2
    assert back.contacts[0].note == "同事"
    assert back.contacts[1].tones[0]["name"] == "稳"
    assert back.decrypted_dir == "/tmp/kb"
    assert back.poll_interval_sec == 7.5
    assert back.enabled_contacts() == [back.contacts[0]]      # 未启用的/空 username 的不监控


def test_load_missing_file_gives_defaults(tmp_path: Path):
    cfg = AppConfig.load(tmp_path / "nope" / "config.json")
    assert cfg.contacts and cfg.llm.provider == "offline"
    assert (tmp_path / "nope").exists()


def test_load_tolerates_unknown_fields(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"contacts": [{"label": "x", "username": "y", "未来字段": 1}],
                             "llm": {"provider": "offline", "未知": 2}}), encoding="utf-8")
    cfg = AppConfig.load(p)
    assert cfg.contacts[0].label == "x"
    assert cfg.llm.provider == "offline"


def test_build_runtime_dir_mode(tmp_path: Path):
    from make_demo_kb import build
    kb = tmp_path / "kb"
    build(kb)
    cfg = AppConfig(data_dir=str(tmp_path / "d"), decrypted_dir=str(kb), source_mode="dir")
    reader, mirror, note = build_runtime(cfg)
    assert reader is not None and mirror is None
    assert "知识库就绪" in note
    assert ReplyEngine(cfg, reader).reader is reader


def test_build_runtime_without_dir_says_what_is_missing(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path / "d"), decrypted_dir="")
    reader, mirror, note = build_runtime(cfg)
    assert reader is None and "解密" in note or "目录" in note


def test_build_runtime_decrypt_mode(tmp_path: Path):
    from sqlcipher_stub import encrypt_db
    from make_demo_kb import build
    import os

    plain = tmp_path / "plain"
    build(plain)
    enc = tmp_path / "enc"
    key = bytes(range(32))
    for rel in ("contact/contact.db", "session/session.db", "message/message_0.db"):
        (enc / rel).parent.mkdir(parents=True, exist_ok=True)
        encrypt_db(plain / rel, enc / rel, key, os.urandom(16))
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({rel: {"enc_key": key.hex()}
                                for rel in ("contact/contact.db", "session/session.db",
                                            "message/message_0.db")}), encoding="utf-8")

    cfg = AppConfig(data_dir=str(tmp_path / "d"), source_mode="decrypt",
                    wechat_dir=str(enc), keys_file=str(keys), decrypted_dir=str(tmp_path / "dec"))
    reader, mirror, note = build_runtime(cfg)
    assert mirror is not None
    assert reader is not None and reader.stats()["chats"] == 3
    assert (tmp_path / "dec" / "message" / "message_0.db").exists()


def test_config_path_becomes_data_dir_when_absent(tmp_path: Path):
    """用 --config 指定配置文件时，记录/配置应落在同一个目录，而不是默认目录。"""
    custom = tmp_path / "somewhere" / "my.json"
    custom.parent.mkdir(parents=True)
    custom.write_text(json.dumps({"decrypted_dir": "/tmp/kb"}), encoding="utf-8")

    cfg = AppConfig.load(custom)
    assert cfg.data_dir == str(custom.parent)
    assert cfg.history_path() == custom.parent / "history.jsonl"
    assert cfg.save() == custom.parent / "config.json"
    assert json.loads(custom.read_text(encoding="utf-8"))["decrypted_dir"] == "/tmp/kb"


def test_enabled_contacts_dedupes_same_username(tmp_path: Path):
    cfg = AppConfig(data_dir=str(tmp_path / "d"))
    cfg.contacts = [ContactBinding(label="联系人 1", username=ZHANG, enabled=True),
                    ContactBinding(label="备用名", username=ZHANG, enabled=True),
                    ContactBinding(label="联系人 2", username=GROUP, enabled=False)]
    assert [c.label for c in cfg.enabled_contacts()] == ["联系人 1"]
