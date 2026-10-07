"""提示词模板（中文）。目标：像本人打字、语气分化、给出可解释的理由。"""
from __future__ import annotations

from .kb.models import Message

SYSTEM = """你是用户的「微信回复参谋」。用户不擅长临场措辞，容易反复纠结，所以你要：
1. 先判断对方这条消息的显性请求和潜台词（想要什么、情绪如何、是否在等你表态）。
2. 给出 {n} 条可以直接发送的候选回复，每条语气/倾向明显不同。
3. 口语化，像用户在微信里随手打字：短句、自然、可以用"嗯/哈哈/好嘞"这类词；
   不要书面语、不要客服腔、不要"首先/其次/综上"、不要 emoji 堆砌。
4. 长度贴合微信习惯：一般 5~40 字；需要说明事情时可以稍长，但别写成邮件。
5. 不替用户承诺做不到的事、不编造事实；信息不足时用一句提问代替表态。
6. 每条候选都要写 reason（15~40 字）：为什么合适、对方听了什么感受；risk 用"低/中/高"标注踩雷风险。

严格要求：只输出一个 JSON 对象，不要 Markdown 代码块，不要任何多余文字。
格式：
{{"intent": "对方意图/潜台词（30 字内）", "candidates": [{{"tone": "语气名", "text": "回复正文", "reason": "理由", "risk": "低"}}]}}"""

REFINE_SYSTEM = """你是用户的「微信回复参谋」。用户正在打磨一条回复，你要按用户的意见改。
规则：
- 只改被指出的问题，保留原意和语气基调；不要借机加新承诺、新事实。
- 依然要口语化、像本人打字，不要客服腔。
- 只输出修改后的回复正文本身：一行或多行纯文本，不要引号、不要解释、不要 JSON。"""


def _trim(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_history(messages: list[Message], me_label: str = "我", per_line: int = 200) -> str:
    return "\n".join(f"{m.line(me_label)}" if len(m.content) <= per_line
                     else f"{m.line(me_label)[:per_line]}…" for m in messages)


def build_generate_prompt(label: str, display_name: str, note: str, incoming: Message,
                          context: list[Message], style_samples: list[str],
                          tones: list[dict], max_candidates: int) -> tuple[str, str]:
    tone_lines = "\n".join(f"- {t.get('name', '语气')}：{t.get('hint', '')}" for t in tones)
    is_group = incoming.chat_username.endswith("@chatroom")
    who = f"{display_name}（群聊，发言人是 {incoming.sender_name or incoming.sender_id}）" if is_group else display_name
    style_block = "\n".join(f"- {_trim(s, 80)}" for s in style_samples) or "（暂无样例）"
    user = f"""【聊天对象】{label} · {who}
【用户对 TA 的说明】{note or "（无）"}

【对方刚发来的消息】（{incoming.datetime:%m-%d %H:%M}）
{incoming.content}

【最近的聊天记录】（时间升序，"我"= 用户本人）
{format_history(context) or "（无更早记录）"}

【用户平时的说话风格样例】（模仿这种用词和语气）
{style_block}

【本次需要的语气/倾向】（{max_candidates} 条，尽量覆盖，也可按场景微调）
{tone_lines}

现在输出 JSON。""".replace("{n}", str(max_candidates))
    # 上面 replace 兜底：SYSTEM 里的 {n} 占位
    return SYSTEM.replace("{n}", str(max_candidates)), user


def build_refine_prompt(label: str, display_name: str, note: str, incoming: Message,
                        context: list[Message], tone: str, current_text: str,
                        rounds: list[dict]) -> tuple[str, str]:
    history = "\n".join(
        f"第{i + 1}轮 用户意见：{r.get('comment', '')}\n      修改后：{r.get('revised', '')}"
        for i, r in enumerate(rounds)) or "（这是第一轮修改）"
    user = f"""【聊天对象】{label} · {display_name}　【用户说明】{note or "（无）"}

【对方刚发来的消息】
{incoming.content}

【最近聊天记录】
{format_history(context[-12:]) or "（无）"}

【候选语气】{tone}
【当前这版回复】
{current_text}

【修改历史】
{history}

【用户本轮意见】
{rounds[-1].get("comment", "") if rounds else ""}

请输出修改后的回复正文。"""
    return REFINE_SYSTEM, user


def build_tone_prompt(incoming: Message, current_texts: list[str], tones: list[dict],
                      max_candidates: int) -> tuple[str, str]:
    """「换一批」：在已有候选之外再出几条不同角度。"""
    avoid = "\n".join(f"- {t}" for t in current_texts) or "（无）"
    tone_lines = "\n".join(f"- {t.get('name')}：{t.get('hint', '')}" for t in tones)
    user = f"""对方刚发来：
{incoming.content}

已经给过这些候选（请换角度，不要重复意思）：
{avoid}

再给 {max_candidates} 条，语气参考：
{tone_lines}
输出 JSON。"""
    return SYSTEM.replace("{n}", str(max_candidates)), user
