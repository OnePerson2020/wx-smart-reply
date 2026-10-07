# 微信辅助回复助手（WxReply）

Windows 桌面小工具：**用你本地已解密的微信 4.x 聊天记录当知识库**，在指定联系人发来消息时自动弹窗，
给出几条语气/倾向不同的候选回复并说明理由；你可以直接复制，也可以对某条候选写意见（"太硬了软一点""顺便问下周末"），
反复改到满意再复制去微信里发送。

**它不替你发消息**：候选文本只写进剪贴板，发送永远由你本人动手。

![弹窗](docs/evidence/popup.png)
![主窗口](docs/evidence/main_window.png)

> **免责声明 / 使用边界**
> 本工具只读取**你自己**在本机已经解密好的聊天数据，用来给你自己起草回复；它**不注入微信、不修改任何微信数据库、
> 不自动发送消息**，只把候选文字写进剪贴板。数据全程留在本机，唯一的外部请求是你在「模型」页自己填的那个接口
> （不接模型时完全不出网）。请只在符合当地法律与微信服务条款的前提下使用，使用后果自负；作者不提供任何担保。

---

## 0. 给 Agent：从这里开始

改代码前先跑两个门，**都绿才算完成**：

```powershell
.\.venv\Scripts\python.exe -m pytest -q                  # 期望 68 passed
.\.venv\Scripts\python.exe -m wxreply selftest           # 期望 全部通过 ✅
```

约定、模块↔测试对应表、只有踩过才知道的坑、各类任务的配方，都在：

| 材料 | 内容 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 常驻约定：不变量、改哪个文件跑哪个测试、Qt/打包/密钥相关坑 |
| [`docs/AGENT_TASKS.md`](docs/AGENT_TASKS.md) | 任务配方（加语气、改提示词、接模型、加数据源、加命令、改弹窗、打包、发布前检查），每个带完成判据 |
| [`docs/WINDOWS_SETUP_GUIDE.md`](docs/WINDOWS_SETUP_GUIDE.md) | 从空白 Windows 跑通真实数据的完整流程（含取密钥、解密、排障速查） |
| [`docs/evidence/verification.md`](docs/evidence/verification.md) | 需求↔实现↔证据对照表；涉及 4.4.x 库结构时先读它 |

---

## 1. 它解决什么

- 发消息前反复纠结措辞、怕踩雷 → 直接给几条不同语气的成品句，附「为什么这样说」。
- 想模仿自己平时的说话习惯 → 提示词里带上你在该会话里历史消息的用词样例。
- 想改而不是重来 → 每条候选都能写意见，多轮改写，历史意见保留在卡片上。
- 不想被全自动代替 → 没有"自动发送"功能；剪贴板 + 你自己粘贴。

## 2. 运行环境

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10/11（macOS / Linux 也能跑，用于调试） |
| Python | 3.10+（用打包好的 exe 则不需要） |
| 微信 | 4.x（4.4.x 实测），需要**已经解密的**数据库作为知识库 |
| 模型 | 任意 OpenAI 兼容接口（火山方舟 / OpenAI / DeepSeek / 本地 Ollama…）；也可先用离线模板模式 |

## 3. 安装与运行

> 完整版（含最卡的「取密钥」环节、每步自检、排障表）见 **[docs/WINDOWS_SETUP_GUIDE.md](docs/WINDOWS_SETUP_GUIDE.md)**。

```powershell
git clone https://github.com/OnePerson2020/wx-smart-reply.git
cd wx-smart-reply
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m wxreply selftest      # 自检（用虚拟库，不碰你的真实数据）
start_wxreply.bat                                   # 或 .\.venv\Scripts\python.exe -m wxreply
```

打包成分发的 exe（会依次跑测试、生成图标、自检、PyInstaller）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1   # 产物 dist\WxReply\WxReply.exe
```

> PyInstaller 不能交叉编译，`.exe` 必须在 Windows 上生成。

## 4. 数据来源：两种模式

解密链路是「**拿到密钥 → 解成明文库 → 当只读知识库**」。密钥与库的 salt 绑定，**不能跨机复用**。

| 模式 | 你提供 | 程序做什么 |
| --- | --- | --- |
| `dir` | 你已有的解密目录（含 `contact/ session/ message/`） | 每个轮询周期读一次新消息；刷新由你的工具负责 |
| `decrypt` | 微信数据目录 + `keys.json` + 输出目录 | 按源文件 (mtime, size) 增量解密（只解 `contact.db`/`session.db`/`message_N.db`），顺手尽力解密 `-wal` |

拿到 `keys.json` 后先验密钥再解密（HMAC 强校验，不解密不写盘）：

```powershell
.\.venv\Scripts\python.exe -m wxreply keycheck --src "...\xwechat_files" --keys "keys.json"
.\.venv\Scripts\python.exe -m wxreply refresh  --src "...\xwechat_files" --out "D:\wxreply_decrypted" --keys "keys.json"
.\.venv\Scripts\python.exe -m wxreply check    --kb "D:\wxreply_decrypted"
```

- 程序**有意不内置**进程内存取密钥（边界与合规原因，见指引 2.5）；`keys.json` 支持两种格式，见指引 2.3。
- 实时性：解密本身秒级；消息多久被读到取决于微信落盘/checkpoint（通常几秒到几分钟）。
  实测 4.4.x 的 `-wal` 常是未提交残页（`dbsize=0`），此时**主库就是权威来源**。

## 5. 界面里的三处配置

1. **模型**：提供方（`offline` 离线模板 / `openai` 兼容接口）、Base URL、API Key、模型 ID、最大输出 tokens
   （带思考的模型调到 3000–4000）、候选条数。本地地址免 Key，点「测试连接」真发一条极小请求。
2. **联系人**：点「从知识库选择…」挑 wxid/备注；起名（`联系人 1`/`老板`/`妈妈`）；写「额外说明」（会进提示词）；
   可自定义语气；点「测试：这些联系人能读到吗」确认解析到的身份与最近消息时间。
3. **监控**：数据来源与目录、轮询间隔、只提醒 N 分钟内的新消息、弹窗位置、「立即检查一次」。
   状态栏出现 `知识库就绪：N 个会话 | 监控：运行中` 即生效。

首次绑定某联系人时**只记录进度、不倒放历史**（勾「启动时为历史消息也弹窗」可改成倒放）。

## 6. 日常交互

收到消息 → 右下角弹窗 + 系统通知 → 卡片上有语气、踩雷风险、理由：

- **复制**：这一条进剪贴板，去微信粘贴发送；
- **提意见改写**：写"太硬了""加一句约时间"，可连续多轮，历史意见留在卡上；
- **换一批**：再要几条不同角度；
- **复制我给对方发的最终回复**：带走当前这版。

托盘菜单：打开设置 / 立即检查一次 / 暂停监控 / 退出。**记录** 页可回看最近 100 条事件。
配置与记录在本机：Windows `%APPDATA%\wxreply\`，macOS `~/.wxreply/`。

## 7. 命令行

最常用的四条：

| 命令 | 用途 | 完成判据 |
| --- | --- | --- |
| `python -m wxreply` | 打开图形界面 | 主窗口出现，状态栏显示知识库与会话数 |
| `python -m wxreply selftest [--kb DIR]` | 体检（依赖/知识库/生成/界面） | 输出 `全部通过 ✅` |
| `python -m wxreply keycheck --src ... --keys ...` | 校验 keys.json | 每个库 `✅ 密钥正确（HMAC 强校验通过）` |
| `python -m wxreply once --kb DIR --chat 备注名 [--text ...] [--refine ...]` | 不开界面跑一次生成/改写 | 打印意图 + 多条候选（含理由），改写后文本确实变了 |

其余子命令（`check` / `demo` / `refresh` / `gui`）用 `python -m wxreply --help` 查看——**别在这里抄清单，会过时**。

## 8. 模块地图

| 路径 | 职责 |
| --- | --- |
| `wxreply/__main__.py` | CLI 子命令：gui / selftest / keycheck / check / once / demo / refresh |
| `wxreply/config.py` | 配置（JSON 落盘）、默认语气、联系人绑定 |
| `wxreply/engine.py` | 候选生成 / 换一批 / 多轮改写 / 事件记录 |
| `wxreply/prompts.py` | 中文提示词唯一真源 |
| `wxreply/llm.py` | OpenAI 兼容客户端（参数兼容降级、JSON 兜底解析） |
| `wxreply/watcher.py` | 轮询知识库 → 新消息事件；运行时装配 |
| `wxreply/kb/crypto.py` | SQLCipher4 解密、增量镜像、密钥 HMAC 校验、WAL 尽力解密 |
| `wxreply/kb/reader.py` | 读解密库：联系人 / 消息 / 方向 / 群发言人 |
| `wxreply/ui/app.py` | 装配：托盘 + 监控线程 + 生成线程 + 弹窗（队列回主线程） |
| `wxreply/ui/popup.py` | 弹窗与候选卡片（复制 / 提意见 / 换一批） |
| `wxreply/ui/main_window.py` | 联系人 / 模型 / 监控 / 记录 四个页 |
| `tools/make_demo_kb.py` | 生成虚拟演示知识库（测试、自检、截图都用它，**不含真实聊天**） |
| `tools/preflight.py` | 发布前扫描：本机路径 / 真实 wxid / 密钥 / 数据库文件 / 虚拟环境 |
| `tools/make_icon.py` · `wxreply.spec` · `tools/build_windows.ps1` | 图标与 Windows 打包 |
| `tests/` | 68 项，含离屏界面联调（真点按钮、真读剪贴板、真跑监控线程）；文件↔模块对应见 `AGENTS.md` |

## 9. 已验证的技术事实（微信 4.4.x，写下来免得后人重踩）

1. 会话表名 `Msg_<md5(username)>`，表分散在 `message_0..N.db`（`Name2Id` 可反查）。
2. **`real_sender_id` 是该消息库 `Name2Id` 表的 rowid**，不是 `contact.id`。
   自己的 rowid 用 `Name2Id.user_name == 自己的 wxid` 定位（账号目录名 `wxid_xxx_<hash>` 可推出自己的 wxid）；
   方向判断 `is_from_me = (real_sender_id == 自己的 rowid)`。
3. 群消息正文形如 `wxid_xxx:\n内容`（自己发的没有前缀），前缀就是发言人。
4. 库是 SQLCipher 4：页 4096、保留区 80 = IV(16)+HMAC(64)、AES-256-CBC、raw-key；
   页 1 = `salt(16) + enc(4000) + iv + hmac`，页 N = `enc(4016) + iv + hmac`；解密库页头第 21 字节为 80（reserve）。
5. `-wal` 帧与普通页同构（页 1 同 `salt+enc(4000)` 形态），可套用同一解密函数；但真实库多数帧 `dbsize=0`（未提交），别指望它更快。

逐条证据（含真机实测与测试名）见 [`docs/evidence/verification.md`](docs/evidence/verification.md)。

## 10. 边界（有意不做）

- **不自动发送、不操作微信窗口**：没有注入、没有 RPA、不点坐标；只做候选 + 剪贴板。
- 只理解**文本**消息；图片/语音/视频/文件当占位（`[图片]` 等），不解析内容。
- 群消息按发言人区分，不处理 @ 列表、不区分群昵称与备注。
- 不做历史检索问答（知识库只用于"最近上下文 + 语气样例"）。
- 不修改任何微信数据库：`dir` 只读；`decrypt` 只往你指定的输出目录写。

## 11. 排障

完整表格（密钥不匹配、`refresh` 报错、0 个会话、弹窗不出现、延迟、正文为空、杀软误报、托盘图标…）在
**[docs/WINDOWS_SETUP_GUIDE.md → 排障速查](docs/WINDOWS_SETUP_GUIDE.md#排障速查)**。

一句话：**消息延迟几分钟是微信落盘频率，不是程序问题**；其余先跑 `keycheck` 和「立即检查一次」，再看状态栏报什么。

## 12. 开发

```powershell
.\.venv\Scripts\python.exe -m pytest -q                                                   # 68 项
.\.venv\Scripts\python.exe -m ruff check --select F,E9,B,UP006,UP035 wxreply tools tests run.py
.\.venv\Scripts\python.exe -m wxreply selftest
.\.venv\Scripts\python.exe -m wxreply selftest --kb "<已解密目录>"                          # 顺便体检真实知识库
```

测试覆盖：解密往返（含真实 SQLite 写出的 WAL 重放）、两种数据源、密钥 HMAC 校验、方向与群发言人识别、
监控游标与去重、JSON 容错与多轮改写、参数兼容降级，以及**离屏界面联调**（真点复制/提意见/换一批/最终复制并断言剪贴板）。
