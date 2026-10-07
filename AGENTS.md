# AGENTS.md — 给在本仓库里干活的 Agent

本文件是**常驻上下文**，只放「看代码看不出来」的约定与验证义务。背景知识、用法、部署细节走指针。

## 这个仓库是什么

Windows 桌面应用（Python + PySide6）：读**本机已解密的微信 4.x 聊天库**当只读知识库，
指定联系人发新消息时弹窗给多条语气候选（附理由），支持多轮"提意见改写"，最终把文本写进剪贴板。
它**不注入微信、不自动发送**。

环境：Windows 上 `python -m venv .venv`，解释器是 `.\.venv\Scripts\python.exe`（POSIX 是 `.venv/bin/python`）。

## 两个门（改完必须跑，缺一不可）

```powershell
.\.venv\Scripts\python.exe -m pytest -q                                    # 期望：68 passed
.\.venv\Scripts\python.exe -m wxreply selftest                             # 期望：全部通过 ✅
```

再加一条静态检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check --select F,E9,B,UP006,UP035 wxreply tools tests run.py
```

判据：**三条都绿**才算这次改动完成。`pytest` 红了就去看 `tests/` 里对应模块的文件（见下表）。

## 不变量

新增/修改代码时守住这些；它们不是建议，是数据安全与合规边界：

- **微信数据只读**：打开源库与解密库一律 `mode=ro`（见 `kb/reader.py:_open_ro`、`kb/crypto.py`）。任何写操作只允许发生在用户指定的**解密输出目录**。
- **只写剪贴板**：用户可见的副作用止步于 `QGuiApplication.clipboard()`；候选文本不自动发送、不模拟按键、不操作微信窗口。
- **密钥与解密产物留在仓库外**：`keys.json`、解密目录、`history.jsonl` 只出现在用户机器（`%APPDATA%\wxreply` 或用户指定的目录）。提交前扫一遍，见下面"发布前"。
- **不合法就报错，不写垃圾**：解密失败时返回失败并让调用方跳过（`decrypt_file` 先验页 1 结构，`decrypt_wal` 验不过就删输出）。
- **诚实标注降级**：模型不可用/离线模板模式要在 UI 与 `ev.error` 里说清楚，不能假装是模型产出。

## 改哪里 → 跑哪个测试

| 你改了 | 必须绿的测试 |
| --- | --- |
| `kb/crypto.py`（解密/密钥/镜像） | `tests/test_crypto.py` |
| `kb/reader.py`（库结构、方向、群发言人） | `tests/test_reader.py` |
| `watcher.py`（轮询、游标、事件） | `tests/test_watcher.py` |
| `engine.py` / `prompts.py`（候选、改写） | `tests/test_engine.py` |
| `llm.py`（HTTP、参数兼容、JSON 兜底） | `tests/test_llm.py` |
| `config.py`（配置读写、运行时装配） | `tests/test_config.py` |
| `ui/*`（弹窗、卡片、主窗口） | `tests/test_ui_smoke.py`（离屏跑，真点按钮、真读剪贴板） |
| `__main__.py`（CLI 子命令） | 对应命令自己跑一遍 + `tests/test_crypto.py::test_keycheck_cli` 这类 CLI 测试 |
| `tools/preflight.py`（新增脱敏规则） | `tests/test_preflight.py`（含「这条规则真的抓得到」的反向自测） |

## 只有踩过才知道的坑

- **Qt 属性名**：`PopupWindow` 的事件对象叫 `reply_event`，**不能叫 `event`**——会覆盖 `QWidget.event()`，
  Qt 会在内部调用时抛 `TypeError`。同理别用 `close`/`layout`/`paintEvent` 之类做属性名。
- **打包入口必须是 `run.py`**：用 `wxreply/__main__.py` 当 PyInstaller 入口会 `attempted relative import with no known parent package`。
- **Windows 的 `console=False`**：冻结产物没有控制台，所以 `selftest` 在 Windows 冻结环境里会把报告弹成 MessageBox（`__main__.py:cmd_selftest` 末尾）。
- **密钥不能跨机**：mac_key 用库文件前 16 字节 salt 派生（`PBKDF2-HMAC-SHA512(key, salt^0x3a, 2轮)`），换机器/换库就要重新取。`keycheck` 就是用这个 HMAC 判真的。
- **只解密三类库**：`kb/crypto.py:_WANTED`（`contact.db` / `session.db` / `message_N.db`）。真机 `message/` 下还有 `media_*`、`biz_message_*`、`*_fts`，**有意跳过**；新增要解密的库就往 `_WANTED` 里加，并更新 `tests/test_crypto.py::test_only_relevant_dbs_are_decrypted`。
- **微信 4.x 的方向判定**：`real_sender_id` 是**该消息库 `Name2Id` 表的 rowid**，不是 `contact.id`；群消息正文前缀 `wxid_xxx:\n` 才是发言人。改动这块前先看 `docs/evidence/verification.md` 的实测结论。
- **`-wal` 是尽力而为**：实测真实库的 `-wal` 多是未提交残页（`dbsize=0`），主库就是权威来源。测试里用"真实 SQLite 写出的 WAL 帧"来验证解密正确性。
- **测试库没有真 HMAC**：`tests/sqlcipher_stub.py` 写的假库 HMAC 是占位，所以 `keycheck` 会报 `ok-structural` 而不是 `ok`。别把它当 bug 修。

## 常见任务的配方

→ [`docs/AGENT_TASKS.md`](docs/AGENT_TASKS.md)：加语气、改提示词、接新模型、加数据源、加 CLI 命令、改弹窗、打包、发布前检查。每个配方都带完成判据。

## 其他材料的指针

| 材料 | 什么时候去读 |
| --- | --- |
| [`docs/WINDOWS_SETUP_GUIDE.md`](docs/WINDOWS_SETUP_GUIDE.md) | 要**跑通真实数据**：定位微信目录、取/校验密钥、解密、配模型联系人、开监控、打包，逐步带自检 |
| [`README.md`](README.md) | 要给人类解释用法、看截图、看 CLI 与模块地图 |
| [`docs/evidence/verification.md`](docs/evidence/verification.md) | 想知道"哪条能力已经被真实验证过、证据在哪"，或改动涉及 4.4.x 库结构时 |
| `wxreply/prompts.py` | 调优候选质量时的唯一真源（中文提示词全在这里） |
| `.\.venv\Scripts\python.exe -m wxreply --help` | 想知道有哪些子命令（别在文档里抄命令清单） |
