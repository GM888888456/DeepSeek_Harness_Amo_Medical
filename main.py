# -*- coding: utf-8 -*-
"""我想永远陪着你-医疗（Astrbot_Amo_Medical）。

她生病了，要不要治、治不治得起，在这里算。

**关键设计：治病前必须向终端申请扣款。**

```
医疗算出要花 2400
  → 上报终端（Amo）
  → 终端下发金融扣款 + 数据记账
  → 扣到了？执行治疗 : 不治疗，病情继续
```

医疗自己**不碰钱**，也不判断"该不该花"——它只负责「这病怎么治、多少钱、医保报多少」，
钱的事交给终端统一调度。这样每笔账她都知情。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger

try:
    from . import config_watch as _config_watch
except ImportError:
    import config_watch as _config_watch  # type: ignore

try:
    from . import period as _period
except ImportError:
    import period as _period  # type: ignore
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.api.web import error_response, json_response, request as web_request
except Exception:  # pragma: no cover
    error_response = json_response = web_request = None

try:
    from .chain import Chain
except ImportError:
    from chain import Chain  # type: ignore

try:
    from .catalog import (
        HealthState,
        get_illness,
        insurance_split,
        roll_illness,
        will_tough_it_out,
    )
except Exception:  # pragma: no cover
    from catalog import (  # type: ignore
        HealthState,
        get_illness,
        insurance_split,
        roll_illness,
        will_tough_it_out,
    )

try:
    from .link import probe as _link_probe
except Exception:  # pragma: no cover
    from link import probe as _link_probe  # type: ignore

try:
    from .catalog import (MEDICINES, MODE_LABELS, environment_factors,
                          next_stage, roll_illness, season_of_month,
                          treatment_plan)
except Exception:  # pragma: no cover
    from catalog import (MEDICINES, MODE_LABELS, environment_factors,  # type: ignore
                         next_stage, roll_illness, season_of_month,
                         treatment_plan)

PLUGIN_NAME = "DeepSeek_Harness_Amo_Medical"
PLUGIN_VERSION = "1.0.0"
DISPLAY_NAME = "DeepSeek Harness Amo Medical"

try:
    import yaml as _meta_yaml

    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata.yaml"),
        encoding="utf-8",
    ) as _fh:
        _meta = _meta_yaml.safe_load(_fh) or {}
    if _meta.get("version"):
        PLUGIN_VERSION = str(_meta["version"])
except Exception:
    pass

SICK_STATUSES = ("轻微不适", "感冒", "低烧", "发烧", "不舒服", "生病", "拉肚子",
                 "肠胃炎", "阑尾炎", "骨折", "过度疲劳")


def _now() -> float:
    return time.time()


class AmoMedicalPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 配置自愈：升级/重装/改目录名之后，把空掉的 key 从快照补回来。
        # 快照在 plugin_data/<插件名>/config_snapshot.json（0600），升级不覆盖、
        # schema 重写也删不到它。
        try:
            from .config_guard import heal as _cfg_heal, fallback_data_dir as _cfg_data_dir
        except ImportError:
            try:
                from config_guard import heal as _cfg_heal, fallback_data_dir as _cfg_data_dir
            except Exception:
                _cfg_heal = None
        if _cfg_heal is not None:
            try:
                _guard_dir = os.path.dirname(os.path.abspath(__file__))
                _healed = _cfg_heal(self.config, plugin_name=PLUGIN_NAME,
                                    plugin_dir=_guard_dir,
                                    data_dir=_cfg_data_dir(_guard_dir, PLUGIN_NAME),
                                    logger=logger)
                if _healed.get("restored"):
                    logger.info("[config] 本次启动补回 %d 项配置（%s）"
                                % (len(_healed["restored"]), ", ".join(_healed["restored"])))
            except Exception as exc:
                logger.debug("[config] 配置自愈失败: %s" % exc)
        self._started_at = _now()
        self._health = HealthState()
        self._history: list[dict[str, Any]] = []
        self._rng = random.Random()
        self._last_roll = ""
        self._load()
        # 后台：一天掷一次「今天会不会不舒服」，以及「拖着不治会不会变重」
        try:
            self._auto_task = asyncio.create_task(self._auto_loop())
        except Exception:
            self._auto_task = None
        self._register_web_apis()
        logger.info(
            "[amo_med] %s %s 已加载：当前 %s"
            % (DISPLAY_NAME, PLUGIN_VERSION, self._health.status)
        )

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _bool(self, key: str, default: bool) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _int(self, key: str, default: int) -> int:
        try:
            return int(float(self._cfg(key, default)))
        except Exception:
            return default

    def _float(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except Exception:
            return default

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _data_dir(self) -> str:
        base = ""
        for getter in ("get_plugin_data_dir", "plugin_data_dir"):
            try:
                value = getattr(self.context, getter, None)
                value = value() if callable(value) else value
                value = str(value or "").strip()
                if value:
                    base = value
                    break
            except Exception:
                continue
        if not base:
            base = os.path.join(os.getcwd(), "data", "plugin_data")
        path = os.path.join(base, PLUGIN_NAME)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        return path

    def _state_path(self) -> str:
        return os.path.join(self._data_dir(), "health.json")

    def _load(self) -> None:
        try:
            with open(self._state_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._last_roll = str(data.get("last_roll") or "")
        health = data.get("health") or {}
        self._health = HealthState(
            status=str(health.get("status") or "健康"),
            since=float(health.get("since") or 0),
            until=float(health.get("until") or 0),
            note=str(health.get("note") or ""),
            illness=str(health.get("illness") or ""),
            treated=bool(health.get("treated")),
            used_insurance=float(health.get("used_insurance") or 0),
        )
        history = data.get("history")
        self._history = list(history) if isinstance(history, list) else []
        self._period_start = str(data.get("period_start") or "")

    def _save(self) -> None:
        payload = {"health": self._health.as_dict(), "history": self._history[-200:],
                   "last_roll": self._last_roll,
                   "period_start": str(getattr(self, "_period_start", "") or "")}
        try:
            tmp = self._state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_path())
        except Exception as exc:
            logger.warning("[amo_med] 状态写入失败: %s" % exc)

    # ------------------------------------------------------------------
    # 病情
    # ------------------------------------------------------------------

    async def _auto_loop(self) -> None:
        """每 30 分钟看一次：今天要不要掷骰子、有没有拖成大病。"""
        import asyncio
        first = True
        while True:
            try:
                if first:
                    first = False
                    self._emit_period_event()
                await asyncio.sleep(1800)
                self._auto_roll()
                self._progress_roll()
                self._emit_period_event()
                self._emit_health_event()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("[amo_med] 自动病情检查失败: %s" % exc)

    def _emit_health_event(self) -> None:
        """生病/治好：只发事件，心流加减交给终端。"""
        terminal = self._companion()
        emitter = getattr(terminal, "emit_event", None) if terminal is not None else None
        if not callable(emitter):
            return
        info = self.status() or {}
        today = datetime.now().strftime("%Y-%m-%d")
        marks = self._state.get("health_events")
        if not isinstance(marks, dict):
            marks = {}
        changed = False
        if info.get("sick") and marks.get("sick_day") != today:
            try:
                emitter("medical_sick", {"severity": 6})
            except Exception as exc:
                logger.debug("[amo_med] 发 medical_sick 失败: %s" % exc)
            marks["sick_day"] = today
            changed = True
        if getattr(self._health, "treated", False) and marks.get("treated_day") != today:
            try:
                emitter("medical_treated", {})
            except Exception as exc:
                logger.debug("[amo_med] 发 medical_treated 失败: %s" % exc)
            marks["treated_day"] = today
            changed = True
        if changed:
            self._state["health_events"] = marks
            self._save()

    def _emit_period_event(self) -> None:
        """生理期开始/结束：只发事件给终端总线，本插件不碰心流数值。

        事件里带 ``severity``（痛经等级 0–10），由 heartflow 决定加减多少。
        """
        terminal = self._companion()
        emitter = getattr(terminal, "emit_event", None) if terminal is not None else None
        if not callable(emitter):
            return
        info = self.period_info() or {}
        try:
            day = int(info.get("day") or 0)
            bleed = int(info.get("bleed_days") or 0)
        except (TypeError, ValueError):
            return
        if not day or not bleed:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        marks = self._state.get("period_events")
        if not isinstance(marks, dict):
            marks = {}
        try:
            pain = float(info.get("pain") or 3)
        except (TypeError, ValueError):
            pain = 3.0
        changed = False
        if day == 1 and marks.get("start_day") != today:
            try:
                emitter("period_start", {"severity": pain})
            except Exception as exc:
                logger.debug("[amo_med] 发 period_start 失败: %s" % exc)
            marks["start_day"] = today
            changed = True
        elif day == bleed + 1 and marks.get("end_day") != today:
            try:
                emitter("period_end", {})
            except Exception as exc:
                logger.debug("[amo_med] 发 period_end 失败: %s" % exc)
            marks["end_day"] = today
            changed = True
        if changed:
            self._state["period_events"] = marks
            self._save()
            logger.info("[amo_med] 已向终端发事件：生理期（第 %d 天，痛经 %.0f 级）", day, pain)

    def _auto_roll(self) -> dict[str, Any]:
        """一天最多掷一次「今天会不会不舒服」。"""
        if not self._bool("enable_sick_random", True):
            return {"ok": False, "skipped": "开关关着"}
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_roll == today:
            return {"ok": False, "skipped": "今天已经掷过"}
        self._last_roll = today
        self._save()
        if self.status().get("sick"):
            return {"ok": False, "skipped": "已经在病着"}

        base = max(0.0, min(0.5, self._float("sick_prob", 0.02)))
        ctx = self.context()
        env = environment_factors(
            season=ctx.get("season", ""), weather=ctx.get("weather", ""),
            temp=ctx.get("temp"), satiety=ctx.get("satiety"),
            hydration=ctx.get("hydration"), tired=ctx.get("tired"),
            stress=ctx.get("stress"), period_phase=ctx.get("period_phase", ""),
        )
        # 环境越差越容易病：湿冷、饿、渴、累、经期各加一点
        chance = base
        chance *= 1.0 + 0.6 * bool(env.get("cold")) + 0.4 * bool(env.get("rain"))
        chance *= 1.0 + 0.5 * bool(env.get("hungry")) + 0.5 * bool(env.get("thirsty"))
        chance *= 1.0 + 0.4 * bool(env.get("tired")) + 0.3 * bool(env.get("stress"))
        chance *= 1.0 + 0.3 * bool(env.get("period"))
        chance = max(0.0, min(0.6, chance))
        if self._rng.random() >= chance:
            return {"ok": False, "skipped": "今天没事", "chance": round(chance, 3)}
        return self.fall_sick()

    def _progress_roll(self) -> dict[str, Any]:
        """拖着不治：过了病程 60% 还没看，就有机会进到相邻的下一档。"""
        if not self._bool("worsen_enable", True):
            return {"ok": False, "skipped": "开关关着"}
        if self._health.treated or self._health.status in ("健康", "恢复中"):
            return {"ok": False, "skipped": "没在病着或已经治过"}
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False, "skipped": "没有对应病种"}
        span = max(1.0, float(current.hours) * 3600.0)
        elapsed = _now() - float(self._health.since or _now())
        if elapsed < span * 0.6:
            return {"ok": False, "skipped": "还没到点"}
        if next_stage(current) is None:
            return {"ok": False, "skipped": "已经是最后一档"}
        if self._rng.random() >= 0.25:
            return {"ok": False, "skipped": "这次没变重"}
        return self.worsen()

    def status(self) -> dict[str, Any]:
        """当前状态；到点了自动好。"""
        if (self._health.status != "健康" and 0 < self._health.until <= _now()):
            self._health = HealthState(used_insurance=self._health.used_insurance)
            self._save()
        return {
            "status": self._health.status,
            "since": self._health.since,
            "until": self._health.until,
            "note": self._health.note,
            "illness": self._health.illness,
            "treated": self._health.treated,
            "sick": self._health.status in SICK_STATUSES,
        }

    def context(self) -> dict[str, Any]:
        """今天的环境：季节、天气、温度、吃饱没、累不累、是不是经期。

        天气/心情向终端要，饱食度向记忆库要；要不到的留空，不影响发病判定。
        """
        ctx: dict[str, Any] = {
            "season": season_of_month(datetime.now().month), "weather": "",
            "temp": None, "satiety": None, "hydration": None,
            "tired": None, "stress": None, "period_phase": "",
        }
        companion = self._companion()
        state = getattr(companion, "state", None) if companion is not None else None
        if state is not None:
            weather = getattr(state, "weather", None) or {}
            try:
                ctx["weather"] = str(weather.get("text") or "")
                if weather.get("temp") is not None:
                    ctx["temp"] = float(weather.get("temp"))
            except Exception:
                pass
            try:
                ctx["period_phase"] = str((state.period_info() or {}).get("phase") or "")
            except Exception:
                pass
            try:
                mood = getattr(state, "mood", None) or {}
                ctx["tired"] = float(mood.get("疲惫") or 0)
                ctx["stress"] = max(float(mood.get("焦虑") or 0),
                                    float(mood.get("暴躁") or 0),
                                    float(mood.get("生气") or 0))
            except Exception:
                pass
        try:  # 饱食度/水分在记忆库
            from . import link as _l
        except ImportError:  # pragma: no cover
            _l = None  # type: ignore
        if _l is not None:
            try:
                data = _l._find(getattr(self, "context", None),
                                _l.DATA_NAMES, _l.DATA_MARKERS, require_mem=True)
                getter = getattr(data, "vitals", None) if data is not None else None
                if callable(getter):
                    vitals = getter() or {}
                    ctx["satiety"] = vitals.get("satiety")
                    ctx["hydration"] = vitals.get("hydration")
            except Exception:
                pass
        return ctx

    def fall_sick(self, key: str = "") -> dict[str, Any]:
        """让她生病。

        ``key`` 留空时按**今天的真实环境**抽：冬天容易感冒、夏天吃坏肚子、
        饿着渴着容易脱水、经期容易痛经；住院级的病不会被直接抽到，
        只能从上一档拖出来。
        """
        illness = get_illness(key) if key else None
        if illness is None and not key:
            ctx = self.context()
            env = environment_factors(
                season=ctx.get("season", ""), weather=ctx.get("weather", ""),
                temp=ctx.get("temp"), satiety=ctx.get("satiety"),
                hydration=ctx.get("hydration"), tired=ctx.get("tired"),
                stress=ctx.get("stress"), period_phase=ctx.get("period_phase", ""),
            )
            illness = roll_illness(self._rng, season=ctx.get("season", ""), env=env)
            logger.info("[amo_med] 今天的环境：%s %s %s度 饱%s 水%s → 得病 %s"
                        % (ctx.get("season"), ctx.get("weather") or "-",
                           ctx.get("temp"), ctx.get("satiety"), ctx.get("hydration"),
                           illness.name))
        if illness is None:
            return {"ok": False, "reason": "没有这种病"}
        self._health = HealthState(
            status=illness.name,
            since=_now(),
            until=_now() + illness.hours * 3600,
            note=illness.note,
            illness=illness.key,
            treated=False,
            used_insurance=self._health.used_insurance,
        )
        self._save()
        logger.info("[amo_med] 生病：%s（自愈 %d 小时）" % (illness.name, illness.hours))
        return {
            "ok": True,
            "illness": illness.key,
            "name": illness.name,
            "hours": illness.hours,
            "severity": illness.severity,
        }

    def worsen(self) -> dict[str, Any]:
        """拖着不治 → 病情升级。没有下一级就延长。"""
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False}
        nxt = get_illness(current.worsen_to)
        if nxt is None:
            self._health.until = max(self._health.until, _now()) + 12 * 3600
            self._save()
            return {"ok": True, "name": self._health.status, "worsened": False}
        self._health.status = nxt.name
        self._health.illness = nxt.key
        self._health.note = nxt.note
        self._health.until = _now() + nxt.hours * 3600
        self._health.treated = False
        self._save()
        logger.info("[amo_med] 病情加重：%s → %s" % (current.name, nxt.name))
        return {"ok": True, "name": nxt.name, "worsened": True}

    def heal(self) -> None:
        self._health = HealthState(used_insurance=self._health.used_insurance)
        self._save()

    # ------------------------------------------------------------------
    # 治疗：先要钱，再治病
    # ------------------------------------------------------------------

    def _hospital_live(self):
        """医院实时业务层：挂号/候诊/检查/住院/出院。"""
        try:
            from . import hospital_live as _hl  # type: ignore
            return _hl
        except Exception:
            pass
        try:
            import hospital_live as _hl  # type: ignore
            return _hl
        except Exception:
            return None

    def _hospital(self):
        """医院数据层（hospital.py）。拿不到就 None，退回老算法。"""
        try:
            from . import hospital as _h  # type: ignore
            return _h
        except Exception:
            pass
        try:
            import hospital as _h  # type: ignore
            return _h
        except Exception:
            pass
        try:
            import importlib.util as _ilu
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hospital.py")
            spec = _ilu.spec_from_file_location("amo_hospital", path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
        except Exception as exc:
            logger.debug("[amo_med] 医院数据层加载失败: %s" % exc)
            return None

    def quote(self) -> dict[str, Any]:
        """报个价：先去哪科、挂什么号、开什么检查，医保报多少、自己要掏多少。

        2026-11-25（用户口径）：医疗改成医院——挂号 + 诊疗 + 检查 + 药，
        按职工医保结算；钱还是请终端扣，扣不到不治。
        """
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False, "reason": "现在没病"}

        plan = treatment_plan(current, self._rng)
        meds = plan.get("medicines") or []
        medicine = round(sum(float(m.get("price") or 0) for m in meds), 2)

        hosp = self._hospital()
        if hosp is None:
            # 兜底：老算法（目录已经删了，理论上走不到这里）
            total = float(plan.get("total") or 0.0)
            split = insurance_split(total, current.admission)
            return {
                "ok": True, "illness": current.key, "name": current.name,
                "admission": current.admission,
                "total": split["total"], "reimbursed": split["reimbursed"],
                "self_pay": split["self_pay"],
                "mode": plan.get("mode"), "mode_label": plan.get("mode_label"),
                "items": plan.get("items") or [], "medicines": meds,
                "advice": plan.get("advice") or "",
            }

        # 挂什么号：手头宽裕走特需，一般走专家，紧的时候排普通号
        try:
            balance = float(self._companion_balance() or 0)
        except Exception:
            balance = 0.0
        if balance >= 5000:
            tier = "特需"
        elif balance >= 800:
            tier = "专家"
        else:
            tier = "普通"

        dept = hosp.dept_for(current.key)
        want = 2 if current.admission else 1
        exams = [x["name"] for x in hosp.exam_list(dept["id"])[:want]]
        clinic = {"普通": 15.0, "专家": 30.0, "特需": 80.0}.get(tier, 30.0)
        bill = hosp.visit(current.key, tier, exams, medicine=medicine, clinic_fee=clinic)

        return {
            "ok": True,
            "illness": current.key,
            "name": current.name,
            "admission": current.admission,
            "total": bill["gross"],
            "reimbursed": bill["reimburse"],
            "self_pay": bill["self_pay"],
            "mode": plan.get("mode"),
            "mode_label": "%s · %s · %s号" % (dept["name"], dept["head"], tier),
            "items": bill["items"],
            "medicines": meds,
            "advice": plan.get("advice") or "",
            "hospital": {
                "name": hosp.HOSPITAL["name"],
                "floor": dept["floor"],
                "dept": dept["name"],
                "doctor": dept["head"],
                "tier": tier,
                "wait": dept.get("wait") or "",
                "exams": exams,
                "insurance": bill.get("type") or "",
            },
        }

    def _market_drug_prices(self) -> dict[str, float]:
        """市场插件当天的药价（名字 → 价格），拿不到就返回空。"""
        try:
            for meta in self.context.get_all_stars():
                text = (str(getattr(meta, "name", "") or "") + " "
                        + str(getattr(meta, "module_path", "") or ""))
                if "Marketplace" not in text and "市场" not in text:
                    continue
                inst = None
                for attr in ("star_cls", "star", "instance"):
                    obj = getattr(meta, attr, None)
                    if obj is None:
                        continue
                    inst = obj() if isinstance(obj, type) else obj
                    break
                items = getattr(inst, "items", None) if inst is not None else None
                if callable(items):
                    return {str(row.get("name") or ""): float(row.get("price") or 0)
                            for row in (items("drugs") or [])
                            if row.get("name") and float(row.get("price") or 0) > 0}
        except Exception as exc:
            logger.debug("[amo_med] 读市场药价失败: %s" % exc)
        return {}

    def _companion_balance(self) -> float:
        """终端那边的余额；读不到按 0 算（= 付不起，先扛）。"""
        companion = self._companion()
        getter = getattr(companion, "balance", None) if companion is not None else None
        if not callable(getter):
            return 0.0
        try:
            return float(getter() or 0)
        except Exception:
            return 0.0

    COMPANION_NAMES = ("DeepSeek_Harness_Amo_Terminal", "deepseek_harness_amo_terminal",
                       "Astrbot_Amo_forever", "astrbot_plugin_amo_forever", "amo_forever")

    def _companion(self):
        for name in self.COMPANION_NAMES:
            try:
                meta = self.context.get_registered_star(name)
            except Exception:
                meta = None
            star = self._instance(meta)
            if star is not None:
                return star
        try:
            for meta in self.context.get_all_stars():
                marker = ((getattr(meta, "name", "") or "") + " "
                          + (getattr(meta, "module_path", "") or "")).lower()
                if "amo_terminal" not in marker and "amo_forever" not in marker:
                    continue
                star = self._instance(meta)
                if star is not None:
                    return star
        except Exception:
            pass
        return None

    @staticmethod
    def _instance(meta):
        if meta is None:
            return None
        for attr in ("star_cls", "instance", "star", "plugin"):
            value = getattr(meta, attr, None)
            if value is not None:
                return value
        return None

    async def treat(self, auto: bool = False) -> dict[str, Any]:
        """走完整流程：报价 → 请终端扣款 → 扣到就治，扣不到不治。

        ``auto=True`` 表示是后台自动判断触发的（终端循环）；面板上的按钮走
        ``auto=False``。两种入口的差别只有「要不要拦」：自动模式下尊重
        ``auto_treat`` / ``tough_it_out`` 两个配置，手动点了就一定去治。
        """
        # 已经治好了就别再扣一次：病情结束后 illness 会被清空，
        # 这里再兜一层，防止后台循环和手动指令撞在一起重复扣款。
        if self._health.treated and self._health.status not in ("健康", "恢复中"):
            return {"ok": True, "repeated": True, "name": self._health.status,
                    "reason": "已经治过了"}
        info = self.quote()
        if not info.get("ok"):
            return {"ok": False, "reason": info.get("reason") or "现在没病"}
        if auto and not self._bool("auto_treat", True):
            return {"ok": False, "skipped": True, "manual": True,
                    "reason": "自动就医关着，等手动处理"}
        self_pay = float(info["self_pay"])
        # 一次治疗一条链：幂等号贯穿，重试也是同一条
        request_id = "med-%s-%d" % (
            str(info.get("illness") or info.get("name") or "sick"),
            int(self._health.since or 0),
        )
        chain = Chain("medical.treat", event_id=request_id, logger=logger)
        chain.step("quote", cost=self_pay, total=info.get("total"),
                   reimbursed=info.get("reimbursed"), plan=info.get("mode_label") or "")
        if auto and self._bool("tough_it_out", True) and self_pay > 0:
            illness = get_illness(info.get("illness") or "")
            balance = self._companion_balance()
            if illness is not None and will_tough_it_out(illness, balance, self_pay, self._rng):
                return {"ok": False, "skipped": True, "tough": True,
                        "reason": "小病先扛一扛", "self_pay": self_pay}
        paid = True
        reason = ""

        if self_pay > 0:
            companion = self._companion()
            if companion is None or not callable(getattr(companion, "charge", None)):
                logger.warning("[amo_med] 找不到终端扣款入口，按未支付处理")
                paid = False
                reason = "没连上终端"
            else:
                event_id = request_id
                approved, deny_reason = True, ""
                submit = getattr(companion, "submit_event", None)
                if callable(submit):
                    try:
                        decision = submit({
                            "event_id": event_id,
                            "event_type": "medical_treat",
                            "source": "medical",
                            "scene": "private",
                            "payload": {
                                "cost": self_pay,
                                "reason": "%s治疗" % info["name"],
                                "detail": {"illness": info.get("illness"),
                                           "name": info.get("name")},
                            },
                            "need_approve": True,
                        })
                        approved = bool(decision.get("approved")) or \
                            str(decision.get("reason") or "") == "duplicate"
                        deny_reason = str(decision.get("reason") or "")
                    except Exception as exc:
                        # 老终端/接口异常：别把看病卡死，按原流程走
                        logger.warning("[amo_med] 提案失败，按原流程走: %s" % exc)
                        approved = True
                result = {}
                chain.step("approve", ok=approved, detail=deny_reason)
                if not approved:
                    paid = False
                    reason = deny_reason or "终端没批"
                    logger.info("[amo_med] 提案未通过（%s），这病先不治" % reason)
                    chain.step("approve", ok=False, detail=reason)
                else:
                    try:
                        result = await companion.charge(
                            self_pay, reason="%s治疗" % info["name"], payee="医院",
                            request_id=request_id, source="medical",
                            detail=str(info.get("name") or ""),
                        )
                        paid = bool(result.get("ok"))
                        reason = str(result.get("reason") or "")
                    except Exception as exc:
                        logger.warning("[amo_med] 扣款调用失败: %s" % exc)
                        paid = False
                        result = {"code": "call_failed"}
                        reason = "扣款出错"
                    chain.step("pay", ok=paid, detail=reason,
                               balance=(result or {}).get("balance"), payee="医院")
                    receipt = getattr(companion, "event_receipt", None)
                    if callable(receipt):
                        try:
                            receipt({
                                "event_id": event_id,
                                "status": "success" if paid else "fail",
                                "code": str(result.get("code") or ""),
                                "reason": reason,
                                "amount": self_pay,
                                "balance": result.get("balance"),
                            })
                        except Exception as exc:
                            logger.warning("[amo_med] 回执失败: %s" % exc)

        self._history.append({
            "ts": _now(), "illness": info["illness"], "name": info["name"],
            "total": info["total"], "self_pay": self_pay, "paid": paid,
            "reason": reason,
        })
        del self._history[:-200]

        if not paid:
            # 没钱治：病继续，还有可能加重
            chain.fail("execute", reason or "未支付", need=self_pay)
            self.worsen()
            self._save()
            logger.info("[amo_med] 未支付，%s 继续加重" % info["name"])
            return {"ok": False, "reason": reason or "余额不足", "need": self_pay,
                    **{k: info[k] for k in ("name", "total", "reimbursed", "self_pay")},
                    "mode_label": info.get("mode_label"),
                    "medicines": info.get("medicines") or [],
                    "advice": info.get("advice") or ""}

        illness = get_illness(info["illness"])
        recovery_hours = max(2, int((illness.hours if illness else 12) / 2))
        # 病结束：状态进入「恢复中」，不再算 sick，后台循环也就不会重复来治
        self._health.status = "恢复中"
        self._health.illness = ""
        self._health.note = "刚看完病，还在恢复"
        self._health.treated = True
        self._health.until = _now() + recovery_hours * 3600
        self._health.used_insurance = round(
            self._health.used_insurance + float(info["reimbursed"]), 2
        )
        self._save()
        chain.step("execute", detail=self._health.status, hours=recovery_hours,
                   medicines=len(info.get("medicines") or []))
        chain.done(executed=True, self_pay=self_pay)
        logger.info(
            "[amo_med] 已治疗 %s（%s）：总 %.0f，医保报 %.0f，自付 %.0f，用药 %s"
            % (info["name"], info.get("mode_label") or "", info["total"],
               info["reimbursed"], self_pay,
               "、".join(m.get("name", "") for m in (info.get("medicines") or [])) or "无")
        )
        return {"ok": True, "recovered": True, **info}

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------

    @filter.command("health", alias={"身体"})
    async def cmd_health(self, event: AstrMessageEvent):
        info = self.status()
        if not info["sick"]:
            yield event.plain_result("现在好好的。")
            return
        quote = self.quote()
        if quote.get("ok"):
            yield event.plain_result(
                "%s（%s）\n治疗总费 %.0f，医保报 %.0f，自付 %.0f"
                % (info["status"], info["note"], quote["total"],
                   quote["reimbursed"], quote["self_pay"])
            )
        else:
            yield event.plain_result("%s（%s）" % (info["status"], info["note"]))

    @filter.command("treat", alias={"看病"})
    async def cmd_treat(self, event: AstrMessageEvent):
        result = await self.treat()
        if result.get("ok"):
            yield event.plain_result(
                "治好了：总费 %.0f，医保报 %.0f，自付 %.0f"
                % (result["total"], result["reimbursed"], result["self_pay"])
            )
        else:
            yield event.plain_result("没治成：%s" % result.get("reason"))

    # ------------------------------------------------------------------
    # 面板
    # ------------------------------------------------------------------

    @staticmethod
    async def _payload() -> dict:
        """面板 POST 上来的 body；没有就空字典（读失败也当空，不抛）。"""
        if web_request is None:
            return {}
        try:
            payload = await web_request.json(default={}) or {}
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _json(payload):
        if json_response is not None:
            try:
                return json_response(payload)
            except Exception:
                pass
        return payload

    @staticmethod
    def _fail(message):
        if error_response is not None:
            try:
                return error_response(message)
            except Exception:
                pass
        return {"code": 1, "message": message}

    def _register_web_apis(self) -> None:
        routes = [
            ("/status", self.api_status, ["GET"]),
            ("/hospital", self.api_hospital, ["GET", "POST"]),
            ("/health", self.api_health, ["GET"]),
            ("/history", self.api_history, ["GET"]),
            ("/treat", self.api_treat, ["POST"]),
            ("/fall", self.api_fall, ["POST"]),
            ("/medicines", self.api_medicines, ["GET"]),
            ("/period", self.api_period, ["GET", "POST"]),
        ]
        for path, handler, methods in routes:
            try:
                self.context.register_web_api("/" + PLUGIN_NAME + path, handler, methods, path)
            except TypeError:
                try:
                    self.context.register_web_api("/" + PLUGIN_NAME + path, methods, path, handler)
                except Exception as exc:
                    logger.warning("[amo_med] 接口 %s 注册失败: %s" % (path, exc))
            except Exception as exc:
                logger.warning("[amo_med] 接口 %s 注册失败: %s" % (path, exc))

    def _period_seed_start(self) -> str:
        """第一次用：把终端原来记的「上次开始」搬过来一次，之后以医疗为准。"""
        saved = str(getattr(self, "_period_start", "") or "")
        if saved:
            return saved
        terminal = self._companion()
        state = getattr(terminal, "state", None) if terminal is not None else None
        guess = str(getattr(state, "period_start", "") or "") if state is not None else ""
        if not guess:
            return ""
        self._period_start = guess
        self._save()
        logger.info("[amo_med] 生理期起点已从终端接管：%s", guess)
        return guess

    def period_settings(self) -> dict:
        """生理期面板要回填的那套设置（周期/经期/排卵/开关/四条语气）。"""
        return {
            "cycle_days": self._int("period_cycle_days", 28),
            "period_days": self._int("period_days", 5),
            "ovulation_day": self._int("period_ovulation_day", 14),
            "ovulation_window": self._int("period_ovulation_window", 3),
            "enable": self._bool("enable_period_simulate", True),
            "menstrual_tone": self._cfg("period_menstrual_tone", _period.DEFAULTS["period_menstrual_tone"]),
            "follicular_tone": self._cfg("period_follicular_tone", _period.DEFAULTS["period_follicular_tone"]),
            "ovulatory_tone": self._cfg("period_ovulatory_tone", _period.DEFAULTS["period_ovulatory_tone"]),
            "luteal_tone": self._cfg("period_luteal_tone", _period.DEFAULTS["period_luteal_tone"]),
        }

    def period_info(self) -> dict:
        """今天处在周期哪一天、什么阶段——生理期归医疗管，终端来问这里。"""
        settings = self.period_settings()
        tones = {
            "period_menstrual_tone": settings["menstrual_tone"],
            "period_follicular_tone": settings["follicular_tone"],
            "period_ovulatory_tone": settings["ovulatory_tone"],
            "period_luteal_tone": settings["luteal_tone"],
        }
        info = _period.compute(
            start=self._period_seed_start(),
            cycle_days=settings["cycle_days"],
            bleed_days=settings["period_days"],
            ovulation_day=settings["ovulation_day"],
            ovulation_window=settings["ovulation_window"],
            tones=tones,
            enabled=settings["enable"],
        )
        info["settings"] = settings
        return info

    PERIOD_FIELD_TYPES = {
        "period_cycle_days": int, "period_days": int,
        "period_ovulation_day": int, "period_ovulation_window": int,
        "enable_period_simulate": bool,
        "period_menstrual_tone": str, "period_follicular_tone": str,
        "period_ovulatory_tone": str, "period_luteal_tone": str,
    }

    def set_period(self, cycle_days=None, start=None, **fields) -> dict:
        """面板改生理期：整套设置写配置，上次开始写状态文件。"""
        changed = False
        updates = {}
        if cycle_days is not None:
            updates["period_cycle_days"] = cycle_days
        for key, kind in self.PERIOD_FIELD_TYPES.items():
            short = key.replace("period_", "").replace("enable_", "")
            if short in fields and fields[short] is not None:
                updates[key] = fields[short]
        for key, value in updates.items():
            kind = self.PERIOD_FIELD_TYPES[key]
            try:
                if kind is int:
                    new_value = max(0, min(60, int(value)))
                elif kind is bool:
                    new_value = bool(value)
                else:
                    new_value = str(value).strip()[:60]
            except Exception:
                continue
            if key == "period_cycle_days":
                new_value = max(10, min(60, new_value))
            if key == "period_days":
                new_value = max(1, min(20, new_value)) or 5
            if key == "period_ovulation_day":
                new_value = max(2, min(40, new_value)) or 14
            if key == "period_ovulation_window":
                new_value = max(1, min(9, new_value)) or 3
            if self._cfg(key, None) != new_value:
                self.config[key] = new_value
                changed = True
        parsed = _period.parse_day(start) if start else None
        if parsed is not None:
            self._period_start = parsed.isoformat()
            changed = True
        if changed:
            try:
                saver = getattr(self.config, "save_config", None)
                if callable(saver):
                    saver()
            except Exception:
                pass
            self._save()
        return self.period_info()

    async def api_period(self):
        """生理期设置：GET 读，POST（cycle_days / start）写——数据全在医疗这边。"""
        self._sync_config()
        payload = {}
        try:
            payload = await self._payload()
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        cycle_days = payload.get("cycle_days")
        start = str(payload.get("start") or "").strip()
        extra = {key: payload[key] for key in payload
                 if key not in ("cycle_days", "start")}
        if cycle_days or start or extra:
            try:
                info = self.set_period(cycle_days, start or None, **extra)
            except Exception as exc:
                return self._json({"code": 1, "message": "设置失败：%s" % exc})
            logger.info("[amo_med] 生理期设置已更新：周期 %s 天，上次 %s",
                        info.get("cycle_days"), start or "不变")
        else:
            info = self.period_info()
        return self._json({"code": 0, "data": info})

    async def api_medicines(self):
        """药价表：面板「医疗」页直接用。"""
        rows = []
        for key, item in MEDICINES.items():
            rows.append({"key": item.key, "name": item.name, "price": item.price,
                         "kind": item.kind, "note": item.note})
        rows.sort(key=lambda x: x["price"])
        return self._json({"code": 0, "data": {
            "items": rows,
            "modes": MODE_LABELS,
            "market_total": self._market_drug_total(),
        }})

    def _market_drug_total(self) -> int:
        """市场插件里一共多少种药；拿不到就退回 161（当前目录规模）。"""
        try:
            for meta in self.context.get_all_stars():
                text = (str(getattr(meta, "name", "") or "") + " "
                        + str(getattr(meta, "module_path", "") or ""))
                if "Marketplace" not in text and "市场" not in text:
                    continue
                for attr in ("star_cls", "star", "instance"):
                    obj = getattr(meta, attr, None)
                    if obj is None:
                        continue
                    inst = obj() if isinstance(obj, type) else obj
                    counts = getattr(inst, "counts", None)
                    if callable(counts):
                        return int((counts() or {}).get("drugs") or 0)
        except Exception as exc:
            logger.debug("[amo_med] 读市场药数失败: %s" % exc)
        return 161

    def _sync_config(self) -> None:
        """AstrBot 原生设置页改完，把配置文件的新值并进内存（面板与接口立刻一致）。"""
        try:
            _config_watch.reload_if_changed(self.config, logger)
        except Exception as exc:
            logger.debug("[config] 同步配置文件失败: %s" % exc)

    async def api_health(self):
        """我自己连没连上终端/记忆库。面板顶部那一行就看这个。"""
        self._sync_config()
        return self._json({"code": 0, "data": _link_probe(self)})

    def hospital_data(self) -> dict:
        """医院面板：总览 / 科室 / 检查 / 病房 / 医保。"""
        h = self._hospital()
        if h is None:
            return {"sections": [], "items": {}}
        items, sections = {}, []
        prof = h.HOSPITAL

        group = [
            {"name": "等级", "price": 0, "unit": "", "note": prof["level"]},
            {"name": "床位", "price": prof["beds"], "unit": "张", "note": "员工 %d 人" % prof["staff"]},
            {"name": "科室", "price": len(h.DEPARTMENTS), "unit": "个", "note": "院长 " + prof["dean"]},
            {"name": "检查项目", "price": len(h.EXAMS), "unit": "项", "note": "含影像与内镜"},
            {"name": "门诊起付", "price": h.INSURANCE["outpatient"]["threshold"], "unit": "元",
             "note": "报 %.0f%%" % (h.INSURANCE["outpatient"]["ratio"] * 100)},
            {"name": "住院起付", "price": h.INSURANCE["inpatient"]["threshold"], "unit": "元",
             "note": "报 %.0f%%" % (h.INSURANCE["inpatient"]["ratio"] * 100)},
        ]
        for x in group:
            x.update({"category": "总览", "source": "hospital"})
        items["group"] = group
        sections.append({"id": "group", "title": "医院总览", "count": len(group), "items": group})

        depts = []
        for d in h.DEPARTMENTS:
            fees = d.get("fees") or {}
            depts.append({
                "name": d["name"], "price": fees.get("专家") or 0, "unit": "元",
                "note": "%s · %s｜%s" % (d["head"], d["floor"], d["focus"]),
                "category": "科室", "head": d["head"], "floor": d["floor"],
                "focus": d["focus"], "wait": d.get("wait"), "fees": fees,
                "exams": d.get("exams"), "source": "hospital",
            })
        items["departments"] = depts
        sections.append({"id": "departments", "title": "科室 · %d 个" % len(depts),
                         "count": len(depts), "items": depts})

        exams = []
        for name, info in h.EXAMS.items():
            exams.append({"name": name, "price": info["price"], "unit": "元",
                          "note": "%s 分钟｜%s" % (info["mins"], info["note"]),
                          "category": "检查", "source": "hospital"})
        exams.sort(key=lambda x: x["price"])
        items["exams"] = exams
        sections.append({"id": "exams", "title": "检查项目 · %d 项" % len(exams),
                         "count": len(exams), "items": exams})

        wards = []
        for name, info in h.WARDS.items():
            wards.append({"name": name, "price": info["daily"], "unit": "元/天",
                          "note": "押金 %d 元｜%s" % (info["deposit"], info["note"]),
                          "category": "病房", "source": "hospital"})
        items["wards"] = wards
        sections.append({"id": "wards", "title": "病房 · %d 类" % len(wards),
                         "count": len(wards), "items": wards})

        ins = [
            {"name": "类型", "price": 0, "unit": "", "note": h.INSURANCE["type"], "category": "医保", "source": "hospital"},
            {"name": "门诊", "price": h.INSURANCE["outpatient"]["ratio"] * 100, "unit": "%",
             "note": "起付 %d 元，封顶 %d 元" % (h.INSURANCE["outpatient"]["threshold"], h.INSURANCE["outpatient"]["cap"])},
            {"name": "住院", "price": h.INSURANCE["inpatient"]["ratio"] * 100, "unit": "%",
             "note": "起付 %d 元，封顶 %d 元" % (h.INSURANCE["inpatient"]["threshold"], h.INSURANCE["inpatient"]["cap"])},
        ]
        items["insurance"] = ins
        sections.append({"id": "insurance", "title": "医保", "count": len(ins), "items": ins})

        return {"sections": sections, "items": items,
                "hospital": {"name": prof["name"], "level": prof["level"], "note": prof["note"]}}

    async def api_hospital(self):
        """医院总览 + 真正业务动作。action:
        checkin / consult / finish / admit / discharge / reset。
        """
        params = {}
        try:
            if web_request is not None and hasattr(web_request, "query"):
                params.update(dict(web_request.query))
        except Exception:
            pass
        try:
            body = await self._payload()
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            pass
        action = str(params.get("action") or "").strip().lower()
        live = self._hospital_live()
        data_dir = self._data_dir()
        if live:
            if action == "checkin":
                res = live.checkin(data_dir, str(params.get("patient") or "阿墨"),
                                   str(params.get("illness") or ""), str(params.get("tier") or "专家"))
                return self._json({"code": 0 if res.get("ok") else 1, "data": res})
            if action == "consult":
                res = live.start_visit(data_dir, str(params.get("visit_id") or ""))
                return self._json({"code": 0 if res.get("ok") else 1, "data": res})
            if action == "finish":
                exams = params.get("exams")
                if isinstance(exams, str):
                    exams = [x.strip() for x in exams.split(",") if x.strip()]
                try: medicine = float(params.get("medicine") or 0); clinic_fee = float(params.get("clinic_fee") or 0)
                except Exception: medicine = clinic_fee = 0.0
                res = live.finish_visit(data_dir, str(params.get("visit_id") or ""), exams, medicine, clinic_fee)
                return self._json({"code": 0 if res.get("ok") else 1, "data": res})
            if action == "admit":
                try: days = int(float(params.get("days") or 3)); treat_fee = float(params.get("treat_fee") or 0)
                except Exception: days, treat_fee = 3, 0.0
                res = live.admit(data_dir, str(params.get("patient") or "阿墨"), days,
                                 str(params.get("room") or "双人间"), treat_fee)
                return self._json({"code": 0 if res.get("ok") else 1, "data": res})
            if action == "discharge":
                res = live.discharge(data_dir, str(params.get("admission_id") or ""))
                return self._json({"code": 0 if res.get("ok") else 1, "data": res})
            if action == "reset":
                return self._json({"code": 0, "data": live.reset(data_dir)})
        data = self.hospital_data()
        if live:
            data["live"] = live.snapshot(data_dir)
        return self._json({"code": 0, "data": data})

    async def api_status(self):
        self._sync_config()
        info = self.status()
        quote = self.quote() if info["sick"] else {}
        return self._json({"code": 0, "data": {
            "name": PLUGIN_NAME, "version": PLUGIN_VERSION, "display_name": DISPLAY_NAME,
            "link": _link_probe(self),
            "health": info, "quote": quote,
            "used_insurance": self._health.used_insurance,
            "auto_treat": self._bool("auto_treat", True),
            "tough_it_out": self._bool("tough_it_out", True),
            "period": self.period_info(),
            "uptime": int(_now() - self._started_at),
        }})

    async def api_history(self):
        return self._json({"code": 0, "data": {"items": list(reversed(self._history))}})

    async def api_treat(self):
        result = await self.treat(auto=False)
        return self._json({"code": 0, "data": result})

    async def api_fall(self):
        payload: dict[str, Any] = {}
        if web_request is not None:
            try:
                payload = await web_request.json(default={}) or {}
            except Exception:
                payload = {}
        key = str((payload or {}).get("illness") or "")
        result = self.fall_sick(key)
        return self._json({"code": 0, "data": result})

    async def terminate(self) -> None:
        try:
            if getattr(self, "_auto_task", None) is not None:
                self._auto_task.cancel()
        except Exception:
            pass
        self._save()
