# Agent 任务配方

每个配方 = 改哪些文件 → 怎么验 → **完成判据**（不满足就是没做完）。
命令都是 Windows/PowerShell 形式，POSIX 把 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

前置：`python -m venv .venv` 且 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。

---

## 1. 先把项目跑起来（任何任务的第一步）

```powershell
.\.venv\Scripts\python.exe -m wxreply selftest
```

它自建一份虚拟聊天库（张三/李四，不含真实数据），检查依赖、知识库读取、生成引擎、界面四项。

**完成判据**：输出包含 `全部通过 ✅`。没有就停下修环境，别继续。

真实数据（可选，需要你自己的解密产物）：

```powershell
.\.venv\Scripts\python.exe -m wxreply check --kb "<含 contact/session/message 的目录>"
```

**完成判据**：打印出 `N 个会话 / M 个联系人 / 自己=wxid_xxx`，且 `N > 0`。

---

## 2. 加/改一个语气档位

触点（三处，缺一会出现"界面有、提示词没有"）：

1. `wxreply/config.py` → `DEFAULT_TONES`（默认四档在这里）；
2. `wxreply/prompts.py` → `build_generate_prompt` 把语气清单写进提示词（读的是传进来的 tones，通常不用改）；
3. `wxreply/engine.py` → `_tones()` 决定用联系人自定义还是全局默认。

验：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_ui_smoke.py -q
.\.venv\Scripts\python.exe -m wxreply once --kb "<演示库>" --chat 张三
```

**完成判据**：`pytest` 绿，且 `once` 输出的候选里能看到新语气名；`tests/test_ui_smoke.py` 里断言语气顺序的用例（`tones[:3] == [...]`）已同步更新。

---

## 3. 改提示词（提升候选质量）

唯一真源：`wxreply/prompts.py`（`SYSTEM` / `REFINE_SYSTEM` / `build_generate_prompt` / `build_refine_prompt`）。
改之前先想清楚要哪种行为，`SYSTEM` 里的编号规则就是产出契约（几条、要不要理由、长度）。

验（真模型，最能看出效果）：

```powershell
.\.venv\Scripts\python.exe -m wxreply --config "<你的配置>" once --kb "<演示库>" --chat 张三 `
  --text "对了，评审材料我还没写完，第3页的指标数据要不你先帮我讲一下？我晚上补上" `
  --refine "别催他，轻松一点，顺便问下他周末有没有空"
```

**完成判据**：四条候选语气明显不同、每条都有理由；`--refine` 之后的文本确实按意见改了（不是原文）。
再跑 `pytest tests/test_engine.py tests/test_llm.py -q` 确认解析逻辑没被破坏（JSON 契约：`{"intent","candidates":[{"tone","text","reason","risk"}]}`）。

---

## 4. 接一个新模型 / 修参数兼容

触点：`wxreply/llm.py`（`ChatLLM.chat`）、`wxreply/config.py:LLMConfig`。

已知兼容降级逻辑（改这块别退回去）：模型 400 抱怨 `temperature` → 去掉该字段重试；
抱怨 `max_tokens` → 换 `max_completion_tokens`；`127.0.0.1`/`localhost` 不要求 API Key。

验：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_llm.py -q
.\.venv\Scripts\python.exe -m wxreply --config "<配置>" selftest   # 只是为了跑起来
# 界面「模型」页点「测试连接」也可以
```

**完成判据**：`tests/test_llm.py` 绿（含 `test_drops_temperature_when_model_rejects_it`、`test_renames_max_tokens_when_rejected`）；
新增兼容分支时补一个同样的 fake-httpx 用例，否则这条降级以后没人守。
带思考的模型把 `max_tokens` 调到 3000–4000（默认 2048），否则正文可能为空——这个经验写进了 README。

---

## 5. 加一个数据源（第三种 source_mode）

触点（四处）：

1. `wxreply/kb/` 新增或扩展一个 reader（接口对齐 `KBReader`：`contacts`/`resolve`/`messages`/`new_messages`/`latest_ts`）；
2. `wxreply/config.py` → `source_mode` 的取值与相关字段；
3. `wxreply/watcher.py` → `build_runtime()` 按模式装配；
4. `wxreply/ui/main_window.py` → 监控页下拉项 + `_apply()` 的取值。

验：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_watcher.py tests/test_reader.py -q
```

**完成判据**：`tests/test_config.py::test_build_runtime_*` 里有一个覆盖新模式的用例（照 `test_build_runtime_decrypt_mode` 写），
并且 `tests/test_watcher.py` 在新数据源下仍能"首次不倒放历史、新消息只触发一次"。

---

## 6. 加一条 CLI 子命令

触点：`wxreply/__main__.py`（新增 `cmd_xxx` + `sub.add_parser`），涉及 UI 的不要放进 CLI。

验：

```powershell
.\.venv\Scripts\python.exe -m wxreply --help
.\.venv\Scripts\python.exe -m wxreply <新命令> --help
.\.venv\Scripts\python.exe -m wxreply <新命令>   # 至少跑一次正常路径和一次失败路径
```

**完成判据**：`--help` 里能看到；正常路径退出码 0；缺参数/坏输入给出可行动的提示（照 `cmd_keycheck` 的三种原因那段的写法），
不要把异常栈丢给用户。若这条命令属于"装完就该体检"的范畴，顺手加进 `selftest`。

---

## 7. 改弹窗与候选卡交互

触点：`wxreply/ui/popup.py`（`PopupWindow` / `CandidateCard`）、`wxreply/ui/app.py`（`Controller`：跨线程用 `_inbox`/`_outbox` + QTimer，别在子线程碰 Qt 控件）。

验：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_smoke.py -q
```

**完成判据**：`tests/test_ui_smoke.py` 全绿——它离屏跑真界面，**真点** 复制/提意见/换一批/最终复制，并断言剪贴板内容与改写历史；
改了卡片的可见元素就同步更新断言，别只改代码。截图会被 `test_screenshots_are_saved` 重新写到 `docs/evidence/`，提交时一起带上。

---

## 8. 打包 Windows exe

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
```

脚本顺序：建 venv → 装依赖 → `pytest` → `make_icon.py` → `selftest` → PyInstaller（读 `wxreply.spec`）。

**完成判据**：`dist\WxReply\WxReply.exe` 存在，并且

```powershell
.\dist\WxReply\WxReply.exe selftest
.\dist\WxReply\WxReply.exe check --kb "<目录>"
```

前者弹窗/打印 `全部通过 ✅`，后者能列出会话数。
**已知**：`wxreply.spec` 的入口必须是 `run.py`；`console=False` 时自检结果走 MessageBox。PyInstaller 不能交叉编译，`.exe` 只能在 Windows 上产出。

---

## 9. 发布前检查（改完要 push 的时候）

```powershell
# 1) 四个门：测试 / 自检 / 静态检查 / 脱敏扫描
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m wxreply selftest
.\.venv\Scripts\python.exe -m ruff check --select F,E9,B,UP006,UP035 wxreply tools tests run.py
.\.venv\Scripts\python.exe tools\preflight.py     # 本机路径/真实 wxid/密钥/数据库文件/虚拟环境

# 2) 再看一眼有没有该忽略却没忽略的东西
git status --short
```

> 扫描用 `tools/preflight.py` 而不是临时 grep：`git grep` 默认**不扫未跟踪文件**，新写的文件会漏检；
> 规则写在文档里还会自我命中。这个脚本扫工作区（含未跟踪），跳过 `.git`/`.venv`/缓存/二进制。
>
> 公司域名、内网标识这类**团队私有**规则不要写进脚本（脚本是公开文件，写进去等于泄露）。
> 复制 `.preflight-extra.example.json` 成 `.preflight-extra.json`（已 gitignore）填自己的模式，
> 或用环境变量 `PREFLIGHT_EXTRA_RULES='名字=正则;名字2=正则2'`。

**完成判据**：四条命令全部退出码 0（`preflight.py` 打印 `PASS`）；`git status` 里没有 `keys.json`、解密目录、`history.jsonl`、`.venv/`。
新增规则时，往 `tools/preflight.py` 的 `RULES` 里加一条，并在 `tests/test_preflight.py::_leak_cases` 补一个样例——测试会验证「这条规则真的抓得到」。

---

## 10. 让某项能力"有据可查"

改完涉及微信库结构或密钥逻辑，把证据补进 `docs/evidence/`：

- `verification.md`：需求 → 实现 → 证据的对照表（表格加一行）；
- `docs/evidence/*.log`：命令的原始输出；
- 真模型跑的结果：`real_model_run.md`（**必须用虚拟演示库**，别把真实聊天塞进仓库）。

**完成判据**：新增结论能追到一条**可重跑的命令**或一个测试名，而不是"我试过了"。
