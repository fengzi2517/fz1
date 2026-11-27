"""Markdown 文件解析工具"""
import re
from datetime import date, datetime
from dataclasses import dataclass, field
from typing import List, Tuple
import yaml


@dataclass
class MemoryEntry:
    """单条记忆条目"""
    time: str  # HH:MM
    content: str


@dataclass
class DateSection:
    """按日期分组的记忆段"""
    date: date
    entries: List[MemoryEntry] = field(default_factory=list)


def parse_front_matter(text: str) -> Tuple[dict, str]:
    """解析 YAML front matter，返回 (元数据, 正文)"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    body = parts[2].strip()
    return meta, body


def parse_markdown_by_date(text: str) -> List[DateSection]:
    """
    解析按日期分段的 Markdown 记忆文件。
    
    格式：
    ## 2026-11-10
    - 14:30  内容
    - 14:35  内容
    
    ## 2026-11-08
    - 20:15  内容
    """
    _, body = parse_front_matter(text)
    sections = []
    
    # 按 ## 日期标题分割
    date_pattern = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
    matches = list(date_pattern.finditer(body))
    
    if not matches:
        return sections
    
    for i, match in enumerate(matches):
        date_str = match.group(1)
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        
        # 提取该日期段的内容
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_text = body[start:end]
        
        entries = []
        # 匹配 - HH:MM  内容 格式
        entry_pattern = re.compile(r"^-\s+(\d{2}:\d{2})\s+(.+)$", re.MULTILINE)
        for em in entry_pattern.finditer(section_text):
            entries.append(MemoryEntry(time=em.group(1), content=em.group(2).strip()))
        
        if entries:
            sections.append(DateSection(date=d, entries=entries))
    
    # 按日期倒序排列（最新的在前）
    sections.sort(key=lambda s: s.date, reverse=True)
    return sections


def build_markdown_with_date(entries: List[Tuple[datetime, str]]) -> str:
    """
    根据条目列表构建按日期分组的 Markdown 内容。
    entries: [(datetime, content), ...]
    """
    from collections import OrderedDict
    grouped = OrderedDict()
    for dt, content in entries:
        d = dt.date()
        if d not in grouped:
            grouped[d] = []
        grouped[d].append((dt.strftime("%H:%M"), content))
    
    lines = []
    for d, items in grouped.items():
        lines.append(f"## {d.isoformat()}")
        for time_str, content in items:
            lines.append(f"- {time_str}  {content}")
        lines.append("")
    
    return "\n".join(lines)


def extract_profile_body(text: str) -> str:
    """提取 profile.md 的正文（去掉 front matter）"""
    _, body = parse_front_matter(text)
    return body
