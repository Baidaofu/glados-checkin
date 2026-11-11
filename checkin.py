#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 GLaDOS 自动签到 (多账号独立兑换策略 - GLADOS_COOKIE 一行一个账号)
"""

import hashlib
import random
import requests
import json
import os
import sys
import html
import re
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    BEIJING_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # 无 tzdata 时退回固定 UTC+8
    BEIJING_TZ = timezone(timedelta(hours=8))

# 修复 Windows Unicode 输出问题
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置 =================

# 兑换档位 -> 所需积分
PLAN_REQUIREMENTS = {"plan100": 100, "plan200": 200, "plan500": 500}

# 会话 Cookie 名: 站点现签发 gld:sess; koa:sess 为旧版, 服务端已废弃
CURRENT_SESS = "gld:sess"
DEPRECATED_SESS = "koa:sess"
SESS_NAMES = (CURRENT_SESS, DEPRECATED_SESS)

# gld:sess 值形如 gld_ + 48 位小写 hex; gld:sess.sig 为 27 位 base64url
SESS_PREFIX = "gld_"
SESS_HEX_LEN = 48
SIG_MIN_LEN = 20
# 表示"关闭兑换"的写法
OFF_VALUES = {"off", "none", "false", "0", "no", "disable", "disabled", "关闭"}
OFF_PLAN = "off"


def plan_label(plan):
    """展示用策略标签: #off / #plan100 / #plan200 / #plan500

    行尾空着与 #off 行为一致(都只签到不兑换), 统一显示为 #off。
    """
    return "#off" if (plan is None or plan == OFF_PLAN) else f"#{plan}"

# 2026 签到风控: 服务端要求先 GET /api/user/checkin/challenge 拿 nonce,
# 距签发 >= 2500ms 后用固定盐算 SHA-256(nonce + ".glados-observe-v1") 作为 proof 一起提交。
# 不提交 challenge/proof 时接口照样返回成功, 但只发 1 积分(降权)。
CHALLENGE_SALT = "glados-observe-v1"
# 服务端硬门槛 2500ms, 前端自己也补到 2500ms, 这里作为绝对下限
CHALLENGE_WAIT_FLOOR = 2.5


def _env_float(name, default):
    """读浮点环境变量, 非法值回退默认(此时 log() 还没定义, 用 print)"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[config] 环境变量 {name}='{raw}' 不是数字, 使用默认值 {default}")
        return default


# 每个账号拿到 challenge 后随机等待的区间(秒)。
# 同一时刻批量签到最容易被打上"多账号同源"标签, 抖动越大越像散落的真人操作。
# 想压到最短就设 2.6/2.8; 想让间隔更像人, 把 MAX 拉到 15~30 也完全没问题(challenge ttl 默认 300s)。
CHALLENGE_WAIT_MIN = max(CHALLENGE_WAIT_FLOOR, _env_float("GLADOS_CHALLENGE_WAIT_MIN", 2.8))
CHALLENGE_WAIT_MAX = max(CHALLENGE_WAIT_MIN, _env_float("GLADOS_CHALLENGE_WAIT_MAX", 9.0))

# 相邻两个账号之间的随机间隔(秒), 避免多账号在同一秒内连刷
ACCOUNT_GAP_MIN = max(0.0, _env_float("GLADOS_ACCOUNT_GAP_MIN", 8.0))
ACCOUNT_GAP_MAX = max(ACCOUNT_GAP_MIN, _env_float("GLADOS_ACCOUNT_GAP_MAX", 35.0))

# 请求头格式的 cookie 本身不带域名, 默认走这个(可用 GLADOS_DOMAIN 覆盖;
# JSON 导出会自动读 domain 字段, 优先级高于这里)
DEFAULT_DOMAIN_ENV = os.environ.get("GLADOS_DOMAIN", "glados.network")

# 调试开关: GLADOS_DEBUG=1 时打印原始接口响应(已脱敏), 用来核对积分构成/排查异常
DEBUG = os.environ.get("GLADOS_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Cookie-Editor 完整导出里除 gld:sess/.sig 外还会带 gld:accts/.sig (多账号身份列表),
# 新版登录(邮箱/验证码)都会下发; 只带 gld:sess 也仍可鉴权, 但建议整段照抄。
ACCTS_SESS = "gld:accts"

DOMAINS = [
    "https://glados.cloud",
    "https://railgun.info",
    "https://glados.rocks",
    "https://glados.network",
]

DEFAULT_USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36')

# 多账号防关联: 每个账号固定一个 UA, 由 cookie 哈希决定。
# 为什么不用每次随机: 同一个账号 UA 每次变会直接触发码农风的 device-mismatch 校验;
# 固定但"账号间互不相同"既能打散指纹, 又不会踩风控。
# 全部保持 Windows + Chromium 家族, 和原来能正常签到的 UA 同类, 风险最低。
UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
]
# 设 GLADOS_UA_ROTATE=0 可退回所有人统一用 DEFAULT_USER_AGENT
UA_ROTATE = os.environ.get("GLADOS_UA_ROTATE", "1").strip().lower() not in OFF_VALUES

HEADERS = {
    'User-Agent': DEFAULT_USER_AGENT,
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
}

# ================= 工具函数 =================

def now_beijing(fmt="%Y-%m-%d %H:%M:%S"):
    """北京时间。GitHub Actions runner 是 UTC, 直接用 datetime.now() 会差 8 小时"""
    return datetime.now(BEIJING_TZ).strftime(fmt)

def log(msg):
    print(f"[{now_beijing()}] {msg}")


def checkin_proof(nonce: str) -> str:
    """前端 630 模块 checkinProof: hex(sha256(nonce + "." + 固定盐))"""
    if not nonce:
        return ""
    return hashlib.sha256(f"{nonce}.{CHALLENGE_SALT}".encode("utf-8")).hexdigest()


def pick_ua(cookie: str) -> str:
    """按 cookie 哈希固定选 UA: 同账号跨运行永远一致, 不同账号彼此不同"""
    if not UA_ROTATE:
        return DEFAULT_USER_AGENT
    digest = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
    return UA_POOL[int(digest[:8], 16) % len(UA_POOL)]

def diagnose_cookie(cookie: str):
    """本地体检 gld:sess Cookie 格式, 返回问题列表 (空列表 = 格式正常)

    服务端对所有鉴权失败一律返回 code:-2 没有权限, 不区分原因,
    因此过期/复制不全/格式错误只能靠本地检查给出针对性提示。
    """
    issues = []
    if not cookie:
        return ["Cookie 为空"]

    # 逐段解析 name=value
    parts = {}
    for seg in cookie.split(';'):
        seg = seg.strip()
        if '=' in seg:
            k, v = seg.split('=', 1)
            parts[k.strip()] = v.strip()

    sess = parts.get(CURRENT_SESS)
    sig = parts.get(CURRENT_SESS + '.sig')

    if sess is None:
        issues.append(f"缺少 {CURRENT_SESS}")
    else:
        if not sess:
            issues.append(f"{CURRENT_SESS} 值为空")
        elif not sess.startswith(SESS_PREFIX):
            issues.append(
                f"{CURRENT_SESS} 值不以 '{SESS_PREFIX}' 开头( actual: '{sess[:8]}...'), "
                f"很可能复制不完整")
        else:
            hexpart = sess[len(SESS_PREFIX):]
            if len(hexpart) != SESS_HEX_LEN:
                issues.append(
                    f"{CURRENT_SESS} 长度异常: 期望 {SESS_PREFIX}后 {SESS_HEX_LEN} 位, "
                    f"实际 {len(hexpart)} 位(差 {len(hexpart) - SESS_HEX_LEN:+d} 位), "
                    f"多半是复制时漏字符")
            elif not all(c in '0123456789abcdef' for c in hexpart):
                issues.append(
                    f"{CURRENT_SESS} 含非法字符(期望 {SESS_PREFIX}后全是小写 hex "
                    f"0-9a-f), 复制时可能被截断或混入其它内容")

    if sig is None:
        issues.append(f"缺少 {CURRENT_SESS}.sig, 服务端会拒绝鉴权")
    elif len(sig) < SIG_MIN_LEN:
        issues.append(
            f"{CURRENT_SESS}.sig 过短({len(sig)} 位, 期望约 27 位), 复制可能不完整")

    # gld:accts 是新版登录额外下发的账号列表 Cookie, 只校验格式, 缺失不报错
    accts = parts.get(ACCTS_SESS)
    if accts is not None:
        if not accts.startswith(SESS_PREFIX):
            issues.append(
                f"{ACCTS_SESS} 值不以 '{SESS_PREFIX}' 开头( actual: '{accts[:8]}...'), "
                f"复制可能不完整")
        elif len(accts) - len(SESS_PREFIX) != SESS_HEX_LEN:
            issues.append(
                f"{ACCTS_SESS} 长度异常: 期望 {SESS_PREFIX}后 {SESS_HEX_LEN} 位, "
                f"实际 {len(accts) - len(SESS_PREFIX)} 位")
        if ACCTS_SESS + ".sig" not in parts:
            issues.append(f"有 {ACCTS_SESS} 但缺少 {ACCTS_SESS}.sig, 建议整段重新复制")

    return issues


def _split_plan(entry: str):
    """把 'cookie#plan200' 拆成 (cookie, 'plan200'); 没有 # 后缀返回 (entry, None)"""
    if '#' in entry:
        body, plan_raw = entry.rsplit('#', 1)
        return body.strip(), plan_raw
    return entry.strip(), None


def normalize_domain(dom):
    """把 Cookie-Editor 的 domain 字段规整成 https://host"""
    if not dom:
        return None
    dom = str(dom).strip().lstrip('.').lower()
    if not dom:
        return None
    if not dom.startswith(('http://', 'https://')):
        dom = 'https://' + dom
    return dom.rstrip('/')


def _json_cookie_pairs(obj):
    """从 Cookie-Editor 导出结构里取出 [(name, value)] 和 gld:sess 所在域名

    支持三种形态:
    - 完整导出: [{"name": "gld:sess", "value": "...", "domain": "glados.network"}, ...]
    - 单个 cookie 对象: {"name": "...", "value": "...", "domain": "..."}
    - 旧版 {"token": "..."} 或 {"gld:sess": "...", ...} 映射
    """
    if isinstance(obj, dict):
        if isinstance(obj.get('token'), str):
            return [('koa:sess', obj['token'])], None
        if 'name' in obj and 'value' in obj:
            return [(str(obj['name']), str(obj['value']))], obj.get('domain')
        pairs = [(k, v) for k, v in obj.items() if isinstance(v, str)]
        return pairs, None

    pairs, dom = [], None
    if isinstance(obj, list):
        for item in obj:
            if not isinstance(item, dict):
                continue
            name, value = item.get('name'), item.get('value')
            if not name or value is None:
                continue
            pairs.append((str(name), str(value)))
            if dom is None and str(name) == CURRENT_SESS:
                dom = item.get('domain')
    return pairs, dom


def extract_cookie(raw: str):
    """归一化 cookie, 返回 (cookie字符串, 建议优先访问的域名或 None)

    支持:
    - 请求头格式: gld:sess=...; gld:sess.sig=...; gld:accts=...; gld:accts.sig=...
    - Cookie-Editor 完整 JSON 导出 (数组/对象), 顺便取回 cookie 所属域名
    - 旧版 {"token": "..."} / 裸 JWT -> 补 koa:sess 前缀
    """
    if not raw:
        return None, None
    raw = raw.strip()

    if raw.startswith(('{', '[')):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if obj is not None:
            pairs, dom = _json_cookie_pairs(obj)
            if pairs:
                cookie = '; '.join(f'{n}={v}' for n, v in pairs)
                for issue in diagnose_cookie(cookie):
                    log(f"⚠️ {issue}")
                if CURRENT_SESS not in dict(pairs):
                    log(f"⚠️ JSON 里没有 {CURRENT_SESS}, 该账号无法签到")
                return cookie, normalize_domain(dom)

    for name in SESS_NAMES:
        if f'{name}=' in raw or f'{name}.sig=' in raw:
            if name == CURRENT_SESS:
                # 先做本地格式体检, 给出针对性的复制/失效提示
                for issue in diagnose_cookie(raw):
                    log(f"⚠️ {issue}")
            else:
                log(f"⚠️ 检测到旧版 {DEPRECATED_SESS} Cookie, 站点已改用 {CURRENT_SESS} 并废弃旧格式, "
                    f"该账号签到必然失败; 请重新登录 glados.cloud 复制新的 {CURRENT_SESS} Cookie")
            return raw, None
    if raw.startswith('{'):
        try:
            return CURRENT_SESS + '=' + json.loads(raw).get('token'), None
        except Exception:
            pass
    if raw.count('.') == 2 and '=' not in raw and len(raw) > 50:
        return CURRENT_SESS + '=' + raw, None
    return raw, None

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
    """解析一个账号: cookie#策略。行尾无 # 后缀 = 只签到不兑换

    返回 (cookie, plan, domain)。cookie 可以是请求头格式, 也可以是 Cookie-Editor 的 JSON。
    """
    body, plan_raw = _split_plan(entry)
    cookie, domain = extract_cookie(body)
    plan = parse_plan(plan_raw) if plan_raw is not None else None
    return cookie, plan, domain


def get_plan_overrides():
    """GLADOS_PLANS: 一行一个策略, 按 GLADOS_COOKIE 里账号的先后顺序对应

    - 空行也算一个位置(表示该账号不设置策略)
    - # 开头的行是注释, 不占位置
    - 行内 #planXXX 优先级更高
    """
    raw = os.environ.get("GLADOS_PLANS", "")
    if not raw.strip():
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith('#'):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return out


def get_cookies():
    """解析 GLADOS_COOKIE, 支持三种写法:

    1) 一行一个账号: gld:sess=...; gld:sess.sig=...#plan200
    2) 一整段 Cookie-Editor JSON 数组 (多行也行) = 1 个账号, 末尾可跟 #策略
    3) 数组套数组 [[...],[...]] = 多个账号, 每项一个 Cookie-Editor 导出

    # 开头的行是注释。返回 [(cookie, plan, lineno, domain)]。
    """
    raw = os.environ.get("GLADOS_COOKIE", "")
    if not raw.strip():
        log("❌ 未配置 GLADOS_COOKIE")
        return []

    entries = []  # [(raw_entry, lineno)]
    body, plan_raw = _split_plan(raw)
    obj, parsed = None, False
    try:
        obj = json.loads(body)
        parsed = True
    except Exception:
        pass

    if parsed and isinstance(obj, list) and obj and all(isinstance(x, list) for x in obj):
        # [[cookie_obj...], [cookie_obj...]] -> 多账号
        suffix = f'#{plan_raw}' if plan_raw else ''
        for i, one in enumerate(obj, 1):
            entries.append((json.dumps(one, ensure_ascii=False) + suffix, i))
    elif parsed:
        # 一整段 JSON = 1 个账号
        entries.append((raw.strip(), 1))
    else:
        for lineno, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            entries.append((line, lineno))

    default_domain = normalize_domain(DEFAULT_DOMAIN_ENV)
    overrides = get_plan_overrides()

    accounts = []
    for idx, (entry, lineno) in enumerate(entries):
        cookie, plan, domain = parse_account(entry)
        if not cookie:
            log(f"⚠️ 配置第 {lineno} 行解析为空, 已跳过")
            continue
        # 行内 #planXXX 优先; 否则取 GLADOS_PLANS 里同位置的策略
        if plan is None and idx < len(overrides) and overrides[idx]:
            plan = parse_plan(overrides[idx])
            log(f"🎛 第 {idx + 1} 个账号(配置第 {lineno} 行)使用 GLADOS_PLANS 策略: {plan}")
        # 域名优先级: GLADOS_DOMAIN_<行号> > JSON 里的 domain > GLADOS_DOMAIN > glados.network
        domain = (normalize_domain(os.environ.get(f"GLADOS_DOMAIN_{lineno}"))
                  or domain or default_domain)
        log(f"🔗 配置第 {lineno} 行将优先访问 {domain}")
        accounts.append((cookie, plan, lineno, domain))
    return accounts

# ================= 核心逻辑 =================

class GLaDOS:
    def __init__(self, cookie, ua=None, domain=None):
        self.cookie = cookie
        self.ua = ua or pick_ua(cookie)
        # Cookie 是从哪个域登录下发的就优先打哪个域, 避免跨域 token 导致降权
        self.domain = normalize_domain(domain) or DOMAINS[0]
        self.email = "?"
        self.left_days = "?"
        self.points = "0"
        self.points_change = "?"
        self.challenge_ok = None  # None=未尝试 / True=拿到 nonce / False=降级为无 proof 请求
        self.points_raw = None
        self.vip = None
        self.level = None
        self.streak = None
        self.exchange_info = ""
        
    def _domain_order(self):
        """优先当前/配置的域名, 再依次回退其它入口"""
        return [self.domain] + [d for d in DOMAINS if d != self.domain]

    def req(self, method, path, data=None):
        for d in self._domain_order():
            try:
                url = f"{d}{path}"
                h = HEADERS.copy()
                h['User-Agent'] = self.ua
                h['Cookie'] = self.cookie
                h['Origin'] = d
                h['Referer'] = f"{d}/console/checkin"
                
                if method == 'GET':
                    resp = requests.get(url, headers=h, timeout=10)
                else:
                    resp = requests.post(url, headers=h, json=data, timeout=10)
                
                if DEBUG:
                    log(f"📡 {method} {d}{path} -> HTTP {resp.status_code}")
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
            self.vip = d.get('vip')
            self.level = d.get('level')
            return True
        return False

    def get_points(self):
        res = self.req('GET', '/api/user/points')
        self.points_raw = res
        if res and 'points' in res:
            self.points = str(res.get('points', '0')).split('.')[0]
            self.streak = res.get('streak')
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

    def solve_challenge(self):
        """取签到 challenge, 随机等待后返回 nonce/proof

        前端 solveChallenge: 距 challenge 签发满 2500ms 才提交。这里在拿到响应后
        随机等 CHALLENGE_WAIT_MIN~MAX 秒(下限已钳到 2.5s), 含网络耗时服务端看到的时间差
        必然达标; 实际等待会再按服务端返回的 ttl 钳一次, 不会等到 nonce 过期。
        """
        res = self.req('GET', '/api/user/checkin/challenge')
        if not (isinstance(res, dict) and res.get('code') == 0 and res.get('nonce')):
            self.challenge_ok = False
            log("⚠️ 未获取到签到 challenge, 服务端可能只发 1 积分 (降权)")
            return {}
        self.challenge_ok = True
        nonce = str(res['nonce'])
        wait = random.uniform(CHALLENGE_WAIT_MIN, CHALLENGE_WAIT_MAX)
        ttl = res.get('ttl')
        if isinstance(ttl, (int, float)) and ttl > 0:
            # 前端同样留 15s 余量后再判过期
            wait = min(wait, max(CHALLENGE_WAIT_FLOOR, ttl / 1000.0 - 15))
        time.sleep(wait)
        log(f"🔐 challenge nonce={nonce[:8]}… 随机等待 {wait:.1f}s 后提交 proof")
        return {'nonce': nonce, 'proof': checkin_proof(nonce)}

    def checkin(self):
        payload = {'token': 'glados.cloud'}
        challenge = self.solve_challenge()
        if challenge:
            # challenge 与签到必须在同一域名签发, 否则 nonce 校验不过
            payload['token'] = self.domain.split('//')[-1].strip('/') or 'glados.cloud'
            payload.update(challenge)
        return self.req('POST', '/api/user/checkin', payload)

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


# 三态: 正常签到=绿色对钩 / 检测到异常=黄色警告 / 签到失败=红色叉号
STATUS_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def _status(r):
    """兼容旧结构: 没写 status 的记录按 ok 布尔退化为 ok/fail"""
    st = r.get("status")
    if st in STATUS_ICON:
        return st
    return "ok" if r.get("ok") else "fail"


def _warn_tag(r):
    """折叠状态下也能看到的异常短标签"""
    reasons = r.get("reasons") or []
    return reasons[0] if reasons else "疑似异常"

def _rich_block(r):
    """单个账号的富文本卡片 (Markdown <details> 可折叠区块)"""
    st = _status(r)
    icon = STATUS_ICON[st]
    email = _md(r['email'], 80)
    # 用图标直接表示结果, 折叠起来也能一眼看出正常/异常/失败
    if st == "fail":
        summary = f"{icon} {email} · 签到失败"
    else:
        summary = (f"{icon} {email} · {_md(r['left_days'], 20)}天 · "
                   f"{_md(r['points'], 20)}分 · {_md(r['plan'], 20)}")
        if st == "warn":
            summary += f" · {_md(_warn_tag(r), 40)}"
    opts = "\n".join("- " + ln for ln in str(r["options"]).splitlines() if ln.strip()) or "- 无"
    body = (
        f"- 🎯 签到: {icon} {_md(r['msg'], 200)}\n"
        f"- 💰 积分: {_md(r['points'], 20)} ({_md(r['change'], 20)})\n"
        f"- 📆 剩余: {_md(r['left_days'], 20)} 天\n"
        f"- 🎛 策略: {_md(r['plan'], 20)}\n"
        f"- 🔁 兑换: {_md(r['exchange'], 150)}\n"
    )
    # 失败: 补排障提示，省略无意义的可兑换选项
    if st == "fail":
        body += f"\n**🔧 排障**: {_md(r['hint'], 200)}\n"
    else:
        # 异常(如降权): 也给出原因，但保留可兑换选项
        if st == "warn" and r.get("hint"):
            body += f"\n**🔧 提示**: {_md(r['hint'], 200)}\n"
        body += f"\n**🎁 可兑换选项**\n\n{opts}\n"
    return f"<details><summary>{summary}</summary>\n\n{body}\n</details>"

def _legacy_block(r):
    """单个账号的旧版 HTML 卡片 (可折叠引用, 兜底用)"""
    e = html.escape
    st = _status(r)
    icon = STATUS_ICON[st]
    if st == "fail":
        head = f"{icon} {e(_clip(r['email'], 100))} · 签到失败"
    else:
        head = (f"{icon} {e(_clip(r['email'], 100))} · {e(_clip(r['left_days'], 20))}天 · "
                f"{e(_clip(r['points'], 20))}分 · {e(_clip(r['plan'], 20))}")
        if st == "warn":
            head += f" · {e(_clip(_warn_tag(r), 40))}"
    lines = [
        head,
        f"🎯 签到: {icon} {e(_clip(r['msg'], 200))}",
        f"💰 积分: {e(_clip(r['points'], 20))} ({e(_clip(r['change'], 20))})",
        f"📆 剩余: {e(_clip(r['left_days'], 20))} 天",
        f"🎛 策略: {e(_clip(r['plan'], 20))}",
        f"🔁 兑换: {e(_clip(r['exchange'], 150))}",
    ]
    if st == "fail":
        lines.append(f"🔧 排障: {e(_clip(r['hint'], 200))}")
    else:
        if st == "warn" and r.get("hint"):
            lines.append(f"🔧 提示: {e(_clip(r['hint'], 200))}")
        lines.append(f"🎁 可兑换选项:\n{e(_clip(r['options'], 600))}")
    return "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"

def explain_points(g):
    """按前端 renderPointsBreakdown 的算法, 拆出本次签到的加分来源

    签到分 = 基础分(服务端给, 前端只显示 +X) + 套餐加成 + 连签加成 + 线缆质量加成。
    注意 /api/user/iqi 的真实结构是 {"code":0,"data":{"quality":{"score":..}}}。
    """
    perk = {31: 1.0, 41: 1.5, 51: 2.0}.get(g.vip, 0.0)
    streak = g.streak or 0
    try:
        streak = int(float(streak))
    except (TypeError, ValueError):
        streak = 0
    streak_bonus = 2 if streak > 14 else (1 if streak > 7 else 0)

    iqi = g.req('GET', '/api/user/iqi') or {}
    iqi_data = (iqi.get('data') or {}) if isinstance(iqi, dict) else {}
    score = (iqi_data.get('quality') or {}).get('score')
    cable = 0
    if score:
        cable = 8 if score < 80 else 5 if score < 85 else 3 if score < 90 else 2 if score <= 95 else 1

    # network-insight 里有服务端认定的 cablePoints 和测量时间, 比前端估算准
    insight = g.req('GET', '/api/user/network-insight') or {}
    ins = (insight.get('data') or {}) if isinstance(insight, dict) else {}
    cable_points = (ins.get('current') or {}).get('cablePoints')
    measured = ins.get('updatedAt')

    plan = {31: "Pro", 41: "Team", 51: "Enterprise"}.get(g.vip, f"Free/vip={g.vip}")
    parts = [f"套餐 {plan} +{perk:g}", f"连签 {streak}天 +{streak_bonus}",
             f"线缆 score={score if score is not None else '?'} +{cable}"
             + (f" (服务端 cablePoints={cable_points})" if cable_points is not None else "")]
    if measured:
        try:
            measured_at = datetime.fromtimestamp(measured / 1000, BEIJING_TZ)
            parts.append(f"质量测量于 {measured_at:%Y-%m-%d %H:%M}")
            if measured_at.strftime('%Y-%m-%d') != now_beijing('%Y-%m-%d'):
                parts.append("⚠️ 当天还没有测量记录, 线缆分可能拿不到")
        except Exception:
            pass
    else:
        parts.append("⚠️ 服务端无质量测量记录")
    return " · ".join(parts) + " (基础分由服务端另给)"


def _points_delta(value):
    """把 "+10"/"-1"/"1.5" 这类积分变化转成 float, 解析失败返回 None"""
    try:
        return float(str(value).strip().lstrip('+'))
    except (TypeError, ValueError):
        return None


def build_report(results, success_cnt, total, cur_time):
    """生成 [(富文本Markdown, 旧版HTML), ...] 分块消息列表
    富文本走 sendRichMessage (可折叠 details 区块, 免费无需会员)
    旧版走 sendMessage HTML 可折叠引用, 作为自动兜底
    """
    n_ok = sum(1 for r in results if _status(r) == "ok")
    n_warn = sum(1 for r in results if _status(r) == "warn")
    n_fail = sum(1 for r in results if _status(r) == "fail")
    if n_warn or n_fail:
        # 有异常/失败时把三态分开报, 避免 ⚠️ 被算进 ✅ 里看不清
        rich_title = f"# 🚀 GLaDOS 签到 ✅ {n_ok} · ⚠️ {n_warn} · ❌ {n_fail}"
        legacy_title = (f"🚀 <b>GLaDOS 签到</b>  ✅ {n_ok} · "
                        f"⚠️ {n_warn} · ❌ {n_fail}")
    else:
        rich_title = f"# 🚀 GLaDOS 签到 ✅ {n_ok}/{total}"
        legacy_title = f"🚀 <b>GLaDOS 签到</b>  ✅ {n_ok}/{total}"
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

    # 打乱执行顺序: 固定顺序 + 固定时间间隔是多账号关联最明显的特征。
    # 推送里再按配置行号还原顺序, 不影响阅读。
    order = list(range(len(cookies)))
    random.shuffle(order)
    if len(cookies) > 1:
        log(f"🎲 本次执行顺序: {[cookies[i][2] for i in order]} (配置行号)")

    for pos, idx in enumerate(order):
        cookie, plan, lineno, cookie_domain = cookies[idx]
        if pos:
            gap = random.uniform(ACCOUNT_GAP_MIN, ACCOUNT_GAP_MAX)
            log(f"⏳ 账号之间随机间隔 {gap:.1f}s")
            time.sleep(gap)

        # 可选: GLADOS_USER_AGENT_<行号> 强制指定某账号 UA(填浏览器里真实的 UA 可规避 device-mismatch)
        g = GLaDOS(cookie,
                   ua=os.environ.get(f"GLADOS_USER_AGENT_{lineno}") or None,
                   domain=cookie_domain)
        # 鉴权失败时 email 取不到, 用配置行号定位是哪个账号出问题
        g.lineno = lineno
        if DEBUG:
            log(f"🧪 第 {lineno} 行 UA={g.ua}")

        checkin_res = g.checkin()
        g.get_status()
        g.get_points()

        if DEBUG:
            try:
                log("📊 checkin 原始响应: " + json.dumps(checkin_res, ensure_ascii=False)[:1200])
            except Exception:
                log(f"📊 checkin 原始响应: {checkin_res}")
            try:
                hist = ((g.points_raw or {}).get('history') or [])[:3]
                log("📊 points.history 前 3 条: " + json.dumps(hist, ensure_ascii=False)[:800])
            except Exception:
                pass
            log("🧮 加分构成: " + explain_points(g))
            trend = g.req('GET', '/api/user/points/trend?days=14')
            try:
                days = ((trend or {}).get('data') or {}).get('days') or []
                log("📈 近 14 天积分: " + json.dumps(days[-14:], ensure_ascii=False)[:900])
            except Exception:
                log(f"📈 趋势接口异常: {trend}")
        
        # --- 核心判定逻辑修改 ---
        raw_msg = checkin_res.get('message', 'Failure') if checkin_res else "Network Error"
        
        ok, hint = False, ""
        warn = False
        reasons = []
        code = checkin_res.get('code') if checkin_res else None

        # 鉴权失败: Cookie 失效/格式废弃, 换域名重试也没用
        # 注意: glados.* 返回中文 "没有权限", 只有 railgun.info 返回 "No permission",
        # 因此按 code 判定, 不能匹配 permission 字样
        if code == -2:
            msg = "鉴权失败: Cookie 无效"
            # 服务端不区分原因, 用本地体检结果给出针对性提示
            diag = diagnose_cookie(cookie)
            if diag:
                hint = "格式问题: " + "；".join(diag)
            else:
                hint = (f"格式无误, 判定为 Cookie 已过期或失效; "
                        f"请重新登录 glados.cloud 复制新的 {CURRENT_SESS} + {CURRENT_SESS}.sig")
        # 风控拦截: 需重新登录刷新设备指纹
        elif code == 4:
            msg = "被判定为自动签到"
            hint = f"服务端 reason={checkin_res.get('reason')}; 请手动登录一次后再运行"
        # 只要 message 包含 "Checkin" (首次成功) 或 "observation logged" (今日已签到)
        # 都代表今日已经签到成功了，标题显示 1/1
        elif ("checkin" in raw_msg.lower()
              or "observation logged" in raw_msg.lower()):
            success_cnt += 1
            ok = True
            # 区分首次签到与重复签到, 避免每天都显示同一句英文。
            # 服务端文案大小写/版本不统一("Checkin! got X Points" / "checkin repeats! please try tomorrow"
            # / "today's observation logged"), 统一转小写判断, 首次的特征是带 "got" 且不带 "repeat"
            low = raw_msg.lower()
            first = "got" in low and "repeat" not in low
            msg = "签到成功" if first else "今日已签到 (重复提交)"
            # 降权自检: 正常签到 >= 10 分, 只加 1 分说明 challenge/proof 没通过
            # 负数变化是兑换/调整, 不是签到降权; 只有 0~1 分才算"没拿到正常签到积分"
            delta = _points_delta(g.points_change)
            if delta is not None and 0 <= delta <= 1:
                warn = True
                reasons.append(f"仅 +{delta:g} 分, 疑似降权")
            # challenge 没拿到就发了降级请求 → 即使这次没降权也要提醒
            if g.challenge_ok is False:
                warn = True
                reasons.append("未获取到 challenge, 已降级请求")
            if warn:
                hint = ("本次签到被服务端降权/走降级请求: 检查日志里有没有 "
                        "🔐 challenge 那行; 如果一直 +1 分, 该账号可能被风控, 建议手动登录一次或换 UA")
        else:
            msg = f"{raw_msg}"
            hint = "接口未返回成功状态, 请检查日志或稍后重试"

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
                if isinstance(ex_res, dict):
                    exchange_msg = ex_res.get('message', '提交失败')
                    if ex_res.get('code') not in (0, None):
                        warn = True
                        reasons.append(f"兑换失败: {exchange_msg}")
                else:
                    # 网络抖动/接口异常时可能返回 None, 不能让整份报告崩掉
                    exchange_msg = "兑换请求失败(网络或接口异常)"
                    warn = True
                    reasons.append("兑换请求无响应")
                g.get_status()
                g.get_points()

        # 异常原因统一追加到签到文案, 折叠标题里也会显示第一条
        if warn and reasons:
            msg += " — " + "；".join(dict.fromkeys(reasons))

        # 展示用策略: 空着与 #off 行为一致, 统一显示 #off
        plan_desc = plan_label(plan)
        results.append({
            "email": g.email if g.email != "?" else f"配置第 {lineno} 行 (Cookie 无效)",
            "points": g.points,
            "change": g.points_change,
            "left_days": g.left_days,
            "msg": msg,
            "plan": plan_desc,
            "exchange": exchange_msg,
            "options": g.exchange_info,
            "ok": ok,
            "warn": warn,
            "status": "fail" if not ok else ("warn" if warn else "ok"),
            "reasons": reasons,
            "hint": hint,
            "lineno": lineno,
        })

    # 执行顺序被打乱过, 推送前按配置行号还原
    results.sort(key=lambda r: r["lineno"])

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if tg_token and tg_chat_id:
        # success_cnt 代表今天已完成签到的账号数量
        cur_time = now_beijing('%Y-%m-%d %H:%M:%S') + " (北京时间)"
        chunks = build_report(results, success_cnt, len(cookies), cur_time)
        telegram_push(tg_token, tg_chat_id, chunks)

if __name__ == '__main__':
    main()
