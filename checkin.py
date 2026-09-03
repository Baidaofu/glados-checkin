#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 GLaDOS 自动签到 (多账号独立兑换策略 - GLADOS_COOKIE 一行一个账号)
"""

import requests
import json
import os
import sys
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

def telegram_push(token, chat_id, title, content):
    if not token or not chat_id: return
    try:
        import re
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        text = f"<b>{title}</b>\n\n{content}"
        text = re.sub(r"<(?!\/?(b)\b)[^>]+>", "", text)
        
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=5)
        log("✅ Telegram 推送成功")
    except Exception as e:
        log(f"❌ Telegram 推送失败: {e}")

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
        user_result = (
            f"👤 {g.email}\n\n"
            f"当前积分: {g.points} ({g.points_change})\n\n"
            f"剩余天数: {g.left_days} 天\n\n"
            f"签到结果: {msg}\n\n"
            f"兑换策略: {plan_desc}\n\n"
            f"自动兑换: {exchange_msg}\n\n"
            f"🎁 兑换选项:\n\n"
            f"{g.exchange_info}"
        )
        results.append(user_result)

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if tg_token and tg_chat_id:
        # 此时 success_cnt 代表今天已经完成签到的账号数量
        title = f"GLaDOS签到: 成功{success_cnt}/{len(cookies)}"
        cur_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        content = "\n\n".join(results) + f"\n\n时间: {cur_time}"
        telegram_push(tg_token, tg_chat_id, title, content)

if __name__ == '__main__':
    main()
