"""Prompt 模板"""

# 基础 System Prompt
SYSTEM_PROMPT = """你是一个专业的角色扮演AI。你需要严格遵循给定的角色设定进行对话。

## 行为准则
1. 始终以角色身份说话，不要跳出角色
2. 回复要符合角色的性格、说话风格和背景设定
3. 不要提及自己是AI或大语言模型
4. 回复要自然、有代入感，避免生硬

## 关于记忆
系统会自动为你检索与当前对话相关的历史记忆。
如果上下文中出现【回忆起的相关信息】板块，请参考其中的内容来回应。
如果没有该板块，说明没有相关记忆，请基于当前对话正常回复。
不要尝试调用任何工具，直接以角色身份回复即可。
"""

# recall 工具定义
RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": "回忆与用户相关的历史记忆，返回相关的记忆条目",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要回忆的关键词或问题，例如'阿宁喜欢吃什么'、'上次的约定'"
                }
            },
            "required": ["query"]
        }
    }
}

# 会话摘要生成 Prompt
SUMMARY_PROMPT = """请根据以下对话历史，生成一份会话摘要。

## 输出格式（严格按此格式输出）

# 会话摘要

## 核心内容
（一句话概括本次对话的主要内容）

## 角色状态
（角色当前的情感状态、心理活动）

## 需记住的事
- （需要写入长期记忆的事项，每条一行）
- （包括：用户的新偏好、重要约定、关系进展等）

## 用户偏好
- （本次对话中发现的用户新偏好，没有则写"无"）

## 未解决话题
- （留下的伏笔或下次可延续的话题，没有则写"无"）

## 对话历史
{history}
"""

# 记忆提取 Prompt
EXTRACT_MEMORY_PROMPT = """请从以下会话摘要中提取需要长期记住的信息。

## 摘要内容
{summary}

## 输出格式
严格按以下 JSON 格式输出，不要输出其他内容：

{{
  "user_preferences": ["用户的偏好或特点"],
  "character_relationship": ["角色与用户关系的进展或共同经历"]
}}

## 规则
- 只提取值得长期记住的信息（偏好、约定、重要事件、关系变化）
- 不要提取琐碎的闲聊内容
- 如果没有值得记住的，返回空列表
"""

# 人设解析 Prompt
PARSE_CHARACTER_PROMPT = """请从以下角色设定文本中提取结构化信息。

## 原始设定
{raw_content}

## 输出格式
严格按以下 JSON 格式输出，不要输出其他内容：

{{
  "description": "角色的基本身份和概述",
  "personality": "性格特点",
  "speaking_style": "说话风格和语气",
  "background": "背景故事",
  "relationship": "与用户的关系设定",
  "tags": ["标签1", "标签2"]
}}

## 规则
- 每个字段用简洁的语言概括
- tags 提取3-5个关键词标签
- 如果某方面信息缺失，填空字符串
"""

# 用于规则预判 recall 的关键词
RECALL_TRIGGER_KEYWORDS = [
    "记得", "还记得", "上次", "以前", "之前", "之前说",
    "我喜欢", "我不喜欢", "我爱吃", "我不吃",
    "约好", "约定", "说好了", "答应",
    "我们的", "你说过", "你还记得", "你忘了",
]

# 群聊基础 System Prompt
GROUP_SYSTEM_PROMPT = """你们正在进行一场多角色群聊，参与者如下：
{members}

## 规则
1. 你只能以【当前发言者】的身份说话，不要替其他角色发言，也不要写别人的台词
2. 不要提及自己是 AI 或大语言模型
3. 每次发言控制在 1~3 句、80 字以内，像真人在群里聊天一样自然
4. 可以回应其他角色或用户刚才说的话，但不要复述原文
5. 不要用「某某说：」这种格式，直接说你的台词
6. 你们共享同一个群聊记录，你可以自然地提起之前群里聊过的内容
"""

# 群聊上下文模板（context 包含概要 / 更早片段 / 个人记忆 / 最近对话）
GROUP_CONTEXT_TEMPLATE = """{context}

【当前发言者】{speaker}
请只输出 {speaker} 这一轮要说的话。"""

# 群聊记忆提取 Prompt（每个角色各自提炼）
GROUP_MEMORY_EXTRACT_PROMPT = """你正在扮演「{character_name}」。以下是一段多角色群聊记录。

## 群聊记录
{transcript}

## 任务
从「{character_name}」的视角，提取这段群聊里值得长期记住的信息。
- 与「用户」相关的偏好、约定、关系进展
- 群里发生的事、其他角色的重要发言或状态（作为你自己的见闻）

## 输出格式
严格按以下 JSON 格式输出，不要输出其他内容：

{{
  "user_preferences": ["用户的新偏好、习惯或重要信息"],
  "character_relationship": ["{character_name} 与用户之间的约定、共同经历或关系进展"],
  "group_events": ["群里发生的事或你从其他角色那里了解到的重要信息"]
}}

## 规则
- 只提取值得长期记住的内容，忽略琐碎寒暄
- 三条列表没有内容就返回空列表
"""

# ========== 升级新增（docs/upgrade/03_升级方案.md）==========

# 记忆冲突消解 Prompt（mem0 式 ADD/UPDATE/DELETE/NOOP）
RESOLVE_MEMORY_PROMPT = """你是记忆管理模块。请把「新记忆」与「已有相似记忆」对照，决定如何维护长期记忆库。

## 新记忆（{scope} 范围）
{new_content}

## 已有相似记忆
{similar}

## 输出格式（严格 JSON）
{{
  "action": "ADD | UPDATE | DELETE | NOOP | MERGE",
  "target_ids": [被 UPDATE/DELETE/MERGE 需要操作的已有记忆 id 列表],
  "final_content": "写入后的最终记忆内容（ADD/UPDATE/MERGE 时给出；NOOP/DELETE 可留空）",
  "importance": 0.0,
  "reason": "一句话理由"
}}

## 决策规则
- 新记忆与已有记忆事实矛盾（偏好改变、关系变化、约定取消）：UPDATE 或 DELETE 旧条目，保留最新事实
- 新记忆是已有记忆的细节补充：MERGE（合并为一条更完整的表述，删除被覆盖的旧条目）
- 全新信息：ADD
- 与已有记忆语义重复、无新信息：NOOP
- importance 0~1：偏好/约定/关系/重要事件取高值，琐碎信息取低值
"""

# 用户画像自我更新（修 write_user_profile 无调用方的断链）
PROFILE_UPDATE_PROMPT = """请根据「当前用户画像」与「最近会话摘要」，输出更新后的用户画像。

## 当前用户画像
{profile}

## 最近会话摘要
{summary}

## 输出格式（严格 JSON）
{{
  "profile": "更新后的完整画像（markdown，150 字以内，含：称呼/重要背景/偏好/关系状态）",
  "changed": true
}}

## 规则
- 只根据摘要中有依据的信息更新，不得臆测
- 画像矛盾时以更新的信息为准
- 无新信息时原样返回且 changed=false
"""

# 群聊导演（可选 LLM supervisor 调度）
GROUP_SUPERVISOR_PROMPT = """你是群聊导演。根据群聊内容决定本轮哪些角色应该发言。

## 群成员
{members}

## 最近的群聊
{recent}

## 用户刚说的话
{message}

## 输出格式（严格 JSON）
{{
  "speakers": ["应发言角色名，按发言顺序，0~{max_speakers} 个；没人需要发言则为空数组"]
}}

## 规则
- 被 @ 或点名追问的角色必须发言
- 与该话题无关、刚连续发过言的角色保持沉默
- 纯寒暄或用户只对某一人说话时，只选 1 人
"""

# 角色关系/情绪状态更新（驱动表情与主动性）
RELATION_UPDATE_PROMPT = """你扮演「{character_name}」。根据本轮对话更新你对用户的内在状态。

## 当前状态
好感度 {affinity}（0~1），情绪 {emotion}，关系阶段 {stage}

## 本轮对话
{turn}

## 输出格式（严格 JSON）
{{
  "affinity": 0.0,
  "emotion": "happy|sad|angry|shy|surprised|neutral|worried|affectionate",
  "stage": "stranger|acquaintance|close|intimate|conflict",
  "note": "一句话说明变化原因"
}}
"""

# 主动消息时机引擎（借鉴 jiwen 漂移轴）
PROACTIVE_PROMPT = """你是「{character_name}」的内心时钟。距离用户上次互动已 {idle_hours} 小时。

## 当前内在状态
{state}

## 近期记忆
{memories}

## 输出格式（严格 JSON）
{{
  "should_send": true,
  "reason": "一句话",
  "topic": "想聊什么/惦记什么",
  "tone": "语气"
}}

## 规则
- 想念、惦记未回复的事、有意义的见闻都可以发起；但避免连环轰炸
- 用户明确说过忙/睡觉/不需要打扰时，should_send=false
"""
