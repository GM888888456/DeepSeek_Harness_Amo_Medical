# -*- coding: utf-8 -*-
"""医院实时业务层：挂号、排队、检查、住院、床位、出院、结算。
hospital.py 只保存医院底稿；本文件负责可变状态与业务生命周期。
"""
from __future__ import annotations
import json, os, time
from typing import Any, Dict

try:
    from . import hospital as base
except Exception:
    import hospital as base  # type: ignore

STATE_NAME = "hospital_state.json"

def _path(data_dir): return os.path.join(str(data_dir or "."), STATE_NAME)
def _now(): return time.strftime("%Y-%m-%d %H:%M:%S")
def _id(prefix): return "%s-%d" % (prefix, int(time.time()*1000))

def load(data_dir):
    try:
        with open(_path(data_dir), encoding="utf-8") as f: s=json.load(f)
        if not isinstance(s,dict): raise ValueError("bad")
    except Exception: s={}
    s.setdefault("visits", []); s.setdefault("admissions", []); s.setdefault("beds", {})
    s.setdefault("queue", []); s.setdefault("events", [])
    return s

def save(data_dir,s):
    try:
        os.makedirs(os.path.dirname(_path(data_dir)),exist_ok=True)
        tmp=_path(data_dir)+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(s,f,ensure_ascii=False,indent=1)
        os.replace(tmp,_path(data_dir)); return True
    except Exception: return False

def _event(s,kind,**detail):
    s["events"].append({"at":_now(),"kind":kind,**detail}); s["events"]=s["events"][-300:]

def checkin(data_dir, patient, illness="", tier="专家"):
    s=load(data_dir); reg=base.register(illness,tier)
    row={"id":_id("OPD"),"patient":str(patient or "患者"),"illness":illness,"status":"waiting",
         "dept":reg["dept"],"doctor":reg["head"],"tier":reg["tier"],"fee":reg["fee"],"created_at":_now()}
    s["visits"].append(row); s["queue"].append(row["id"]); _event(s,"checkin",visit=row["id"],dept=row["dept"])
    save(data_dir,s); return {"ok":True,"visit":dict(row),"queue_no":len(s["queue"])}

def start_visit(data_dir, visit_id):
    s=load(data_dir)
    for v in s["visits"]:
        if v.get("id")==visit_id:
            if v.get("status")!="waiting": return {"ok":False,"reason":"当前不在候诊"}
            v["status"]="consulting"; v["started_at"]=_now()
            if visit_id in s["queue"]: s["queue"].remove(visit_id)
            _event(s,"consult",visit=visit_id); save(data_dir,s); return {"ok":True,"visit":dict(v)}
    return {"ok":False,"reason":"没有这条挂号"}

def finish_visit(data_dir, visit_id, exams=None, medicine=0.0, clinic_fee=0.0):
    s=load(data_dir)
    for v in s["visits"]:
        if v.get("id")==visit_id:
            if v.get("status") not in ("consulting","waiting"): return {"ok":False,"reason":"当前状态不能结诊"}
            bill=base.visit(v.get("illness",""),v.get("tier","专家"),exams,medicine,clinic_fee)
            v.update({"status":"finished","finished_at":_now(),"bill":bill})
            _event(s,"visit_finished",visit=visit_id,total=bill["gross"],self_pay=bill["self_pay"])
            save(data_dir,s); return {"ok":True,"visit":dict(v),"bill":bill}
    return {"ok":False,"reason":"没有这条挂号"}

def admit(data_dir, patient, days=3, room="双人间", treat_fee=0.0):
    s=load(data_dir); bill=base.admit(days,room,treat_fee)
    occupied=sum(1 for x in s["admissions"] if x.get("status")=="inpatient")
    if occupied >= int(base.HOSPITAL["beds"]): return {"ok":False,"reason":"当前无床"}
    row={"id":_id("ADM"),"patient":str(patient or "患者"),"room":room,"days":int(days),
         "status":"inpatient","admitted_at":_now(),"bill":bill}
    s["admissions"].append(row); _event(s,"admit",admission=row["id"],patient=row["patient"],room=room)
    save(data_dir,s); return {"ok":True,"admission":dict(row),"bill":bill}

def discharge(data_dir, admission_id):
    s=load(data_dir)
    for a in s["admissions"]:
        if a.get("id")==admission_id:
            if a.get("status")!="inpatient": return {"ok":False,"reason":"已经出院"}
            a["status"]="discharged"; a["discharged_at"]=_now()
            _event(s,"discharge",admission=admission_id)
            save(data_dir,s); return {"ok":True,"admission":dict(a),"bill":a.get("bill") or {}}
    return {"ok":False,"reason":"没有这次住院"}

def snapshot(data_dir):
    s=load(data_dir)
    inpatient=[x for x in s["admissions"] if x.get("status")=="inpatient"]
    waiting=len(s["queue"])
    return {"hospital":base.HOSPITAL,"visits_today":len(s["visits"]),"waiting":waiting,
            "inpatient":len(inpatient),"beds_total":base.HOSPITAL["beds"],
            "beds_available":max(0,int(base.HOSPITAL["beds"])-len(inpatient)),
            "events":s["events"][-20:],"state":s}

def reset(data_dir):
    s={"visits":[],"admissions":[],"beds":{},"queue":[],"events":[]}; save(data_dir,s); return snapshot(data_dir)
