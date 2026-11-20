# -*- coding: utf-8 -*-
"""生理期：现在归医疗管（2026-11-15 从终端 HumanState 整块搬过来）。

终端不再算生理期，需要时问医疗要 `period_info()`。这里只做纯计算，
存储（上次开始日期）由医疗插件的状态文件负责。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

PHASES = ("月经期", "卵泡期", "排卵期", "黄体期")

DEFAULTS = {
    "enable_period_simulate": True,
    "period_cycle_days": 28,
    "period_days": 5,
    "period_ovulation_day": 14,
    "period_ovulation_window": 3,
    "period_menstrual_tone": "身体沉沉的，容易累，也更容易委屈",
    "period_follicular_tone": "身体轻快，心情慢慢变好",
    "period_ovulatory_tone": "精力最好，也更想和人说话",
    "period_luteal_tone": "容易烦躁、敏感，想要被哄",
}


def _md5_int(text: str) -> int:
    return int(hashlib.md5(str(text).encode("utf-8")).hexdigest()[:8], 16)


def parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def compute(start=None, today=None, cycle_days=28, bleed_days=5,
            ovulation_day=14, ovulation_window=3, tones=None,
            enabled=True) -> dict:
    """算今天处在周期哪一天、什么阶段、痛感多少。

    start 认不出就按「10 天前开始」处理（和她原来的兜底一致）。
    """
    tones = dict(DEFAULTS, **(tones or {}))
    cycle_days = max(10, int(cycle_days or 28))
    bleed_days = max(1, min(int(bleed_days or 5), cycle_days - 6))
    if not enabled:
        return {
            "day": 0, "phase": "非生理期", "phase_day": 0, "tone": "", "pain": 0,
            "reply_mult": 1.0, "days_to_next": 0, "cycle_days": cycle_days,
            "bleed_days": 0, "next_start": "", "start": str(start or ""),
        }
    today = today or date.today()
    begin = parse_day(start) or (today - timedelta(days=10))
    day = (today - begin).days % cycle_days + 1
    ovulation_day = max(bleed_days + 1, min(cycle_days - 1, int(ovulation_day or 14)))
    ovulation_window = max(1, min(9, int(ovulation_window or 3)))
    ovulation_start = max(bleed_days + 1, ovulation_day - (ovulation_window - 1) // 2)
    ovulation_end = min(cycle_days - 1, ovulation_start + ovulation_window - 1)

    if day <= bleed_days:
        phase = "月经期"
        tone = tones["period_menstrual_tone"]
        pain = min(9, 5 + max(0, bleed_days + 1 - day))
        reply_mult = 0.8
        phase_day = day
    elif day < ovulation_start:
        phase = "卵泡期"
        tone = tones["period_follicular_tone"]
        pain = 1
        reply_mult = 1.05
        phase_day = day - bleed_days
    elif day <= ovulation_end:
        phase = "排卵期"
        tone = tones["period_ovulatory_tone"]
        pain = 0
        reply_mult = 1.12
        phase_day = day - ovulation_start + 1
    else:
        phase = "黄体期"
        tone = tones["period_luteal_tone"]
        pain = 3
        reply_mult = 0.88
        phase_day = day - ovulation_end

    pain = max(0, min(10, pain + (_md5_int(str(begin) + str(day)) % 3) - 1))
    return {
        "day": day,
        "phase": phase,
        "phase_day": max(1, phase_day),
        "tone": tone,
        "pain": pain,
        "reply_mult": reply_mult,
        "days_to_next": cycle_days - day + 1,
        "cycle_days": cycle_days,
        "bleed_days": bleed_days,
        "ovulation_day": ovulation_day,
        "ovulation_window": ovulation_window,
        "next_start": (today + timedelta(days=cycle_days - day + 1)).isoformat(),
        "start": begin.isoformat(),
    }
