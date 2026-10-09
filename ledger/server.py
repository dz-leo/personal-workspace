#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人账本工作台 — 本地后端（MySQL 持久化 + 静态托管）

设计原则：
  1. 只用标准库 + PyMySQL，零框架，单文件可交付。
  2. 前端的存储层是「全量快照」接口（load / save），后端就按这个契约做：
       GET  /api/db  -> 返回与前端 DB 结构完全一致的 JSON 快照
       POST /api/db  -> 事务化全量落库（先清表再写入，保证与前端状态一致）
     业务代码一行都不用改，只是把 localStorage 换成了 MySQL。
  3. 账户余额不落库，由流水实时派生，避免任何漂移。
     余额 = init_balance + 入账 - 支出 - 转出 + 转入

用法：
    # 只在本机访问
    python3 server.py
    # 手机 / 局域网访问（同一 WiFi，用电脑内网 IP 打开）
    python3 server.py --host 0.0.0.0 --port 8765
"""

import argparse
import glob
import gzip
import json
import os
import socket
import sys
import threading
import time
import traceback
from datetime import date, datetime
from decimal import Decimal
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    sys.stderr.write(
        "缺少 PyMySQL。请先安装：\n"
        "  pip3 install pymysql\n"
        "装完再运行本脚本；start.command 会自动挑一个装了 pymysql 的 python。\n"
    )
    raise

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------------
# 数据库配置：口令不写在本文件里（源码要能公开托管）
#   优先级：环境变量 LEDGER_DB_* > ~/.ledger.conf 的 db_* > 下面的内置默认值
#   首次使用：python3 server.py --init-conf   生成 600 权限的配置模板
# ----------------------------------------------------------------------------
CONF_PATH = os.environ.get("LEDGER_CONF") or os.path.expanduser("~/.ledger.conf")
CONF_DEFAULTS = {
    "host": "127.0.0.1",
    "port": "3306",
    "user": "root",
    "password": "",
    "database": "ledger",
}
# 配置文件里的键名<-> 内部键名。原先内部叫 database、配置里叫 db_name，
# 两者对不上，导致 ~/.ledger.conf 里的 db_name 一直被忽略、永远连ledger 库。
CONF_FILE_KEYS = {
    "host": "db_host",
    "port": "db_port",
    "user": "db_user",
    "password": "db_password",
    "database": "db_name",
}
CONF_ENV_KEYS = {
    "host": "LEDGER_DB_HOST",
    "port": "LEDGER_DB_PORT",
    "user": "LEDGER_DB_USER",
    "password": "LEDGER_DB_PASSWORD",
    "database": "LEDGER_DB_NAME",
}

CONF_TEMPLATE = """\
# 个人账本后端配置（由 server.py --init-conf 生成）
# 本文件含数据库口令，权限必须为 600： chmod 600 %s
db_host=%s
db_port=%s
db_user=%s
db_password=
db_name=%s

# ---- 自动备份（防止一次坏写入把账目全冲掉）----
# 写库前先把当前状态存成 backups/auto-*.json.gz，格式与应用内导出的 JSON 一致。
auto_backup=1                 # 0=关闭
auto_backup_interval=300      # 两次自动备份的最小间隔（秒），记账过程中不会每次都备份
auto_backup_keep=30           # 最多保留多少份自动备份，超出删最旧的
# backup_dir=/Volumes/移动硬盘/ledger-backups   # 想存到外置硬盘就填这行
"""


def load_conf():
    """读 KEY=VALUE 配置；文件不存在时返回空字典（不报错，走默认值）"""
    conf = {}
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k in conf and conf[k] != v:
                    sys.stderr.write("[配置] %s 里 %s 出现两次（%s / %s），以最后一个为准\n"
                                     % (CONF_PATH, k, conf[k], v))
                conf[k] = v
    except FileNotFoundError:
        pass
    except OSError as e:
        sys.stderr.write("读取配置 %s 失败：%s\n" % (CONF_PATH, e))
    return conf


# 启动时读一次配置，后面 db_* 与自动备份参数都从这里取
FILE_CONF = load_conf()


def conf_get(key, default=None):
    v = FILE_CONF.get(key)
    return default if v is None or v == "" else v


def conf_num(key, default):
    try:
        return int(conf_get(key, default))
    except (TypeError, ValueError):
        return default


def build_db_conf():
    """优先级：环境变量 > ~/.ledger.conf 的 db_* > 内置默认值"""
    conf = dict(CONF_DEFAULTS)
    for k in CONF_DEFAULTS:
        val = os.environ.get(CONF_ENV_KEYS[k])
        if val is None:
            val = FILE_CONF.get(CONF_FILE_KEYS[k])
        if val is not None and val != "":
            conf[k] = val
    try:
        port = int(conf["port"])
    except (TypeError, ValueError):
        port = 3306
    return {
        "host": conf["host"] or "127.0.0.1",
        "port": port,
        "user": conf["user"] or "root",
        "password": conf["password"],
        "database": conf["database"] or "ledger",
        "charset": "utf8mb4",
        "autocommit": False,
    }


def write_conf_template():
    """生成配置模板并收紧权限（已存在则覆盖，需用户自行确认）"""
    existed = os.path.exists(CONF_PATH)
    d = os.path.dirname(CONF_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        f.write(CONF_TEMPLATE % (
            CONF_PATH, CONF_DEFAULTS["host"], CONF_DEFAULTS["port"],
            CONF_DEFAULTS["user"], CONF_DEFAULTS["database"],
        ))
    os.chmod(CONF_PATH, 0o600)
    print(("已覆盖配置：" if existed else "已生成配置：") + CONF_PATH)
    print("请把 db_password= 填成本机 MySQL 的 root 密码（填完 chmod 600 %s），再启动服务。" % CONF_PATH)


def check_conf_perms():
    """口令文件被别人读到就等于把数据库敞开，提醒一次"""
    try:
        mode = os.stat(CONF_PATH).st_mode & 0o777
    except OSError:
        return
    if mode & 0o077:
        sys.stderr.write("[安全提醒] %s 权限是 %o，建议 chmod 600 %s\n" % (CONF_PATH, mode, CONF_PATH))


DB_CONF = build_db_conf()

# ----------------------------------------------------------------------------
# 自动备份
#   POST /api/db 是「先清表再全量写回」，一次坏写入（或手滑导入错文件）就足以
#   把全部账目冲掉。所以每次写库前先把当前状态存一份 gzipped JSON，
#   格式与应用内「导出 JSON」完全一致，出了事可以直接导入回去。
#   记账过程中每笔都会触发写库，因此按 auto_backup_interval 节流。
# ----------------------------------------------------------------------------
BACKUP_DIR = os.path.expanduser(conf_get("backup_dir") or os.path.join(BASE_DIR, "backups"))
AUTO_BACKUP = str(conf_get("auto_backup", "1")).lower() not in ("0", "false", "no")
AUTO_BACKUP_INTERVAL = max(0, conf_num("auto_backup_interval", 300))
AUTO_BACKUP_KEEP = max(1, conf_num("auto_backup_keep", 30))
_auto_backup_lock = threading.Lock()
_last_backup_ts = 0.0
READ_ONLY = False   # main() 里按 --read-only 置True


def auto_backup_files():
    return sorted(glob.glob(os.path.join(BACKUP_DIR, "auto-*.json.gz")))


def prune_auto_backups():
    files = auto_backup_files()
    for old in files[:-AUTO_BACKUP_KEEP] if len(files) > AUTO_BACKUP_KEEP else []:
        try:
            os.remove(old)
        except OSError:
            pass


def newest_auto_backup_age():
    """最新一份自动备份距今多少秒；没有则返回 None"""
    files = auto_backup_files()
    if not files:
        return None
    try:
        return max(0.0, time.time() - os.path.getmtime(files[-1]))
    except OSError:
        return None


def auto_backup(reason, force=False):
    """写库前存一份当前快照。返回文件路径，失败返回 None（不阻断写库）"""
    if not AUTO_BACKUP:
        return None
    global _last_backup_ts
    with _auto_backup_lock:
        now = time.time()
        if not force and AUTO_BACKUP_INTERVAL and (now - _last_backup_ts) < AUTO_BACKUP_INTERVAL:
            return None
        _last_backup_ts = now
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            payload = {
                "app": "wb-ledger",
                "version": 1,
                "exportedAt": datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
                "data": read_snapshot(),
            }
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            name = "auto-%s.json.gz" % datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(BACKUP_DIR, name)
            with gzip.open(path, "wb") as f:
                f.write(raw)
            prune_auto_backups()
            print("[自动备份] %s（%s，%d 条流水）"
                  % (name, reason, len(payload["data"].get("records") or [])))
            return path
        except Exception as e:
            sys.stderr.write("[自动备份] 失败（不阻断本次写入）：%s\n" % e)
            return None

_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def connect():
    return pymysql.connect(cursorclass=DictCursor, **DB_CONF)


def jnum(v):
    """DECIMAL -> float，保留 2 位；None -> 0"""
    if v is None:
        return 0
    if isinstance(v, Decimal):
        return float(round(v, 2))
    return v


def jdate(v):
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    return v or ""


def jload(v, fallback):
    """兼容 JSON 列返回 str / list / dict / None 三种情况"""
    if v is None or v == "":
        return fallback
    if isinstance(v, (list, dict, int, float)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return fallback


def as_list(v):
    out = jload(v, [])
    return out if isinstance(out, list) else []


# ----------------------------------------------------------------------------
# 读：组装成前端 DB 快照
# ----------------------------------------------------------------------------
def read_snapshot():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT k, v FROM meta")
            meta_rows = {r["k"]: r["v"] for r in cur.fetchall()}

            cur.execute(
                "SELECT id,type,name,color,icon,subs,custom FROM categories "
                "ORDER BY FIELD(type,'expense','income','transfer'), sort, id"
            )
            categories = [
                {
                    "id": r["id"],
                    "type": r["type"],
                    "name": r["name"],
                    "color": r["color"] or "#8A94A0",
                    "icon": r["icon"] or "other",
                    "subs": as_list(r["subs"]),
                    "custom": bool(r["custom"]),
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                "SELECT id,name,kind,icon,color,init_balance,credit_limit,bill_day,"
                "repay_day,card_tail,in_total,archived,sort,note FROM accounts "
                "ORDER BY archived, sort, id"
            )
            accounts = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "icon": r["icon"] or "wallet",
                    "color": r["color"] or "#7B8FA1",
                    "initBalance": jnum(r["init_balance"]),
                    "creditLimit": jnum(r["credit_limit"]),
                    "billDay": r["bill_day"] or 0,
                    "repayDay": r["repay_day"] or 0,
                    "cardTail": r["card_tail"] or "",
                    "inTotal": bool(r["in_total"]),
                    "archived": bool(r["archived"]),
                    "sort": r["sort"] or 0,
                    "note": r["note"] or "",
                }
                for r in cur.fetchall()
            ]

            cur.execute("SELECT name FROM tags ORDER BY sort, id")
            tags = [r["name"] for r in cur.fetchall()]
            cur.execute("SELECT name FROM scenes ORDER BY sort, id")
            scenes = [r["name"] for r in cur.fetchall()]

            cur.execute("SELECT k, v FROM settings")
            st_rows = {r["k"]: r["v"] for r in cur.fetchall()}

            cur.execute(
                "SELECT id,date,time,type,amount,category,sub_category,account,to_account,"
                "merchant,tags,scene,is_fixed,note,demo,created_at,updated_at,deleted "
                "FROM records ORDER BY date, time, id"
            )
            records = [
                {
                    "id": r["id"],
                    "date": jdate(r["date"]),
                    "time": r["time"] or "",
                    "type": r["type"],
                    "amount": jnum(r["amount"]),
                    "category": r["category"],
                    "subCategory": r["sub_category"] or "",
                    "account": r["account"] or "",
                    "toAccount": r["to_account"] or "",
                    "merchant": r["merchant"] or "",
                    "tags": as_list(r["tags"]),
                    "scene": r["scene"] or "",
                    "isFixed": bool(r["is_fixed"]),
                    "note": r["note"] or "",
                    "demo": bool(r["demo"]),
                    "createdAt": r["created_at"] or 0,
                    "updatedAt": r["updated_at"] or 0,
                    "deleted": bool(r["deleted"]),
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                "SELECT scope,period_key,content,demo,updated_at FROM notes ORDER BY id"
            )
            notes = [
                {
                    "scope": r["scope"],
                    "periodKey": r["period_key"],
                    "content": r["content"] or "",
                    "autoSummary": "",
                    "summaryLocked": False,
                    "demo": bool(r["demo"]),
                    "updatedAt": r["updated_at"] or 0,
                }
                for r in cur.fetchall()
            ]

        budget = jload(st_rows.get("budget"), {"enabled": False, "amount": 0})
        month_start = jload(st_rows.get("monthStartDay"), 1)
        ignored = as_list(st_rows.get("ignoredMissDays"))

        return {
            "meta": {
                "version": int(meta_rows.get("version") or 2),
                "seeded": meta_rows.get("seeded") == "1",
                "isDemo": meta_rows.get("isDemo") == "1",
            },
            "records": records,
            "notes": notes,
            "settings": {
                "categories": categories,
                "accounts": accounts,
                "tags": tags,
                "scenes": scenes,
                "budget": budget if isinstance(budget, dict) else {"enabled": False, "amount": 0},
                "monthStartDay": month_start if isinstance(month_start, int) else 1,
                "ignoredMissDays": ignored,
            },
        }
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# 写：全量落库（事务）
# ----------------------------------------------------------------------------
def write_snapshot(db):
    if not isinstance(db, dict) or not isinstance(db.get("records"), list):
        raise ValueError("快照格式不对：缺少 records")

    settings = db.get("settings") or {}
    cats = settings.get("categories") or []
    accs = settings.get("accounts") or []
    tags = settings.get("tags") or []
    scenes = settings.get("scenes") or []
    meta = db.get("meta") or {}
    notes = db.get("notes") or []
    records = db["records"]

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for t in ("records", "notes", "categories", "accounts", "tags", "scenes", "settings", "meta"):
                cur.execute("DELETE FROM `%s`" % t)

            if cats:
                cur.executemany(
                    "INSERT INTO categories (id,type,name,color,icon,subs,custom,sort) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            c.get("id"), c.get("type", "expense"), c.get("name", ""),
                            c.get("color", "#8A94A0"), c.get("icon", "other"),
                            json.dumps(c.get("subs") or [], ensure_ascii=False),
                            1 if c.get("custom") else 0, i + 1,
                        )
                        for i, c in enumerate(cats)
                    ],
                )

            if accs:
                cur.executemany(
                    "INSERT INTO accounts (id,name,kind,icon,color,init_balance,credit_limit,"
                    "bill_day,repay_day,card_tail,in_total,archived,sort,note) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            a.get("id"), a.get("name", ""), a.get("kind", "other"),
                            a.get("icon", "wallet"), a.get("color", "#7B8FA1"),
                            round(float(a.get("initBalance") or 0), 2),
                            round(float(a.get("creditLimit") or 0), 2),
                            int(a.get("billDay") or 0), int(a.get("repayDay") or 0),
                            a.get("cardTail", "") or "",
                            0 if a.get("inTotal") is False else 1,
                            1 if a.get("archived") else 0,
                            int(a.get("sort") or (i + 1)), a.get("note", "") or "",
                        )
                        for i, a in enumerate(accs)
                    ],
                )

            if tags:
                cur.executemany(
                    "INSERT INTO tags (name,sort) VALUES (%s,%s)",
                    [(t, i + 1) for i, t in enumerate(tags)],
                )
            if scenes:
                cur.executemany(
                    "INSERT INTO scenes (name,sort) VALUES (%s,%s)",
                    [(s, i + 1) for i, s in enumerate(scenes)],
                )

            cur.executemany(
                "INSERT INTO settings (k,v) VALUES (%s,%s)",
                [
                    ("budget", json.dumps(settings.get("budget") or {"enabled": False, "amount": 0}, ensure_ascii=False)),
                    ("monthStartDay", json.dumps(settings.get("monthStartDay") or 1)),
                    ("ignoredMissDays", json.dumps(settings.get("ignoredMissDays") or [], ensure_ascii=False)),
                ],
            )
            cur.executemany(
                "INSERT INTO meta (k,v) VALUES (%s,%s)",
                [
                    ("version", str(meta.get("version") or 2)),
                    ("seeded", "1" if meta.get("seeded") else "0"),
                    ("isDemo", "1" if meta.get("isDemo") else "0"),
                ],
            )

            if records:
                cur.executemany(
                    "INSERT INTO records (id,date,time,type,amount,category,sub_category,"
                    "account,to_account,merchant,tags,scene,is_fixed,note,demo,"
                    "created_at,updated_at,deleted) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            r.get("id"), r.get("date"), r.get("time", "") or "",
                            r.get("type", "expense"),
                            round(float(r.get("amount") or 0), 2),
                            r.get("category", "other"), r.get("subCategory", "") or "",
                            r.get("account", "") or "", r.get("toAccount", "") or "",
                            r.get("merchant", "") or "",
                            json.dumps(r.get("tags") or [], ensure_ascii=False),
                            r.get("scene", "") or "",
                            1 if r.get("isFixed") else 0, r.get("note", "") or "",
                            1 if r.get("demo") else 0,
                            int(r.get("createdAt") or 0), int(r.get("updatedAt") or 0),
                            1 if r.get("deleted") else 0,
                        )
                        for r in records
                    ],
                )

            seen = set()
            note_rows = []
            for n in notes:
                key = (n.get("scope"), n.get("periodKey"))
                if not key[0] or not key[1] or key in seen:
                    continue
                seen.add(key)
                note_rows.append(
                    (key[0], key[1], n.get("content", "") or "",
                     1 if n.get("demo") else 0,
                     int(n.get("updatedAt") or 0), int(n.get("updatedAt") or 0))
                )
            if note_rows:
                cur.executemany(
                    "INSERT INTO notes (scope,period_key,content,demo,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    note_rows,
                )

            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
        return {"records": len(records), "accounts": len(accs), "notes": len(note_rows)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def account_balances():
    """服务端口径的余额，用于对账 / 排查前端算错"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,name,init_balance,archived FROM accounts ORDER BY sort, id")
            accs = cur.fetchall()
            cur.execute(
                "SELECT account AS id,"
                " SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS inflow,"
                " SUM(CASE WHEN type IN ('expense','transfer') THEN amount ELSE 0 END) AS outflow"
                " FROM records WHERE deleted=0 AND account<>'' GROUP BY account"
            )
            flow = {r["id"]: [jnum(r["inflow"]), jnum(r["outflow"])] for r in cur.fetchall()}
            cur.execute(
                "SELECT to_account AS id, SUM(amount) AS inflow FROM records"
                " WHERE deleted=0 AND type='transfer' AND to_account<>'' GROUP BY to_account"
            )
            for r in cur.fetchall():
                flow.setdefault(r["id"], [0, 0])
                flow[r["id"]][0] += jnum(r["inflow"])

        out = []
        for a in accs:
            fin, fout = flow.get(a["id"], [0, 0])
            out.append({
                "id": a["id"],
                "name": a["name"],
                "initBalance": jnum(a["init_balance"]),
                "inflow": round(fin, 2),
                "outflow": round(fout, 2),
                "balance": round(jnum(a["init_balance"]) + fin - fout, 2),
                "archived": bool(a["archived"]),
            })
        return out
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "LedgerLocal/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # --- 响应helper ---
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 同源使用，不开放 CORS：否则任何网页都能用 JS 读你的账本
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=500):
        self._json({"ok": False, "error": str(msg)}, code)

    def do_OPTIONS(self):
        # 不开放跨域：只回 204，不带任何 Access-Control-* 头
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                conn = connect()
                conn.close()
                return self._json({"ok": True, "storage": "mysql", "db": DB_CONF["database"]})
            if path == "/api/db":
                with _LOCK:
                    return self._json({"ok": True, "data": read_snapshot()})
            if path == "/api/balances":
                with _LOCK:
                    return self._json({"ok": True, "data": account_balances()})
            return self._static(path)
        except Exception as e:
            traceback.print_exc()
            return self._err(e)

    def do_POST(self):
        path = urlparse(self.path).path
        if READ_ONLY:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
            except Exception:
                pass
            return self._err("服务以 --read-only 启动，拒绝任何写入。", 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if path == "/api/db":
                data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
                with _LOCK:
                    auto_backup("写库前")      # 先留一份现在的状态，坏写入也能退回去
                    stat = write_snapshot(data)
                return self._json({"ok": True, "saved": stat})
            return self._err("未知接口: " + path, 404)
        except Exception as e:
            traceback.print_exc()
            return self._err(e, 400)

    # 静态资源走白名单：整个应用就是一个 index.html。
    # 之前把整个目录开放下载，等于把 server.py、建库脚本、数据库备份都挂在局域网上。
    # 静态资源走白名单：整个应用就是一个 index.html。
    # 之前把整个目录开放下载，等于把 server.py、建库脚本、数据库备份都挂在局域网上。
    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        if path != "/index.html":
            return self._err("Not Found: " + path, 404)
        with open(os.path.join(BASE_DIR, "index.html"), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    # stdout 默认是块缓冲：重定向到文件或launchd 日志时，print 的内容会卡在缓冲区里，
    # 服务被杀就丢了，所以启动信息必须实时可见。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="个人账本本地后端")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址；手机访问用 0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db-host", default=DB_CONF["host"])
    ap.add_argument("--db-port", type=int, default=DB_CONF["port"])
    ap.add_argument("--db-user", default=DB_CONF["user"])
    ap.add_argument("--db-name", default=DB_CONF["database"])
    ap.add_argument("--db-pass", default=DB_CONF["password"], help="覆盖配置里的数据库口令")
    ap.add_argument("--init-conf", action="store_true",
                    help="生成 ~/.ledger.conf 配置模板（含口令，权限 600）后退出")
    ap.add_argument("--read-only", action="store_true",
                    help="只读模式：拒绝一切写入。排查/演示/验证时用，账本绝不会被改")
    args = ap.parse_args()

    if args.init_conf:
        write_conf_template()
        return

    DB_CONF.update({
        "host": args.db_host, "port": args.db_port,
        "user": args.db_user, "password": args.db_pass, "database": args.db_name,
    })
    global READ_ONLY
    READ_ONLY = args.read_only

    check_conf_perms()
    if args.read_only:
        print("  模式：只读（拒绝一切写入，POST /api/db 会返回 403）")
    if not DB_CONF["password"]:
        sys.stderr.write(
            "没读到数据库口令。请先执行：\n"
            "  python3 server.py --init-conf\n"
            "把 %s 里的 db_password= 填成 MySQL root 密码（改完 chmod 600 %s），\n"
            "或用 --db-pass / 环境变量 LEDGER_DB_PASSWORD 临时指定。\n"
            % (CONF_PATH, CONF_PATH)
        )
        sys.exit(1)

    try:
        snap = read_snapshot()
    except Exception as e:
        sys.stderr.write("数据库连接失败：%s\n" % e)
        if not os.path.exists(CONF_PATH):
            sys.stderr.write("没有找到配置文件 %s，先执行： python3 server.py --init-conf\n" % CONF_PATH)
        sys.stderr.write("首次使用需先建库： mysql -u%s -p %s < ledger-init.sql（-p 后回车再输密码）\n"
                         % (args.db_user, args.db_name))
        sys.exit(1)

    print("MySQL 已连接：%s@%s:%s/%s" % (args.db_user, args.db_host, args.db_port, args.db_name))
    print("  配置来源：%s" % CONF_PATH)
    print("  流水 %d 条 · 账户 %d 个 · 分类 %d 项"
          % (len(snap["records"]), len(snap["settings"]["accounts"]), len(snap["settings"]["categories"])))
    if AUTO_BACKUP:
        age = newest_auto_backup_age()
        if age is None:
            auto_backup("服务启动（首次备份）", force=True)
        elif age > 86400:
            auto_backup("服务启动（距上次备份 %.1f 小时）" % (age / 3600.0), force=True)
        else:
            print("  自动备份：开启（每 %ds 最多一次，保留 %d 份）"
                  % (AUTO_BACKUP_INTERVAL, AUTO_BACKUP_KEEP))
    else:
        print("  自动备份：已关闭（auto_backup=0）")
    print("本机访问：  http://127.0.0.1:%d/" % args.port)
    if args.host == "0.0.0.0":
        print("手机访问：  http://%s:%d/   （同一 WiFi 下）" % (lan_ip(), args.port))
        print("            只在自己的家里这么做：公共 WiFi 下能连到这台机器就能读写账本")
    else:
        print("            只监听本机。手机/局域网访问需改用 --host 0.0.0.0")
    print("Ctrl+C 停止")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.server_close()


if __name__ == "__main__":
    main()
