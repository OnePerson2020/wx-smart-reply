# 从一台空白的 Windows 到「能弹出候选回复」——完整部署指引

> Agent 注意：改代码的约定、模块↔测试对应表、任务配方在 [`../AGENTS.md`](../AGENTS.md) 与
> [`AGENT_TASKS.md`](AGENT_TASKS.md)；本文件只管「跑通真实数据」这条路径。

这份文档按顺序做完即可跑起来。全程大约 20–40 分钟，其中「取密钥」是最容易卡住的一步，
所以每一步都给了**自检命令**：任何一步的输出不对，就别往下走。

> 前提认知：WxReply 本身**不注入微信、不修改微信数据库、不自动发消息**，它只做两件事：
> ① 把你已经解密的聊天记录当只读知识库；② 把候选回复写进剪贴板。
> 所以「解密」是整个链路的入口，而**解密 = 拿到密钥 + 用密钥解出明文数据库**。

---

## 阶段 0：装环境（5 分钟）

| 步骤 | 操作 | 自检 |
| --- | --- | --- |
| 0.1 | 装 Python 3.10+（[python.org](https://www.python.org/downloads/windows/) 安装包，**勾选 Add python.exe to PATH**） | `python --version` 有输出 |
| 0.2 | 装 Git（[git-scm.com](https://git-scm.com/download/win)），或直接下载仓库 zip 解压 | `git --version` 有输出 |
| 0.3 | 拿代码 | `git clone https://github.com/OnePerson2020/wx-smart-reply.git` |
| 0.4 | 建虚拟环境并装依赖 | 见下 |

```powershell
cd wx-smart-reply
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m wxreply selftest
```

`selftest` 会自建一份**虚拟**聊天库（张三/李四，不含你的任何真实数据）并把依赖、知识库读取、
生成引擎、界面四项都跑一遍。看到 `全部通过 ✅` 就说明程序本身没问题，后面出问题一定在数据或模型配置上。

> 不想装 Python：让 WxReply 在任意一台 Windows 上打包一次（阶段 7），把 `dist\WxReply\` 整个文件夹拷过来，
> 双击 `WxReply.exe` 即可。**注意 PyInstaller 不能交叉编译，.exe 必须在 Windows 上生成。**

---

## 阶段 1：找到微信的数据目录（2 分钟）

微信 4.x 的本地库在 `xwechat_files\<wxid>_<后缀>\db_storage\` 下：

```
db_storage/
├── contact/contact.db        联系人（备注、昵称）
├── session/session.db        会话列表
└── message/
    ├── message_0.db          消息主体，每个会话一张 Msg_<md5(wxid)> 表
    ├── message_1.db …
    ├── media_0.db            图片等媒体索引（本程序不用）
    └── biz_message_0.db      公众号/服务号（本程序不用）
```

默认位置是 `C:\Users\<你>\Documents\xwechat_files`。如果你在微信里改过「文件管理」目录，路径在注册表里：

```powershell
Get-ItemProperty 'HKCU:\Software\Tencent\WeChat' -Name FileSavePath | Select-Object FileSavePath
# 输出 MyDocument: 表示默认的“文档”目录
```

**自检**：`db_storage\message` 下有 `message_0.db` 就算找对了。程序也能直接吃 `xwechat_files` 根目录
（会自动落到最近使用的账号），不用自己找 `wxid_xxx` 那层。

---

## 阶段 2：拿到密钥 keys.json（最容易卡住的一步）

### 2.1 先理解：为什么必须要密钥

微信 4.x 的本地库是 **SQLCipher 4（WCDB 封装）** 加密的：

| 参数 | 值 |
| --- | --- |
| 页大小 | 4096 字节 |
| 每页保留区 | 80 字节 = IV(16) + HMAC(64) |
| 加密算法 | AES-256-CBC，**raw-key 模式**（32 字节 enc_key 直接当 AES key，不做 PBKDF2 派生） |
| 页 1 布局 | `salt(16) + 加密体(4000) + iv(16) + hmac(64)` |
| 页 N 布局 | `加密体(4016) + iv(16) + hmac(64)` |
| HMAC 校验 | `PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3a, 2轮, 32字节)` 作为 mac_key，对 `页1[16:4032] + 小端页码` 做 HMAC-SHA512 |

关键点：**密钥与这个库绑定**（mac_key 的派生用到库文件前 16 字节的 salt）。
所以「在 A 机器上取的密钥」解不开「B 机器上的库」，微信重装/换版本重建数据库后旧密钥也会失效。

### 2.2 三条获取路线，挑一条

| 路线 | 适合谁 | 怎么做 |
| --- | --- | --- |
| **A. 你已经有 keys.json** | 之前取过、或同事/工具给过 | 直接进 2.3 校验。**注意跨机失效**，很可能要重新取 |
| **B. 用你信任的工具取一次** | 大多数人 | 用你惯用的 Windows 微信密钥提取工具跑一次，产出 `keys.json`，进 2.3。**本仓库有意不内置密钥提取**（见 2.5） |
| **C. 自己写/自己调** | 想完全掌握链路的人 | 见 2.4 的原理与要点 |

### 2.3 拿到 keys.json 后：先校验，再解密

本仓库支持两种 keys.json 格式，随便哪种都能用：

```json
{
  "contact/contact.db":       { "enc_key": "8f3a…64位十六进制…" },
  "session/session.db":       { "enc_key": "…" },
  "message/message_0.db":     { "enc_key": "…" },
  "message/message_1.db":     { "enc_key": "…" }
}
```

```json
{
  "contact/contact.db":   "x'<64位 hex 密钥><32位 hex salt>'",
  "message/message_0.db": "x'…'"
}
```

校验命令（**不解密、不写盘**，只读页 1 做 HMAC 判断）：

```powershell
.\.venv\Scripts\python.exe -m wxreply keycheck `
  --src "C:\Users\<你>\Documents\xwechat_files" `
  --keys "C:\path\to\keys.json"
```

正常输出：

```
  ✅ contact/contact.db      16.8 MB  密钥正确（HMAC 强校验通过）
  ✅ session/session.db       0.8 MB  密钥正确（HMAC 强校验通过）
  ✅ message/message_0.db    74.1 MB  密钥正确（HMAC 强校验通过）

密钥全部可用。下一步二选一：…
```

如果看到 ❌ / ⚠️，命令会直接把三种常见原因列出来：

1. **跨机/跨批次**：密钥是在另一台机器或数据库重建前取的 → 必须在当前这台机器重新取一次；
2. **微信升级换密钥**：同样需要重新取；
3. **键名写法不匹配**：必须是 `contact/contact.db`、`message/message_0.db` 这种相对路径。

> 为什么这个校验可信：它不是"试解密看运气"，而是用 HMAC-SHA512 对页 1 做密码学校验，
> 密钥错一个字节就会失败。这也是 2.4 里挑选候选密钥的判据。

### 2.4 原理：密钥在哪儿、怎么取（路线 C 用）

- 微信 4.x 运行时，32 字节 enc_key 存在于 `Weixin.dll`（旧版 `WeChatWin.dll`）相关内存中；
  某些版本只在加密调用瞬间出现，所以**固定内存扫描可能扫不到**，需要 hook 加密 API 才能抓到。
- 典型做法：以**管理员权限**打开微信进程 → 枚举可读内存区域（`ReadProcessMemory`）→ 收集 32 字节候选 →
  对每个候选 用 `salt` 派生 mac_key 并做 **页 1 HMAC 校验**（就是 2.3 那个算法）→ 命中的才是真密钥。
- 关键细节：候选要用**数据库文件前 16 字节的 salt** 去验证，所以验证是"密钥 ↔ 具体库"的一对一匹配；
  多账号/多库要各自记录（keys.json 的键名就是干这个的）。
- 若内存扫描拿不到：退路是 hook 加密调用（在 AES 加密函数入口取 key 参数），或直接用别人已经跑通的产物。

### 2.5 为什么本仓库不内置密钥提取

- 取密钥需要读**运行中微信进程的内存**，属于侵入性操作；本程序刻意只做"读你已解密的数据"，边界更干净。
- 这类工具本身有被平台法务关注的风险（社区里出现过下架事件），不适合塞进这个仓库一起分发。
- 你自己的取密钥/解密工具链完全可以与本程序共存：**只要它能产出阶段 2.3 那两种格式之一的 keys.json**。

---

## 阶段 3：解密成可读目录（1 分钟，之后每次增量）

两条路，选一条即可（**推荐先 A，稳**）：

### 路线 A（`dir` 模式）：用你已有的解密产物

如果你的工具已经输出了 `contact/session/message` 三件套（或它可以按密钥解 SQLCipher），
把那个目录直接用起来：

```powershell
.\.venv\Scripts\python.exe -m wxreply check --kb "D:\wechat_decrypted"
```

看到 `N 个会话 / M 个联系人 / 自己=wxid_xxx` 就成了。之后刷新由你的工具负责，
WxReply 每个轮询周期读一次新消息。

### 路线 B（`decrypt` 模式）：让 WxReply 自己增量解密

```powershell
.\.venv\Scripts\python.exe -m wxreply refresh `
  --src "C:\Users\<你>\Documents\xwechat_files" `
  --out "D:\wxreply_decrypted" `
  --keys "C:\path\to\keys.json"

.\.venv\Scripts\python.exe -m wxreply check --kb "D:\wxreply_decrypted"
```

它会：
- 只处理 `contact/contact.db`、`session/session.db`、`message_N.db`（真机上 9 个 .db 里精确挑这 3 类，不动 media/biz/fts）；
- 按源文件 **(修改时间, 大小)** 判断是否需要重新解密，没变化就跳过；实测 74 MB 消息库一次约 0.1–0.3 秒；
- 顺手尝试解密 `-wal`（帧布局不符时自动丢弃，**绝不把脏页喂给 sqlite**）；
- 解密后自检：`contact.db` 能读出表、页 1 结构合法，不合法就报错**不写文件**。

**关于实时性**：解密本身是秒级的，"消息多久能被读到"取决于微信把数据落盘/checkpoint 的时机
（一般几秒到几分钟；切一下聊天窗口通常会触发 checkpoint）。所以轮询间隔设 3 秒不会拖慢机器，也不会漏消息。

---

## 阶段 4：配置模型（2 分钟）

打开程序：

```powershell
.\.venv\Scripts\python.exe -m wxreply      # 或双击 start_wxreply.bat
```

进 **模型** 页：

| 字段 | 说明 |
| --- | --- |
| 提供方 | 先用 `offline（离线模板，不联网）` 把流程跑通；真实使用改 `openai（OpenAI 兼容接口）` |
| Base URL | 火山方舟 `https://ark.cn-beijing.volces.com/api/v3`；OpenAI `https://api.openai.com/v1`；DeepSeek `https://api.deepseek.com/v1`；本地 Ollama `http://127.0.0.1:11434/v1` |
| API Key | 本地地址（`127.0.0.1`/`localhost`）可留空 |
| 模型 ID | 方舟用接入点 `ep-xxxx` 或模型名；OpenAI `gpt-4o-mini`；DeepSeek `deepseek-chat`；Ollama `qwen2.5:7b` |
| 最大输出 tokens | **带思考过程的模型调到 3000–4000**，否则思考吃光预算、正文可能为空 |
| 候选条数 | 默认 4（稳妥/轻松/直球/简短） |

点 **测试连接** 会真发一条极小请求。然后点右下角 **保存并应用**。

> 离线模式是完全可用的：界面、候选卡、多轮改写、复制全部能走通，只是候选是固定模板，
> 弹窗里会明确写「离线模板模式」。

---

## 阶段 5：选联系人（2 分钟）

进 **联系人** 页，每一行是一个被监控对象：

1. 点 **从知识库选择…**，搜索备注名/昵称/wxid，双击选中；
2. **名称** 随便起：`联系人 1`、`老板`、`妈妈`（这就是弹窗标题上的名字）；
3. **额外说明** 会写进提示词，例如"是我老板，注意分寸"、"同学，随便聊"；
4. **语气** 留空 = 默认四档；想自定义就写 `客气, 幽默, 直接`；
5. 点 **测试：这些联系人能读到吗**：

```
✅ 联系人 1: 张三 (wxid_…) 最近消息 10-07 21:11
```

看到 ✅ 且"最近消息"时间在动，说明联系人绑定正确。之后 **保存并应用**。

> 首次绑定某个联系人时程序**只记录进度、不倒放历史**（避免一开就被旧消息刷屏）；
> 想连历史一起弹，勾上监控页的「启动时为历史消息也弹窗」。

---

## 阶段 6：开启监控并验证（3 分钟）

1. **监控** 页：
   - 数据来源：`dir`（指向阶段 3 路线 A 的目录）或 `decrypt`（填微信数据目录 + keys.json + 输出目录）；
   - 「开启实时监控」勾上；轮询间隔 3 秒；「只提醒 N 分钟内的新消息」默认 15 分钟；
   - 弹窗位置可选四个角；
   - 点 **立即检查一次**，状态栏会显示本轮触发了几条。
2. **保存并应用**，状态栏应显示 `知识库就绪：N 个会话 / 自己=wxid_xxx | 监控：运行中`。
3. 用另一台设备/另一个号给你监控的联系人发一条消息（或让朋友发），等待一个轮询周期 + 微信落盘时间。
4. 应该发生：右下角弹窗 + Windows 通知；弹窗里是对方那句话 + 对方意图 + 4 张候选卡（语气、踩雷风险、理由）。
5. 走一遍完整交互：
   - 点某张卡的 **复制** → 去微信粘贴发送（剪贴板里就是这句）；
   - 点 **提意见改写**，输入"太硬了软一点""顺便问下周末" → 卡片上多出「第1轮 你的意见」记录，正文被改写；
     可以连续提多轮，历史都留在卡上，随时回到某一版复制；
   - **换一批** 再要几条不同角度；
   - 满意后点 **复制我给对方发的最终回复**。
6. **记录** 页可以回看最近 100 条事件（对方说了什么、给过哪些候选）。

---

## 阶段 7：打包成 exe 分发（可选，5 分钟）

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
```

脚本会依次：建 `.venv` → 装依赖 → 跑测试 → 生成图标 → 自检 → PyInstaller 打包。
产物 `dist\WxReply\WxReply.exe`（整个文件夹拷给别人即可用）。

打包后仍然可以自检（GUI 程序没有控制台，结果会弹窗显示）：

```powershell
.\dist\WxReply\WxReply.exe selftest
.\dist\WxReply\WxReply.exe check --kb "D:\wxreply_decrypted"
```

---

## 排障速查

| 现象 | 原因 / 处理 |
| --- | --- |
| `keycheck` 报 ❌ 密钥不匹配 | 跨机/跨批次密钥，或微信升级换了密钥 → 在当前机器重新取；见阶段 2.3 三种原因 |
| `keycheck` 报 ⚠️ missing | keys.json 键名不是 `contact/contact.db` 这种相对路径 |
| `refresh` 报「解密后无法用 sqlite 打开」 | 同上；程序会拒绝写出垃圾文件，不会污染输出目录 |
| `check` 显示 0 个会话 | `--kb` 指错了层级：要指到含 `contact/ session/ message/` 的目录（`db_storage`），不是 `xwechat_files` 根目录 |
| 界面状态栏「还没设置已解密数据库目录」 | 监控页目录没填或没点「保存并应用」 |
| 联系人解析不到 | 用「从知识库选择…」挑，别手打；同名多人时取最匹配的一个 |
| 弹窗不出现 | ①监控已勾选？②点「立即检查一次」看是否触发？③消息是否在"只提醒 N 分钟内"窗口内？④数据源是否真的更新了（看 `message_0.db` 的修改时间） |
| 消息延迟几分钟 | 微信落盘/checkpoint 频率所致，不是程序问题；切一下聊天窗口触发 checkpoint |
| 生成很慢或正文为空 | 带思考的模型把「最大输出 tokens」调到 3000–4000；或换非思考模型 |
| 杀软/Defender 报毒 | PyInstaller 产物常见误报，加白名单即可 |
| 托盘图标不见了 | 点任务栏 `^` 把图标拖出来常显；菜单里有「暂停监控」 |
| 想彻底停掉监控 | 托盘 → 暂停监控；或监控页取消勾选 |

## 一页速查（按顺序）

```powershell
# 0
cd wx-smart-reply; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m wxreply selftest                       # 程序自检
# 1  找数据目录（默认：C:\Users\<你>\Documents\xwechat_files）
Get-ItemProperty 'HKCU:\Software\Tencent\WeChat' -Name FileSavePath
# 2  拿到 keys.json → 校验（HMAC 强校验）
.\.venv\Scripts\python.exe -m wxreply keycheck --src "...\xwechat_files" --keys "keys.json"
# 3  解密
.\.venv\Scripts\python.exe -m wxreply refresh --src "...\xwechat_files" --out "D:\wxreply_decrypted" --keys "keys.json"
.\.venv\Scripts\python.exe -m wxreply check --kb "D:\wxreply_decrypted"
# 4  跑起来（界面里：模型 → 联系人 → 监控 → 保存并应用）
.\.venv\Scripts\python.exe -m wxreply
# 5  可选：打包分发
powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
```
