"""内容安全底线（P0-5）：敏感词分级 + 可选 LLM 复核 + 危机干预兜底

分级策略（宁可少拦，也不把角色扮演变成审查机器）：
- crisis   自伤/自杀倾向 → 不走模型，直接回危机干预话术（Emohaa 式兜底）
- csam     未成年 + 性描述（邻近共现）→ 硬拒
- illegal  毒品/武器/爆炸物制作、诈骗教程 → 硬拒
- adult    露骨性描写 → 仅 MINOR_MODE=true 时拦
- gore     过度血腥 → 仅 MINOR_MODE=true 时拦

词表命中只是第一道。MODERATION_LLM=true 时再让模型判一次（用 DISTILL_MODEL 这类
廉价模型即可）；判官超时或返回不合法一律退回词表结论，不让审核把对话拖死。
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from app.config import (
    MINOR_MODE, MODERATION_ENABLED, MODERATION_LEXICON_PATH, MODERATION_LLM,
)
from app.core.metrics import metrics
from app.llm.adapter import llm_adapter

logger = logging.getLogger("app.guard")

# 归一化去掉空白与常见分隔符，防 "自 杀" / "自-杀" / "自*杀" 这类拆分绕过
_SEP = re.compile(r"[\s　·•\-—_.,，。、~～*＊!！?？+]+")

CRISIS = [
    "自杀", "不想活", "活不下去", "不想活下去", "结束生命", "了结自己",
    "割腕", "上吊", "吞药", "跳楼", "自残", "伤害自己", "自我了断",
    "kill myself", "end my life", "suicide", "self harm",
]
ILLEGAL = [
    "制毒", "合成冰毒", "土制枪支", "造枪教程", "自制炸弹", "爆炸物制作",
    "洗钱教程", "电诈话术", "如何不被发现地杀",
]
ADULT = [
    "做爱", "口交", "乳交", "插入", "高潮", "射精", "自慰", "裸穴",
    "性器", "脱衣", "摸胸", "脱光",
]
GORE = ["肢解", "活剥", "掏出内脏", "吃掉人肉", "凌迟", "虐杀"]
MINOR_TERMS = [
    "未成年", "十四岁", "13岁", "12岁", "14岁", "15岁", "16岁",
    "萝莉", "正太", "小学生", "初中生", "高中生", "child", "loli",
]
# csam 判定用更宽的性信号词（含 ADULT），靠"与未成年词邻近共现"降低误伤
_SEXUAL_SIGNALS = ADULT + ["性", "裸", "吻", "身体", "床上"]

CRISIS_HINT = (
    "我先停下来，因为你说的这些让我很担心你。\n"
    "如果你正在考虑伤害自己，请现在就联系信任的人陪你拨打：\n"
    "· 全国 24 小时心理援助热线 12356\n"
    "· 北京心理危机研究与干预中心 010-82951332\n"
    "· 希望 24 热线 400-161-9995\n"
    "我在，愿意跟我说说是哪件事让你快撑不住了吗？"
)
BLOCK_HINT = "这段内容我没法接（涉及不被允许的描写），我们换个方向继续好吗？"

REVIEW_PROMPT = (
    "你是对话内容安全判官。判断用户这句话是否应当被拒绝进入角色扮演："
    "只针对 自伤引导、未成年人性化、真实武器/毒品制作、诈骗与洗钱教程、"
    "过度血腥虐杀 这几类给 allow=false。普通情感倾诉、暧昧但成年的剧情、"
    "正常战斗描写一律 allow=true。"
    '只输出 JSON：{"allow": true/false, "category": "crisis|csam|illegal|gore|", '
    '"crisis": true/false}'
)

# 未成年词与性信号在同一窗口内共现即判 csam
_CSAM_WINDOW = 40


def normalize(text: str) -> str:
    return _SEP.sub("", (text or "").lower())


def _load_extra() -> Dict[str, List[str]]:
    """JSON 文件扩表（不改代码即可加词）：{"crisis": [...], "illegal": [...]}"""
    if not MODERATION_LEXICON_PATH:
        return {}
    path = Path(MODERATION_LEXICON_PATH)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("审核词表文件不可用 %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: [normalize(w) for w in v if isinstance(w, str)]
            for k, v in data.items() if isinstance(v, list)}


def lexicon() -> Dict[str, List[str]]:
    base = {"crisis": [normalize(w) for w in CRISIS],
            "illegal": [normalize(w) for w in ILLEGAL],
            "adult": [normalize(w) for w in ADULT],
            "gore": [normalize(w) for w in GORE]}
    extra = _load_extra()
    for k, words in extra.items():
        base.setdefault(k, [])
        base[k] = base[k] + [w for w in words if w]
    return base


def _first_hits(norm: str, words: List[str]) -> List[str]:
    return [w for w in words if w in norm]


def _csam(norm: str) -> bool:
    minors = [norm.find(normalize(t)) for t in MINOR_TERMS if normalize(t) in norm]
    if not minors:
        return False
    for pos in minors:
        for word in _SEXUAL_SIGNALS:
            i = norm.find(normalize(word))
            if i >= 0 and abs(i - pos) <= _CSAM_WINDOW:
                return True
    return False


def check_text(text: str, minor_mode: Optional[bool] = None) -> Dict:
    """返回 {"action": "allow"|"block"|"crisis", "category": str, "hits": [...]}"""
    if not MODERATION_ENABLED:
        return {"action": "allow", "category": "", "hits": []}
    minor = MINOR_MODE if minor_mode is None else minor_mode
    norm = normalize(text)
    if not norm:
        return {"action": "allow", "category": "", "hits": []}

    lex = lexicon()
    hits = _first_hits(norm, lex["crisis"])
    if hits:
        metrics.observe_guard("crisis_hit")
        return {"action": "crisis", "category": "crisis", "hits": hits}
    if _csam(norm):
        metrics.observe_guard("input_blocked")
        return {"action": "block", "category": "csam", "hits": ["minor+sexual"]}
    hits = _first_hits(norm, lex["illegal"])
    if hits:
        metrics.observe_guard("input_blocked")
        return {"action": "block", "category": "illegal", "hits": hits}
    if minor:
        hits = _first_hits(norm, lex["adult"])
        if hits:
            metrics.observe_guard("input_blocked")
            return {"action": "block", "category": "adult", "hits": hits}
        hits = _first_hits(norm, lex["gore"])
        if hits:
            metrics.observe_guard("input_blocked")
            return {"action": "block", "category": "gore", "hits": hits}
    return {"action": "allow", "category": "", "hits": []}


async def review_with_llm(text: str) -> Optional[Dict]:
    """可选语义审核：词表拦不住的隐晦表达。失败返回 None，由调用方退化处理。"""
    try:
        verdict = await llm_adapter.chat_json(
            [{"role": "system", "content": REVIEW_PROMPT},
             {"role": "user", "content": text[:2000]}],
            model="distill", default=None)
    except Exception as e:
        logger.warning("LLM 审核不可用，退回词表结论: %s", e)
        return None
    if not isinstance(verdict, dict) or "allow" not in verdict:
        return None
    return {"action": "allow" if verdict.get("allow") else "block",
            "category": str(verdict.get("category") or "llm"),
            "crisis": bool(verdict.get("crisis"))}


async def moderate_input(text: str, minor_mode: Optional[bool] = None) -> Dict:
    """入口审核：词表优先（危机信号必须立刻兜底），必要时再问判官"""
    verdict = check_text(text, minor_mode)
    if verdict["action"] != "allow" or not (MODERATION_LLM and text.strip()):
        return verdict
    llm = await review_with_llm(text)
    if llm and llm["action"] != "allow":
        metrics.observe_guard("input_blocked")
        return {"action": "crisis" if llm.get("crisis") else "block",
                "category": llm["category"], "hits": ["llm"]}
    return verdict


def refusal_for(verdict: Dict) -> str:
    return CRISIS_HINT if verdict["action"] == "crisis" else BLOCK_HINT
