# -*- coding: utf-8 -*-
"""贵阳观山湖医院：科室 / 医生 / 号源 / 检查 / 住院 / 医保结算。

用户口径（2026-11-25）：医疗改成「医院」，要有完整的架构。

本模块只给数据与纯函数，不落库、不发消息。金额单位：元。

一次就诊 = 挂号费 + 诊疗费 + （检查费）+ 药费，按医保规则结算；
住院 = 押金 + 床位日费 × 天数 + 治疗费，出院结算。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

HOSPITAL = {
    "name": "中国陆军医疗医院",
    "level": "三级甲等",
    "since": 1958,
    "phone": "0851-8xx xxxx（总机）",
    "areas": ["贵阳院区（门诊/住院）", "金阳院区（儿科/妇产）", "体检中心（独立楼）"],
    "beds": 1200,
    "staff": 1860,
    "dean": "苏静（院长，外科出身）",
    "note": "三甲军队医院；二婶苏静是院长，姨妈江馥贞是心内科主任——走熟人路子，账照算。",
    "rules": [
        "门诊先挂号再看诊；专家号需提前一天约",
        "检查开单后 24 小时内有效",
        "住院需押金；出院当日结算",
        "职工医保：门诊起付 200，报销 55%；住院起付 800，报销 82%",
    ],
}

# 科室：挂号费按号别（普通/专家/特需）
DEPARTMENTS: List[Dict[str, Any]] = [
    {"id": "internal", "name": "内科", "floor": "门诊楼 2F", "head": "江馥贞",
     "focus": "感冒发烧、肠胃不适、血压血糖", "wait": "30~50 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["血常规", "生化全套", "心电图"]},
    {"id": "surgery", "name": "外科", "floor": "门诊楼 3F", "head": "苏静",
     "focus": "外伤、阑尾、疝气、换药拆线", "wait": "20~40 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["血常规", "腹部彩超", "CT"]},
    {"id": "ortho", "name": "骨科", "floor": "门诊楼 3F", "head": "何斯年",
     "focus": "颈椎腰椎、扭伤、骨折", "wait": "25~45 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["X 光", "CT", "核磁"]},
    {"id": "gastro", "name": "消化内科", "floor": "门诊楼 3F", "head": "陆景明",
     "focus": "胃痛、反酸、腹泻、肝功能", "wait": "30~60 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["胃镜", "腹部彩超", "幽门螺杆菌"]},
    {"id": "resp", "name": "呼吸内科", "floor": "门诊楼 3F", "head": "莫岚",
     "focus": "咳嗽、咽喉、哮喘、肺部", "wait": "25~45 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["胸片", "肺功能", "血常规"]},
    {"id": "cardio", "name": "心内科", "floor": "门诊楼 4F", "head": "江馥贞",
     "focus": "心悸、胸闷、血压、心律", "wait": "35~60 分钟",
     "fees": {"普通": 15, "专家": 60, "特需": 260},
     "exams": ["心电图", "心脏彩超", "动态心电"]},
    {"id": "gyn", "name": "妇科", "floor": "金阳院区 2F", "head": "周叶语",
     "focus": "经期不适、炎症、体检", "wait": "20~40 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 220},
     "exams": ["白带常规", "盆腔彩超", "宫颈筛查"]},
    {"id": "derm", "name": "皮肤科", "floor": "门诊楼 2F", "head": "叶轻舟",
     "focus": "过敏、湿疹、痘、脱发", "wait": "15~30 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 180},
     "exams": ["过敏原筛查", "真菌镜检"]},
    {"id": "eye", "name": "眼科", "floor": "门诊楼 5F", "head": "文竹",
     "focus": "干眼、结膜炎、视力", "wait": "20~35 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 180},
     "exams": ["视力与验光", "眼压", "眼底照相"]},
    {"id": "ent", "name": "耳鼻喉科", "floor": "门诊楼 5F", "head": "石见",
     "focus": "鼻炎、咽炎、耳朵闷", "wait": "20~35 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 180},
     "exams": ["鼻内镜", "纯音测听"]},
    {"id": "dental", "name": "口腔科", "floor": "门诊楼 1F", "head": "凌可",
     "focus": "补牙、洗牙、智齿、正畸", "wait": "预约制",
     "fees": {"普通": 15, "专家": 60, "特需": 240},
     "exams": ["口腔全景片"]},
    {"id": "tcm", "name": "中医科", "floor": "门诊楼 4F", "head": "白素英",
     "focus": "调理、失眠、脾胃、气血", "wait": "30~60 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["脉诊", "中医体质辨识"]},
    {"id": "psych", "name": "心理科", "floor": "门诊楼 6F", "head": "聂行舟",
     "focus": "焦虑、失眠、情绪低落", "wait": "40~70 分钟",
     "fees": {"普通": 20, "专家": 80, "特需": 300},
     "exams": ["心理测评", "睡眠监测"]},
    {"id": "pediatric", "name": "儿科", "floor": "金阳院区 1F", "head": "齐悦",
     "focus": "儿童发热、咳嗽、腹泻", "wait": "25~50 分钟",
     "fees": {"普通": 12, "专家": 50, "特需": 200},
     "exams": ["血常规", "胸片"]},
    {"id": "checkup", "name": "体检中心", "floor": "体检楼", "head": "路遥",
     "focus": "年度体检、入职体检、专项筛查", "wait": "预约制",
     "fees": {"普通": 0, "专家": 0, "特需": 0},
     "exams": ["基础体检", "女性专项", "心脑血管专项"]},
]

EXAMS: Dict[str, Dict[str, Any]] = {
    "血常规": {"price": 35, "mins": 30, "note": "扎手指，当天出"},
    "生化全套": {"price": 180, "mins": 120, "note": "空腹抽血，肝功能肾功能血糖血脂"},
    "心电图": {"price": 45, "mins": 15, "note": "躺两分钟"},
    "动态心电": {"price": 320, "mins": 1440, "note": "背 24 小时盒子"},
    "心脏彩超": {"price": 260, "mins": 40, "note": "看结构与射血分数"},
    "腹部彩超": {"price": 160, "mins": 30, "note": "肝胆胰脾肾，空腹"},
    "盆腔彩超": {"price": 150, "mins": 30, "note": "憋尿"},
    "CT": {"price": 480, "mins": 45, "note": "含造影剂另计"},
    "核磁": {"price": 860, "mins": 60, "note": "体内有金属不能做"},
    "X 光": {"price": 90, "mins": 20, "note": "拍片即走"},
    "胃镜": {"price": 620, "mins": 90, "note": "空腹，可选无痛（+300）"},
    "幽门螺杆菌": {"price": 120, "mins": 40, "note": "吹气"},
    "肺功能": {"price": 180, "mins": 40, "note": "吹气测试"},
    "胸片": {"price": 110, "mins": 20, "note": "正侧位"},
    "过敏原筛查": {"price": 420, "mins": 120, "note": "抽血查 20 项"},
    "真菌镜检": {"price": 60, "mins": 30, "note": "刮一点皮屑"},
    "白带常规": {"price": 45, "mins": 30, "note": "当天出"},
    "宫颈筛查": {"price": 380, "mins": 60, "note": "TCT + HPV"},
    "视力与验光": {"price": 40, "mins": 20, "note": "含试镜"},
    "眼压": {"price": 35, "mins": 15, "note": "吹气测"},
    "眼底照相": {"price": 120, "mins": 30, "note": "散瞳后看不清两小时"},
    "鼻内镜": {"price": 220, "mins": 30, "note": "会有点难受"},
    "纯音测听": {"price": 90, "mins": 30, "note": "戴耳机听声音"},
    "口腔全景片": {"price": 120, "mins": 20, "note": "站机器里转一圈"},
    "心理测评": {"price": 180, "mins": 60, "note": "量表 + 面谈"},
    "睡眠监测": {"price": 680, "mins": 1440, "note": "住一晚，贴电极"},
    "脉诊": {"price": 20, "mins": 15, "note": "中医搭脉"},
    "中医体质辨识": {"price": 60, "mins": 30, "note": "问卷 + 舌象"},
    "基础体检": {"price": 680, "mins": 180, "note": "血尿常规+肝肾+B超+胸片"},
    "女性专项": {"price": 1280, "mins": 240, "note": "加乳腺、宫颈、妇科彩超"},
    "心脑血管专项": {"price": 1880, "mins": 300, "note": "加颈动脉、心脏、同型半胱氨酸"},
}

WARDS = {
    "三人间": {"deposit": 2000, "daily": 90, "note": "公用卫生间，晚上有点吵"},
    "双人间": {"deposit": 4000, "daily": 220, "note": "有电视，陪床一张椅"},
    "单人间": {"deposit": 8000, "daily": 580, "note": "独立卫浴，可以安静养病"},
    "特需病房": {"deposit": 15000, "daily": 1280, "note": "套间，护士一对二"},
}

INSURANCE = {
    "type": "职工医保",
    "outpatient": {"threshold": 200, "ratio": 0.55, "cap": 8000},
    "inpatient": {"threshold": 800, "ratio": 0.82, "cap": 300000},
    "note": "门诊先花够起付线才报；住院按次起付，封顶 30 万。",
}

# 常见病 → 科室（终端那边的病种 key 用它映射）
ILLNESS_DEPT = {
    "cold": "internal", "flu": "internal", "fever": "internal",
    "gastroenteritis": "gastro", "stomach": "gastro", "diarrhea": "gastro",
    "headache": "internal", "insomnia": "psych", "anxiety": "psych",
    "period": "gyn", "cramp": "gyn", "allergy": "derm", "rash": "derm",
    "cough": "resp", "sore_throat": "ent", "rhinitis": "ent",
    "injury": "ortho", "sprain": "ortho", "toothache": "dental",
    "eye": "eye", "fatigue": "tcm",
}


def department(key: str) -> Optional[Dict[str, Any]]:
    key = str(key or "").strip()
    for d in DEPARTMENTS:
        if key in (d["id"], d["name"], d["head"]):
            return dict(d)
    return None


def dept_for(illness: str) -> Dict[str, Any]:
    """按病种找科室，找不到给内科。"""
    key = str(illness or "").strip().lower()
    dep = ILLNESS_DEPT.get(key)
    if dep:
        found = department(dep)
        if found:
            return found
    for k, v in ILLNESS_DEPT.items():
        if k in key or key in k:
            found = department(v)
            if found:
                return found
    return department("internal") or DEPARTMENTS[0]


def register(illness: str = "", tier: str = "专家") -> Dict[str, Any]:
    """挂号：给科室、医生、号别和费用。"""
    dep = dept_for(illness)
    tier = tier if tier in ("普通", "专家", "特需") else "专家"
    fee = float((dep.get("fees") or {}).get(tier) or 0)
    return {
        "dept": dep["name"], "dept_id": dep["id"], "head": dep["head"],
        "floor": dep["floor"], "tier": tier, "fee": fee,
        "wait": dep.get("wait") or "30 分钟",
        "focus": dep.get("focus") or "",
    }


def exam_list(dept_key: str) -> List[Dict[str, Any]]:
    dep = department(dept_key)
    if not dep:
        return []
    out = []
    for name in dep.get("exams") or []:
        info = dict(EXAMS.get(name) or {})
        info["name"] = name
        out.append(info)
    return out


def _insurance(kind: str, gross: float) -> Dict[str, Any]:
    rule = INSURANCE.get(kind) or INSURANCE["outpatient"]
    threshold = float(rule["threshold"])
    ratio = float(rule["ratio"])
    cap = float(rule["cap"])
    payable = max(0.0, gross - threshold)
    reimburse = min(cap, payable * ratio)
    return {
        "type": INSURANCE["type"], "gross": round(gross, 2),
        "threshold": threshold, "ratio": ratio,
        "reimburse": round(reimburse, 2),
        "self_pay": round(max(0.0, gross - reimburse), 2),
    }


def visit(illness: str = "", tier: str = "专家", exams: Optional[List[str]] = None,
          medicine: float = 0.0, clinic_fee: float = 0.0) -> Dict[str, Any]:
    """一次门诊：挂号 + 诊疗 + 检查 + 药，按医保结算。"""
    reg = register(illness, tier)
    picked = []
    if exams is None:
        exams = [x["name"] for x in exam_list(reg["dept_id"])[:1]]
    for name in exams:
        info = EXAMS.get(name)
        if not info:
            continue
        picked.append({"name": name, "price": float(info["price"]), "mins": info.get("mins"),
                       "note": info.get("note", "")})
    exam_total = sum(x["price"] for x in picked)
    gross = float(reg["fee"]) + float(clinic_fee or 0) + exam_total + float(medicine or 0)
    bill = _insurance("outpatient", gross)
    bill.update({
        "kind": "门诊", "dept": reg["dept"], "doctor": reg["head"], "tier": reg["tier"],
        "items": [
            {"name": "挂号费（%s号）" % reg["tier"], "price": float(reg["fee"])},
            {"name": "诊疗费", "price": float(clinic_fee or 0)},
        ] + [{"name": "检查：" + x["name"], "price": x["price"]} for x in picked]
          + ([{"name": "药费", "price": float(medicine)}] if medicine else []),
        "exams": picked, "register": reg,
    })
    return bill


def admit(days: int = 3, room: str = "双人间", treat_fee: float = 0.0) -> Dict[str, Any]:
    """住院：押金 + 床位日费 × 天数 + 治疗费，出院按住院比例结算。"""
    info = WARDS.get(room) or WARDS["双人间"]
    days = max(1, int(days or 1))
    bed = float(info["daily"]) * days
    gross = bed + float(treat_fee or 0)
    bill = _insurance("inpatient", gross)
    bill.update({
        "kind": "住院", "room": room, "days": days,
        "deposit": float(info["deposit"]),
        "items": [
            {"name": "床位费 %s × %d 天" % (room, days), "price": round(bed, 2)},
            {"name": "治疗费", "price": float(treat_fee or 0)},
        ],
        "room_note": info.get("note", ""),
    })
    return bill


def outline() -> Dict[str, Any]:
    """给上下文/面板用的一页架构说明。"""
    return {
        "hospital": HOSPITAL,
        "departments": [
            {"name": d["name"], "head": d["head"], "floor": d["floor"],
             "focus": d["focus"], "fees": d["fees"], "exams": d["exams"]}
            for d in DEPARTMENTS
        ],
        "wards": WARDS,
        "insurance": INSURANCE,
        "exams": len(EXAMS),
    }


if __name__ == "__main__":   # python3 hospital.py
    import json
    print(json.dumps(visit("cold", "专家", ["血常规"], medicine=48, clinic_fee=20), ensure_ascii=False)[:600])
    print(json.dumps(admit(3, "双人间", 1200), ensure_ascii=False)[:400])
