#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 GLaDOS 自动签到 (多账号独立兑换策略 - GLADOS_COOKIE 一行一个账号)
"""

import requests
import json
import os
import sys
import html
import re
import time
from datetime import datetime

# 修复 Windows Unicode 输出问题
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置 =================

# 兑换档位 -> 所需积分
PLAN_REQUIREMENTS = {"plan100": 100, "plan200": 200, "plan500": 500}
# 表示"关闭兑换"的写法
OFF_VALUES = {"off", "none", "false", "0", "no", "disable", "disabled", "关闭"}
OFF_PLAN = "off"

DOMAINS = [
    "https://glados.cloud",
    "https://railgun.info",
    "https://glados.rocks",
    "https://glados.network",
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
}

# ================= 工具函数 =================

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def extract_cookie(raw: str):
    if not raw: return None
    raw = raw.strip()
    if 'koa:sess=' in raw or 'koa:sess.sig=' in raw:
        return raw
    if raw.startswith('{'):
        try:
            return 'koa.sess=' + json.loads(raw).get('token')
        except: pass
    if raw.count('.') == 2 and '=' not in raw and len(raw) > 50:
        return 'koa:sess=' + raw
    return raw

def parse_plan(value):
    """解析行尾兑换策略: plan100/plan200/plan500 或 off(不兑换)"""
    v = (value or "").strip().lower()
    if v in OFF_VALUES:
        return OFF_PLAN
    if v in PLAN_REQUIREMENTS:
        return v
    log(f"⚠️ 未知的兑换策略 '{value}'，该账号将只签到不兑换")
    return OFF_PLAN

def parse_account(entry: str):
    """解析一行账号: cookie#策略。行尾无 # 后缀 = 只签到不兑换"""
    if '#' in entry:
        cookie, plan_raw = entry.rsplit('#', 1)
        return cookie.strip(), parse_plan(plan_raw)
    return entry.strip(), None  # None = 未设置兑换策略

def get_cookies():
    """GLADOS_COOKIE 一行一个账号，# 开头的行视为注释跳过"""
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw:
        log("❌ 未配置 GLADOS_COOKIE")
        return []
    accounts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        cookie, plan = parse_account(line)
        accounts.append((cookie, plan))
    return accounts

# ================= 核心逻辑 =================

class GLaDOS:
    def __init__(self, cookie):
        self.cookie = cookie
        self.domain = DOMAINS[0]
        self.email = "?"
        self.left_days = "?"
        self.points = "0"
        self.points_change = "?"
        self.exchange_info = ""
        
    def req(self, method, path, data=None):
        for d in DOMAINS:
            try:
                url = f"{d}{path}"
                h = HEADERS.copy()
                h['Cookie'] = self.cookie
                h['Origin'] = d
                h['Referer'] = f"{d}/console/checkin"
                
                if method == 'GET':
                    resp = requests.get(url, headers=h, timeout=10)
                else:
                    resp = requests.post(url, headers=h, json=data, timeout=10)
                
                if resp.status_code == 200:
                    self.domain = d
                    return resp.json()
            except Exception as e:
                log(f"⚠️ {d} 请求失败: {e}")
                continue
        return None

    def get_status(self):
        res = self.req('GET', '/api/user/status')
        if res and 'data' in res:
            d = res['data']
            self.email = d.get('email', 'Unknown')
            self.left_days = str(d.get('leftDays', '?')).split('.')[0]
            return True
        return False

    def get_points(self):
        res = self.req('GET', '/api/user/points')
        if res and 'points' in res:
            self.points = str(res.get('points', '0')).split('.')[0]
            history = res.get('history', [])
            if history:
                last = history[0]
                change = str(last.get('change', '0')).split('.')[0]
                if not change.startswith('-'): change = '+' + change
                self.points_change = change
            
            plans = res.get('plans', {})
            pts = int(self.points)
            exchange_lines = []
            sorted_plans = sorted(plans.items(), key=lambda x: x[1]['points'])
            
            for plan_id, plan_data in sorted_plans:
                need = plan_data['points']
                days = plan_data['days']
                status = "✅" if pts >= need else "❌"
                desc = "(可兑换)" if pts >= need else f"(差{need-pts}分)"
                exchange_lines.append(f"{status} {need}分→{days}天 {desc}")
            
            self.exchange_info = "\n".join(exchange_lines)
            return True
        return False

    def checkin(self):
        return self.req('POST', '/api/user/checkin', {'token': 'glados.cloud'})

    def exchange(self, plan):
        return self.req('POST', '/api/user/exchange', {'planType': plan})

# ================= 推送函数 =================

def _clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n] + "…"

def _md(value, limit):
    """转义 Markdown 特殊字符并截断, 防止内容破坏富文本结构"""
    s = str(value)
    for ch in ("\\", "`", "*", "_", "~", "[", "]"):
        s = s.replace(ch, "\\" + ch)
    return _clip(s, limit)

def _legacy(value, limit):
    """HTML 转义并截断"""
    return html.escape(_clip(value, limit))

def _rich_block(r):
    """单个账号的富文本卡片 (Markdown <details> 可折叠区块)"""
    summary = f"👤 {_md(r['email'], 80)} · {_md(r['plan'], 20)} · {_md(r['exchange'], 30)}"
    opts = "\n".join("- " + ln for ln in str(r["options"]).splitlines() if ln.strip()) or "- 无"
    body = (
        f"- 💰 积分: {_md(r['points'], 20)} ({_md(r['change'], 20)})\n"
        f"- 📆 剩余: {_md(r['left_days'], 20)} 天\n"
        f"- 🎯 签到: {_md(r['msg'], 200)}\n"
        f"- 🎛 策略: {_md(r['plan'], 20)}\n"
        f"- 🔁 兑换: {_md(r['exchange'], 150)}\n"
        f"\n**🎁 可兑换选项**\n\n{opts}\n"
    )
    return f"<details><summary>{summary}</summary>\n\n{body}\n</details>"

def _legacy_block(r):
    """单个账号的旧版 HTML 卡片 (可折叠引用, 兜底用)"""
    e = html.escape
    return (
        "<blockquote expandable>"
        f"👤 {e(_clip(r['email'], 100))}\n"
        f"💰 积分: {e(_clip(r['points'], 20))} ({e(_clip(r['change'], 20))})\n"
        f"📆 剩余: {e(_clip(r['left_days'], 20))} 天\n"
        f"🎯 签到: {e(_clip(r['msg'], 200))}\n"
        f"🎛 策略: {e(_clip(r['plan'], 20))}\n"
        f"🔁 兑换: {e(_clip(r['exchange'], 150))}\n"
        f"🎁 可兑换选项:\n{e(_clip(r['options'], 600))}"
        "</blockquote>"
    )

def build_report(results, success_cnt, total, cur_time):
    """生成 [(富文本Markdown, 旧版HTML), ...] 分块消息列表
    富文本走 sendRichMessage (可折叠 details 区块, 免费无需会员)
    旧版走 sendMessage HTML 可折叠引用, 作为自动兜底
    """
    rich_title = f"# 🚀 GLaDOS 签到 ✅ {success_cnt}/{total}"
    legacy_title = f"🚀 <b>GLaDOS 签到</b>  ✅ {success_cnt}/{total}"
    pairs = [(_rich_block(r), _legacy_block(r)) for r in results]

    chunks = []
    rich_cur, legacy_cur = rich_title, legacy_title
    for rb, lb in pairs:
        # 富文本单条上限 32768 字符; 旧版 4096, 留余量分块
        if len(rich_cur) + len(rb) > 30000 or len(legacy_cur) + len(lb) > 3800:
            chunks.append((rich_cur, legacy_cur))
            rich_cur, legacy_cur = rich_title, legacy_title
        rich_cur += "\n\n" + rb
        legacy_cur += "\n\n" + lb
    rich_cur += f"\n\n🕘 {_md(cur_time, 40)}"
    legacy_cur += f"\n\n🕘 {html.escape(str(cur_time))}"
    chunks.append((rich_cur, legacy_cur))
    return chunks

def telegram_push(token, chat_id, chunks):
    if not token or not chat_id or not chunks: return
    base = f"https://api.telegram.org/bot{token}"
    sent = 0
    for rich_md, legacy_html in chunks:
        ok = False
        try:
            resp = requests.post(f"{base}/sendRichMessage",
                                 json={"chat_id": chat_id,
                                       "rich_message": {"markdown": rich_md}},
                                 timeout=15)
            if resp.status_code == 200:
                sent += 1
                ok = True
            else:
                try: desc = (resp.json() or {}).get('description', '') or ''
                except Exception: desc = ''
                log(f"⚠️ sendRichMessage 失败: HTTP {resp.status_code} {desc}，改用普通格式重发")
        except Exception as e:
            log(f"⚠️ sendRichMessage 异常: {e}，改用普通格式重发")
        if not ok:
            # 兜底: 旧版 sendMessage + HTML 可折叠引用
            try:
                data = {"chat_id": chat_id, "text": legacy_html, "parse_mode": "HTML",
                        "link_preview_options": {"is_disabled": True}}
                resp = requests.post(f"{base}/sendMessage", json=data, timeout=10)
                if resp.status_code == 200:
                    sent += 1
                else:
                    try: desc = (resp.json() or {}).get('description', '') or ''
                    except Exception: desc = ''
                    if 'parse' in desc.lower():
                        plain = re.sub(r'</?blockquote[^>]*>', '', legacy_html)
                        requests.post(f"{base}/sendMessage",
                                      json={"chat_id": chat_id, "text": plain}, timeout=10)
                        sent += 1
                    else:
                        log(f"❌ Telegram 推送失败: HTTP {resp.status_code} {desc}")
            except Exception as e:
                log(f"❌ Telegram 推送失败: {e}")
    if sent == len(chunks):
        log(f"✅ Telegram 推送成功 ({sent}/{len(chunks)} 条)")

# ================= 主程序 =================

def main():
    log("🚀 GLaDOS Checkin Starting...")
    cookies = get_cookies()
    if not cookies: sys.exit(1)
    
    results = []
    success_cnt = 0
    
    for cookie, plan in cookies:
        g = GLaDOS(cookie)
        
        checkin_res = g.checkin()
        g.get_status()
        g.get_points()
        
        # --- 核心判定逻辑修改 ---
        raw_msg = checkin_res.get('message', 'Failure') if checkin_res else "Network Error"
        
        # 只要 message 包含 "Checkin" (首次成功) 或 "observation logged" (今日已签到)
        # 都代表今日已经签到成功了，标题显示 1/1
        if "Checkin" in raw_msg or "observation logged" in raw_msg:
            success_cnt += 1
            msg = "Today's observation logged. Return tomorrow for more points."
        else:
            msg = raw_msg

        current_pts = int(g.points)
        if plan is None:
            # 行尾未加 #策略 → 只签到不兑换
            exchange_msg = "仅签到（行尾未配置 #策略）"
        elif plan == OFF_PLAN:
            exchange_msg = "已按配置跳过自动兑换"
        else:
            need_pts = PLAN_REQUIREMENTS[plan]
            exchange_msg = f"积分不足 ({current_pts}/{need_pts})"
            if current_pts >= need_pts:
                ex_res = g.exchange(plan)
                exchange_msg = ex_res.get('message', '提交失败')
                g.get_status()
                g.get_points()

        # 保持要求的全空行排版
        plan_desc = "未设置" if plan is None else ("不兑换" if plan == OFF_PLAN else plan)
        results.append({
            "email": g.email,
            "points": g.points,
            "change": g.points_change,
            "left_days": g.left_days,
            "msg": msg,
            "plan": plan_desc,
            "exchange": exchange_msg,
            "options": g.exchange_info,
        })

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if tg_token and tg_chat_id:
        # success_cnt 代表今天已完成签到的账号数量
        cur_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        chunks = build_report(results, success_cnt, len(cookies), cur_time)
        telegram_push(tg_token, tg_chat_id, chunks)

if __name__ == '__main__':
    main()
