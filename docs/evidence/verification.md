# 验收证据（逐条对应需求）

所有命令都在项目根目录下执行，Python 用项目自带 `.venv`。

## A. 需求 → 实现 → 证据

| 需求 | 实现 | 证据 |
| --- | --- | --- |
| 阅读交接文档 | — | `/tmp/handoff_im_smart_reply_assistant.md`（66 行）已读；其中 4 个候选项目被判为停更/形态不符（老 RPA 链、无「提意见多轮改写」、无「解密库当知识库」），故自建 |
| Windows 桌面 AP | PySide6 界面 + PyInstaller 打包 | `wxreply.spec`、`tools/build_windows.ps1`、`start_wxreply.bat`；**同 spec 在本机实际打包并运行通过**（见 C） |
| 已解密 4.4.x 数据当知识库 | `kb/reader.py` 直读 `contact/session/message_*.db` | 真实库体检：`selftest --kb <本机已解密目录>` → `275 个会话 / 18568 个联系人 / 自己 wxid 自动识别成功` |
| 设置联系人 1/2/3/4 | 联系人页表格：启用/名称/微信ID或备注/额外说明/语气 | `test_main_window_tabs_and_contact_collection`；截图 `main_window.png` |
| 特定联系人发消息自动检测并提示 | `watcher.py` 轮询 → 事件 → 弹窗 + 系统通知 | `test_first_pass_records_cursor_without_firing`、`test_new_incoming_message_fires_once`、`test_full_threaded_pipeline_pops_up_without_manual_injection`（真线程 + QTimer，无手动喂数据） |
| 多候选（不同语气/倾向） | `engine.generate` + `prompts.py` 四档语气 | 真模型运行（见 B）：稳妥/轻松/直球/简短 4 条，语气明显分化 |
| 每条候选说明理由 | 候选卡显示 tone + 踩雷风险 + 理由 | `test_incoming_message_pops_up_with_candidates`（断言理由文本在界面上） |
| 提示框自动弹出 | `ui/popup.py` 无边框置顶弹窗，四角可选 | 截图 `popup.png`；`test_full_threaded_pipeline...` 断言自动出现 |
| 候选展示 + 复制 | 每张卡「复制」按钮 → 剪贴板 | `test_copy_button_puts_text_in_clipboard`（断言剪贴板内容） |
| 点候选写评论、可多轮 | 卡片内意见框 → `engine.refine`，历史保留 | `test_comment_round_refines_text_and_keeps_history`（两轮，断言 `rounds` 与界面「第1轮/第2轮」标签）；真模型运行见 B |
| 最终生成 → 剪贴板，用户自己发 | 底部「复制我给对方发的最终回复」 | `test_final_copy_uses_refined_text`（断言剪贴板 == 改写后文本） |
| 实时捕获微信消息 | `dir`：轮询已解密目录；`decrypt`：按源文件 mtime 增量解密（只解密 contact/session/message_N 三个库，真机 9 个 .db 里精确挑出 3 个）+ WAL 尽力解密 | `test_full_decrypt_and_incremental`（源文件变化 → 只重新解密变化文件）；`test_wal_frames_are_replayed`（**用真实 SQLite 写出的 WAL 帧**，解密后 sqlite 成功重放） |

## B. 真模型端到端

命令与输出全文见 `real_model_run.md`（本机自建 OpenAI 兼容网关，非 mock）：

- 意图识别：`想让帮忙讲第3页指标数据，等你表态是否能接。`
- 4 条候选，含理由与风险分级。
- 按意见「别催他，轻松一点，顺便问下他周末有没有空」改写成功（见 `real_model_run.md`）。

> 另外验证过某云厂商 ARK 直连：其控制面接口返回频控（服务账号到阈值），按平台规则不重试、
> 不换入口；随后用本机已就绪的 OpenAI 兼容网关验证同一段 HTTP/JSON 代码路径。

## C. 打包与冻结运行

用**同一份** `wxreply.spec` 在 macOS 上实际打包（PyInstaller 6，`console=False`）：

- 产物 `dist/WxReply/WxReply`，107MB（含 PySide6）。
- 冻结后自检（`docs/evidence/frozen_selftest.log`，用的是虚拟演示库）：
  ```
  [PASS] 依赖: PySide6 … / pycryptodome / httpx 已就绪
  [PASS] 知识库: 自建演示库可读：3 个会话，自己=wxid_demo_me
  [PASS] 生成引擎: 离线生成 4 条候选
  [PASS] 界面: 弹窗可构建（1 张候选卡）
  全部通过 ✅
  ```
  冻结产物另跑过 `check --kb <本机真实已解密目录>`：275 个会话 / 18568 个联系人，正常读出。
- 冻结后启动 GUI：进程存活 6 秒、无异常退出。

首次打包失败过一次（`ImportError: attempted relative import with no known parent package`），
已通过新增入口 `run.py`（绝对导入 + `sys.exit(main())`）修复并重新打包验证。
Windows 上的 .exe 需要你在 Windows 机器执行 `tools\build_windows.ps1`（本机无法产出 exe）。

## D. 测试与静态检查

- `python -m pytest -q` → **62 passed**（含 12 项离屏界面联调）
- `python -m ruff check --select F,E9,B,UP006,UP035 wxreply tools tests run.py` → **All checks passed**
- `python -m wxreply selftest` → 全部通过

## E. 真实数据上验证过的库结构（写进 README 第 10 节，避免后人重踩）

在一份本机真实 4.4.x 解密库上确认（只读打开，未打印任何聊天内容，下列标识已打码）：

1. `Msg_<md5(username)>` 表名成立（Name2Id 2153 个 username → 275 张表全部命中）。
2. `real_sender_id` = 该消息库 `Name2Id` 的 **rowid**：自己对应的 rowid 出现在 153/175 个单聊里，
   且与群消息正文前缀逐一吻合；若按 `contact.id` 解释会得出「某个公众号在 153 个单聊里发言」的荒谬结论（已排除）。
3. 群消息正文前缀 `wxid_xxx:\n` = 发言人；自己发的不带前缀（13/13 一致）。
4. SQLCipher4 页布局（页 1/页 N/WAL 帧）与 reserve=80 的页头字段，均已用真实库 + HMAC 校验确认。
5. 真实库的 `-wal` 帧 `dbsize` 多为 0（未提交残页），重放不产生更早/更新的数据 → 主库即权威来源。
