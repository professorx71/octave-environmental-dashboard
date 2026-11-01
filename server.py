import os
import smtplib
import threading
import csv
import io
import asyncio
import json
import uuid
import secrets
import hmac
import hashlib
import base64
import re
import zipfile
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from datetime import datetime,timedelta,timezone
from pathlib import Path
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.pdfencrypt import StandardEncryption

# Use the Windows certificate store when available. This is important on
# corporate networks that install an internal TLS inspection/root certificate.
TRUSTSTORE_ACTIVE=False
try:
    import truststore
    truststore.inject_into_ssl()
    TRUSTSTORE_ACTIVE=True
except Exception as e:
    print("Windows trust store support unavailable:", repr(e))


BASE=Path(__file__).parent
load_dotenv(BASE/".env", override=True, interpolate=False)
TOKEN=os.getenv("LICOR_API_TOKEN","").strip()
API=os.getenv("LICOR_API_BASE","https://api.licor.cloud").rstrip("/")
GMAIL_SENDER=os.getenv("GMAIL_SENDER","").strip()
GMAIL_APP_PASSWORD=os.getenv("GMAIL_APP_PASSWORD","").replace(" ","").strip()
ALERT_EMAIL=os.getenv("ALERT_EMAIL","").strip()
NOTIFICATIONS_ENABLED=os.getenv("NOTIFICATIONS_ENABLED","false").strip().lower() in ("1","true","yes","on")
DEPLOYMENT_ROLE=os.getenv("DEPLOYMENT_ROLE","CLOUD-TEST").strip().upper() or "CLOUD-TEST"
email_alarm_state={}

# v5.0 persistent alarm event log / acknowledgement state
DATA_DIR=BASE/"data"
DATA_DIR.mkdir(exist_ok=True)
EVENT_FILE=DATA_DIR/"alarm_events.json"
STATE_FILE=DATA_DIR/"alarm_state.json"

CONFIG_FILE=DATA_DIR/"alarm_config.json"
DOWNTIME_FILE=DATA_DIR/"downtime_history.json"
DOWNTIME_ACTIVE_FILE=DATA_DIR/"downtime_active.json"
REPORT_CONFIG_FILE=DATA_DIR/"maintenance_report_config.json"
REPORT_STATE_FILE=DATA_DIR/"maintenance_report_state.json"
CALIBRATION_FILE=DATA_DIR/"calibration_records.json"
AUDIT_FILE=DATA_DIR/"audit_trail.json"
AUDIT_CONFIG_FILE=DATA_DIR/"audit_config.json"
DEVIATION_FILE=DATA_DIR/"deviation_records.json"
AUDIT_BACKUP_STATE_FILE=DATA_DIR/"audit_backup_state.json"
BACKUP_DIR=DATA_DIR/"backups"
BACKUP_DIR.mkdir(exist_ok=True)
ADMIN_USERNAME=os.getenv("ADMIN_USERNAME","admin").strip() or "admin"
ADMIN_PASSWORD_HASH=os.getenv("ADMIN_PASSWORD_HASH","").strip()
OPERATOR_USERNAME=os.getenv("OPERATOR_USERNAME","operator").strip() or "operator"
OPERATOR_PASSWORD_HASH=os.getenv("OPERATOR_PASSWORD_HASH","").strip()
SESSION_HOURS=8
admin_sessions={}
admin_lock=threading.Lock()

def _password_hash_ok(password: str, encoded_hash: str) -> bool:
    """Validate a PBKDF2-SHA256 password hash stored in .env."""
    if not encoded_hash:
        return False
    try:
        scheme, iterations, salt_b64, digest_b64 = encoded_hash.split("$",3)
        if scheme != "pbkdf2_sha256":
            return False
        salt=base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected=base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual=hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

def _password_ok(password: str) -> bool:
    return _password_hash_ok(password, ADMIN_PASSWORD_HASH)

def _operator_password_ok(password: str) -> bool:
    return _password_hash_ok(password, OPERATOR_PASSWORD_HASH)

def _valid_settings_password(password: str) -> bool:
    """Admin/Operator setting passwords: exactly 6 ASCII letters or numbers."""
    return bool(re.fullmatch(r"[A-Za-z0-9]{6}", password or ""))

def _valid_settings_username(username: str) -> bool:
    """Allow normal letters/numbers/spaces/symbols in usernames.

    Leading/trailing whitespace is ignored by the login form. Newlines and
    control characters are rejected because usernames are stored in .env.
    """
    username=(username or "").strip()
    if not username or len(username) > 64:
        return False
    return all(ch.isprintable() and ch not in "\r\n" for ch in username)

def _env_value(value: str) -> str:
    """Safely quote a value for python-dotenv, including spaces/#/=/$ symbols."""
    return json.dumps(str(value), ensure_ascii=False)

def _make_password_hash(password: str) -> str:
    iterations=310000
    salt=secrets.token_bytes(18)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),salt,iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )

def _update_env_value(key: str, value: str):
    env_path=BASE/".env"
    lines=[]
    if env_path.exists():
        lines=env_path.read_text(encoding="utf-8").splitlines()
    out=[]
    found=False
    if key.endswith("_USERNAME"):
        stored=_env_value(value)
    elif key.endswith("_PASSWORD_HASH"):
        stored="'" + str(value).replace("'", "") + "'"
    else:
        stored=str(value)
    for line in lines:
        if line.startswith(key+"="):
            out.append(key+"="+stored)
            found=True
        else:
            out.append(line)
    if not found:
        out.append(key+"="+stored)
    tmp=env_path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out)+"\n",encoding="utf-8")
    tmp.replace(env_path)

def _new_session(username: str, role: str):
    token=secrets.token_urlsafe(32)
    expires=datetime.now(timezone.utc)+timedelta(hours=SESSION_HOURS)
    with admin_lock:
        admin_sessions[token]={"expires":expires,"username":username,"role":role}
    return token, expires

def _new_admin_session():
    return _new_session(ADMIN_USERNAME,"admin")

def _session_info(request: Request):
    token=request.cookies.get("pbf_admin_session","")
    if not token:
        return None
    with admin_lock:
        item=admin_sessions.get(token)
        if not item:
            return None
        # Backward-compatible handling if an old in-memory session value exists.
        if isinstance(item,datetime):
            if item < datetime.now(timezone.utc):
                admin_sessions.pop(token,None)
                return None
            return {"username":ADMIN_USERNAME,"role":"admin","expires":item}
        expiry=item.get("expires")
        if not expiry or expiry < datetime.now(timezone.utc):
            admin_sessions.pop(token,None)
            return None
        return dict(item)

def _admin_user(request: Request):
    info=_session_info(request)
    return info.get("username") if info and info.get("role")=="admin" else None

def require_admin(request: Request):
    info=_session_info(request)
    if not info or info.get("role")!="admin":
        raise HTTPException(status_code=403 if info else 401,detail="Administrator login required")
    return info

def require_settings_access(request: Request):
    info=_session_info(request)
    if not info or info.get("role") not in ("admin","operator"):
        raise HTTPException(status_code=401,detail="Admin or operator login required")
    return info

def load_alarm_config():
    """Persistent operator settings. Defaults are defined in LOGGERS."""
    cfg=_read_json(CONFIG_FILE,{})
    if not isinstance(cfg,dict):
        return {}
    return cfg

PLANT_TZ=ZoneInfo("America/Vancouver")
SCHEDULE_DAY_NAMES=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

def _valid_hhmm(value, default="00:00"):
    value=str(value or "").strip()
    try:
        datetime.strptime(value,"%H:%M")
        return value
    except Exception:
        return default

def _normalize_schedule_days(value):
    if not isinstance(value,list):
        return [0,1,2,3,4,5,6]
    out=[]
    for x in value:
        try:
            n=int(x)
            if 0 <= n <= 6 and n not in out:
                out.append(n)
        except Exception:
            pass
    return sorted(out)

def _minutes_hhmm(value):
    h,m=map(int,_valid_hhmm(value).split(":"))
    return h*60+m

def schedule_is_active(cfg, when_utc=None):
    """Return whether the weekly alarm-notification schedule is active now.

    Schedule semantics:
    - schedule disabled: active all the time (subject to manual RUNNING/NOT IN USE).
    - start == end: active 24 hours on selected start days.
    - an overnight window (e.g. 22:00-06:00) belongs to the day on which it starts.
    """
    if not bool(cfg.get("schedule_enabled",False)):
        return True
    days=set(_normalize_schedule_days(cfg.get("schedule_days",[0,1,2,3,4,5,6])))
    if not days:
        return False
    now=(when_utc or datetime.now(timezone.utc)).astimezone(PLANT_TZ)
    cur=now.hour*60+now.minute
    start=_minutes_hhmm(cfg.get("schedule_start","00:00"))
    end=_minutes_hhmm(cfg.get("schedule_end","00:00"))
    wd=now.weekday()
    if start == end:
        return wd in days
    if start < end:
        return wd in days and start <= cur < end
    # Overnight schedule; early-morning hours belong to the previous start day.
    if cur >= start:
        return wd in days
    if cur < end:
        return ((wd-1) % 7) in days
    return False

def schedule_summary(cfg):
    if not bool(cfg.get("schedule_enabled",False)):
        return "Schedule OFF — manual mode"
    days=_normalize_schedule_days(cfg.get("schedule_days",[]))
    labels=",".join(SCHEDULE_DAY_NAMES[d] for d in days) if days else "No days"
    start=_valid_hhmm(cfg.get("schedule_start","00:00"))
    end=_valid_hhmm(cfg.get("schedule_end","00:00"))
    if start == end:
        return f"{labels} · 24 hours"
    return f"{labels} · {start}-{end} Pacific"

def get_runtime_cfg(sn):
    base_cfg=dict(LOGGERS[sn])
    saved=load_alarm_config().get(sn,{})
    if isinstance(saved,dict):
        if "limit" in saved:
            try: base_cfg["limit"]=float(saved["limit"])
            except: pass
        if "monitoring_enabled" in saved:
            base_cfg["monitoring_enabled"]=bool(saved["monitoring_enabled"])
        if "mode" in saved:
            base_cfg["mode"]=str(saved["mode"])
        base_cfg["schedule_enabled"]=bool(saved.get("schedule_enabled",False))
        base_cfg["schedule_days"]=_normalize_schedule_days(saved.get("schedule_days",[0,1,2,3,4,5,6]))
        base_cfg["schedule_start"]=_valid_hhmm(saved.get("schedule_start","00:00"),"00:00")
        base_cfg["schedule_end"]=_valid_hhmm(saved.get("schedule_end","00:00"),"00:00")
        try: base_cfg["temperature_offset"]=float(saved.get("temperature_offset",0.0))
        except: base_cfg["temperature_offset"]=0.0
        try: base_cfg["humidity_offset"]=float(saved.get("humidity_offset",0.0))
        except: base_cfg["humidity_offset"]=0.0
        if "updated_at" in saved:
            base_cfg["config_updated_at"]=saved["updated_at"]
        if "updated_by" in saved:
            base_cfg["config_updated_by"]=saved["updated_by"]
    base_cfg.setdefault("monitoring_enabled",True)
    base_cfg.setdefault("mode","RUNNING")
    base_cfg.setdefault("schedule_enabled",False)
    base_cfg.setdefault("schedule_days",[0,1,2,3,4,5,6])
    base_cfg.setdefault("schedule_start","00:00")
    base_cfg.setdefault("schedule_end","00:00")
    base_cfg.setdefault("temperature_offset",0.0)
    base_cfg.setdefault("humidity_offset",0.0)
    sched_active=schedule_is_active(base_cfg)
    base_cfg["schedule_active_now"]=bool(sched_active)
    base_cfg["schedule_summary"]=schedule_summary(base_cfg)
    base_cfg["effective_monitoring_enabled"]=bool(base_cfg["monitoring_enabled"] and sched_active)
    if not base_cfg["monitoring_enabled"]:
        base_cfg["effective_mode"]="NOT_IN_USE"
    elif base_cfg["schedule_enabled"] and not sched_active:
        base_cfg["effective_mode"]="SCHEDULED_OFF"
    else:
        base_cfg["effective_mode"]="RUNNING"
    return base_cfg

def save_runtime_cfg(sn, *, limit=None, monitoring_enabled=None, mode=None,
                     schedule_enabled=None, schedule_days=None, schedule_start=None, schedule_end=None,
                     temperature_offset=None, humidity_offset=None, updated_by="admin"):
    with event_lock:
        cfg=_read_json(CONFIG_FILE,{})
        if not isinstance(cfg,dict): cfg={}
        row=cfg.setdefault(sn,{})
        if limit is not None: row["limit"]=float(limit)
        if monitoring_enabled is not None: row["monitoring_enabled"]=bool(monitoring_enabled)
        if mode is not None: row["mode"]=str(mode)
        if schedule_enabled is not None: row["schedule_enabled"]=bool(schedule_enabled)
        if schedule_days is not None: row["schedule_days"]=_normalize_schedule_days(schedule_days)
        if schedule_start is not None: row["schedule_start"]=_valid_hhmm(schedule_start,"00:00")
        if schedule_end is not None: row["schedule_end"]=_valid_hhmm(schedule_end,"00:00")
        if temperature_offset is not None: row["temperature_offset"]=float(temperature_offset)
        if humidity_offset is not None: row["humidity_offset"]=float(humidity_offset)
        row["updated_at"]=datetime.now(timezone.utc).isoformat()
        row["updated_by"]=updated_by
        _write_json(CONFIG_FILE,cfg)
    return get_runtime_cfg(sn)

def calibrated_value(sn, measurement_type, value):
    """Apply the saved per-logger calibration offset to a LI-COR reading."""
    if value is None:
        return None
    try:
        v=float(value)
    except Exception:
        return value
    cfg=get_runtime_cfg(str(sn)) if str(sn) in LOGGERS else {}
    typ=str(measurement_type or "").lower()
    if typ=="temperature":
        return v + float(cfg.get("temperature_offset",0.0))
    if typ=="rh":
        # Relative humidity is physically bounded to 0-100 %.
        return max(0.0,min(100.0,v + float(cfg.get("humidity_offset",0.0))))
    return v

def calibrated_row(q):
    row=dict(q)
    sn=str(row.get("logger_sn",""))
    typ=str(row.get("sensor_measurement_type","")).lower()
    if sn in LOGGERS and typ in ("temperature","rh"):
        row["raw_value"]=row.get("value")
        row["value"]=calibrated_value(sn,typ,row.get("value"))
    return row

event_lock=threading.Lock()

def _read_json(path, default):
    try:
        if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print("State read error:", path.name, repr(e))
    return default

def _write_json(path, obj):
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,indent=2),encoding="utf-8")
    tmp.replace(path)

audit_lock=threading.Lock()

def load_audit_config():
    default={
        "retention_years":3,
        "backup_enabled":True,
        "backup_time":"02:15",
        "backup_keep":30,
    }
    with audit_lock:
        saved=_read_json(AUDIT_CONFIG_FILE,{})
    if not isinstance(saved,dict):
        saved={}
    cfg={**default,**saved}
    try:
        cfg["retention_years"]=max(1,min(3,int(cfg.get("retention_years",3))))
    except Exception:
        cfg["retention_years"]=3
    cfg["backup_enabled"]=bool(cfg.get("backup_enabled",True))
    cfg["backup_time"]=_valid_hhmm(cfg.get("backup_time","02:15"),"02:15")
    try:
        cfg["backup_keep"]=max(7,min(365,int(cfg.get("backup_keep",30))))
    except Exception:
        cfg["backup_keep"]=30
    return cfg

def save_audit_config(retention_years,backup_enabled,backup_time,backup_keep,updated_by):
    try:
        retention_years=max(1,min(3,int(retention_years)))
    except Exception:
        raise HTTPException(status_code=400,detail="Retention must be 1, 2 or 3 years")
    backup_time=_valid_hhmm(backup_time,"")
    if not backup_time:
        raise HTTPException(status_code=400,detail="Backup time must use HH:MM")
    try:
        backup_keep=max(7,min(365,int(backup_keep)))
    except Exception:
        raise HTTPException(status_code=400,detail="Backup retention must be 7-365 daily backups")
    cfg={
        "retention_years":retention_years,
        "backup_enabled":bool(backup_enabled),
        "backup_time":backup_time,
        "backup_keep":backup_keep,
        "updated_at":datetime.now(timezone.utc).isoformat(),
        "updated_by":updated_by,
    }
    with audit_lock:
        _write_json(AUDIT_CONFIG_FILE,cfg)
    return cfg

def _record_time(rec):
    for key in ("time","time_utc","updated_at","end_utc","start_utc","created_at","calibration_date"):
        value=rec.get(key) if isinstance(rec,dict) else None
        if not value:
            continue
        if key=="calibration_date":
            try:
                d=datetime.strptime(str(value),"%Y-%m-%d").date()
                return datetime.combine(d,datetime.min.time(),tzinfo=PLANT_TZ).astimezone(timezone.utc)
            except Exception:
                continue
        dt=_iso_dt(value)
        if dt:
            return dt
    return None

def _prune_list_by_retention(rows,years=None):
    if not isinstance(rows,list):
        return []
    years=years or load_audit_config().get("retention_years",3)
    cutoff=datetime.now(timezone.utc)-timedelta(days=365*int(years))
    out=[]
    for rec in rows:
        dt=_record_time(rec)
        if dt is None or dt >= cutoff:
            out.append(rec)
    return out

def append_audit_record(action, *, category="SYSTEM", actor="SYSTEM", role="system",
                        logger_sn="", name="", detail="", source_event_id="", extra=None):
    now=datetime.now(timezone.utc)
    rec={
        "id":uuid.uuid4().hex,
        "time_utc":now.isoformat(),
        "time_pacific":now.astimezone(PLANT_TZ).isoformat(),
        "timezone":"America/Vancouver",
        "category":str(category or "SYSTEM"),
        "action":str(action or ""),
        "actor":str(actor or "SYSTEM"),
        "role":str(role or "system"),
        "logger_sn":str(logger_sn or ""),
        "name":str(name or ""),
        "detail":str(detail or ""),
        "source_event_id":str(source_event_id or ""),
    }
    if isinstance(extra,dict):
        rec["extra"]=extra
    with audit_lock:
        rows=_read_json(AUDIT_FILE,[])
        if not isinstance(rows,list):
            rows=[]
        rows.append(rec)
        rows=_prune_list_by_retention(rows)
        _write_json(AUDIT_FILE,rows)
    return rec["id"]

def get_audit_records(limit=1000):
    with audit_lock:
        rows=_read_json(AUDIT_FILE,[])
    if not isinstance(rows,list):
        rows=[]
    # Include legacy alarm/config events so pre-v6.2 history remains visible.
    with event_lock:
        legacy=_read_json(EVENT_FILE,[])
    known={r.get("source_event_id") for r in rows if isinstance(r,dict) and r.get("source_event_id")}
    for e in legacy if isinstance(legacy,list) else []:
        if e.get("id") in known:
            continue
        rows.append({
            "id":"legacy-"+str(e.get("id","")),
            "time_utc":e.get("time"),
            "time_pacific":_pt_text(_iso_dt(e.get("time"))) if e.get("time") else "",
            "timezone":"America/Vancouver",
            "category":"LEGACY EVENT",
            "action":e.get("event",""),
            "actor":e.get("actor") or "—",
            "role":e.get("role") or "—",
            "logger_sn":e.get("logger_sn",""),
            "name":e.get("name",""),
            "detail":e.get("detail",""),
            "source_event_id":e.get("id",""),
        })
    rows=sorted(rows,key=lambda r:str(r.get("time_utc","")))
    return rows[-max(1,min(int(limit),5000)):]

def load_calibration_records():
    with audit_lock:
        data=_read_json(CALIBRATION_FILE,{})
    return data if isinstance(data,dict) else {}

def latest_calibration(sn,as_of_date=None):
    rows=load_calibration_records().get(str(sn),[])
    if not isinstance(rows,list):
        return None
    if as_of_date:
        rows=[r for r in rows if str(r.get("calibration_date","")) <= str(as_of_date)]
    rows=sorted(rows,key=lambda r:(str(r.get("calibration_date","")),str(r.get("created_at",""))))
    return rows[-1] if rows else None

def add_calibration_record(sn,data,actor):
    if sn not in LOGGERS:
        raise HTTPException(status_code=404,detail="Unknown logger")
    date_text=str(data.get("calibration_date","")).strip()
    due_text=str(data.get("next_due_date","")).strip()
    try:
        datetime.strptime(date_text,"%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400,detail="Calibration date is required")
    if due_text:
        try:
            datetime.strptime(due_text,"%Y-%m-%d")
        except Exception:
            raise HTTPException(status_code=400,detail="Next calibration due date is invalid")
    try:
        temp_offset=float(data.get("temperature_offset",0.0))
        rh_offset=float(data.get("humidity_offset",0.0))
    except Exception:
        raise HTTPException(status_code=400,detail="Calibration offsets must be numeric")
    if not -20.0 <= temp_offset <= 20.0:
        raise HTTPException(status_code=400,detail="Temperature offset must be between -20 and +20 C")
    if not -50.0 <= rh_offset <= 50.0:
        raise HTTPException(status_code=400,detail="Humidity offset must be between -50 and +50 %RH")
    reference=str(data.get("reference_instrument","")).strip()
    performed_by=str(data.get("performed_by","")).strip()
    if not reference or not performed_by:
        raise HTTPException(status_code=400,detail="Reference instrument and performed by are required")
    rec={
        "id":uuid.uuid4().hex,
        "logger_sn":sn,
        "name":LOGGERS[sn]["name"],
        "calibration_date":date_text,
        "temperature_offset":temp_offset,
        "humidity_offset":rh_offset,
        "reference_instrument":reference,
        "reference_serial":str(data.get("reference_serial","")).strip(),
        "certificate_id":str(data.get("certificate_id","")).strip(),
        "next_due_date":due_text,
        "performed_by":performed_by,
        "notes":str(data.get("notes","")).strip(),
        "created_at":datetime.now(timezone.utc).isoformat(),
        "created_by":actor,
    }
    with audit_lock:
        allrows=_read_json(CALIBRATION_FILE,{})
        if not isinstance(allrows,dict):
            allrows={}
        rows=allrows.setdefault(sn,[])
        rows.append(rec)
        rows.sort(key=lambda r:(str(r.get("calibration_date","")),str(r.get("created_at",""))))
        allrows[sn]=rows
        _write_json(CALIBRATION_FILE,allrows)
    # Apply the documented calibration offsets to the live dashboard.
    save_runtime_cfg(sn,temperature_offset=temp_offset,humidity_offset=rh_offset,updated_by=actor)
    return rec

def load_deviations():
    with audit_lock:
        rows=_read_json(DEVIATION_FILE,[])
    return rows if isinstance(rows,list) else []

def save_deviation(data,actor,role):
    episode_id=str(data.get("episode_id","")).strip()
    if not episode_id:
        raise HTTPException(status_code=400,detail="Select an alarm episode")
    episodes={str(r.get("id")):r for r in get_all_downtime_records()}
    episode=episodes.get(episode_id)
    if not episode:
        raise HTTPException(status_code=404,detail="Alarm episode was not found")
    now=datetime.now(timezone.utc)
    rows=load_deviations()
    existing=next((r for r in rows if str(r.get("episode_id"))==episode_id),None)
    rec=existing or {
        "id":uuid.uuid4().hex,
        "episode_id":episode_id,
        "logger_sn":episode.get("logger_sn",""),
        "name":episode.get("name",""),
        "created_at":now.isoformat(),
        "created_by":actor,
    }
    rec.update({
        "status":str(data.get("status","OPEN")).upper(),
        "department":str(data.get("department","Maintenance")).strip(),
        "responsible_person":str(data.get("responsible_person","")).strip(),
        "root_cause":str(data.get("root_cause","")).strip(),
        "corrective_action":str(data.get("corrective_action","")).strip(),
        "product_disposition":str(data.get("product_disposition","")).strip(),
        "reviewed_by":str(data.get("reviewed_by","")).strip(),
        "review_date":str(data.get("review_date","")).strip(),
        "notes":str(data.get("notes","")).strip(),
        "updated_at":now.isoformat(),
        "updated_by":actor,
        "updated_role":role,
    })
    if rec["status"] not in ("OPEN","CLOSED"):
        raise HTTPException(status_code=400,detail="Deviation status must be OPEN or CLOSED")
    if not rec["responsible_person"] or not rec["corrective_action"]:
        raise HTTPException(status_code=400,detail="Responsible person and corrective action are required")
    if existing:
        rows=[rec if r is existing else r for r in rows]
    else:
        rows.append(rec)
    with audit_lock:
        _write_json(DEVIATION_FILE,_prune_list_by_retention(rows))
    return rec

def mark_active_downtime_ack(sn,who,when_utc):
    with downtime_lock:
        active=_read_json(DOWNTIME_ACTIVE_FILE,{})
        if not isinstance(active,dict) or sn not in active:
            return
        active[sn]["acknowledged_by"]=who
        active[sn]["acknowledged_at"]=when_utc.isoformat()
        _write_json(DOWNTIME_ACTIVE_FILE,active)

def enforce_audit_retention():
    years=load_audit_config().get("retention_years",3)
    with event_lock:
        rows=_read_json(EVENT_FILE,[])
        _write_json(EVENT_FILE,_prune_list_by_retention(rows,years))
    with downtime_lock:
        rows=_read_json(DOWNTIME_FILE,[])
        _write_json(DOWNTIME_FILE,_prune_list_by_retention(rows,years))
    with audit_lock:
        rows=_read_json(AUDIT_FILE,[])
        _write_json(AUDIT_FILE,_prune_list_by_retention(rows,years))
        rows=_read_json(DEVIATION_FILE,[])
        _write_json(DEVIATION_FILE,_prune_list_by_retention(rows,years))

def create_audit_backup(reason="scheduled",actor="SYSTEM"):
    now=datetime.now(timezone.utc)
    stamp=now.astimezone(PLANT_TZ).strftime("%Y%m%d_%H%M%S")
    out=BACKUP_DIR/f"PBF_Audit_Backup_{stamp}.zip"
    files=[
        EVENT_FILE,STATE_FILE,CONFIG_FILE,DOWNTIME_FILE,DOWNTIME_ACTIVE_FILE,
        REPORT_CONFIG_FILE,REPORT_STATE_FILE,CALIBRATION_FILE,AUDIT_FILE,
        AUDIT_CONFIG_FILE,DEVIATION_FILE,
    ]
    manifest={
        "created_utc":now.isoformat(),
        "created_pacific":now.astimezone(PLANT_TZ).isoformat(),
        "timezone":"America/Vancouver",
        "version":"6.2.0",
        "reason":reason,
        "actor":actor,
        "note":"Data/config backup only. Secrets in .env are intentionally excluded.",
        "files":[p.name for p in files if p.exists()],
    }
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("BACKUP_MANIFEST.json",json.dumps(manifest,indent=2))
        for p in files:
            if p.exists():
                z.write(p,arcname="data/"+p.name)
    keep=load_audit_config().get("backup_keep",30)
    backups=sorted(BACKUP_DIR.glob("PBF_Audit_Backup_*.zip"),key=lambda p:p.stat().st_mtime,reverse=True)
    for old in backups[int(keep):]:
        try:
            old.unlink()
        except Exception:
            pass
    with audit_lock:
        _write_json(AUDIT_BACKUP_STATE_FILE,{
            "last_backup_utc":now.isoformat(),
            "last_backup_pacific":now.astimezone(PLANT_TZ).isoformat(),
            "filename":out.name,
            "reason":reason,
            "actor":actor,
        })
    return out

def audit_backup_status():
    with audit_lock:
        state=_read_json(AUDIT_BACKUP_STATE_FILE,{})
    if not isinstance(state,dict):
        state={}
    latest=state.get("filename","")
    path=BACKUP_DIR/latest if latest else None
    return {
        **state,
        "available":bool(path and path.exists()),
        "size_bytes":path.stat().st_size if path and path.exists() else 0,
    }

def _audit_day_events(sn,start_utc,end_utc):
    with event_lock:
        rows=_read_json(EVENT_FILE,[])
    out=[]
    for e in rows if isinstance(rows,list) else []:
        if sn!="ALL" and str(e.get("logger_sn",""))!=sn:
            continue
        t=_iso_dt(e.get("time"))
        if t and start_utc <= t < end_utc:
            out.append(e)
    return sorted(out,key=lambda e:str(e.get("time","")))

def _audit_episode_ack(episode,events):
    start=_iso_dt(episode.get("start_utc"))
    end=_iso_dt(episode.get("end_utc")) or datetime.max.replace(tzinfo=timezone.utc)
    for e in events:
        if e.get("event")!="ALARM ACKNOWLEDGED":
            continue
        t=_iso_dt(e.get("time"))
        if t and start and start <= t <= end:
            return {
                "by":e.get("actor") or e.get("detail") or "Dashboard acknowledgement",
                "at":e.get("time"),
            }
    if episode.get("acknowledged_by"):
        return {"by":episode.get("acknowledged_by"),"at":episode.get("acknowledged_at")}
    return None

def _pdf_line(c,x,y,text,size=9,bold=False,max_chars=100):
    font="Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font,size)
    chunks=[]
    text=str(text or "")
    while len(text)>max_chars:
        cut=text.rfind(" ",0,max_chars)
        if cut<20:
            cut=max_chars
        chunks.append(text[:cut].strip())
        text=text[cut:].strip()
    chunks.append(text)
    for line in chunks:
        c.drawString(x,y,line)
        y-=size+3
    return y

async def build_audit_pdf(sn,date_text):
    selected,start_utc,end_utc=_calendar_day_window(date_text)
    serials=list(LOGGERS) if sn=="ALL" else [sn]
    if sn!="ALL" and sn not in LOGGERS:
        raise HTTPException(status_code=404,detail="Unknown logger")
    # Pull calibrated LI-COR history for the exact Pacific calendar day.
    if sn=="ALL":
        history=await get_report_history_all(start_utc,end_utc)
    else:
        history=await get_logger_history_chunked(sn,start_utc,end_utc)
        for q in history:
            q["logger_sn"]=sn
    episodes=[
        r for r in get_all_downtime_records()
        if str(r.get("logger_sn","")) in serials and _episode_overlap_minutes(r,start_utc,end_utc)>0
    ]
    events=_audit_day_events(sn,start_utc,end_utc)
    deviations={str(r.get("episode_id")):r for r in load_deviations()}

    dataset={
        "version":"6.2.0",
        "date":selected.isoformat(),
        "timezone":"America/Vancouver",
        "logger_serials":serials,
        "episodes":episodes,
        "events":events,
        "calibration":{s:latest_calibration(s,selected.isoformat()) for s in serials},
        "deviations":deviations,
    }
    snapshot_hash=hashlib.sha256(
        json.dumps(dataset,sort_keys=True,default=str,separators=(",",":")).encode("utf-8")
    ).hexdigest()

    buf=io.BytesIO()
    encrypt=StandardEncryption(
        userPassword="",
        ownerPassword=secrets.token_urlsafe(24),
        canPrint=1,canModify=0,canCopy=1,canAnnotate=0,strength=128
    )
    c=canvas.Canvas(buf,pagesize=letter,encrypt=encrypt)
    W,H=letter

    def footer(page_no):
        c.setStrokeColor(colors.HexColor("#D0D7DE"))
        c.line(36,40,W-36,40)
        c.setFillColor(colors.HexColor("#5D6670"))
        c.setFont("Helvetica",7)
        c.drawString(36,27,f"PBF CMS Audit-Supporting Report · Page {page_no}")
        c.drawRightString(W-36,27,"Modification-restricted PDF · not a digital signature")

    page=1
    y=H-48
    c.setFillColor(colors.HexColor("#0B1F33"))
    c.setFont("Helvetica-Bold",16)
    c.drawString(36,y,"PBF Continuous Monitoring System")
    y-=22
    c.setFont("Helvetica-Bold",13)
    c.drawString(36,y,"Audit-Supporting Environmental Report")
    y-=24
    c.setFillColor(colors.black)
    y=_pdf_line(c,36,y,f"Report Date (Pacific): {selected.isoformat()}",9,True)
    y=_pdf_line(c,36,y,f"Plant Timezone: America/Vancouver | Stored source timestamps: UTC",8)
    y=_pdf_line(c,36,y,f"Generated: {_pt_text(datetime.now(timezone.utc))}",8)
    y=_pdf_line(c,36,y,f"Record Snapshot SHA-256: {snapshot_hash}",7,max_chars=82)
    y-=8

    # Group raw history by logger.
    by_logger={s:[] for s in serials}
    for q in history:
        s=str(q.get("logger_sn",""))
        if s in by_logger and str(q.get("sensor_measurement_type","")).lower()=="temperature":
            try:
                by_logger[s].append(float(q.get("value")))
            except Exception:
                pass

    for serial in serials:
        cfg=get_runtime_cfg(serial)
        eps=[r for r in episodes if str(r.get("logger_sn",""))==serial]
        vals=by_logger.get(serial,[])
        starts=[
            r for r in eps
            if (lambda d: d is not None and start_utc <= d < end_utc)(_iso_dt(r.get("start_utc")))
        ]
        durations=[_episode_overlap_minutes(r,start_utc,end_utc) for r in eps]
        total=sum(durations)
        longest=max(durations) if durations else 0.0
        alarm_limit="MONITOR ONLY" if cfg.get("alarm_type")=="monitor" else (
            ("< " if cfg.get("alarm_type")=="low" else "> ")+f"{float(cfg.get('limit',0)):.1f} C"
        )
        if y<220:
            footer(page); c.showPage(); page+=1; y=H-48

        c.setFillColor(colors.HexColor("#1F6FEB"))
        c.setFont("Helvetica-Bold",11)
        c.drawString(36,y,f"{cfg['name']} · Logger {serial}")
        y-=18
        c.setFillColor(colors.black)
        y=_pdf_line(c,48,y,f"Required Limit: {alarm_limit}",9,True)
        y=_pdf_line(c,48,y,f"Daily Min: {min(vals):.2f} C" if vals else "Daily Min: N/A",9)
        y=_pdf_line(c,48,y,f"Daily Max: {max(vals):.2f} C" if vals else "Daily Max: N/A",9)
        y=_pdf_line(c,48,y,f"Daily Average: {sum(vals)/len(vals):.2f} C" if vals else "Daily Average: N/A",9)
        y=_pdf_line(c,48,y,f"Temperature Samples: {len(vals)}",9)
        y=_pdf_line(c,48,y,f"Alarm Events Started: {len(starts)}",9)
        y=_pdf_line(c,48,y,f"Total Temperature Downtime: {total:.1f} minutes",9)
        y=_pdf_line(c,48,y,f"Longest Event: {longest:.1f} minutes",9)
        cal=latest_calibration(serial,selected.isoformat())
        if cal:
            y=_pdf_line(c,48,y,
                f"Calibration: {cal.get('calibration_date')} · Ref: {cal.get('reference_instrument')} "
                f"{cal.get('reference_serial','')} · Temp offset {float(cal.get('temperature_offset',0)):+.2f} C · "
                f"Next due {cal.get('next_due_date') or 'N/A'}",8,max_chars=88)
        else:
            y=_pdf_line(c,48,y,"Calibration record: No local calibration record found for this date.",8)
        y-=5

        for idx,ep in enumerate(eps,1):
            if y<150:
                footer(page); c.showPage(); page+=1; y=H-48
            start=_iso_dt(ep.get("start_utc"))
            end=_iso_dt(ep.get("end_utc"))
            ack=_audit_episode_ack(ep,events)
            dev=deviations.get(str(ep.get("id")))
            c.setFillColor(colors.HexColor("#C94C4C"))
            c.setFont("Helvetica-Bold",9)
            c.drawString(48,y,f"Alarm #{idx}")
            y-=14
            c.setFillColor(colors.black)
            y=_pdf_line(c,60,y,f"Start: {_pt_text(start)}",8)
            y=_pdf_line(c,60,y,f"Start Temperature: {_fmt_num(ep.get('start_temp'))} C",8)
            y=_pdf_line(c,60,y,f"Worst Temperature: {_fmt_num(ep.get('worst_temp'))} C",8)
            y=_pdf_line(c,60,y,f"Recovered: {_pt_text(end) if end else 'OPEN'}",8)
            y=_pdf_line(c,60,y,f"Duration in selected day: {_episode_overlap_minutes(ep,start_utc,end_utc):.1f} minutes",8)
            y=_pdf_line(c,60,y,f"Acknowledged By: {(ack or {}).get('by','Not recorded')}",8,max_chars=85)
            y=_pdf_line(c,60,y,f"Corrective Action: {(dev or {}).get('corrective_action','Not documented')}",8,max_chars=85)
            y=_pdf_line(c,60,y,f"Root Cause: {(dev or {}).get('root_cause','Not documented')}",8,max_chars=85)
            y=_pdf_line(c,60,y,f"Product Disposition: {(dev or {}).get('product_disposition','Not documented')}",8,max_chars=85)
            y=_pdf_line(c,60,y,f"Deviation Status: {(dev or {}).get('status','Not documented')}",8)
            y-=5
        y-=10

    footer(page)
    c.save()
    pdf=buf.getvalue()
    name_part="All_Loggers" if sn=="ALL" else re.sub(r"[^A-Za-z0-9_-]+","_",LOGGERS[sn]["name"]).strip("_")
    filename=f"PBF_Audit_Report_{name_part}_{selected.isoformat()}.pdf"
    return pdf,filename,snapshot_hash

async def maybe_daily_audit_maintenance():
    if DEPLOYMENT_ROLE!="CLOUD-PRIMARY":
        return
    cfg=load_audit_config()
    now_pt=datetime.now(timezone.utc).astimezone(PLANT_TZ)
    with audit_lock:
        state=_read_json(AUDIT_BACKUP_STATE_FILE,{})
        if not isinstance(state,dict):
            state={}
    # Retention enforcement once each local calendar day.
    if state.get("last_retention_date") != now_pt.date().isoformat():
        enforce_audit_retention()
        with audit_lock:
            state=_read_json(AUDIT_BACKUP_STATE_FILE,{})
            state["last_retention_date"]=now_pt.date().isoformat()
            state["last_retention_utc"]=datetime.now(timezone.utc).isoformat()
            _write_json(AUDIT_BACKUP_STATE_FILE,state)
    if not cfg.get("backup_enabled",True):
        return
    hh,mm=map(int,cfg.get("backup_time","02:15").split(":"))
    scheduled=now_pt.replace(hour=hh,minute=mm,second=0,microsecond=0)
    if now_pt < scheduled or state.get("last_backup_date")==now_pt.date().isoformat():
        return
    path=await asyncio.to_thread(create_audit_backup,"scheduled","SYSTEM")
    with audit_lock:
        state=_read_json(AUDIT_BACKUP_STATE_FILE,{})
        state["last_backup_date"]=now_pt.date().isoformat()
        state["filename"]=path.name
        _write_json(AUDIT_BACKUP_STATE_FILE,state)
    append_audit_record("AUTOMATIC AUDIT BACKUP CREATED",category="BACKUP",
                        detail=f"Created {path.name}",extra={"filename":path.name})

downtime_lock=threading.Lock()
report_lock=threading.Lock()

def _iso_dt(value):
    if isinstance(value,datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except Exception:
        return None

def _pt_text(dt):
    if not dt:
        return ""
    return dt.astimezone(PLANT_TZ).strftime("%Y-%m-%d %I:%M:%S %p %Z")

def _update_worst_temp(rec, temp):
    if temp is None:
        return
    try:
        t=float(temp)
    except Exception:
        return
    rec["last_temp"]=t
    cur=rec.get("worst_temp")
    if cur is None:
        rec["worst_temp"]=t
    elif rec.get("alarm_type")=="low":
        rec["worst_temp"]=min(float(cur),t)
    else:
        rec["worst_temp"]=max(float(cur),t)

def start_or_resume_downtime(sn, device, when_utc, temp):
    """Persist an alarm episode so server restarts do not lose downtime duration."""
    with downtime_lock:
        active=_read_json(DOWNTIME_ACTIVE_FILE,{})
        if not isinstance(active,dict):
            active={}
        rec=active.get(sn)
        if not isinstance(rec,dict):
            rec={
                "id":uuid.uuid4().hex,
                "logger_sn":sn,
                "name":device.get("name",sn),
                "alarm_type":device.get("alarm_type"),
                "limit":device.get("limit"),
                "start_utc":when_utc.isoformat(),
                "start_temp":float(temp) if temp is not None else None,
                "worst_temp":float(temp) if temp is not None else None,
                "last_temp":float(temp) if temp is not None else None,
                "status":"OPEN",
                "source":"v6.1+ persistent tracker",
            }
            active[sn]=rec
        else:
            rec["name"]=device.get("name",rec.get("name",sn))
            rec["limit"]=device.get("limit",rec.get("limit"))
            rec["alarm_type"]=device.get("alarm_type",rec.get("alarm_type"))
            _update_worst_temp(rec,temp)
            active[sn]=rec
        _write_json(DOWNTIME_ACTIVE_FILE,active)
        return dict(rec)

def update_active_downtime(sn,temp):
    with downtime_lock:
        active=_read_json(DOWNTIME_ACTIVE_FILE,{})
        if not isinstance(active,dict) or sn not in active:
            return
        rec=active[sn]
        _update_worst_temp(rec,temp)
        active[sn]=rec
        _write_json(DOWNTIME_ACTIVE_FILE,active)

def close_downtime(sn, when_utc, recovery_temp=None, reason="RETURN TO NORMAL"):
    with downtime_lock:
        active=_read_json(DOWNTIME_ACTIVE_FILE,{})
        if not isinstance(active,dict):
            active={}
        rec=active.pop(sn,None)
        if not isinstance(rec,dict):
            return None
        started=_iso_dt(rec.get("start_utc")) or when_utc
        rec["end_utc"]=when_utc.isoformat()
        rec["duration_minutes"]=round(max(0.0,(when_utc-started).total_seconds()/60.0),2)
        rec["recovery_temp"]=float(recovery_temp) if recovery_temp is not None else None
        rec["status"]="CLOSED"
        rec["end_reason"]=reason
        _update_worst_temp(rec,recovery_temp)
        history=_read_json(DOWNTIME_FILE,[])
        if not isinstance(history,list):
            history=[]
        history.append(rec)
        history=history[-10000:]
        _write_json(DOWNTIME_FILE,history)
        _write_json(DOWNTIME_ACTIVE_FILE,active)
        return dict(rec)

def _legacy_downtime_from_events(cutoff_utc=None):
    """Best-effort reconstruction of pre-v6.1 alarm episodes from persistent events."""
    with event_lock:
        rows=_read_json(EVENT_FILE,[])
    if not isinstance(rows,list):
        return []
    open_by={}
    out=[]
    for e in sorted(rows,key=lambda x:str(x.get("time",""))):
        sn=str(e.get("logger_sn",""))
        if sn not in LOGGERS:
            continue
        t=_iso_dt(e.get("time"))
        if not t or (cutoff_utc and t >= cutoff_utc):
            continue
        ev=str(e.get("event",""))
        if ev in ("HIGH TEMPERATURE ALARM","LOW TEMPERATURE ALARM"):
            if sn not in open_by:
                open_by[sn]={
                    "id":"legacy-"+str(e.get("id") or uuid.uuid4().hex),
                    "logger_sn":sn,
                    "name":e.get("name") or LOGGERS[sn]["name"],
                    "alarm_type":e.get("alarm_type") or LOGGERS[sn].get("alarm_type"),
                    "limit":e.get("limit"),
                    "start_utc":t.isoformat(),
                    "start_temp":e.get("reading"),
                    "worst_temp":e.get("reading"),
                    "last_temp":e.get("reading"),
                    "status":"OPEN",
                    "source":"reconstructed from alarm_events.json",
                }
        elif ev=="5-MIN REMINDER" and sn in open_by:
            _update_worst_temp(open_by[sn],e.get("reading"))
        elif ev in ("RETURN TO NORMAL","ALARM NOTIFICATION SUSPENDED") and sn in open_by:
            rec=open_by.pop(sn)
            _update_worst_temp(rec,e.get("reading"))
            rec["end_utc"]=t.isoformat()
            started=_iso_dt(rec.get("start_utc")) or t
            rec["duration_minutes"]=round(max(0.0,(t-started).total_seconds()/60.0),2)
            rec["recovery_temp"]=e.get("reading")
            rec["status"]="CLOSED"
            rec["end_reason"]=ev
            out.append(rec)
    # Keep unclosed legacy episodes visible as open history.
    out.extend(open_by.values())
    return out

def get_all_downtime_records():
    with downtime_lock:
        history=_read_json(DOWNTIME_FILE,[])
        active=_read_json(DOWNTIME_ACTIVE_FILE,{})
    if not isinstance(history,list):
        history=[]
    if not isinstance(active,dict):
        active={}
    first_new=None
    starts=[_iso_dt(r.get("start_utc")) for r in history if isinstance(r,dict)]
    starts.extend(_iso_dt(r.get("start_utc")) for r in active.values() if isinstance(r,dict))
    starts=[d for d in starts if d]
    if starts:
        first_new=min(starts)
    legacy=_legacy_downtime_from_events(first_new)
    combined=[r for r in legacy if isinstance(r,dict)]
    combined.extend(r for r in history if isinstance(r,dict))
    combined.extend(dict(r) for r in active.values() if isinstance(r,dict))
    return sorted(combined,key=lambda r:str(r.get("start_utc","")))

def load_report_config():
    default={
        "enabled":True,
        "time":"08:00",
        "recipient":ALERT_EMAIL,
        "hours":24,
    }
    with report_lock:
        saved=_read_json(REPORT_CONFIG_FILE,{})
    if not isinstance(saved,dict):
        saved={}
    cfg={**default,**saved}
    cfg["enabled"]=bool(cfg.get("enabled",True))
    cfg["time"]=_valid_hhmm(cfg.get("time","08:00"),"08:00")
    cfg["recipient"]=str(cfg.get("recipient") or ALERT_EMAIL).strip()
    try:
        cfg["hours"]=max(1,min(168,int(cfg.get("hours",24))))
    except Exception:
        cfg["hours"]=24
    return cfg

def save_report_config(enabled,time_text,recipient,hours=24,updated_by="admin"):
    time_text=_valid_hhmm(time_text,"")
    if not time_text:
        raise HTTPException(status_code=400,detail="Daily report time must use HH:MM")
    recipient=str(recipient or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",recipient):
        raise HTTPException(status_code=400,detail="Enter a valid report recipient email")
    try:
        hours=max(1,min(168,int(hours)))
    except Exception:
        hours=24
    cfg={
        "enabled":bool(enabled),
        "time":time_text,
        "recipient":recipient,
        "hours":hours,
        "updated_at":datetime.now(timezone.utc).isoformat(),
        "updated_by":updated_by,
    }
    with report_lock:
        _write_json(REPORT_CONFIG_FILE,cfg)
    return cfg

def _episode_overlap_minutes(rec,start_utc,end_utc):
    rs=_iso_dt(rec.get("start_utc"))
    re_dt=_iso_dt(rec.get("end_utc")) or end_utc
    if not rs:
        return 0.0
    lo=max(rs,start_utc)
    hi=min(re_dt,end_utc)
    return max(0.0,(hi-lo).total_seconds()/60.0)

def _report_alarm_events(start_utc,end_utc):
    with event_lock:
        rows=_read_json(EVENT_FILE,[])
    if not isinstance(rows,list):
        return []
    keep=[]
    for e in rows:
        sn=str(e.get("logger_sn",""))
        if sn not in LOGGERS:
            continue
        t=_iso_dt(e.get("time"))
        if not t or not (start_utc <= t < end_utc):
            continue
        ev=str(e.get("event",""))
        if (
            ev in ("HIGH TEMPERATURE ALARM","LOW TEMPERATURE ALARM","5-MIN REMINDER",
                   "RETURN TO NORMAL","ALARM ACKNOWLEDGED","ALARM NOTIFICATION SUSPENDED")
            or "TEMPERATURE" in ev
        ):
            keep.append(e)
    return sorted(keep,key=lambda e:str(e.get("time","")))

def add_event(logger_sn, name, event, reading=None, limit=None, alarm_type=None, email="—", push="—", detail="",
              actor="", role="", category="EVENT"):
    rec={"id":uuid.uuid4().hex,"time":datetime.now(timezone.utc).isoformat(),"logger_sn":logger_sn,"name":name,
         "event":event,"reading":reading,"limit":limit,"alarm_type":alarm_type,"email":email,"push":push,"detail":detail,
         "actor":actor or "","role":role or ""}
    with event_lock:
        rows=_read_json(EVENT_FILE,[])
        if not isinstance(rows,list): rows=[]
        rows.append(rec)
        rows=_prune_list_by_retention(rows)
        _write_json(EVENT_FILE,rows)
    append_audit_record(event,category=category,actor=actor or "SYSTEM",role=role or "system",
                        logger_sn=logger_sn,name=name,detail=detail,source_event_id=rec["id"],
                        extra={"reading":reading,"limit":limit,"alarm_type":alarm_type,"email":email,"push":push})
    return rec["id"]

def update_event(event_id, **changes):
    with event_lock:
        rows=_read_json(EVENT_FILE,[])
        for r in rows:
            if r.get("id")==event_id:
                r.update(changes); break
        _write_json(EVENT_FILE,rows)

def get_alarm_state():
    with event_lock: return _read_json(STATE_FILE,{"acked":{}})

def set_ack(logger_sn, value=True):
    with event_lock:
        st=_read_json(STATE_FILE,{"acked":{}}); st.setdefault("acked",{})[logger_sn]=bool(value); _write_json(STATE_FILE,st)

def clear_ack(logger_sn):
    with event_lock:
        st=_read_json(STATE_FILE,{"acked":{}}); st.setdefault("acked",{}).pop(logger_sn,None); _write_json(STATE_FILE,st)

# Free phone push notifications via ntfy.sh
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
push_status = {"configured": bool(NTFY_TOPIC), "last_success": None, "last_error": None, "last_title": None}

def send_push(title: str, message: str, priority: str = "max", tags: str = "rotating_light"):
    """Send a free push notification through ntfy. Topic should be long/random and kept private."""
    if not NTFY_TOPIC:
        raise RuntimeError("NTFY_TOPIC is not configured in .env")
    import urllib.request
    url = f"{NTFY_BASE_URL}/{NTFY_TOPIC}"
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title.encode("ascii", "ignore").decode("ascii") or "PBF Temperature Alert",
            "Priority": priority,
            "Tags": tags,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()
    push_status["last_success"] = datetime.now().isoformat(timespec="seconds")
    push_status["last_error"] = None
    push_status["last_title"] = title

def _safe_push(title: str, message: str, priority: str = "max", tags: str = "rotating_light", event_id=None):
    try:
        send_push(title, message, priority, tags)
        print(f"Phone push sent: {title}")
        if event_id: update_event(event_id,push="SENT")
    except Exception as e:
        push_status["last_error"] = str(e)
        push_status["last_title"] = title
        print(f"Phone push notification error: {e}")
        if event_id: update_event(event_id,push="FAILED: "+str(e)[:80])

email_alarm_started={}
email_alarm_last_sent={}
email_lock=threading.Lock()
email_status={"last_success":None,"last_error":None,"last_subject":None}

def gmail_ready():
    return bool(GMAIL_SENDER and GMAIL_APP_PASSWORD and ALERT_EMAIL)

def send_gmail(subject,body,recipient=None,attachments=None):
    if not gmail_ready(): raise RuntimeError("Gmail settings are incomplete in .env")
    to_addr=(recipient or ALERT_EMAIL).strip()
    msg=EmailMessage(); msg["From"]=GMAIL_SENDER; msg["To"]=to_addr; msg["Subject"]=subject; msg.set_content(body)
    for item in (attachments or []):
        filename,data,mime=item
        maintype,subtype=(mime.split("/",1)+["octet-stream"])[:2] if "/" in mime else ("application","octet-stream")
        msg.add_attachment(data,maintype=maintype,subtype=subtype,filename=filename)
    with smtplib.SMTP_SSL("smtp.gmail.com",465,timeout=20) as smtp:
        smtp.login(GMAIL_SENDER,GMAIL_APP_PASSWORD); smtp.send_message(msg)

def fmt_local_time(dt):
    return dt.astimezone().strftime("%b %d, %Y %I:%M %p")

def _safe_send(subject,body,event_id=None):
    try:
        send_gmail(subject,body)
        email_status["last_success"]=datetime.now(timezone.utc).isoformat()
        email_status["last_error"]=None
        email_status["last_subject"]=subject
        print("Gmail sent:",subject)
        if event_id: update_event(event_id,email="SENT")
    except Exception as e:
        email_status["last_error"]=str(e)
        email_status["last_subject"]=subject
        print("Gmail notification error:",repr(e))
        if event_id: update_event(event_id,email="FAILED: "+str(e)[:80])

def process_email_notifications(devices):
    if not NOTIFICATIONS_ENABLED:
        return
    now=datetime.now(timezone.utc)
    with email_lock:
        for d in devices:
            sn=d["logger_sn"]; current=bool(d["alarm"]); previous=email_alarm_state.get(sn)
            if previous is None:
                email_alarm_state[sn]=current
                if not current: continue
            temp=float(d["temperature"]) if d.get("temperature") is not None else None
            ll="Low" if d["alarm_type"]=="low" else "High"
            st="LOW TEMPERATURE" if d["alarm_type"]=="low" else "HIGH TEMPERATURE"
            if current and previous is not True:
                episode=start_or_resume_downtime(sn,d,now,temp)
                episode_started=_iso_dt(episode.get("start_utc")) or now
                email_alarm_started[sn]=episode_started; email_alarm_last_sent[sn]=now; clear_ack(sn)
                eid=add_event(sn,d["name"],st+" ALARM",temp,d["limit"],d["alarm_type"],
                              "QUEUED" if gmail_ready() else "NOT CONFIGURED", "QUEUED" if NTFY_TOPIC else "NOT CONFIGURED")
                body=(f"PBF TEMPERATURE ALARM\n\nEquipment: {d['name']}\nTemperature: {temp:.1f} C\n"
                      f"{ll} Limit: {d['limit']:.1f} C\nStatus: {st}\nTime: {fmt_local_time(now)}\n\nLI-COR Temperature Monitoring")
                if gmail_ready(): threading.Thread(target=_safe_send,args=(f"PBF Temperature Alarm - {d['name']}",body,eid),daemon=True).start()
                push_title=f"{d['name']} HIGH TEMP" if d["alarm_type"]=="high" else f"{d['name']} LOW TEMP"
                push_msg=f"{temp:.1f} C | Limit {d['limit']:.1f} C | ALARM ACTIVE"
                if NTFY_TOPIC: threading.Thread(target=_safe_push,args=(push_title,push_msg,"max","rotating_light",eid),daemon=True).start()
            elif current and previous is True:
                update_active_downtime(sn,temp)
                last_sent=email_alarm_last_sent.get(sn)
                if last_sent is None or (now-last_sent).total_seconds() >= 300:
                    email_alarm_last_sent[sn]=now; started=email_alarm_started.get(sn,now)
                    mins=max(1,round((now-started).total_seconds()/60))
                    eid=add_event(sn,d["name"],"5-MIN REMINDER",temp,d["limit"],d["alarm_type"],
                                  "QUEUED" if gmail_ready() else "NOT CONFIGURED", "QUEUED" if NTFY_TOPIC else "NOT CONFIGURED", f"Active {mins} min")
                    body=(f"PBF TEMPERATURE ALARM - 5 MIN REMINDER\n\nEquipment: {d['name']}\nTemperature: {temp:.1f} C\n{ll} Limit: {d['limit']:.1f} C\n"
                          f"Status: {st} - STILL ACTIVE\nAlarm Active: {mins} minutes\nTime: {fmt_local_time(now)}\n\nLI-COR Temperature Monitoring")
                    if gmail_ready(): threading.Thread(target=_safe_send,args=(f"PBF Temperature Alarm 5-Min Reminder - {d['name']}",body,eid),daemon=True).start()
                    push_title=f"{d['name']} HIGH TEMP" if d["alarm_type"]=="high" else f"{d['name']} LOW TEMP"
                    push_msg=f"{temp:.1f} C | Limit {d['limit']:.1f} C | STILL ACTIVE {mins} min"
                    if NTFY_TOPIC: threading.Thread(target=_safe_push,args=(push_title,push_msg,"max","rotating_light",eid),daemon=True).start()
            elif not current and previous is True:
                started=email_alarm_started.pop(sn,None); email_alarm_last_sent.pop(sn,None); clear_ack(sn)
                # If the temperature is still outside the limit but monitoring was intentionally
                # disabled by NOT IN USE or by the weekly scheduler, do not send a false
                # 'return to normal' message. Log the suspension instead.
                if bool(d.get("raw_alarm")) and not bool(d.get("effective_monitoring_enabled",True)):
                    reason=d.get("suppression_reason") or "Alarm monitoring disabled"
                    close_downtime(sn,now,temp,"ALARM NOTIFICATION SUSPENDED - "+reason)
                    add_event(sn,d["name"],"ALARM NOTIFICATION SUSPENDED",temp,d["limit"],d["alarm_type"],
                              detail=f"Temperature still outside limit; {reason}")
                else:
                    closed_episode=close_downtime(sn,now,temp,"RETURN TO NORMAL")
                    if closed_episode:
                        duration=f"{max(1,round(float(closed_episode.get('duration_minutes',0))))} minutes"
                    else:
                        duration="Unknown" if not started else f"{max(1,round((now-started).total_seconds()/60))} minutes"
                    eid=add_event(sn,d["name"],"RETURN TO NORMAL",temp,d["limit"],d["alarm_type"],
                                  "QUEUED" if gmail_ready() else "NOT CONFIGURED", "QUEUED" if NTFY_TOPIC else "NOT CONFIGURED", "Duration "+duration)
                    body=(f"PBF TEMPERATURE NORMAL\n\nEquipment: {d['name']}\nTemperature: {temp:.1f} C\nStatus: RETURNED TO NORMAL\nAlarm Duration: {duration}\nTime: {fmt_local_time(now)}\n\nLI-COR Temperature Monitoring")
                    if gmail_ready(): threading.Thread(target=_safe_send,args=(f"PBF Temperature Normal - {d['name']}",body,eid),daemon=True).start()
                    if NTFY_TOPIC: threading.Thread(target=_safe_push,args=(f"{d['name']} TEMP NORMAL",f"{temp:.1f} C | Alarm cleared | Duration {duration}","high","white_check_mark",eid),daemon=True).start()
            email_alarm_state[sn]=current


LOGGERS={
"21387391":{"id":"L1","name":"Tortilla L1 Cooler","alarm_type":"high","limit":13.0},
"22519364":{"id":"L2","name":"Tortilla L2 Cooler","alarm_type":"high","limit":13.0},
"21387388":{"id":"L3","name":"Tortilla L3 Cooler","alarm_type":"high","limit":13.0},
"21147189":{"id":"BAGEL","name":"Bagel Cooler","alarm_type":"high","limit":9.0},
"22547311":{"id":"BP1","name":"Bagel Proofer 1","alarm_type":"low","limit":30.0},
"22547310":{"id":"BP2","name":"Bagel Proofer 2","alarm_type":"low","limit":30.0},
"22212731":{"id":"PLANT","name":"PBF Plant Temperature","model":"MX1101","alarm_type":"monitor","limit":0.0}}

app=FastAPI(title="PBF Rise Baking Burnaby Dashboard v6.2.0 Cloud Edition")

@app.get("/api/push-status")
def api_push_status():
    return {
        "configured": bool(NTFY_TOPIC),
        "service": "ntfy",
        "last_success": push_status.get("last_success"),
        "last_error": push_status.get("last_error"),
        "last_title": push_status.get("last_title"),
        "windows_trust_store": TRUSTSTORE_ACTIVE,
    }

@app.post("/api/test-push")
def api_test_push():
    title = "PBF ALARM TEST"
    body = "34.2 C | HIGH TEMP test | iPhone alerts are working"
    if not NTFY_TOPIC:
        return {"ok": False, "configured": False, "error": "NTFY_TOPIC is not configured"}
    try:
        send_push(title, body, "max", "rotating_light")
        return {"ok": True, "configured": True}
    except Exception as e:
        push_status["last_error"] = str(e)
        push_status["last_title"] = title
        return {"ok": False, "configured": True, "error": str(e)}

@app.get("/api/deployment-status")
def api_deployment_status():
    return {
        "version":"6.2.0",
        "edition":"OVH Cloud",
        "deployment_role":DEPLOYMENT_ROLE,
        "automatic_notifications_enabled":NOTIFICATIONS_ENABLED,
        "gmail_configured":gmail_ready(),
        "ntfy_configured":bool(NTFY_TOPIC),
    }

app.mount("/static",StaticFiles(directory=BASE/"static"),name="static")

@app.get("/")
def home(): return FileResponse(BASE/"static"/"index.html")

def hdr():
    if not TOKEN or TOKEN=="PASTE_YOUR_NEW_TOKEN_HERE":
        raise HTTPException(500,"Configure LI-COR token in .env")
    return {"Authorization":f"Bearer {TOKEN}","accept":"application/json"}

async def getdata(serials,minutes):
    end=datetime.now(timezone.utc).replace(microsecond=0)
    start=end-timedelta(minutes=minutes)
    p={"loggers":",".join(serials),
       "start_date_time":start.strftime("%Y-%m-%d %H:%M:%S"),
       "end_date_time":end.strftime("%Y-%m-%d %H:%M:%S")}
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.get(API+"/v1/data",params=p,headers=hdr())
    if r.status_code!=200:
        raise HTTPException(r.status_code,"LI-COR API error: "+r.text[:300])
    return r.json().get("data",[])

async def getdata_range(serials,start,end):
    p={"loggers":",".join(serials),
       "start_date_time":start.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
       "end_date_time":end.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r=await c.get(API+"/v1/data",params=p,headers=hdr())
        if r.status_code!=200:
            raise HTTPException(r.status_code,"LI-COR API error: "+r.text[:300])
        payload=r.json()
        if isinstance(payload,list): return payload
        if isinstance(payload,dict):
            for key in ("data","items","results"):
                if isinstance(payload.get(key),list): return payload[key]
        return []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502,detail="LI-COR download request failed: "+str(e))

async def get_24h_chunked(sn):
    # Four 6-hour requests are more reliable than one large 24-hour API request.
    end=datetime.now(timezone.utc).replace(microsecond=0)
    start=end-timedelta(hours=24)
    rows=[]
    cursor=start
    while cursor < end:
        chunk_end=min(cursor+timedelta(hours=6),end)
        rows.extend(await getdata_range([sn],cursor,chunk_end))
        cursor=chunk_end
    # Deduplicate records where chunk boundaries overlap.
    unique={}
    for q in rows:
        key=(str(q.get("timestamp","")),str(q.get("sensor_measurement_type","")).lower(),
             str(q.get("sensor_sn","")),str(q.get("value","")))
        unique[key]=q
    return list(unique.values())


@app.get("/trends")
def trends_page():
    return RedirectResponse(url="/static/trends.html", status_code=307)

@app.get("/events")
def events_page():
    return RedirectResponse(url="/static/events.html", status_code=307)


@app.get("/api/dashboard-history")
async def dashboard_history():
    """Return the latest 6 hours of temperature/RH history for all dashboard loggers."""
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=6)
    rows = await getdata_range(list(LOGGERS), start, end)

    unique = {}
    for q in rows:
        sn = str(q.get("logger_sn", ""))
        typ = str(q.get("sensor_measurement_type", "")).lower()
        if sn not in LOGGERS or typ not in ("temperature", "rh"):
            continue
        key = (
            sn,
            str(q.get("timestamp", "")),
            typ,
            str(q.get("sensor_sn", "")),
            str(q.get("value", "")),
        )
        unique[key] = calibrated_row(q)

    data = sorted(
        unique.values(),
        key=lambda q: (str(q.get("logger_sn", "")), str(q.get("timestamp", "")))
    )
    return {
        "hours": 6,
        "data": data,
        "generated_utc": end.isoformat()
    }

@app.get("/api/current")
async def current():
    rows=await getdata(list(LOGGERS),40); latest={}
    for q in rows:
        sn=str(q.get("logger_sn","")); typ=str(q.get("sensor_measurement_type","")).lower()
        if sn in LOGGERS and typ in ("temperature","rh"):
            k=(sn,typ)
            if k not in latest or q.get("timestamp","")>latest[k].get("timestamp",""):
                latest[k]=q
    now=datetime.now(timezone.utc); out=[]
    for sn in LOGGERS:
        cfg=get_runtime_cfg(sn)
        t=latest.get((sn,"temperature")); rh=latest.get((sn,"rh"))
        newest=max([x for x in (t,rh) if x],key=lambda x:x.get("timestamp",""),default=None)
        age=None
        if newest:
            try: age=round((now-datetime.fromisoformat(newest["timestamp"].replace("Z","+00:00"))).total_seconds()/60,1)
            except: pass
        raw_temp=t.get("value") if t else None
        raw_rh=rh.get("value") if rh else None
        temp=calibrated_value(sn,"temperature",raw_temp) if raw_temp is not None else None
        humidity=calibrated_value(sn,"rh",raw_rh) if raw_rh is not None else None
        lim=cfg["limit"]; typ=cfg["alarm_type"]
        raw_alarm=temp is not None and ((typ=="high" and float(temp)>lim) or (typ=="low" and float(temp)<lim))
        monitoring_enabled=bool(cfg.get("monitoring_enabled",True))
        schedule_active=bool(cfg.get("schedule_active_now",True))
        effective_monitoring=bool(monitoring_enabled and schedule_active)
        alarm=bool(raw_alarm and effective_monitoring)
        if not monitoring_enabled:
            suppression_reason="NOT IN USE"
        elif cfg.get("schedule_enabled") and not schedule_active:
            suppression_reason="OUTSIDE ALARM SCHEDULE"
        else:
            suppression_reason=None
        if age is None or age>30: comm_status="OFFLINE"
        elif age>15: comm_status="STALE"
        else: comm_status="ONLINE"
        acked=bool(get_alarm_state().get("acked",{}).get(sn,False)) if alarm else False
        out.append({**cfg,"logger_sn":sn,"temperature":temp,
                    "rh":humidity,
                    "raw_temperature":raw_temp,"raw_rh":raw_rh,
                    "timestamp":newest.get("timestamp") if newest else None,
                    "age_minutes":age,"stale":comm_status!="ONLINE","comm_status":comm_status,
                    "effective_monitoring_enabled":effective_monitoring,
                    "suppression_reason":suppression_reason,
                    "raw_alarm":raw_alarm,"alarm_suppressed":bool(raw_alarm and not effective_monitoring),
                    "alarm":alarm,"acked":acked})
    process_email_notifications(out)
    # Refresh acknowledgement values in case a new transition cleared one.
    ackmap=get_alarm_state().get("acked",{})
    for d in out: d["acked"]=bool(ackmap.get(d["logger_sn"],False)) if d["alarm"] else False
    return {"devices":out,"server_utc":now.isoformat(),"gmail_configured":gmail_ready()}


@app.get("/api/v501-status")
async def v501_status():
    """Small diagnostic endpoint for the v5.0.1 communication/alarm panels."""
    cur = await current()
    with event_lock:
        events = _read_json(EVENT_FILE, [])
    return {
        "version": "6.2.0",
        "logger_count": len(cur.get("devices", [])),
        "loggers": [{"name": d.get("name"), "logger_sn": d.get("logger_sn"), "comm_status": d.get("comm_status"), "age_minutes": d.get("age_minutes")} for d in cur.get("devices", [])],
        "alarm_event_count": len(events),
    }


@app.get("/settings")
def settings_page():
    return RedirectResponse(url="/static/settings.html", status_code=307)

@app.get("/audit")
def audit_page():
    return RedirectResponse(url="/static/audit.html", status_code=307)

@app.get("/api/admin/status")
def admin_status(request: Request):
    info=_session_info(request)
    return {
        "configured": bool(ADMIN_PASSWORD_HASH),
        "operator_configured": bool(OPERATOR_PASSWORD_HASH),
        "logged_in": bool(info),
        "username": info.get("username") if info else None,
        "role": info.get("role") if info else None,
        "session_hours": SESSION_HOURS,
    }

@app.post("/api/admin/login")
async def admin_login(request: Request):
    try:
        data=await request.json()
    except Exception:
        data={}
    username=str(data.get("username","")).strip()
    password=str(data.get("password",""))

    role=None
    canonical_user=None
    if hmac.compare_digest(username,ADMIN_USERNAME) and _password_ok(password):
        role="admin"; canonical_user=ADMIN_USERNAME
    elif hmac.compare_digest(username,OPERATOR_USERNAME) and _operator_password_ok(password):
        role="operator"; canonical_user=OPERATOR_USERNAME

    if not role:
        add_event("AUTH","Settings","LOGIN FAILED",detail=f"Username: {username or '(blank)'}")
        raise HTTPException(status_code=401,detail="Invalid username or password")

    token, expiry=_new_session(canonical_user,role)
    add_event("AUTH","Settings",f"{role.upper()} LOGIN",detail=f"User {canonical_user}",actor=canonical_user,role=role,category="ACCESS")
    resp=JSONResponse({"ok":True,"username":canonical_user,"role":role,"expires":expiry.isoformat()})
    resp.set_cookie(
        "pbf_admin_session",token,
        max_age=SESSION_HOURS*3600,
        httponly=True,samesite="strict",secure=False,path="/"
    )
    return resp

@app.post("/api/admin/change-password")
async def admin_change_password(request: Request):
    global ADMIN_PASSWORD_HASH, ADMIN_USERNAME
    info=require_admin(request)
    user=info["username"]
    try:
        data=await request.json()
    except Exception:
        data={}

    current_password=str(data.get("current_password",""))
    new_username=str(data.get("new_username",ADMIN_USERNAME)).strip() or ADMIN_USERNAME
    new_password=str(data.get("new_password",""))
    confirm_password=str(data.get("confirm_password",""))

    if not _password_ok(current_password):
        add_event("ADMIN","Admin","ADMIN ACCOUNT CHANGE FAILED",detail=f"Incorrect current password for {user}")
        raise HTTPException(status_code=400,detail="Current password is incorrect")
    if not _valid_settings_username(new_username):
        raise HTTPException(status_code=400,detail="Admin username must be 1-64 printable characters; letters, numbers, spaces and symbols are allowed")
    if new_password != confirm_password:
        raise HTTPException(status_code=400,detail="New passwords do not match")
    if not _valid_settings_password(new_password):
        raise HTTPException(status_code=400,detail="Admin password must be exactly 6 letters or numbers")
    if hmac.compare_digest(current_password,new_password) and hmac.compare_digest(new_username,ADMIN_USERNAME):
        raise HTTPException(status_code=400,detail="Change the username or choose a different password")

    old_username=ADMIN_USERNAME
    new_hash=_make_password_hash(new_password)
    _update_env_value("ADMIN_USERNAME",new_username)
    _update_env_value("ADMIN_PASSWORD_HASH",new_hash)
    ADMIN_USERNAME=new_username
    ADMIN_PASSWORD_HASH=new_hash

    with admin_lock:
        admin_sessions.clear()
    token, expiry=_new_session(ADMIN_USERNAME,"admin")
    detail=f"Admin account updated by {user}; username {old_username!r} -> {new_username!r}"
    add_event("ADMIN","Admin","ADMIN ACCOUNT CHANGED",detail=detail)
    resp=JSONResponse({"ok":True,"message":"Admin username/password updated successfully","username":ADMIN_USERNAME,"expires":expiry.isoformat()})
    resp.set_cookie("pbf_admin_session",token,max_age=SESSION_HOURS*3600,httponly=True,samesite="strict",secure=False,path="/")
    return resp

@app.post("/api/admin/operator-password")
async def admin_set_operator_password(request: Request):
    global OPERATOR_PASSWORD_HASH, OPERATOR_USERNAME
    info=require_admin(request)
    try:
        data=await request.json()
    except Exception:
        data={}
    username=str(data.get("username",OPERATOR_USERNAME)).strip() or "operator"
    new_password=str(data.get("new_password",""))
    confirm_password=str(data.get("confirm_password",""))
    if not _valid_settings_username(username):
        raise HTTPException(status_code=400,detail="Operator username must be 1-64 printable characters; letters, numbers, spaces and symbols are allowed")
    if new_password != confirm_password:
        raise HTTPException(status_code=400,detail="Operator passwords do not match")
    if not _valid_settings_password(new_password):
        raise HTTPException(status_code=400,detail="Operator password must be exactly 6 letters or numbers")
    new_hash=_make_password_hash(new_password)
    _update_env_value("OPERATOR_USERNAME",username)
    _update_env_value("OPERATOR_PASSWORD_HASH",new_hash)
    OPERATOR_USERNAME=username
    OPERATOR_PASSWORD_HASH=new_hash
    # Invalidate existing operator sessions but preserve admin sessions.
    with admin_lock:
        for token,item in list(admin_sessions.items()):
            if isinstance(item,dict) and item.get("role")=="operator":
                admin_sessions.pop(token,None)
    add_event("ADMIN","Admin","OPERATOR PASSWORD SET",detail=f"Operator account {username} set/reset by {info['username']}")
    return {"ok":True,"username":username}

@app.post("/api/admin/logout")
def admin_logout(request: Request):
    info=_session_info(request)
    token=request.cookies.get("pbf_admin_session","")
    if info:
        add_event("AUTH","Settings",f"{str(info.get('role','')).upper()} LOGOUT",
                  detail=f"User {info.get('username')}",actor=info.get("username",""),role=info.get("role",""),category="ACCESS")
    with admin_lock:
        if token: admin_sessions.pop(token,None)
    resp=JSONResponse({"ok":True})
    resp.delete_cookie("pbf_admin_session",path="/")
    return resp

@app.get("/api/admin/config")
def admin_get_config(request: Request):
    info=require_settings_access(request)
    return {
        "devices":[{**get_runtime_cfg(sn),"logger_sn":sn} for sn in LOGGERS],
        "reminder_minutes":5,
        "role":info["role"],
        "username":info["username"],
        "operator_configured":bool(OPERATOR_PASSWORD_HASH),
        "operator_username":OPERATOR_USERNAME,
        "report_config":load_report_config(),
    }

@app.post("/api/admin/schedule/all")
async def admin_update_all_schedules(request: Request):
    """Apply one weekly alarm-notification schedule to every logger.

    Both Admin and Operator may change schedules. This does not override a
    logger placed in NOT IN USE mode by an Administrator.
    """
    info=require_settings_access(request)
    user=f"{info['username']} ({info['role']})"
    try:
        data=await request.json()
    except Exception:
        data={}

    schedule_enabled=bool(data.get("schedule_enabled",True))
    schedule_days=_normalize_schedule_days(data.get("schedule_days",[0,1,2,3,4]))
    schedule_start=_valid_hhmm(data.get("schedule_start","00:00"),"")
    schedule_end=_valid_hhmm(data.get("schedule_end","00:00"),"")
    if not schedule_start or not schedule_end:
        raise HTTPException(status_code=400,detail="Schedule start/end must use HH:MM")
    if schedule_enabled and not schedule_days:
        raise HTTPException(status_code=400,detail="Select at least one ON day for the alarm schedule")

    updated=[]
    for sn in LOGGERS:
        if LOGGERS[sn].get("alarm_type") == "monitor":
            continue
        old=get_runtime_cfg(sn)
        old_effective=bool(old.get("effective_monitoring_enabled",old.get("monitoring_enabled",True)))
        new=save_runtime_cfg(sn,schedule_enabled=schedule_enabled,schedule_days=schedule_days,
                             schedule_start=schedule_start,schedule_end=schedule_end,updated_by=user)
        new_effective=bool(new.get("effective_monitoring_enabled",new.get("monitoring_enabled",True)))
        add_event(sn,new["name"],"ALARM SCHEDULE CHANGED",limit=new["limit"],alarm_type=new["alarm_type"],
                  detail=(f"Applied to ALL loggers: {new['schedule_summary']} by {user}; "
                          f"currently {'ACTIVE' if new.get('schedule_active_now') else 'OFF'}"),actor=user,role=role,category="CONFIG")
        if old_effective != new_effective:
            email_alarm_state[sn]=False
            email_alarm_started.pop(sn,None)
            email_alarm_last_sent.pop(sn,None)
            clear_ack(sn)
        updated.append({"logger_sn":sn,"name":new["name"],"schedule_summary":new["schedule_summary"]})
    return {"ok":True,"updated":updated,"count":len(updated)}

@app.post("/api/admin/config/{sn}")
async def admin_update_config(sn: str, request: Request):
    info=require_settings_access(request)
    if sn not in LOGGERS:
        raise HTTPException(status_code=404,detail="Unknown logger")
    try:
        data=await request.json()
    except Exception:
        data={}

    role=info["role"]
    user=f"{info['username']} ({role})"
    old=get_runtime_cfg(sn)

    # Schedule fields are available to both Admin and Operator.
    schedule_enabled=bool(data.get("schedule_enabled",old.get("schedule_enabled",False)))
    schedule_days=_normalize_schedule_days(data.get("schedule_days",old.get("schedule_days",[0,1,2,3,4,5,6])))
    schedule_start=_valid_hhmm(data.get("schedule_start",old.get("schedule_start","00:00")),"")
    schedule_end=_valid_hhmm(data.get("schedule_end",old.get("schedule_end","00:00")),"")
    if not schedule_start or not schedule_end:
        raise HTTPException(status_code=400,detail="Schedule start/end must use HH:MM")
    if schedule_enabled and not schedule_days:
        raise HTTPException(status_code=400,detail="Select at least one day for the alarm schedule")

    old_effective=bool(old.get("effective_monitoring_enabled",old.get("monitoring_enabled",True)))

    if role=="operator":
        forbidden={"limit","monitoring_enabled","mode","temperature_offset","humidity_offset"}.intersection(data.keys())
        if forbidden:
            raise HTTPException(status_code=403,detail="Operator access is limited to the alarm schedule")
        new=save_runtime_cfg(sn,schedule_enabled=schedule_enabled,schedule_days=schedule_days,
                             schedule_start=schedule_start,schedule_end=schedule_end,updated_by=user)
    else:
        limit=data.get("limit",old["limit"])
        try: limit=float(limit)
        except Exception: raise HTTPException(status_code=400,detail="Invalid temperature setpoint")
        if limit < -40 or limit > 100:
            raise HTTPException(status_code=400,detail="Setpoint must be between -40 C and 100 C")
        monitoring_enabled=bool(data.get("monitoring_enabled",old.get("monitoring_enabled",True)))
        mode="RUNNING" if monitoring_enabled else "NOT_IN_USE"
        try: temperature_offset=float(data.get("temperature_offset",old.get("temperature_offset",0.0)))
        except Exception: raise HTTPException(status_code=400,detail="Invalid temperature calibration offset")
        try: humidity_offset=float(data.get("humidity_offset",old.get("humidity_offset",0.0)))
        except Exception: raise HTTPException(status_code=400,detail="Invalid humidity calibration offset")
        if not -20.0 <= temperature_offset <= 20.0:
            raise HTTPException(status_code=400,detail="Temperature offset must be between -20.0 and +20.0 C")
        if not -50.0 <= humidity_offset <= 50.0:
            raise HTTPException(status_code=400,detail="Humidity offset must be between -50.0 and +50.0 %RH")
        new=save_runtime_cfg(sn,limit=limit,monitoring_enabled=monitoring_enabled,mode=mode,
                             schedule_enabled=schedule_enabled,schedule_days=schedule_days,
                             schedule_start=schedule_start,schedule_end=schedule_end,
                             temperature_offset=temperature_offset,humidity_offset=humidity_offset,updated_by=user)

        if float(old["limit"]) != float(new["limit"]):
            add_event(sn,new["name"],"ALARM SETPOINT CHANGED",limit=new["limit"],alarm_type=new["alarm_type"],
                      detail=f"{old['limit']:.1f} C -> {new['limit']:.1f} C by {user}",actor=user,role=role,category="CONFIG")
        if bool(old.get("monitoring_enabled",True)) != monitoring_enabled:
            if monitoring_enabled:
                add_event(sn,new["name"],"ALARM MONITORING ENABLED",limit=new["limit"],alarm_type=new["alarm_type"],
                          detail=f"Equipment returned to RUNNING mode by {user}",actor=user,role=role,category="CONFIG")
            else:
                add_event(sn,new["name"],"ALARM MONITORING DISABLED",limit=new["limit"],alarm_type=new["alarm_type"],
                          detail=f"Equipment set to NOT IN USE by {user}; temperature alarms suppressed",actor=user,role=role,category="CONFIG")

        if float(old.get("temperature_offset",0.0)) != float(new.get("temperature_offset",0.0)):
            add_event(sn,new["name"],"TEMPERATURE CALIBRATION OFFSET CHANGED",limit=new["limit"],alarm_type=new["alarm_type"],
                      detail=(f"{old.get('temperature_offset',0.0):+.1f} C -> {new.get('temperature_offset',0.0):+.1f} C by {user}. "
                              "Adjusted temperature is used for display, trends, CSV and alarm evaluation."),actor=user,role=role,category="CALIBRATION")
        if float(old.get("humidity_offset",0.0)) != float(new.get("humidity_offset",0.0)):
            add_event(sn,new["name"],"HUMIDITY CALIBRATION OFFSET CHANGED",limit=new["limit"],alarm_type=new["alarm_type"],
                      detail=(f"{old.get('humidity_offset',0.0):+.1f} %RH -> {new.get('humidity_offset',0.0):+.1f} %RH by {user}. "
                              "Adjusted humidity is used for display, trends and CSV."),actor=user,role=role,category="CALIBRATION")

    new_effective=bool(new.get("effective_monitoring_enabled",new.get("monitoring_enabled",True)))
    schedule_changed=(
        bool(old.get("schedule_enabled",False)) != schedule_enabled or
        _normalize_schedule_days(old.get("schedule_days",[])) != schedule_days or
        old.get("schedule_start","00:00") != schedule_start or
        old.get("schedule_end","00:00") != schedule_end
    )
    if schedule_changed:
        add_event(sn,new["name"],"ALARM SCHEDULE CHANGED",limit=new["limit"],alarm_type=new["alarm_type"],
                  detail=(f"{new['schedule_summary']} by {user}; "
                          f"currently {'ACTIVE' if new.get('schedule_active_now') else 'OFF'}"),actor=user,role=role,category="CONFIG")

    calibration_changed=(
        float(old.get("temperature_offset",0.0)) != float(new.get("temperature_offset",0.0)) or
        float(old.get("humidity_offset",0.0)) != float(new.get("humidity_offset",0.0))
    )
    if old_effective != new_effective or calibration_changed:
        email_alarm_state[sn]=False
        email_alarm_started.pop(sn,None)
        email_alarm_last_sent.pop(sn,None)
        clear_ack(sn)

    return {"ok":True,"device":new}

@app.get("/api/alarm-events")
def alarm_events(limit:int=250):
    with event_lock: rows=_read_json(EVENT_FILE,[])
    return {"events":rows[-max(1,min(limit,1000)):]}

@app.delete("/api/alarm-events")
def clear_alarm_events(request: Request):
    info=require_admin(request)
    append_audit_record("AUDIT LOG DELETE ATTEMPT BLOCKED",category="AUDIT",
                        actor=info["username"],role=info["role"],
                        detail="Alarm/event history deletion is disabled by the audit retention policy.")
    raise HTTPException(status_code=403,detail="Event history deletion is disabled by the audit retention policy")

@app.post("/api/ack/{sn}")
async def acknowledge_alarm(sn:str, request: Request):
    if sn not in LOGGERS: raise HTTPException(404,"Unknown logger")
    try:
        data=await request.json()
    except Exception:
        data={}
    info=_session_info(request)
    who=str(data.get("acknowledged_by","")).strip()
    if not who and info:
        who=str(info.get("username","")).strip()
    if not who:
        raise HTTPException(status_code=400,detail="Enter the name or initials of the person acknowledging the alarm")
    if len(who)>80:
        raise HTTPException(status_code=400,detail="Acknowledgement name is too long")
    now=datetime.now(timezone.utc)
    set_ack(sn,True)
    mark_active_downtime_ack(sn,who,now)
    cfg=get_runtime_cfg(sn)
    role=info.get("role","dashboard") if info else "dashboard"
    add_event(sn,cfg["name"],"ALARM ACKNOWLEDGED",None,cfg["limit"],cfg["alarm_type"],
              detail=f"Acknowledged by {who} from dashboard",actor=who,role=role,category="ALARM")
    return {"ok":True,"logger_sn":sn,"acknowledged_by":who}

@app.get("/api/audit/status")
def audit_status(request: Request):
    info=_session_info(request)
    now=datetime.now(timezone.utc)
    cfg=load_audit_config()
    return {
        "version":"6.2.0",
        "server_utc":now.isoformat(),
        "server_pacific":now.astimezone(PLANT_TZ).isoformat(),
        "timezone":"America/Vancouver",
        "retention":cfg,
        "backup":audit_backup_status(),
        "access":{
            "logged_in":bool(info),
            "username":info.get("username") if info else None,
            "role":info.get("role") if info else "read-only",
            "admin_username":ADMIN_USERNAME,
            "operator_username":OPERATOR_USERNAME,
        },
        "devices":[{"logger_sn":sn,"name":get_runtime_cfg(sn)["name"],"alarm_type":get_runtime_cfg(sn).get("alarm_type"),
                    "limit":get_runtime_cfg(sn).get("limit")} for sn in LOGGERS],
    }

@app.get("/api/audit/calibrations")
def audit_calibrations():
    return {"records":load_calibration_records()}

@app.post("/api/admin/audit/calibration/{sn}")
async def admin_audit_calibration(sn:str, request: Request):
    info=require_admin(request)
    try:
        data=await request.json()
    except Exception:
        data={}
    rec=add_calibration_record(sn,data,info["username"])
    add_event(sn,LOGGERS[sn]["name"],"CALIBRATION RECORD ADDED",
              detail=(f"Calibration date {rec['calibration_date']}; ref {rec['reference_instrument']} "
                      f"{rec.get('reference_serial','')}; temp offset {rec['temperature_offset']:+.2f} C; "
                      f"RH offset {rec['humidity_offset']:+.2f} %RH; next due {rec.get('next_due_date') or 'N/A'}"),
              actor=info["username"],role=info["role"],category="CALIBRATION")
    return {"ok":True,"record":rec,"device":get_runtime_cfg(sn)}

@app.get("/api/audit/episodes")
def audit_episodes(days:int=90):
    days=max(1,min(int(days),1095))
    cutoff=datetime.now(timezone.utc)-timedelta(days=days)
    deviations={str(r.get("episode_id")):r for r in load_deviations()}
    out=[]
    for r in get_all_downtime_records():
        start=_iso_dt(r.get("start_utc"))
        if not start or start < cutoff:
            continue
        rec=dict(r)
        rec["deviation"]=deviations.get(str(r.get("id")))
        out.append(rec)
    return {"episodes":sorted(out,key=lambda r:str(r.get("start_utc","")),reverse=True)}

@app.get("/api/audit/deviations")
def audit_deviations():
    return {"records":load_deviations()}

@app.post("/api/audit/deviation")
async def audit_deviation_save(request: Request):
    info=require_settings_access(request)
    try:
        data=await request.json()
    except Exception:
        data={}
    rec=save_deviation(data,info["username"],info["role"])
    add_event(rec.get("logger_sn",""),rec.get("name",""),"DEVIATION / CORRECTIVE ACTION UPDATED",
              detail=(f"Episode {rec.get('episode_id')} · status {rec.get('status')} · responsible "
                      f"{rec.get('responsible_person')} · corrective action: {rec.get('corrective_action')}"),
              actor=info["username"],role=info["role"],category="DEVIATION")
    return {"ok":True,"record":rec}

@app.get("/api/audit-trail")
def audit_trail(limit:int=1000):
    return {"records":get_audit_records(limit)}

@app.get("/api/audit-trail.csv")
def audit_trail_csv():
    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow(["Time UTC","Time Pacific","Category","Action","Actor","Role","Logger","Equipment","Detail"])
    for r in get_audit_records(5000):
        w.writerow([r.get("time_utc",""),r.get("time_pacific",""),r.get("category",""),r.get("action",""),
                    r.get("actor",""),r.get("role",""),r.get("logger_sn",""),r.get("name",""),r.get("detail","")])
    return Response(content=output.getvalue().encode("utf-8-sig"),media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":'attachment; filename="PBF_CMS_Audit_Trail.csv"'})

@app.get("/api/audit-report.pdf")
async def audit_report_pdf(logger_sn:str,date:str):
    pdf,filename,snapshot_hash=await build_audit_pdf(str(logger_sn),str(date))
    return Response(content=pdf,media_type="application/pdf",
                    headers={
                        "Content-Disposition":f'attachment; filename="{filename}"',
                        "X-PBF-Record-SHA256":snapshot_hash,
                        "Cache-Control":"no-store",
                    })

@app.post("/api/admin/audit/config")
async def admin_audit_config(request: Request):
    info=require_admin(request)
    try:
        data=await request.json()
    except Exception:
        data={}
    cfg=save_audit_config(
        data.get("retention_years",3),
        data.get("backup_enabled",True),
        data.get("backup_time","02:15"),
        data.get("backup_keep",30),
        info["username"],
    )
    add_event("AUDIT","Audit Controls","AUDIT RETENTION / BACKUP SETTINGS CHANGED",
              detail=(f"Retention {cfg['retention_years']} years; automatic backup "
                      f"{'enabled' if cfg['backup_enabled'] else 'disabled'} at {cfg['backup_time']} Pacific; "
                      f"keep {cfg['backup_keep']} backups"),
              actor=info["username"],role=info["role"],category="AUDIT")
    return {"ok":True,"config":cfg}

@app.post("/api/admin/audit/backup-now")
async def admin_audit_backup_now(request: Request):
    info=require_admin(request)
    path=await asyncio.to_thread(create_audit_backup,"manual",info["username"])
    append_audit_record("MANUAL AUDIT BACKUP CREATED",category="BACKUP",
                        actor=info["username"],role=info["role"],detail=f"Created {path.name}")
    return {"ok":True,"filename":path.name,"size_bytes":path.stat().st_size}

@app.get("/api/admin/audit/latest-backup")
def admin_audit_latest_backup(request: Request):
    require_admin(request)
    status=audit_backup_status()
    if not status.get("available"):
        raise HTTPException(status_code=404,detail="No audit backup is available yet")
    path=BACKUP_DIR/status["filename"]
    return FileResponse(path,media_type="application/zip",filename=path.name)

@app.post("/api/test-email")
async def test_email():
    if not gmail_ready(): raise HTTPException(status_code=400,detail="Gmail settings are incomplete in .env")
    now=datetime.now(timezone.utc)
    body=f"PBF TEMPERATURE DASHBOARD TEST EMAIL\n\nGmail notifications are configured correctly.\nTime: {fmt_local_time(now)}\n\nLI-COR Temperature Monitoring"
    try:
        await asyncio.to_thread(send_gmail,"PBF Temperature Dashboard - Test Email",body)
        return {"ok":True,"message":f"Test email sent to {ALERT_EMAIL}"}
    except Exception as e:
        raise HTTPException(status_code=500,detail=f"Email send failed: {str(e)}")

@app.get("/api/email-status")
def get_email_status():
    return {"configured":gmail_ready(),"sender":GMAIL_SENDER,"recipient":ALERT_EMAIL,**email_status}

@app.post("/api/test-alarm-email")
async def test_alarm_email():
    if not gmail_ready(): raise HTTPException(status_code=400,detail="Gmail settings are incomplete in .env")
    now=datetime.now(timezone.utc)
    body=(f"PBF TEMPERATURE ALARM - TEST\n\nEquipment: Tortilla L3 Cooler\nTemperature: 34.20 C\n"
          f"High Limit: 13.00 C\nStatus: HIGH TEMPERATURE - TEST\nTime: {fmt_local_time(now)}\n\n"
          f"This is a test of the automatic alarm email format.\nLI-COR Temperature Monitoring")
    try:
        await asyncio.to_thread(send_gmail,"PBF Temperature Alarm TEST - Tortilla L3 Cooler",body)
        email_status["last_success"]=datetime.now(timezone.utc).isoformat()
        email_status["last_error"]=None
        email_status["last_subject"]="PBF Temperature Alarm TEST - Tortilla L3 Cooler"
        return {"ok":True,"message":f"Alarm-format test sent to {ALERT_EMAIL}"}
    except Exception as e:
        email_status["last_error"]=str(e)
        raise HTTPException(status_code=500,detail=f"Email send failed: {str(e)}")

async def get_report_history_all(start_utc,end_utc):
    """Chunk LI-COR requests to keep 24h/all-logger reports reliable."""
    rows=[]
    cursor=start_utc
    while cursor < end_utc:
        chunk_end=min(cursor+timedelta(hours=6),end_utc)
        rows.extend(await getdata_range(list(LOGGERS),cursor,chunk_end))
        cursor=chunk_end
    unique={}
    for q in rows:
        sn=str(q.get("logger_sn",""))
        typ=str(q.get("sensor_measurement_type","")).lower()
        if sn not in LOGGERS or typ not in ("temperature","rh"):
            continue
        key=(sn,str(q.get("timestamp","")),typ,str(q.get("sensor_sn","")),str(q.get("value","")))
        unique[key]=calibrated_row(q)
    return sorted(unique.values(),key=lambda q:(str(q.get("timestamp","")),str(q.get("logger_sn","")),str(q.get("sensor_measurement_type",""))))

def _temperature_stats(rows,start_utc,end_utc):
    stats={sn:{"values":[],"samples":0} for sn in LOGGERS}
    daily={}
    for q in rows:
        sn=str(q.get("logger_sn",""))
        if sn not in LOGGERS or str(q.get("sensor_measurement_type","")).lower()!="temperature":
            continue
        t=_iso_dt(q.get("timestamp"))
        if not t or not (start_utc <= t < end_utc):
            continue
        try:
            v=float(q.get("value"))
        except Exception:
            continue
        stats[sn]["values"].append(v); stats[sn]["samples"]+=1
        day=t.astimezone(PLANT_TZ).date().isoformat()
        daily.setdefault((day,sn),[]).append(v)
    out={}
    for sn,rec in stats.items():
        vals=rec["values"]
        out[sn]={
            "min":min(vals) if vals else None,
            "max":max(vals) if vals else None,
            "avg":sum(vals)/len(vals) if vals else None,
            "samples":len(vals),
        }
    daily_out={}
    for key,vals in daily.items():
        daily_out[key]={"min":min(vals),"max":max(vals),"avg":sum(vals)/len(vals),"samples":len(vals)}
    return out,daily_out

def _fmt_num(value,places=2):
    if value is None or value=="":
        return ""
    try:
        return f"{float(value):.{places}f}"
    except Exception:
        return str(value)

async def build_maintenance_report_csv(hours=24,end_utc=None):
    end_utc=(end_utc or datetime.now(timezone.utc)).replace(microsecond=0)
    hours=max(1,min(2160,int(hours)))
    start_utc=end_utc-timedelta(hours=hours)
    api_error=""
    try:
        temp_rows=await get_report_history_all(start_utc,end_utc)
    except Exception as e:
        temp_rows=[]
        api_error=str(e)
    temp_stats,daily_temp_stats=_temperature_stats(temp_rows,start_utc,end_utc)

    episodes=[
        r for r in get_all_downtime_records()
        if _episode_overlap_minutes(r,start_utc,end_utc)>0
    ]
    alarm_events=_report_alarm_events(start_utc,end_utc)

    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow(["PBF RISE BAKING BURNABY - TEMPERATURE ALARM DOWNTIME REPORT"])
    w.writerow(["Report Version","v6.2.0"])
    w.writerow(["Report Window Start (Pacific)",_pt_text(start_utc)])
    w.writerow(["Report Window End (Pacific)",_pt_text(end_utc)])
    w.writerow(["Window Hours",hours])
    w.writerow(["Purpose","Maintenance temperature-alarm downtime history and performance validation"])
    w.writerow(["Temperature Basis","Calibrated / offset-adjusted LI-COR readings"])
    if api_error:
        w.writerow(["LI-COR Temperature Statistics","Unavailable for this report: "+api_error[:180]])
    w.writerow([])

    w.writerow(["LOGGER SUMMARY"])
    w.writerow([
        "Logger","Serial","Alarm Type","Limit (C)","Alarm Events Started",
        "Episodes Affecting Window","Total Downtime (min)","Average Downtime (min)",
        "Longest Downtime (min)","Availability (%)","5-Min Reminders",
        "Min Temp (C)","Max Temp (C)","Avg Temp (C)","Temperature Samples","Open Alarm"
    ])
    for sn in LOGGERS:
        cfg=get_runtime_cfg(sn)
        recs=[r for r in episodes if str(r.get("logger_sn"))==sn]
        started=[r for r in recs if (lambda d: d is not None and start_utc <= d < end_utc)(_iso_dt(r.get("start_utc")))]
        overlaps=[_episode_overlap_minutes(r,start_utc,end_utc) for r in recs]
        total=sum(overlaps)
        avg=(sum(overlaps)/len(overlaps)) if overlaps else 0.0
        longest=max(overlaps) if overlaps else 0.0
        availability=max(0.0,100.0-(total/(hours*60.0)*100.0))
        reminders=sum(1 for e in alarm_events if str(e.get("logger_sn"))==sn and e.get("event")=="5-MIN REMINDER")
        st=temp_stats.get(sn,{})
        open_alarm=any(str(r.get("status","")).upper()=="OPEN" for r in recs)
        if cfg.get("alarm_type")=="monitor":
            w.writerow([
                cfg["name"],sn,"MONITOR ONLY","",0,0,0,0,0,"N/A",0,
                _fmt_num(st.get("min")),_fmt_num(st.get("max")),_fmt_num(st.get("avg")),st.get("samples",0),"NO"
            ])
        else:
            w.writerow([
                cfg["name"],sn,cfg.get("alarm_type","").upper(),_fmt_num(cfg.get("limit"),1),len(started),len(recs),
                _fmt_num(total),_fmt_num(avg),_fmt_num(longest),_fmt_num(availability),reminders,
                _fmt_num(st.get("min")),_fmt_num(st.get("max")),_fmt_num(st.get("avg")),st.get("samples",0),
                "YES" if open_alarm else "NO"
            ])

    # Daily breakdown: event count and downtime per logger, by Pacific calendar date.
    w.writerow([])
    w.writerow(["DAILY LOGGER SUMMARY"])
    w.writerow([
        "Date (Pacific)","Logger","Serial","Alarm Events Started","Episodes Affecting Day",
        "Downtime (min)","Availability in Reported Portion (%)","5-Min Reminders",
        "Min Temp (C)","Max Temp (C)","Avg Temp (C)","Temperature Samples"
    ])
    start_pt=start_utc.astimezone(PLANT_TZ)
    end_pt=end_utc.astimezone(PLANT_TZ)
    day=start_pt.date()
    while day <= end_pt.date():
        day_start_local=datetime.combine(day,datetime.min.time(),tzinfo=PLANT_TZ)
        next_day_local=day_start_local+timedelta(days=1)
        ds=max(start_utc,day_start_local.astimezone(timezone.utc))
        de=min(end_utc,next_day_local.astimezone(timezone.utc))
        if de>ds:
            portion_minutes=(de-ds).total_seconds()/60.0
            day_text=day.isoformat()
            for sn in LOGGERS:
                cfg=get_runtime_cfg(sn)
                recs=[r for r in episodes if str(r.get("logger_sn"))==sn and _episode_overlap_minutes(r,ds,de)>0]
                starts=sum(1 for r in recs if (lambda d: d is not None and ds <= d < de)(_iso_dt(r.get("start_utc"))))
                downtime=sum(_episode_overlap_minutes(r,ds,de) for r in recs)
                availability=max(0.0,100.0-(downtime/portion_minutes*100.0)) if portion_minutes else 100.0
                reminders=sum(1 for e in alarm_events if str(e.get("logger_sn"))==sn and e.get("event")=="5-MIN REMINDER" and ds <= (_iso_dt(e.get("time")) or ds-timedelta(seconds=1)) < de)
                st=daily_temp_stats.get((day_text,sn),{})
                if cfg.get("alarm_type")=="monitor":
                    starts=0; recs=[]; downtime=0.0
                w.writerow([
                    day_text,cfg["name"],sn,starts,len(recs),_fmt_num(downtime),
                    "N/A" if cfg.get("alarm_type")=="monitor" else _fmt_num(availability),
                    reminders,_fmt_num(st.get("min")),_fmt_num(st.get("max")),_fmt_num(st.get("avg")),st.get("samples",0)
                ])
        day=day+timedelta(days=1)

    w.writerow([])
    w.writerow(["DOWNTIME HISTORY"])
    w.writerow([
        "Episode ID","Logger","Serial","Alarm Type","Limit (C)","Start (Pacific)","End (Pacific)",
        "Downtime in Report Window (min)","Full Episode Duration (min)","Start Temp (C)",
        "Worst Temp (C)","Recovery Temp (C)","Status","End Reason","Source"
    ])
    for r in episodes:
        sn=str(r.get("logger_sn",""))
        rs=_iso_dt(r.get("start_utc"))
        re_dt=_iso_dt(r.get("end_utc"))
        full_duration=r.get("duration_minutes")
        if full_duration is None and rs:
            full_duration=((re_dt or end_utc)-rs).total_seconds()/60.0
        w.writerow([
            r.get("id",""),r.get("name") or LOGGERS.get(sn,{}).get("name",sn),sn,
            str(r.get("alarm_type","")).upper(),_fmt_num(r.get("limit"),1),_pt_text(rs),
            _pt_text(re_dt) if re_dt else "OPEN",
            _fmt_num(_episode_overlap_minutes(r,start_utc,end_utc)),_fmt_num(full_duration),
            _fmt_num(r.get("start_temp")),_fmt_num(r.get("worst_temp")),_fmt_num(r.get("recovery_temp")),
            r.get("status",""),r.get("end_reason",""),r.get("source","")
        ])

    w.writerow([])
    w.writerow(["ALARM EVENT AUDIT TRAIL"])
    w.writerow(["Time (Pacific)","Logger","Serial","Event","Reading (C)","Limit (C)","Email","Phone Push","Detail"])
    for e in alarm_events:
        w.writerow([
            _pt_text(_iso_dt(e.get("time"))),e.get("name",""),e.get("logger_sn",""),e.get("event",""),
            _fmt_num(e.get("reading")),_fmt_num(e.get("limit"),1),e.get("email",""),e.get("push",""),e.get("detail","")
        ])

    csv_bytes=output.getvalue().encode("utf-8-sig")
    stamp=end_utc.astimezone(PLANT_TZ).strftime("%Y%m%d_%H%M")
    filename=f"PBF_Temperature_Downtime_{hours}h_{stamp}.csv"
    return csv_bytes,filename,temp_rows,api_error,start_utc,end_utc

def build_all_logger_temperature_csv(temp_rows,start_utc,end_utc):
    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow(["PBF RISE BAKING BURNABY - ALL LOGGER TEMPERATURE HISTORY"])
    w.writerow(["Window Start (Pacific)",_pt_text(start_utc)])
    w.writerow(["Window End (Pacific)",_pt_text(end_utc)])
    w.writerow(["Reading Basis","Calibrated / offset-adjusted LI-COR readings"])
    w.writerow([])
    w.writerow(["Date (Pacific)","Time (Pacific)","Logger","Serial","Measurement","Value","Unit"])
    for q in temp_rows:
        t=_iso_dt(q.get("timestamp"))
        if not t:
            continue
        typ=str(q.get("sensor_measurement_type","")).lower()
        sn=str(q.get("logger_sn",""))
        cfg=LOGGERS.get(sn,{})
        pt=t.astimezone(PLANT_TZ)
        unit="C" if typ=="temperature" else "%RH"
        w.writerow([
            pt.strftime("%Y-%m-%d"),pt.strftime("%I:%M:%S %p"),cfg.get("name",sn),sn,
            "Temperature" if typ=="temperature" else "Humidity",_fmt_num(q.get("value")),unit
        ])
    return output.getvalue().encode("utf-8-sig")

async def send_maintenance_report(hours=None,recipient=None,manual=False):
    cfg=load_report_config()
    hours=int(hours or cfg.get("hours",24))
    to_addr=(recipient or cfg.get("recipient") or ALERT_EMAIL).strip()
    report_bytes,report_name,temp_rows,api_error,start_utc,end_utc=await build_maintenance_report_csv(hours)
    temp_bytes=build_all_logger_temperature_csv(temp_rows,start_utc,end_utc)
    temp_name=f"PBF_All_Logger_Temperature_History_{hours}h_{end_utc.astimezone(PLANT_TZ).strftime('%Y%m%d_%H%M')}.csv"
    body=(
        "PBF RISE BAKING BURNABY - TEMPERATURE MAINTENANCE REPORT\n\n"
        f"Reporting window: {_pt_text(start_utc)} to {_pt_text(end_utc)}\n"
        f"Duration: {hours} hours\n"
        "Attached:\n"
        "1. Temperature alarm downtime report: daily event count, downtime duration, availability, and alarm audit trail.\n"
        "2. All-logger temperature/humidity history for the same reporting window.\n\n"
        "Purpose: maintenance performance validation and temperature downtime history."
    )
    if api_error:
        body += "\n\nNOTE: LI-COR history/statistics had an API error; the downtime/event report is still attached."
    subject=f"PBF 24H Temperature Downtime Report - {end_utc.astimezone(PLANT_TZ).strftime('%Y-%m-%d')}"
    attachments=[
        (report_name,report_bytes,"text/csv"),
        (temp_name,temp_bytes,"text/csv"),
    ]
    await asyncio.to_thread(send_gmail,subject,body,to_addr,attachments)
    add_event("REPORT","Maintenance Report","24H MAINTENANCE REPORT EMAILED",
              detail=f"Sent to {to_addr}; {hours}h window; manual={manual}")
    return {"ok":True,"recipient":to_addr,"hours":hours,"report_file":report_name,"temperature_file":temp_name}

async def maybe_send_daily_maintenance_report():
    # Prevent duplicate reporting if a backup/test deployment is ever started.
    if DEPLOYMENT_ROLE!="CLOUD-PRIMARY" or not NOTIFICATIONS_ENABLED or not gmail_ready():
        return
    cfg=load_report_config()
    if not cfg.get("enabled",True):
        return
    now_pt=datetime.now(timezone.utc).astimezone(PLANT_TZ)
    hh,mm=map(int,cfg.get("time","08:00").split(":"))
    scheduled=now_pt.replace(hour=hh,minute=mm,second=0,microsecond=0)
    if now_pt < scheduled:
        return
    with report_lock:
        state=_read_json(REPORT_STATE_FILE,{})
        if not isinstance(state,dict):
            state={}
        if state.get("last_sent_date")==now_pt.date().isoformat():
            return
        # Claim the date before sending so the 60-second monitor cannot double-send.
        state["last_attempt_date"]=now_pt.date().isoformat()
        state["last_attempt_utc"]=datetime.now(timezone.utc).isoformat()
        _write_json(REPORT_STATE_FILE,state)
    try:
        await send_maintenance_report(cfg.get("hours",24),cfg.get("recipient"),manual=False)
        with report_lock:
            state=_read_json(REPORT_STATE_FILE,{})
            state["last_sent_date"]=now_pt.date().isoformat()
            state["last_sent_utc"]=datetime.now(timezone.utc).isoformat()
            state["last_error"]=""
            _write_json(REPORT_STATE_FILE,state)
    except Exception as e:
        with report_lock:
            state=_read_json(REPORT_STATE_FILE,{})
            state["last_error"]=str(e)
            state["last_error_utc"]=datetime.now(timezone.utc).isoformat()
            _write_json(REPORT_STATE_FILE,state)
        print("Daily maintenance report email error:",repr(e))

@app.get("/api/maintenance-report.csv")
async def maintenance_report_download(hours:int=24):
    hours=max(1,min(2160,int(hours)))
    report_bytes,filename,_,_,_,_=await build_maintenance_report_csv(hours)
    from fastapi.responses import Response
    return Response(
        content=report_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    )

@app.get("/api/temperature-history-all.csv")
async def temperature_history_all_download(hours:int=24):
    hours=max(1,min(168,int(hours)))
    end_utc=datetime.now(timezone.utc).replace(microsecond=0)
    start_utc=end_utc-timedelta(hours=hours)
    rows=await get_report_history_all(start_utc,end_utc)
    content=build_all_logger_temperature_csv(rows,start_utc,end_utc)
    filename=f"PBF_All_Logger_Temperature_History_{hours}h_{end_utc.astimezone(PLANT_TZ).strftime('%Y%m%d_%H%M')}.csv"
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    )

@app.post("/api/admin/report-config")
async def admin_report_config(request: Request):
    info=require_admin(request)
    try:
        data=await request.json()
    except Exception:
        data={}
    cfg=save_report_config(
        data.get("enabled",True),
        data.get("time","08:00"),
        data.get("recipient",ALERT_EMAIL),
        data.get("hours",24),
        updated_by=info["username"],
    )
    add_event("REPORT","Maintenance Report","DAILY REPORT SETTINGS CHANGED",
              detail=f"Enabled={cfg['enabled']}; time={cfg['time']} Pacific; recipient={cfg['recipient']}; by {info['username']}")
    return {"ok":True,"report_config":cfg}

@app.post("/api/admin/send-maintenance-report")
async def admin_send_maintenance_report(request: Request):
    info=require_admin(request)
    cfg=load_report_config()
    result=await send_maintenance_report(cfg.get("hours",24),cfg.get("recipient"),manual=True)
    result["requested_by"]=info["username"]
    return result


async def get_logger_history_chunked(sn: str, start_utc: datetime, end_utc: datetime):
    """Reliable range retrieval for exports: query LI-COR in 6-hour chunks."""
    rows=[]
    cursor=start_utc
    while cursor < end_utc:
        chunk_end=min(cursor+timedelta(hours=6),end_utc)
        rows.extend(await getdata_range([sn],cursor,chunk_end))
        cursor=chunk_end
    unique={}
    for q in rows:
        typ=str(q.get("sensor_measurement_type","")).lower()
        if typ not in ("temperature","rh"):
            continue
        key=(str(q.get("timestamp","")),typ,str(q.get("sensor_sn","")),str(q.get("value","")))
        unique[key]=calibrated_row(q)
    return sorted(unique.values(),key=lambda q:(str(q.get("timestamp","")),str(q.get("sensor_measurement_type",""))))

def _range_label(minutes: int):
    if minutes == 60:
        return "1 Hour", "1hr"
    if minutes == 1440:
        return "24 Hours", "24hr"
    if minutes == 10080:
        return "7 Days", "7day"
    if minutes % 1440 == 0:
        days=minutes//1440
        return f"{days} Days", f"{days}day"
    if minutes % 60 == 0:
        hours=minutes//60
        return f"{hours} Hours", f"{hours}hr"
    return f"{minutes} Minutes", f"{minutes}min"

def build_paired_logger_csv(sn: str, rows, minutes: int, end_utc: datetime):
    cfg=get_runtime_cfg(sn)
    paired={}
    for q in rows:
        typ=str(q.get("sensor_measurement_type","")).lower()
        if typ not in ("temperature","rh"):
            continue
        ts=str(q.get("timestamp",""))
        rec=paired.setdefault(ts,{"temperature":"","rh":""})
        if typ=="temperature":
            rec["temperature"]=q.get("value","")
        else:
            rec["rh"]=q.get("value","")

    title,file_tag=_range_label(minutes)
    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow([f"PBF LI-COR {title} Temperature and Humidity Data Log"])
    w.writerow(["Equipment",cfg["name"]])
    w.writerow(["Logger Serial",sn])
    w.writerow(["Report Range",title])
    w.writerow(["Generated Pacific Time",_pt_text(end_utc)])
    w.writerow(["Temperature Calibration Offset (C)",f"{float(cfg.get('temperature_offset',0.0)):+.2f}"])
    w.writerow(["Humidity Calibration Offset (%RH)",f"{float(cfg.get('humidity_offset',0.0)):+.2f}"])
    w.writerow(["Reading Basis","Calibrated / offset-adjusted readings"])

    temp_values=[]
    rh_values=[]
    for rec in paired.values():
        try:
            temp_values.append(float(rec["temperature"]))
        except Exception:
            pass
        try:
            rh_values.append(float(rec["rh"]))
        except Exception:
            pass

    w.writerow([])
    w.writerow([f"{title} Summary","Minimum","Maximum","Average","Samples"])
    if temp_values:
        w.writerow(["Temperature (C)",f"{min(temp_values):.2f}",f"{max(temp_values):.2f}",
                    f"{sum(temp_values)/len(temp_values):.2f}",len(temp_values)])
    else:
        w.writerow(["Temperature (C)","","","",0])
    if rh_values:
        w.writerow(["Humidity (%RH)",f"{min(rh_values):.2f}",f"{max(rh_values):.2f}",
                    f"{sum(rh_values)/len(rh_values):.2f}",len(rh_values)])
    else:
        w.writerow(["Humidity (%RH)","","","",0])

    w.writerow([])
    w.writerow(["Date (Pacific)","Time (Pacific)","Temperature (C)","Humidity (%RH)"])
    for ts in sorted(paired):
        dt=_iso_dt(ts)
        if dt:
            pt=dt.astimezone(PLANT_TZ)
            date_text=pt.strftime("%Y-%m-%d")
            time_text=pt.strftime("%I:%M:%S %p")
        else:
            date_text=ts
            time_text=""
        tv=paired[ts]["temperature"]
        hv=paired[ts]["rh"]
        try:
            tv=f"{float(tv):.2f}"
        except Exception:
            pass
        try:
            hv=f"{float(hv):.2f}"
        except Exception:
            pass
        w.writerow([date_text,time_text,tv,hv])

    filename=f"PBF_{cfg['id']}_Temp_Humidity_{file_tag}_{end_utc.astimezone(PLANT_TZ).strftime('%Y%m%d_%H%M')}.csv"
    return output.getvalue().encode("utf-8-sig"), filename

def build_all_logger_paired_csv(rows, minutes: int, end_utc: datetime):
    """Excel-friendly wide export for all loggers."""
    title,file_tag=_range_label(minutes)
    paired={}
    for q in rows:
        sn=str(q.get("logger_sn",""))
        if sn not in LOGGERS:
            continue
        typ=str(q.get("sensor_measurement_type","")).lower()
        if typ not in ("temperature","rh"):
            continue
        ts=str(q.get("timestamp",""))
        key=(sn,ts)
        rec=paired.setdefault(key,{"temperature":"","rh":""})
        if typ=="temperature":
            rec["temperature"]=q.get("value","")
        else:
            rec["rh"]=q.get("value","")

    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow([f"PBF RISE BAKING BURNABY - ALL LOGGER {title} TEMPERATURE + HUMIDITY"])
    w.writerow(["Report Range",title])
    w.writerow(["Generated Pacific Time",_pt_text(end_utc)])
    w.writerow(["Reading Basis","Calibrated / offset-adjusted LI-COR readings"])
    w.writerow([])
    w.writerow(["Date (Pacific)","Time (Pacific)","Logger","Serial","Temperature (C)","Humidity (%RH)"])

    for (sn,ts),rec in sorted(paired.items(), key=lambda item:(item[0][1],LOGGERS.get(item[0][0],{}).get("name",item[0][0]))):
        dt=_iso_dt(ts)
        if dt:
            pt=dt.astimezone(PLANT_TZ)
            date_text=pt.strftime("%Y-%m-%d")
            time_text=pt.strftime("%I:%M:%S %p")
        else:
            date_text=ts
            time_text=""
        tv=rec["temperature"]
        hv=rec["rh"]
        try:
            tv=f"{float(tv):.2f}"
        except Exception:
            pass
        try:
            hv=f"{float(hv):.2f}"
        except Exception:
            pass
        w.writerow([date_text,time_text,LOGGERS.get(sn,{}).get("name",sn),sn,tv,hv])

    filename=f"PBF_All_Loggers_Temp_Humidity_{file_tag}_{end_utc.astimezone(PLANT_TZ).strftime('%Y%m%d_%H%M')}.csv"
    return output.getvalue().encode("utf-8-sig"),filename


def _calendar_day_window(date_text: str):
    """Return exact local plant calendar-day boundaries as UTC datetimes."""
    try:
        selected=datetime.strptime(str(date_text),"%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400,detail="Date must use YYYY-MM-DD")
    today=datetime.now(timezone.utc).astimezone(PLANT_TZ).date()
    if selected > today:
        raise HTTPException(status_code=400,detail="Future dates are not available")
    start_local=datetime.combine(selected,datetime.min.time(),tzinfo=PLANT_TZ)
    end_local=start_local+timedelta(days=1)
    return selected,start_local.astimezone(timezone.utc),end_local.astimezone(timezone.utc)

def build_calendar_logger_csv(sn: str, rows, selected_date):
    cfg=get_runtime_cfg(sn)
    paired={}
    for q in rows:
        typ=str(q.get("sensor_measurement_type","")).lower()
        if typ not in ("temperature","rh"):
            continue
        ts=str(q.get("timestamp",""))
        rec=paired.setdefault(ts,{"temperature":"","rh":""})
        if typ=="temperature":
            rec["temperature"]=q.get("value","")
        else:
            rec["rh"]=q.get("value","")

    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow(["PBF RISE BAKING BURNABY - CALENDAR DATE TEMPERATURE + HUMIDITY HISTORY"])
    w.writerow(["Equipment",cfg["name"]])
    w.writerow(["Logger Serial",sn])
    w.writerow(["Selected Date (Pacific)",selected_date.isoformat()])
    w.writerow(["Temperature Calibration Offset (C)",f"{float(cfg.get('temperature_offset',0.0)):+.2f}"])
    w.writerow(["Humidity Calibration Offset (%RH)",f"{float(cfg.get('humidity_offset',0.0)):+.2f}"])
    w.writerow(["Reading Basis","Calibrated / offset-adjusted LI-COR readings"])

    temp_values=[]
    rh_values=[]
    for rec in paired.values():
        try:
            temp_values.append(float(rec["temperature"]))
        except Exception:
            pass
        try:
            rh_values.append(float(rec["rh"]))
        except Exception:
            pass

    w.writerow([])
    w.writerow(["Selected Date Summary","Minimum","Maximum","Average","Samples"])
    if temp_values:
        w.writerow(["Temperature (C)",f"{min(temp_values):.2f}",f"{max(temp_values):.2f}",
                    f"{sum(temp_values)/len(temp_values):.2f}",len(temp_values)])
    else:
        w.writerow(["Temperature (C)","","","",0])
    if rh_values:
        w.writerow(["Humidity (%RH)",f"{min(rh_values):.2f}",f"{max(rh_values):.2f}",
                    f"{sum(rh_values)/len(rh_values):.2f}",len(rh_values)])
    else:
        w.writerow(["Humidity (%RH)","","","",0])

    w.writerow([])
    w.writerow(["Date (Pacific)","Time (Pacific)","Temperature (C)","Humidity (%RH)"])
    for ts in sorted(paired):
        dt=_iso_dt(ts)
        if dt:
            pt=dt.astimezone(PLANT_TZ)
            date_value=pt.strftime("%Y-%m-%d")
            time_value=pt.strftime("%I:%M:%S %p")
        else:
            date_value=ts
            time_value=""
        tv=paired[ts]["temperature"]
        hv=paired[ts]["rh"]
        try:
            tv=f"{float(tv):.2f}"
        except Exception:
            pass
        try:
            hv=f"{float(hv):.2f}"
        except Exception:
            pass
        w.writerow([date_value,time_value,tv,hv])

    filename=f"PBF_{cfg['id']}_Temp_Humidity_{selected_date.isoformat()}.csv"
    return output.getvalue().encode("utf-8-sig"),filename

def build_calendar_all_csv(rows, selected_date):
    paired={}
    for q in rows:
        sn=str(q.get("logger_sn",""))
        if sn not in LOGGERS:
            continue
        typ=str(q.get("sensor_measurement_type","")).lower()
        if typ not in ("temperature","rh"):
            continue
        ts=str(q.get("timestamp",""))
        rec=paired.setdefault((sn,ts),{"temperature":"","rh":""})
        if typ=="temperature":
            rec["temperature"]=q.get("value","")
        else:
            rec["rh"]=q.get("value","")

    output=io.StringIO(newline="")
    w=csv.writer(output)
    w.writerow(["PBF RISE BAKING BURNABY - ALL LOGGER CALENDAR DATE HISTORY"])
    w.writerow(["Selected Date (Pacific)",selected_date.isoformat()])
    w.writerow(["Reading Basis","Calibrated / offset-adjusted LI-COR readings"])
    w.writerow([])
    w.writerow(["Date (Pacific)","Time (Pacific)","Logger","Serial","Temperature (C)","Humidity (%RH)"])

    for (sn,ts),rec in sorted(
        paired.items(),
        key=lambda item:(item[0][1],LOGGERS.get(item[0][0],{}).get("name",item[0][0]))
    ):
        dt=_iso_dt(ts)
        if dt:
            pt=dt.astimezone(PLANT_TZ)
            date_value=pt.strftime("%Y-%m-%d")
            time_value=pt.strftime("%I:%M:%S %p")
        else:
            date_value=ts
            time_value=""
        tv=rec["temperature"]
        hv=rec["rh"]
        try:
            tv=f"{float(tv):.2f}"
        except Exception:
            pass
        try:
            hv=f"{float(hv):.2f}"
        except Exception:
            pass
        w.writerow([
            date_value,time_value,LOGGERS.get(sn,{}).get("name",sn),sn,tv,hv
        ])

    filename=f"PBF_All_Loggers_Temp_Humidity_{selected_date.isoformat()}.csv"
    return output.getvalue().encode("utf-8-sig"),filename

@app.get("/api/download-date/{sn}")
async def download_calendar_date(sn: str, date: str):
    if sn not in LOGGERS:
        raise HTTPException(status_code=404,detail="Unknown logger")
    selected,start_utc,end_utc=_calendar_day_window(date)
    rows=await get_logger_history_chunked(sn,start_utc,end_utc)
    content,filename=build_calendar_logger_csv(sn,rows,selected)
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    )

@app.get("/api/download-date-all.csv")
async def download_calendar_date_all(date: str):
    selected,start_utc,end_utc=_calendar_day_window(date)
    rows=await get_report_history_all(start_utc,end_utc)
    content,filename=build_calendar_all_csv(rows,selected)
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    )

@app.get("/api/download-range/{sn}")
async def download_range(sn: str, minutes: int=1440):
    if sn not in LOGGERS:
        raise HTTPException(status_code=404,detail="Unknown logger")
    minutes=max(10,min(int(minutes),10080))
    end_utc=datetime.now(timezone.utc).replace(microsecond=0)
    start_utc=end_utc-timedelta(minutes=minutes)
    rows=await get_logger_history_chunked(sn,start_utc,end_utc)
    content,filename=build_paired_logger_csv(sn,rows,minutes,end_utc)
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    )

@app.get("/api/download-range-all.csv")
async def download_range_all(minutes: int=1440):
    minutes=max(10,min(int(minutes),10080))
    end_utc=datetime.now(timezone.utc).replace(microsecond=0)
    start_utc=end_utc-timedelta(minutes=minutes)
    rows=await get_report_history_all(start_utc,end_utc)
    content,filename=build_all_logger_paired_csv(rows,minutes,end_utc)
    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":f'attachment; filename="{filename}"'}
    )

@app.get("/api/download-24h/{sn}")
async def download_24h(sn: str):
    if sn not in LOGGERS:
        raise HTTPException(status_code=404,detail="Unknown logger")
    rows=[calibrated_row(q) for q in await get_24h_chunked(sn)]
    paired={}
    for q in rows:
        typ=str(q.get("sensor_measurement_type","")).lower()
        if typ not in ("temperature","rh"): continue
        ts=str(q.get("timestamp",""))
        rec=paired.setdefault(ts,{"temperature":"","rh":""})
        val=q.get("value","")
        if typ=="temperature": rec["temperature"]=val
        else: rec["rh"]=val

    output=io.StringIO(newline="")
    w=csv.writer(output)
    cfg=get_runtime_cfg(sn)
    end=datetime.now(timezone.utc)
    w.writerow(["PBF LI-COR 24 Hour Temperature and Humidity Data Log"])
    w.writerow(["Equipment",cfg["name"]])
    w.writerow(["Logger Serial",sn])
    try:
        pacific=ZoneInfo("America/Vancouver")
    except Exception:
        # Windows normally gets IANA timezone data from the tzdata package.
        # If unavailable, fail clearly instead of a generic 500.
        raise HTTPException(status_code=500,detail="Pacific timezone data missing. Close dashboard and run start_dashboard.bat again.")
    generated_pt=end.astimezone(pacific)
    w.writerow(["Generated Pacific Time",generated_pt.strftime("%Y-%m-%d %I:%M:%S %p %Z")])
    w.writerow(["Temperature Calibration Offset (C)",f"{float(cfg.get('temperature_offset',0.0)):+.2f}"])
    w.writerow(["Humidity Calibration Offset (%RH)",f"{float(cfg.get('humidity_offset',0.0)):+.2f}"])
    w.writerow(["Reading Basis","Calibrated / offset-adjusted readings"])

    temp_values=[]
    rh_values=[]
    for rec in paired.values():
        try: temp_values.append(float(rec["temperature"]))
        except: pass
        try: rh_values.append(float(rec["rh"]))
        except: pass
    w.writerow([])
    w.writerow(["24-Hour Summary","Minimum","Maximum","Average"])
    if temp_values:
        w.writerow(["Temperature (C)",f"{min(temp_values):.2f}",f"{max(temp_values):.2f}",f"{sum(temp_values)/len(temp_values):.2f}"])
    else:
        w.writerow(["Temperature (C)","","",""])
    if rh_values:
        w.writerow(["Humidity (%RH)",f"{min(rh_values):.2f}",f"{max(rh_values):.2f}",f"{sum(rh_values)/len(rh_values):.2f}"])
    else:
        w.writerow(["Humidity (%RH)","","",""])
    w.writerow([])
    w.writerow(["Date (Pacific)","Time (Pacific)","Temperature (C)","Humidity (%RH)"])
    for ts in sorted(paired):
        raw=ts.replace("Z","+00:00")
        try:
            dt=datetime.fromisoformat(raw).astimezone(pacific)
            date_text=dt.strftime("%Y-%m-%d")
            time_text=dt.strftime("%I:%M:%S %p")
        except Exception:
            date_text=ts
            time_text=""
        tv=paired[ts]["temperature"]
        hv=paired[ts]["rh"]
        try: tv=f"{float(tv):.2f}"
        except: pass
        try: hv=f"{float(hv):.2f}"
        except: pass
        w.writerow([date_text,time_text,tv,hv])

    from fastapi.responses import Response
    filename=f"PBF_{cfg['id']}_Temp_Humidity_24hr_{end.strftime('%Y%m%d_%H%M')}.csv"
    return Response(content=output.getvalue(),media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":f'attachment; filename="{filename}"'})

@app.get("/api/download-test/{sn}")
async def download_test(sn: str):
    if sn not in LOGGERS: raise HTTPException(status_code=404,detail="Unknown logger")
    rows=await getdata_range([sn],datetime.now(timezone.utc)-timedelta(minutes=30),datetime.now(timezone.utc))
    return {"ok":True,"logger":sn,"records_returned":len(rows)}

@app.get("/api/history/{sn}")
async def history(sn:str,minutes:int=60):
    if sn not in LOGGERS: raise HTTPException(404,"Unknown logger")
    rows=await getdata([sn],max(10,min(minutes,10080)))
    return {"logger":get_runtime_cfg(sn),"data":[calibrated_row(q) for q in rows]}


@app.on_event("startup")
async def start_email_monitor():
    async def monitor():
        await asyncio.sleep(5)
        while True:
            try:
                await current()
                await maybe_send_daily_maintenance_report()
                await maybe_daily_audit_maintenance()
            except Exception as e:
                print("Background LI-COR/email/report monitor error:",repr(e))
            await asyncio.sleep(60)
    asyncio.create_task(monitor())
