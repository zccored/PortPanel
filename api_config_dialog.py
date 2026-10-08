# -*- coding: utf-8 -*-
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QGroupBox, QGridLayout,
                              QMessageBox, QTabWidget, QTextEdit, QProgressBar,
                              QFileDialog, QListWidget, QListWidgetItem, QWidget, QFormLayout,
                              QCheckBox, QComboBox, QSplitter, QTableWidget, QTableWidgetItem,
                              QHeaderView, QMenu, QDialogButtonBox, QFrame,QAbstractItemView,
                              QPlainTextEdit)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSize
from PySide6.QtGui import (QDragEnterEvent, QDropEvent, QFont, QColor, QPalette, QIcon,
                           QBrush)
import json
import os
import time
from datetime import datetime, timezone
import email.utils
import csv
import tempfile
import re
import uuid
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    curl_requests = None
from logger_manager import global_logger
import hashlib
import base64
try:
    import secure_store
except ImportError:
    secure_store = None
try:
    import cryptography
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from aliyun_client import get_local_sync_server, start_local_sync_server

# 尝试导入阿里云客户端
try:
    from aliyun_client import (get_global_aliyun_client, initialize_aliyun_services,
                              test_aliyun_connection, send_verification_bytes)
    HAS_ALIYUN = True
except ImportError:
    HAS_ALIYUN = False
    print("阿里云客户端模块不可用")


# ==================== 浏览器请求头工具（img_server 共用） ====================
# ⚠️ 本段被 img_server.py 的 DownloadWorker 直接引用。
#    改动默认值等于同时改动"下载器"的发包行为，务必同步验证两条链路。

DEFAULT_BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/125.0.0.0 Safari/537.36')

# 下载链路用的 Accept（与 img_server.DownloadWorker 历史行为一致）
DEFAULT_BROWSER_ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'

# 调试链路用的 Accept（调试的是 JSON API，不是网页）
DEFAULT_API_ACCEPT = 'application/json'

DEFAULT_BROWSER_HEADERS = {
    'User-Agent': DEFAULT_BROWSER_UA,
    'Accept': DEFAULT_BROWSER_ACCEPT,
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 敏感请求头关键字：命中则 UI 给出"敏感"提示，日志/展示时打码
SENSITIVE_HEADER_KEYWORDS = ('cookie', 'authorization', 'token', 'secret',
                             'apikey', 'api-key', 'x-api-key', 'password',
                             'session', 'auth', 'signature', 'credential')

# 值形如 ${ENV:NAME} 时，运行时从环境变量取值（避免把凭据明文写进配置与 .wbt）
_ENV_PLACEHOLDER_RE = re.compile(r'^\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}$')


def friendly_net_error(e) -> str:
    """把底层网络异常翻译成用户看得懂的短句（保持调试器原有的提示口径）。"""
    try:
        if isinstance(e, requests.exceptions.Timeout):
            return '请求超时(30秒)'
        if isinstance(e, requests.exceptions.ConnectionError):
            return '连接失败，请检查URL和网络'
        if isinstance(e, requests.exceptions.SSLError):
            return 'SSL 证书校验失败'
        if isinstance(e, requests.exceptions.TooManyRedirects):
            return '重定向次数过多'
    except Exception:
        pass
    return f'{type(e).__name__}: {e}'


def is_sensitive_header(name) -> bool:
    """键名命中敏感关键字则返回 True（大小写不敏感）。"""
    low = str(name or '').strip().lower()
    if not low:
        return False
    return any(kw in low for kw in SENSITIVE_HEADER_KEYWORDS)


def mask_header_value(name, value) -> str:
    """展示/记录用：敏感头的值打码，非敏感头原样返回。"""
    s = '' if value is None else str(value)
    if not s:
        return ''
    if not is_sensitive_header(name):
        return s
    if len(s) <= 8:
        return '*' * len(s)
    return f"{s[:4]}{'*' * 6}{s[-4:]} (长度 {len(s)})"


def describe_headers(headers) -> str:
    """一句话摘要：`accept, cookie(***), referer`。

    环境变量占位符会**顺手报出这个变量在当前进程里到底有没有** ——
    「填了 ${ENV:X} 却取不到真值」九成是没设置、或者设置完没重启程序，
    与其让用户自己猜，不如直接写在摘要里。
    """
    if not headers:
        return ''
    parts = []
    for k, v in headers.items():
        if not v:
            parts.append(f"{k}(空/不发)")
            continue
        sv = str(v).strip()
        m_env = _ENV_PLACEHOLDER_RE.match(sv)
        if m_env:
            env_name = m_env.group(1)
            env_val = os.environ.get(env_name)
            if env_val:
                parts.append(f"{k}(← 环境变量 {env_name} ✅ 长度 {len(env_val)})")
            else:
                parts.append(f"{k}(← 环境变量 {env_name} ❌ 当前程序没读到，实际不会发送)")
            continue
        if is_sensitive_header(k):
            parts.append(f"{k}(***)")
        elif _DATE_PLACEHOLDER_RE.match(sv):
            # 时间头：显示成「实时 GMT」这种一眼能懂的形式，而不是占位符原文
            m = _DATE_PLACEHOLDER_RE.match(sv)
            if m.group(1).upper() == 'TIMESTAMP':
                parts.append(f"{k}(实时时间戳)")
            else:
                parts.append(f"{k}(实时 {m.group(2) or DEFAULT_TIMEZONE_KEY})")
        else:
            parts.append(str(k))
    return ', '.join(parts)


def env_status_text(name) -> str:
    """某个环境变量在当前进程里到底有没有 —— 一句话结论。"""
    n = str(name or '').strip()
    if not n:
        return ''
    val = os.environ.get(n)
    if val:
        return f'✅ 环境变量 {n} 已就绪（长度 {len(val)}）'
    return f'❌ 环境变量 {n} 当前程序读不到 —— 请求不会带上它'


def describe_headers_sent(headers) -> str:
    """「实际发出去的请求头」摘要：敏感值打码但**保留长度**。

    与 describe_headers 的区别是它看的是**解析后**的字典，所以
    `cookie=cf_c******alue (长度 75)` 能一眼证明真值确实取到了。
    """
    if not headers:
        return ''
    parts = []
    for k, v in headers.items():
        s = '' if v is None else str(v)
        if not s:
            parts.append(f"{k}(空/不发)")
            continue
        shown = mask_header_value(k, s)
        if len(shown) > 80:
            shown = shown[:77] + '…'
        parts.append(f"{k}={shown}")
    return ', '.join(parts)


# 从注册表回读环境变量时要跳过的系统关键变量（动 PATH 这类东西风险大于收益）
_ENV_RELOAD_SKIP = {
    'path', 'pathext', 'psmodulepath', 'temp', 'tmp', 'comspec', 'systemroot',
    'windir', 'systemdrive', 'userprofile', 'appdata', 'localappdata',
    'programdata', 'programfiles', 'programfiles(x86)', 'programw6432',
    'homedrive', 'homepath', 'userdomain', 'username', 'userdnsdomain',
    'logonserver', 'number_of_processors', 'processor_architecture', 'os',
}


def reload_env_from_system():
    """把系统里持久化的环境变量重新读进当前进程（Windows 注册表）。

    为什么需要它：`setx` 和「系统属性 → 环境变量」写的是注册表，
    **已经在运行的进程不会自动看到**；有时连刚启动的程序也拿不到
    （Explorer 持有的环境块是旧的）。与其让用户反复重启，不如直接读注册表。

    只读 `HKCU\\Environment`（用户级，`setx` 的落点），跳过 PATH 等系统关键变量，
    只做新增/覆盖、绝不删除。

    返回 (发生变化或新读到的变量名列表, 给用户看的说明文本)。
    """
    try:
        import winreg
    except ImportError:
        return [], '当前系统不是 Windows，无法从注册表回读环境变量。'

    updated = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            i = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if not name or name.lower() in _ENV_RELOAD_SKIP:
                    continue
                sval = str(value)
                if kind == winreg.REG_EXPAND_SZ:
                    try:
                        sval = os.path.expandvars(sval)
                    except Exception:
                        pass
                if os.environ.get(name) != sval:
                    os.environ[name] = sval
                    updated.append(name)
    except OSError as e:
        return [], f'读取注册表失败：{e}'

    if not updated:
        return [], ('系统里没有读到新的用户级环境变量（注册表 HKCU\\Environment）。\n'
                    '如果还没执行过 setx，请先点「💾 立即生效」。')
    return updated, '✅ 已从系统重新读取 ' + str(len(updated)) + ' 个环境变量：' + \
        '、'.join(updated[:12]) + ('…' if len(updated) > 12 else '')


def env_is_persisted(name) -> bool:
    """这个变量是否已经**写进系统**（注册表 HKCU\\Environment），而不只是活在当前进程里。

    「💾 立即生效」只写进程内 `os.environ`，程序一关就没了 —— 这是
    "配好了 cookie，重启程序又 401" 的根因。这里用来把两种状态区分开。

    注意：本函数**只读**注册表。本程序不会替用户往系统里写环境变量
    （写 HKCU\\Environment 是杀软眼里的持久化行为，会被当成木马拦截）。
    """
    if not name:
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            val, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return False
    return bool(str(val))


def resolve_env_in_headers(extra):
    """把 ${ENV:NAME} 形式的头值替换为环境变量值。

    返回 (解析后的字典, 缺失的环境变量名列表)。
    - 非占位符的值原样保留；
    - 占位符但环境变量不存在（或为空串）→ 值为空串（等于"不发送该头"），名字进 missing。
      「设成空串」也算缺失：空的 cookie / token 没有任何意义，报出来比默默发空头有用。
    """
    resolved = {}
    missing = []
    for k, v in dict(extra or {}).items():
        kk = str(k).strip()
        if not kk:
            continue
        sv = '' if v is None else str(v)
        m = _ENV_PLACEHOLDER_RE.match(sv.strip())
        if m:
            env_name = m.group(1)
            env_val = os.environ.get(env_name)
            if not env_val:
                missing.append(env_name)
                resolved[kk] = ''
            else:
                resolved[kk] = env_val
        else:
            resolved[kk] = sv
    return resolved, missing


def setdefault_ci(headers, key, value):
    """大小写不敏感的 setdefault。

    HTTP 头名不区分大小写，而 dict.setdefault 区分 —— 用户填了 'content-type'
    时再 setdefault('Content-Type', ...) 会把两个头一起发出去，服务端行为未定义。
    """
    low = str(key).strip().lower()
    for k in headers:
        if str(k).strip().lower() == low:
            return headers
    headers[key] = value
    return headers


def build_browser_headers(extra=None, accept=None) -> dict:
    """构造一套完整、不冲突的浏览器请求头。

    合并语义（两条链路共用，不要各自实现）：
      1. 以 DEFAULT_BROWSER_HEADERS 为底；
      2. extra 中**同名（大小写不敏感）**的项会顶掉默认项 ——
         否则 requests 会把 'User-Agent' 与 'user-agent' 两条一起发出去；
      3. 值为空串/None 表示"显式不发这个头"（含屏蔽默认头）；
      4. accept 非空时替换默认 Accept（调试 JSON API 用 application/json）。

    注意：这里**不**解析 ${ENV:NAME}，调用方需要时先过 resolve_env_in_headers()。
    也**不**注入 Referer/Sec-Fetch-*：真实浏览器对这些头的取值随场景变化，
    硬编码反而可能与用户验证通过的 curl 不一致；需要时请在请求头表格里显式填写。
    """
    extra = dict(extra or {})
    taken = {}   # 小写键 -> 用户原始键
    for k in extra:
        kk = str(k).strip()
        if kk:
            taken[kk.lower()] = kk

    out = {}
    for k, v in DEFAULT_BROWSER_HEADERS.items():
        if k.lower() in taken:
            continue          # 被用户接管（可能是改值，也可能是显式置空）
        out[k] = accept if (accept and k == 'Accept') else v
    if accept and 'accept' not in taken:
        out['Accept'] = accept

    for k, v in extra.items():
        kk = str(k).strip()
        if not kk:
            continue
        if v is None or str(v) == '':
            continue          # 空值 = 不发这个头
        out[kk] = str(v)
    return out


# ==================== 动态时间头（Date / Time / Timestamp） ====================
# 用途：某些接口（尤其是带签名的 API）要求请求头里带"当前时间"，且服务端会校验
#       时间偏差。这里让用户选时区，值在**每次请求发出前**实时生成。
#
# ⚠️ 一个常见误解值得写在这里：HTTP 的 `Date` 响应头是**服务端**生成的，客户端
#    一般**不需要**发它。抓包看到的 `date: Thu, 01 Oct 2026 07:41:56 GMT` 是
#    Cloudflare 给的响应头，把它原样抄进请求头没有任何作用。
#    真正需要"发时间"的场景是：服务端要求 X-Date / X-Timestamp 做签名校验，
#    或者要求 If-Modified-Since 之类。那时用本功能才有意义。

TIMEZONE_CHOICES = [
    ('GMT（HTTP 标准，推荐）', 'GMT'),
    ('UTC', 'UTC'),
    ('本机时区', 'LOCAL'),
    ('UTC+08:00  北京 / 上海 / 香港 / 台北', 'Asia/Shanghai'),
    ('UTC+09:00  东京 / 首尔', 'Asia/Tokyo'),
    ('UTC+08:00  新加坡 / 吉隆坡', 'Asia/Singapore'),
    ('UTC+07:00  曼谷 / 雅加达 / 河内', 'Asia/Bangkok'),
    ('UTC+05:30  新德里 / 孟买', 'Asia/Kolkata'),
    ('UTC+04:00  迪拜', 'Asia/Dubai'),
    ('UTC+03:00  莫斯科 / 利雅得', 'Europe/Moscow'),
    ('UTC+02:00  雅典 / 赫尔辛基 / 开罗', 'Europe/Athens'),
    ('UTC+01:00  巴黎 / 柏林 / 罗马 / 马德里', 'Europe/Paris'),
    ('UTC+00:00  伦敦 / 都柏林 / 里斯本', 'Europe/London'),
    ('UTC-03:00  圣保罗 / 布宜诺斯艾利斯', 'America/Sao_Paulo'),
    ('UTC-05:00  纽约 / 多伦多 / 华盛顿', 'America/New_York'),
    ('UTC-06:00  芝加哥 / 墨西哥城', 'America/Chicago'),
    ('UTC-07:00  丹佛 / 菲尼克斯', 'America/Denver'),
    ('UTC-08:00  洛杉矶 / 温哥华 / 西雅图', 'America/Los_Angeles'),
    ('UTC-10:00  檀香山', 'Pacific/Honolulu'),
]
DEFAULT_TIMEZONE_KEY = 'GMT'

# 时间头的值形如 ${DATE:时区} / ${DATE:时区|格式} / ${TIME:时区} / ${TIMESTAMP}
_DATE_PLACEHOLDER_RE = re.compile(
    r'^\$\{(DATE|TIME|TIMESTAMP)(?::([^}|]+))?(?:\|([^}]*))?\}$', re.IGNORECASE)

# 键名属于"时间头"的（值栏会自动换成时区下拉框）
_TIME_HEADER_KEYS = {
    'date', 'time', 'timestamp', 'x-date', 'x-time', 'x-timestamp',
    'x-timestamp-ms', 'if-modified-since', 'if-unmodified-since',
    'last-modified', 'expires', 'if-range', 'x-date-ms',
}


def is_time_header(name) -> bool:
    """键名像时间头吗？（决定值栏用普通文本格还是时区下拉框）"""
    low = str(name or '').strip().lower()
    if not low:
        return False
    if low in _TIME_HEADER_KEYS:
        return True
    return low.endswith('-date') or low.endswith('-time')


def timezone_label(key) -> str:
    """把时区键翻译成下拉框里的显示名（找不到就用键本身）。"""
    k = str(key or DEFAULT_TIMEZONE_KEY).strip()
    for label, val in TIMEZONE_CHOICES:
        if val == k:
            return label
    return k


def _tzinfo_for(key):
    """把时区键解析成 tzinfo。返回 (tzinfo, 是否真 GMT)。解析不了就回退 UTC。"""
    k = str(key or DEFAULT_TIMEZONE_KEY).strip()
    up = k.upper()
    if up in ('GMT', 'UTC'):
        return timezone.utc, up
    if up == 'LOCAL':
        return datetime.now().astimezone().tzinfo, False
    try:
        from zoneinfo import ZoneInfo          # Python 3.9+，本机 tzdata 可用
        return ZoneInfo(k), False
    except Exception:
        return timezone.utc, 'GMT'


def make_date_value(tz_key=DEFAULT_TIMEZONE_KEY, fmt=None) -> str:
    """实时生成时间头的值。

    - fmt 为空且是 GMT/UTC → HTTP 标准 RFC 1123：`Thu, 01 Oct 2026 08:03:09 GMT`
    - fmt 为空且是其他时区 → `Thu, 01 Oct 2026 16:03:09 +0800`
    - fmt 非空 → 该时区下的 strftime 结果
    """
    tz, is_gmt = _tzinfo_for(tz_key)
    if fmt:
        try:
            return datetime.now(tz).strftime(fmt)
        except Exception:
            return datetime.now(tz).isoformat()
    # RFC 1123 规定时间头的时区后缀只能写 GMT —— UTC 也要写成 GMT，否则
    # 一些严格的服务端（如 AWS SigV4 系列）会判定格式非法。
    if is_gmt in ('GMT', 'UTC'):
        return email.utils.formatdate(time.time(), usegmt=True)
    return datetime.now(tz).strftime('%a, %d %b %Y %H:%M:%S %z')


def resolve_dynamic_in_headers(headers):
    """把 ${DATE:时区} / ${TIME:时区|格式} / ${TIMESTAMP} 展开成实时值。

    返回 (新字典, 说明列表)。说明用于写进调试详情，让用户看清实际发出去的是什么时间。
    """
    out = {}
    notes = []
    for k, v in dict(headers or {}).items():
        sv = '' if v is None else str(v)
        m = _DATE_PLACEHOLDER_RE.match(sv.strip())
        if not m:
            out[k] = sv
            continue
        kind = m.group(1).upper()
        tz_key = (m.group(2) or DEFAULT_TIMEZONE_KEY).strip()
        fmt = m.group(3)
        if kind == 'TIMESTAMP':
            out[k] = str(int(time.time()))
            notes.append(f'{k} = 当前 Unix 时间戳 {out[k]}')
        else:
            out[k] = make_date_value(tz_key, fmt)
            notes.append(f'{k} = {out[k]}（时区 {tz_key}）')
    return out, notes


def resolve_header_placeholders(headers):
    """一次性解析请求头里的所有占位符。

    顺序有讲究：先 ${ENV:名}（用户凭据），再 ${DATE:时区}（实时时间）——
    这样"从环境变量读一个格式串，再套时间"这种组合也能工作。

    返回 (最终字典, 缺失环境变量列表, 动态值说明列表)
    """
    env_resolved, missing = resolve_env_in_headers(headers)
    final, notes = resolve_dynamic_in_headers(env_resolved)
    return final, missing, notes


# ==================== 请求头文本批量解析（粘贴导入） ====================

# HTTP token 合法字符（RFC 7230），用来判断"这行左边像不像一个头名"
_HEADER_KEY_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~\-]+$")
# 从整条 curl 命令里抠请求头（-H/--header 单双引号或无引号；-b/--cookie 也当 cookie 头）
_CURL_HEADER_RE = re.compile(r"""(?:-H|--header)\s+(?:"([^"]*)"|'([^']*)'|(\S+))""")
_CURL_COOKIE_RE = re.compile(r"""(?:-b|--cookie)\s+(?:"([^"]*)"|'([^']*)'|(\S+))""")


def looks_like_header_key(name) -> bool:
    """像请求头键名吗？必须含字母——纯数字（例如误粘的 "12345"）不算头名。"""
    s = str(name or '').strip()
    return bool(s) and bool(_HEADER_KEY_RE.match(s)) and bool(re.search(r'[A-Za-z]', s))


def parse_headers_text(text):
    """把用户粘进来的任意文本解析成 {键: 值} 字典。

    依次尝试（能认出多少算多少，认不出的行记进 warnings）：
      1. 整体 JSON 对象：              {"accept": "application/json", "cookie": "..."}
      2. JSON 数组（Postman 风格）：   [{"name": "cookie", "value": "..."}]
      3. curl 复制出来的 -H 行：       -H 'cookie: abc'
      4. 每行一条 `键: 值`：           cookie: abc
      5. `键=值`：                     cookie=abc
      6. `键 值`（空白分隔）：         cookie abc

    返回 (字典, 警告列表)。值一律转成字符串，保留空串（空串 = 显式不发送该头）。
    """
    raw = (text or '').strip()
    warnings = []
    if not raw:
        return {}, ['内容为空']

    # ---- 1/2. 先当整体 JSON 试 ----
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                kk = str(k).strip()
                if not kk:
                    continue
                if v is None:
                    out[kk] = ''
                elif isinstance(v, str):
                    out[kk] = v
                else:
                    out[kk] = json.dumps(v, ensure_ascii=False)
            if out:
                return out, warnings
        elif isinstance(obj, list):
            out = {}
            for it in obj:
                if isinstance(it, dict) and it.get('name'):
                    out[str(it['name']).strip()] = (
                        '' if it.get('value') is None else str(it.get('value')))
            if out:
                return out, warnings
            warnings.append('JSON 数组里没有 name/value 结构')
        else:
            warnings.append('JSON 不是对象或键值数组，已按纯文本解析')
    except Exception:
        pass

    # ---- 3'. 整条 curl 命令（用户很可能直接把 DevTools 的 "Copy as cURL" 粘进来）----
    if re.search(r'(^|\s)curl(\s|$)', raw) and (
            _CURL_HEADER_RE.search(raw) or _CURL_COOKIE_RE.search(raw)):
        out = {}
        for groups in _CURL_HEADER_RE.findall(raw):
            s = groups[0] or groups[1] or groups[2]
            if ':' not in s:
                continue
            k, v = s.split(':', 1)
            k, v = k.strip(), v.strip()
            if looks_like_header_key(k):
                out[k] = v
        for groups in _CURL_COOKIE_RE.findall(raw):
            s = groups[0] or groups[1] or groups[2]
            if s.strip():
                out.setdefault('cookie', s.strip())
        if out:
            warnings.append('已从 curl 命令里提取 -H/--header（以及 -b/--cookie）')
            return out, warnings
        warnings.append('看起来是 curl 命令，但没找到 -H / --header 请求头')
        return {}, warnings

    # ---- 4~7. 按行拆 ----
    out = {}
    for ln in raw.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        s = ln.strip()
        # 去掉行尾续行符与 JSON 语法残留
        s = s.rstrip('\\').strip().rstrip(',').strip()
        if not s or s in ('{', '}', '[', ']', '},', '],'):
            continue
        # curl 的 -H / --header
        for pref in ('-H ', '--header ', '-h '):
            if s.lower().startswith(pref):
                s = s[len(pref):].strip()
                break
        # 去掉整行包裹的引号
        if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':
            s = s[1:-1]
        s = s.strip()
        if not s:
            continue

        key = val = None
        if s.startswith('"') and '":' in s:                 # JSON 单行： "k": "v"
            k2, v2 = s.split(':', 1)
            key, val = k2.strip().strip('"'), v2.strip()
        elif ':' in s and looks_like_header_key(s.split(':', 1)[0].strip().strip('"\'')):
            key, val = s.split(':', 1)
        elif '=' in s and looks_like_header_key(s.split('=', 1)[0].strip()):
            key, val = s.split('=', 1)
        else:
            parts = s.split(None, 1)                        # 空白分隔
            if len(parts) == 2:
                key, val = parts
            elif len(parts) == 1:
                key, val = parts[0], ''

        key = (key or '').strip().strip('"\'').strip()
        val = (val or '').strip().rstrip(',').strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in '"\'':
            val = val[1:-1]
        if not looks_like_header_key(key):
            warnings.append(f'跳过无法识别的行：{s[:50]}')
            continue
        out[key] = val

    if not out:
        warnings.append('没能解析出任何请求头，请检查格式')
    return out, warnings


# ==================== Cookie 导入工具 ====================

# Set-Cookie 行尾的那些属性，粘进来时要去掉（它们不是 cookie 本身）
_COOKIE_ATTR_NAMES = {
    'path', 'domain', 'expires', 'max-age', 'samesite', 'httponly',
    'secure', 'priority', 'partitioned', 'comment', 'version',
}

# 用户可能粘进来的"整行"前缀（Set-Cookie 也会被认出来，属性部分随后丢掉）
_COOKIE_LINE_RE = re.compile(r'^\s*(?:-H\s+|--header\s+)?["\']?(?:set-)?cookie["\']?\s*:\s*',
                             re.IGNORECASE)


def _clean_cookie_pairs(text, warnings):
    """把 `a=1; b=2` 这种串拆成 [(名, 值)]，顺手丢掉 Set-Cookie 的属性段。"""
    pairs = []
    for chunk in re.split(r'[;\n\r]+', text or ''):
        s = chunk.strip().strip('"\'')
        if not s:
            continue
        if '=' not in s:
            # HttpOnly / Secure 这类没有等号的属性
            if s.lower() in _COOKIE_ATTR_NAMES:
                continue
            warnings.append(f'跳过看不懂的片段：{s[:40]}')
            continue
        name, value = s.split('=', 1)
        name, value = name.strip(), value.strip()
        if name.lower() in _COOKIE_ATTR_NAMES:
            # Set-Cookie 的 Path=/ / Expires=... 等属性，不属于 cookie
            continue
        if not name:
            warnings.append(f'跳过没有名字的片段：{s[:40]}')
            continue
        pairs.append((name, value))
    return pairs


def parse_cookie_text(text):
    """把用户粘进来的任意文本解析成**一条 cookie 请求头的值**。

    认这些形式（这是 DevTools / 扩展 / 各种站点最常给出的几种）：
      1. 浏览器 Request Headers 里的整行：   cookie: a=1; b=2
      2. 只有值（从面板里复制 value）：       a=1; b=2
      3. DevTools「Copy as cURL」整条命令：   curl '...' -H 'cookie: a=1' -b 'a=1'
      4. Cookie-Editor / EditThisCookie 导出的 JSON 数组：
         [{"name":"a","value":"1","domain":".x.com"}, ...]
      5. JSON 对象：                          {"cookie": "a=1"} 或 {"a": "1"}
      6. document.cookie 的输出：             a=1; b=2
      7. Set-Cookie 整行（会自动丢掉 Path/Expires/HttpOnly 等属性）

    返回 (cookie值, cookie条数, 警告列表)。解析不出时 cookie 值为空串。
    """
    raw = (text or '').strip()
    warnings = []
    if not raw:
        return '', 0, ['内容为空']

    # ---- 4/5. JSON（Cookie-Editor 那种数组最常见）----
    if raw[0] in '[{':
        try:
            obj = json.loads(raw)
        except Exception:
            obj = None
        if obj is not None:
            pairs = []
            if isinstance(obj, list):
                for it in obj:
                    if isinstance(it, dict) and it.get('name') is not None:
                        pairs.append((str(it['name']).strip(),
                                      '' if it.get('value') is None else str(it['value'])))
                    elif isinstance(it, str) and '=' in it:
                        n, v = it.split('=', 1)
                        pairs.append((n.strip(), v.strip()))
                if not pairs:
                    warnings.append('JSON 数组里没有 name/value 结构')
            elif isinstance(obj, dict):
                low = {str(k).lower(): k for k in obj}
                if 'cookie' in low:
                    return str(obj[low['cookie']]).strip(), -1, warnings
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        continue
                    pairs.append((str(k).strip(),
                                  '' if v is None else str(v)))
            if pairs:
                names = [n for n, _ in pairs]
                warnings.append(f'已从 JSON 里取到 {len(pairs)} 个 cookie')
                return '; '.join(f'{n}={v}' for n, v in pairs), len(names), warnings

    # ---- 3. 整条 curl 命令 ----
    if re.search(r'(^|\s)curl(\s|$)', raw):
        parsed, _w = parse_headers_text(raw)
        low = {k.lower(): v for k, v in parsed.items()}
        if low.get('cookie'):
            warnings.append('已从 curl 命令里提取 cookie')
            return low['cookie'], -1, warnings
        warnings.append('看起来是 curl 命令，但里面没有 cookie / -b 参数')

    # ---- 1. 整行 `cookie: ...` / `Set-Cookie: ...` ----
    lines = raw.splitlines() or [raw]
    if _COOKIE_LINE_RE.match(lines[0]):
        stripped = []
        for ln in lines:
            if _COOKIE_LINE_RE.match(ln):
                ln = _COOKIE_LINE_RE.sub('', ln).strip().strip('"\'')
            stripped.append(ln)
        joined = '; '.join(x for x in stripped if x.strip())
        pairs = _clean_cookie_pairs(joined, warnings)
        if pairs:
            return '; '.join(f'{n}={v}' for n, v in pairs), len(pairs), warnings
        return '', 0, warnings

    # ---- 2/6/7. 纯 cookie 值 / document.cookie / Set-Cookie ----
    pairs = _clean_cookie_pairs(raw, warnings)
    if not pairs:
        warnings.append('没能解析出任何 cookie（是不是粘成了响应头？cookie 在 Request Headers 里）')
        return '', 0, warnings
    return '; '.join(f'{n}={v}' for n, v in pairs), len(pairs), warnings


def describe_cookie(value) -> str:
    """cookie 头的人类可读摘要 —— 只列名字与长度，绝不回显值。"""
    v = str(value or '').strip()
    if not v:
        return ''
    if _DATE_PLACEHOLDER_RE.match(v) or _ENV_PLACEHOLDER_RE.match(v):
        return f'{v}（占位符，运行时取值）'
    names = []
    for chunk in re.split(r'[;\n\r]+', v):
        chunk = chunk.strip()
        if not chunk:
            continue
        name = chunk.split('=', 1)[0].strip()
        if name:
            names.append(name)
    if not names:
        return f'已配置（长度 {len(v)}）'
    shown = '、'.join(names[:6]) + ('…' if len(names) > 6 else '')
    return f'{len(names)} 个 cookie（{shown}），长度 {len(v)}'


def cookie_status_text(headers) -> str:
    """给详情面板用的一行 Cookie 状态。"""
    hs = headers or {}
    val = None
    for k, v in hs.items():
        if str(k).strip().lower() == 'cookie':
            val = v
            break
    if val is None:
        return '🍪 Cookie: 未配置 —— 需要登录态的接口会返回 401（可从 Request Headers 复制后点「导入 Cookie」）'
    sv = str(val).strip()
    if not sv:
        return '🍪 Cookie: 配置为空值（= 显式不发送）'
    m_env = _ENV_PLACEHOLDER_RE.match(sv)
    if m_env:
        # 存的是占位符：把"这个变量现在到底有没有"直接写在状态行上，
        # 不然用户只会看到"我明明配了 cookie，怎么还是 401"。
        return '🍪 Cookie: ' + env_status_text(m_env.group(1))
    return '🍪 Cookie: ' + describe_cookie(val)


def suggest_cookie_env_name(url='') -> str:
    """按域名猜一个环境变量名，例如 https://example.com/... → EXAMPLE_COM_COOKIE。"""
    host = ''
    try:
        host = (urlparse(str(url or '')).hostname or '')
    except Exception:
        host = ''
    name = re.sub(r'[^A-Za-z0-9]+', '_', host or 'api').strip('_').upper()
    return f'{name or "API"}_COOKIE'


class CookieImportDialog(QDialog):
    """🍪 导入 Cookie —— 把浏览器里复制到的 cookie 变成一条 cookie 请求头。

    统一在这里解决"复制粘贴形式千奇百怪"的问题：不管用户粘的是整行、
    纯值、cURL、Cookie-Editor 的 JSON 数组还是 Set-Cookie，出来的都是
    一条标准的 `cookie: a=1; b=2`。
    """

    def __init__(self, parent=None, base_url='', current=''):
        super().__init__(parent)
        self.setWindowTitle('🍪 导入 Cookie')
        self.resize(780, 600)
        self.base_url = base_url or ''
        self._parsed = ''
        self._count = 0
        # 当前配置里存的可能就是 ${ENV:名}（上一次已经是环境变量模式）——
        # 那就别把它塞回输入框：占位符不是 cookie，粘进去只会得到"没能认出"。
        # 只把变量名带过来，用户要换内容就重新粘一次。
        cur = str(current or '').strip()
        self._existing_env = ''
        m_cur = _ENV_PLACEHOLDER_RE.match(cur)
        if m_cur:
            self._existing_env = m_cur.group(1)
            cur = ''
        self._build_ui(cur)

    # ---------- UI ----------
    def _build_ui(self, current):
        layout = QVBoxLayout(self)

        tip = QLabel(
            "把浏览器里复制到的 Cookie 粘进来，任意一种形式都能认：\n"
            "  · Request Headers 里的整行 —— <code>cookie: cf_clearance=...; session=...</code>\n"
            "  · 只有值（CF 面板里直接选中的那一串）—— <code>cf_clearance=...; session=...</code>\n"
            "  · DevTools「Copy as cURL」整条命令（含 -H 'cookie: …' 或 -b '…'）\n"
            "  · Cookie-Editor / EditThisCookie 导出的 JSON：[{\"name\":\"a\",\"value\":\"1\"}, …]\n"
            "  · Set-Cookie 整行（Path / Expires / HttpOnly 这些属性会被自动丢掉）")
        if self._existing_env:
            tip.setText(
                tip.text()
                + f"\n\n当前这个子接口存的是环境变量 <b>{self._existing_env}</b>。"
                  "要换内容就把新的 cookie 粘到下面的框里；只想确认它有没有生效，"
                  "看输入框下面那行状态。")
        tip.setWordWrap(True)
        tip.setTextFormat(Qt.RichText)
        tip.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(tip)

        warn = QLabel(
            "⚠️ Cookie 就是你的登录态，等同于账号密码。保存后它会明文写进 "
            "<code>data/api_config.json</code>，导出流程时还会进 <code>data/webtree/*.wbt</code>。\n"
            "不想明文落盘，就勾下面的「用环境变量代替明文」。")
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.RichText)
        warn.setStyleSheet(
            "background-color: #fff3cd; color: #7a5b00; border: 1px solid #ffe08a;"
            "border-radius: 4px; padding: 6px; font-size: 11px;")
        layout.addWidget(warn)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText('在这里粘贴 Cookie …')
        if current:
            self.text_edit.setPlainText(current)
        self.text_edit.textChanged.connect(self._refresh_preview)
        layout.addWidget(self.text_edit, 1)

        self.preview_label = QLabel('')
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview_label.setStyleSheet("font-size: 11px; color: #4a90d9;")
        layout.addWidget(self.preview_label)

        self.use_env_check = QCheckBox('用环境变量代替明文（推荐）—— 存 ${ENV:名字}，运行时才取真值')
        self.use_env_check.setChecked(True)
        self.use_env_check.toggled.connect(self._on_env_toggled)
        layout.addWidget(self.use_env_check)

        env_row = QHBoxLayout()
        env_row.addWidget(QLabel('环境变量名:'))
        self.env_name_edit = QLineEdit(self._existing_env or suggest_cookie_env_name(self.base_url))
        self.env_name_edit.textChanged.connect(self._refresh_preview)
        env_row.addWidget(self.env_name_edit, 1)
        self.env_apply_btn = QPushButton('💾 立即生效（本程序）+ 复制 setx 命令')
        self.env_apply_btn.setToolTip(
            '把值写进当前进程的环境变量（本程序马上就能用），\n'
            '同时把 setx 命令复制到剪贴板，粘到终端执行可永久保存。\n'
            '注意 setx 命令行长度上限 1024 字符，超长请用「系统属性 → 环境变量」图形界面添加。')
        self.env_apply_btn.clicked.connect(self._apply_env_now)
        env_row.addWidget(self.env_apply_btn)
        self.env_reload_btn = QPushButton('📥 重新读取系统环境变量')
        self.env_reload_btn.setToolTip(
            'setx / 系统属性 里设过的变量，**已经在运行的程序不一定看得到**；\n'
            '点这里直接从注册表 HKCU\\Environment 再读一次，省得重启程序。')
        self.env_reload_btn.clicked.connect(self._reload_env)
        env_row.addWidget(self.env_reload_btn)
        layout.addLayout(env_row)

        self.env_state_label = QLabel('')
        self.env_state_label.setWordWrap(True)
        self.env_state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.env_state_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.env_state_label)

        self.env_result_label = QLabel('')
        self.env_result_label.setWordWrap(True)
        self.env_result_label.setStyleSheet("font-size: 11px; color: #2e7d32;")
        layout.addWidget(self.env_result_label)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText('导入')
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._on_env_toggled(self.use_env_check.isChecked())
        self._refresh_preview()

    # ---------- 逻辑 ----------
    def _on_env_toggled(self, checked):
        self.env_name_edit.setEnabled(checked)
        self.env_apply_btn.setEnabled(checked and bool(self.text_edit.toPlainText().strip()))
        self._refresh_preview()

    def _refresh_preview(self):
        raw = self.text_edit.toPlainText()
        if not raw.strip():
            self._parsed, self._count, warns = '', 0, []
            self.preview_label.setText('（等待粘贴）')
        else:
            self._parsed, self._count, warns = parse_cookie_text(raw)
            if not self._parsed:
                self.preview_label.setText('❌ 没能认出 cookie —— '
                                           + '；'.join(warns) if warns else '❌ 没能认出 cookie')
            else:
                cnt = f'{self._count} 个' if self._count and self._count > 0 else '若干'
                self.preview_label.setText(
                    f'✅ 识别到 {cnt} cookie：{describe_cookie(self._parsed)}'
                    + ('\n⚠ ' + '；'.join(warns) if warns else ''))
        if hasattr(self, 'env_apply_btn'):
            self.env_apply_btn.setEnabled(
                self.use_env_check.isChecked() and bool(self._parsed))
        self._refresh_env_state()

    def _refresh_env_state(self):
        """把"这个环境变量现在到底有没有"直接显示出来。

        这是"明明配了 cookie 却还是 401"最常见的根因：值存在占位符里，
        而变量根本没设（或者设完没重启程序）。
        """
        if not hasattr(self, 'env_state_label'):
            return
        if not self.use_env_check.isChecked():
            self.env_state_label.setText(
                '⚠️ 明文模式：cookie 会直接写进 data/api_config.json（该文件本身是加密的），'
                '但导出/保存流程时仍会进 .wbt —— 那个是明文 JSON。')
            self.env_state_label.setStyleSheet("font-size: 11px; color: #8a6d3b;")
            return
        name = self.env_name_edit.text().strip()
        if not name:
            self.env_state_label.setText(
                '⚠️ 环境变量名为空 —— 这样保存下去不会变成占位符，而是直接存明文。')
            self.env_state_label.setStyleSheet("font-size: 11px; color: #8a6d3b;")
            return
        has = bool(os.environ.get(name))
        txt = env_status_text(name)
        if has:
            # 区分"只活在当前进程"与"系统里也有"——前者关掉程序就没了
            txt += ('　✅ 系统里也有，重启后依然有效。' if env_is_persisted(name)
                    else '　⚠️ 只在本程序进程里，系统里没有 —— 关掉程序就没了。'
                         '请点「💾 立即生效」把 setx 命令粘到终端执行一次。')
        else:
            txt += ('　→ 点「💾 立即生效」把上面的值写进本程序并拿到 setx 命令；'
                    '如果变量是别处设好的，点「📥 重新读取系统环境变量」免重启读进来。')
        self.env_state_label.setText(txt)
        self.env_state_label.setStyleSheet(
            "font-size: 11px; color: %s;" % ('#2e7d32' if has else '#c0392b'))

    def _apply_env_now(self):
        """把值写进**当前程序进程**，并把 setx 命令放进剪贴板。

        为什么只写进程内、不替用户写系统：往 `HKCU\\Environment` 写变量
        是杀软眼里的持久化行为（本程序就因此被拦过），所以这一步交给用户
        自己执行一次 setx —— 执行完回来点「📥 重新读取系统环境变量」，不用重启。
        """
        name = self.env_name_edit.text().strip()
        if not name or not self._parsed:
            return
        os.environ[name] = self._parsed

        cmd = f'setx {name} "{self._parsed}"'
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(cmd)
            copied = '✅ setx 命令已复制到剪贴板。'
        except Exception:
            copied = ''
        longer = ' ⚠️ 值超过 1024 字符，setx 会失败，请改用系统「环境变量」图形界面。' \
            if len(cmd) > 1024 else ''
        self.env_result_label.setText(
            f'本程序内已生效（{name}，长度 {len(self._parsed)}）。\n'
            f'{copied}\n'
            '想让它在重启后依然有效：把这条命令粘到 PowerShell / CMD 里执行一次，'
            '然后回到这里点「📥 重新读取系统环境变量」（不用重启程序）。'
            f'{longer}')
        self._refresh_env_state()

    def _reload_env(self):
        """从注册表把 setx 过的变量读回来 —— 免重启。"""
        updated, msg = reload_env_from_system()
        name = self.env_name_edit.text().strip()
        self.env_result_label.setText(msg)
        self.env_result_label.setStyleSheet(
            "font-size: 11px; color: %s;"
            % ('#2e7d32' if (name and os.environ.get(name)) else '#8a6d3b'))
        self._refresh_env_state()

    def _on_accept(self):
        if not self._parsed:
            QMessageBox.warning(self, '还没认出 cookie',
                                '没能从这段文本里解析出 cookie，请检查是不是粘成了响应头。\n\n'
                                'cookie 在 DevTools 的 **Request Headers** 面板里（不是 Response Headers）。')
            return
        # 环境变量模式 + 变量没设 = 保存下去也一定取不到真值，在关闭前拦住
        if self.use_env_check.isChecked():
            name = self.env_name_edit.text().strip()
            if name and not os.environ.get(name):
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle('环境变量还没生效')
                box.setText(f'环境变量 {name} 在当前程序里读不到。\n'
                            '就这样保存的话，请求不会带上 cookie，接口会返回 401。')
                box.setInformativeText('要现在把真值写进本程序吗？'
                                       '（会同时把 setx 命令复制到剪贴板，方便永久保存）')
                b_now = box.addButton('💾 现在写入', QMessageBox.AcceptRole)
                b_only = box.addButton('仅保存占位符', QMessageBox.DestructiveRole)
                box.addButton('取消', QMessageBox.RejectRole)
                box.setDefaultButton(b_now)
                box.exec()
                clicked = box.clickedButton()
                if clicked is b_now:
                    self._apply_env_now()
                elif clicked is not b_only:
                    return          # 点了取消 / 直接关掉窗口 → 什么都不做
        self.accept()

    # ---------- 对外 ----------
    def get_cookie_value(self) -> str:
        """返回要写进请求头表格的值（可能是 ${ENV:名} 占位符）。"""
        if not self._parsed:
            return ''
        if self.use_env_check.isChecked():
            name = self.env_name_edit.text().strip()
            if name:
                return f'${{ENV:{name}}}'
        return self._parsed

    def get_env_name(self) -> str:
        return self.env_name_edit.text().strip() if self.use_env_check.isChecked() else ''

    def raw_cookie(self) -> str:
        return self._parsed


# ==================== 图源配置相关新增类 ====================

class EndpointDebugWorker(QThread):
    """子端口调试工作线程（requests 失败/被风控时自动降级 curl_cffi）"""
    # 响应体, 状态码, 错误消息, 诊断信息(dict)
    debug_completed = Signal(str, int, str, dict)

    # 命中这些特征说明返回的是风控挑战页，而不是真实内容
    _CHALLENGE_MARKERS = ('ddos-guard', 'just a moment', 'cf-chl',
                          'challenge-platform', 'attention required',
                          '__cf_chl', 'enable javascript and cookies')

    def __init__(self, method, url, headers, query_params, body_data, parent=None):
        super().__init__(parent)
        self.method = method.upper()
        self.url = url
        self.headers = headers
        self.query_params = query_params
        self.body_data = body_data
        self.running = True

    @classmethod
    def _looks_like_challenge(cls, resp) -> bool:
        if resp is None:
            return False
        try:
            if resp.status_code not in (403, 429, 503):
                return False
            body = (resp.text or '')[:4000].lower()
        except Exception:
            return False
        return any(mk in body for mk in cls._CHALLENGE_MARKERS)

    def _build_kwargs(self, req_headers):
        kwargs = {'headers': req_headers, 'timeout': 30}
        if self.query_params:
            kwargs['params'] = {k: v for k, v in self.query_params.items() if v}
        if self.method in ('POST', 'PUT', 'PATCH'):
            payload = self.body_data if self.body_data else '{}'
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False)
            kwargs['data'] = str(payload).encode('utf-8')
            setdefault_ci(req_headers, 'Content-Type', 'application/json')
        return kwargs

    def run(self):
        started = time.time()
        meta = {
            'used_curl': False,
            'sent_headers': {},
            'response_headers': {},
            'elapsed_ms': 0,
            'final_url': self.url,
            'attempts': [],
            'curl_available': HAS_CURL_CFFI,
            'missing_env': [],
            'dynamic_headers': [],
        }

        try:
            # 先 ${ENV:名}（凭据）再 ${DATE:时区}（实时时间），顺序不能反
            user_headers, missing_env, dynamic_notes = resolve_header_placeholders(
                self.headers or {})
            meta['missing_env'] = missing_env
            meta['dynamic_headers'] = dynamic_notes
            req_headers = build_browser_headers(user_headers,
                                                accept=DEFAULT_API_ACCEPT)
            kwargs = self._build_kwargs(req_headers)
            # 放在 _build_kwargs 之后取，才能把自动补的 Content-Type 一并记录下来
            meta['sent_headers'] = dict(req_headers)

            if self.method not in ('GET', 'POST', 'DELETE', 'PUT', 'PATCH'):
                self.debug_completed.emit('', 0, f'不支持的HTTP方法: {self.method}', meta)
                return

            resp = None
            err = ''
            # ---- 第 1 轮：requests ----
            try:
                resp = requests.Session().request(self.method, self.url, **kwargs)
                meta['attempts'].append({'via': 'requests', 'code': resp.status_code, 'error': ''})
            except Exception as e:
                err = friendly_net_error(e)
                meta['attempts'].append({'via': 'requests', 'code': 0, 'error': str(e)})

            # ---- 第 2 轮：curl_cffi 回退 ----
            # 触发条件：请求异常 / 403 / 429 / 503 / 命中风控挑战页特征。
            # 理由：Cloudflare、DDoS-Guard 一类只拦 TLS 指纹，换浏览器指纹即可通过，
            #       而这条链路正是 img_server 下载器已经在用的策略。
            need_fallback = (resp is None
                             or resp.status_code in (403, 429, 503)
                             or self._looks_like_challenge(resp))
            if need_fallback and not HAS_CURL_CFFI:
                meta['attempts'].append({
                    'via': 'curl_cffi', 'code': 0,
                    'error': '未安装 curl_cffi（pip install curl_cffi 可绕过部分风控）'})
            elif need_fallback and HAS_CURL_CFFI:
                try:
                    ck = dict(kwargs)
                    ck['impersonate'] = 'chrome124'
                    r2 = curl_requests.request(self.method, self.url, **ck)
                    meta['attempts'].append({'via': 'curl_cffi', 'code': r2.status_code, 'error': ''})
                    if r2.status_code < 400 or resp is None or resp.status_code >= 400:
                        resp = r2
                        meta['used_curl'] = True
                        err = ''
                except Exception as e:
                    meta['attempts'].append({'via': 'curl_cffi', 'code': 0, 'error': str(e)})
                    if resp is None:
                        err = friendly_net_error(e)

            meta['elapsed_ms'] = int((time.time() - started) * 1000)

            if resp is None:
                self.debug_completed.emit('', 0, err or '请求失败', meta)
                return

            try:
                meta['final_url'] = str(getattr(resp, 'url', self.url) or self.url)
                meta['response_headers'] = {str(k): str(v) for k, v in dict(resp.headers).items()}
            except Exception:
                pass

            try:
                response_text = resp.text
                # 尝试格式化JSON
                try:
                    parsed = resp.json()
                    response_text = json.dumps(parsed, indent=4, ensure_ascii=False)
                except Exception:
                    pass
                self.debug_completed.emit(response_text, resp.status_code, '', meta)
            except Exception as e:
                self.debug_completed.emit(resp.text if resp is not None else '',
                                          resp.status_code if resp is not None else 0,
                                          str(e), meta)

        except Exception as e:
            meta['elapsed_ms'] = int((time.time() - started) * 1000)
            self.debug_completed.emit('', 0, f'调试异常: {str(e)}', meta)

    def stop(self):
        self.running = False


class NewImageSourceDialog(QDialog):
    """新建/编辑API入口对话框"""
    def __init__(self, parent=None, existing_data=None):
        super().__init__(parent)
        self.existing_data = existing_data
        self.setWindowTitle("新建图源API入口" if not existing_data else "编辑图源API入口")
        self.setMinimumSize(500, 200)
        self.setup_ui()
        if existing_data:
            self.apply_data(existing_data)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如: 我的图库API")
        form.addRow("API名称:", self.name_edit)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("例如: https://example.com/api/v1")
        form.addRow("基础URL入口:", self.base_url_edit)

        layout.addLayout(form)

        # 说明
        hint = QLabel("提示：基础URL是API的根地址，子端口路径将拼接在此URL之后。")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate_and_accept(self):
        name = self.name_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "输入错误", "请输入API名称")
            return
        if not base_url:
            QMessageBox.warning(self, "输入错误", "请输入基础URL入口")
            return
        # 去掉末尾斜杠
        if base_url.endswith('/'):
            base_url = base_url[:-1]
        self.base_url_edit.setText(base_url)
        self.accept()

    def apply_data(self, data):
        self.name_edit.setText(data.get('name', ''))
        self.base_url_edit.setText(data.get('base_url', ''))

    def get_data(self):
        return {
            'name': self.name_edit.text().strip(),
            'base_url': self.base_url_edit.text().strip().rstrip('/')
        }


class BulkHeaderPasteDialog(QDialog):
    """批量粘贴导入请求头。

    用户可以直接把抓包工具/浏览器 DevTools/Postman/curl 命令里的一整段复制进来，
    这里负责识别并预览，确认后再写回请求头表格。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量粘贴请求头")
        self.setMinimumSize(620, 460)
        self._headers = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        tip = QLabel(
            "把请求头整段粘进来即可，支持这些写法（可混排、多余的空行和逗号会自动忽略）：\n"
            "    ① JSON 对象：  {\"accept\": \"application/json\", \"cookie\": \"a=1; b=2\"}\n"
            "    ② JSON 数组：  [{\"name\": \"cookie\", \"value\": \"a=1; b=2\"}]\n"
            "    ③ curl 的 -H：  -H 'cookie: a=1; b=2'\n"
            "    ④ 每行一条：    cookie: a=1; b=2      （也支持  cookie=值  或  cookie 值）"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(tip)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            '在这里粘贴，例如：\n'
            'accept: application/json\n'
            'cookie: session=abc123; cf_clearance=xyz\n'
            'user-agent: Mozilla/5.0 ...')
        self.text_edit.textChanged.connect(self._refresh_preview)
        layout.addWidget(self.text_edit, 1)

        self.preview_label = QLabel("等待粘贴...")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet("color: #4a90d9; font-size: 11px;")
        layout.addWidget(self.preview_label)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("导入")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _refresh_preview(self):
        headers, warnings = parse_headers_text(self.text_edit.toPlainText())
        self._headers = headers
        if not headers:
            self.preview_label.setText(
                '未识别到请求头' + ('：' + '；'.join(warnings) if warnings else ''))
            self.preview_label.setStyleSheet("color: #c0392b; font-size: 11px;")
            return
        names = '、'.join(list(headers.keys())[:10])
        if len(headers) > 10:
            names += f' 等 {len(headers)} 条'
        text = f'✅ 识别到 {len(headers)} 条请求头：{names}'
        sensitive = [k for k in headers if is_sensitive_header(k)]
        if sensitive:
            text += f'\n⚠️ 其中含敏感头（{"、".join(sensitive)}），保存后请留意配置文件与流程文件的明文风险。'
        if warnings:
            text += '\n⚠️ ' + '；'.join(warnings[:3])
        self.preview_label.setText(text)
        self.preview_label.setStyleSheet("color: #4a90d9; font-size: 11px;")

    def _on_accept(self):
        if not self._headers:
            QMessageBox.warning(self, "没有内容", "没有识别到任何请求头，请检查粘贴的格式。")
            return
        self.accept()

    def get_headers(self):
        return dict(self._headers)


class EndpointEditDialog(QDialog):
    """子端口编辑对话框"""
    def __init__(self, parent=None, existing_data=None, base_url=''):
        super().__init__(parent)
        self.existing_data = existing_data
        self._base_url = base_url or ''  # 仅用于给「导入 Cookie」猜环境变量名
        self.setWindowTitle("新建子端口" if not existing_data else "编辑子端口")
        self.setMinimumSize(700, 550)
        self.parameters = []  # 存储参数定义: [{name, type, value}]
        self.custom_headers = {}  # 自定义请求头
        self.setup_ui()
        if existing_data:
            self.apply_data(existing_data)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 第一行：方法和路径
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("方法:"))
        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST", "DELETE", "PUT", "PATCH"])
        self.method_combo.setMinimumWidth(100)
        self.method_combo.currentTextChanged.connect(lambda _t: self._refresh_body_hint())
        row1.addWidget(self.method_combo)

        row1.addWidget(QLabel("路径:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("例如: /{category}/images/{id}")
        self.path_edit.textChanged.connect(self.on_path_changed)
        row1.addWidget(self.path_edit, 1)
        layout.addLayout(row1)

        # 注释行
        row_desc = QHBoxLayout()
        row_desc.addWidget(QLabel("注释:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("描述该子端口的用途...")
        self.desc_edit.setStyleSheet("color: #999; font-style: italic;")
        row_desc.addWidget(self.desc_edit, 1)
        layout.addLayout(row_desc)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 参数区域标题
        param_header = QHBoxLayout()
        param_header.addWidget(QLabel("📌 路径参数 (自动检测花括号):"))
        param_header.addStretch()
        self.param_count_label = QLabel("未检测到参数")
        self.param_count_label.setStyleSheet("color: #888; font-size: 11px;")
        param_header.addWidget(self.param_count_label)
        layout.addLayout(param_header)

        # 参数表格
        # 注意：列数必须是 5 —— _sync_param_table 会把删除按钮放在第 4 列，
        # 历史上这里写的是 4 列，导致 setCellWidget(row, 4, ...) 越界、删除按钮从未显示。
        self.param_table = QTableWidget(0, 5)
        self.param_table.setHorizontalHeaderLabels(["参数名", "数据类型", "参数值", "注释", ""])
        self.param_table.horizontalHeader().setStretchLastSection(False)
        self.param_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.param_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.param_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.param_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.param_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.param_table.setColumnWidth(1, 130)
        self.param_table.setColumnWidth(4, 30)
        self.param_table.setMinimumHeight(80)
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.param_table)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 自定义请求头（= 本子接口的默认请求头，每次调试与整合器调用都会带上）
        headers_header = QHBoxLayout()
        headers_header.addWidget(QLabel("🔧 自定义请求头 (本子接口的默认请求头):"))
        headers_header.addStretch()
        self.import_cookie_btn = QPushButton("🍪 导入 Cookie")
        self.import_cookie_btn.setMaximumWidth(130)
        self.import_cookie_btn.setToolTip(
            "需要登录态的接口必须先带 Cookie，否则会返回 401。\n"
            "点这里把浏览器里复制到的 Cookie 粘进来 —— 纯值、整行、\n"
            "cURL 命令、Cookie-Editor 导出的 JSON 都能自动认出来，\n"
            "统一合成一条 cookie 请求头。\n"
            "注意：要复制的是 Request Headers 里的 cookie，不是响应头。")
        self.import_cookie_btn.clicked.connect(self._import_cookie)
        headers_header.addWidget(self.import_cookie_btn)
        self.paste_header_btn = QPushButton("📋 批量粘贴")
        self.paste_header_btn.setMaximumWidth(120)
        self.paste_header_btn.setToolTip(
            "从任意文本批量导入请求头。支持：\n"
            "· JSON 对象  {\"cookie\": \"...\", \"accept\": \"application/json\"}\n"
            "· JSON 数组  [{\"name\": \"cookie\", \"value\": \"...\"}]\n"
            "· curl 复制出来的 -H '键: 值' 行\n"
            "· 每行一条  键: 值   /   键=值   /   键 值")
        self.paste_header_btn.clicked.connect(self._bulk_paste_headers)
        headers_header.addWidget(self.paste_header_btn)
        self.add_header_btn = QPushButton("+ 添加请求头")
        self.add_header_btn.setMaximumWidth(120)
        self.add_header_btn.clicked.connect(self.add_header_row)
        headers_header.addWidget(self.add_header_btn)
        layout.addLayout(headers_header)

        self.headers_table = QTableWidget(0, 3)
        self.headers_table.setHorizontalHeaderLabels(["键", "值", ""])
        self.headers_table.horizontalHeader().setStretchLastSection(False)
        self.headers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.headers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.headers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.headers_table.setColumnWidth(2, 40)
        self.headers_table.setMinimumHeight(60)
        self.headers_table.verticalHeader().setVisible(False)
        self.headers_table.itemChanged.connect(self._on_header_item_changed)
        layout.addWidget(self.headers_table)

        # 请求头说明（值留空 = 不发送该头；可覆盖/屏蔽内置默认头）
        self.headers_hint_label = QLabel(
            "这里是本子接口的默认请求头：保存后每次点「调试」，以及端口画板里对该子接口的调用，"
            "都会自动带上（画布上的节点仍可单独覆盖）。\n"
            "· 值留空 = 不发送该头（可用来屏蔽内置默认头，例如把 User-Agent 留空即不发送 UA）；\n"
            "· 值支持 ${ENV:环境变量名} 占位符，运行时再取，避免把密钥明文存进配置；\n"
            "· 键名是 Date / Time / X-Timestamp 之类时，值栏会变成时区下拉框，每次请求前实时"
            "生成该时区的当前时间（存储为 ${DATE:时区}）。注意：HTTP 的 Date 响应头是服务器给的，"
            "客户端一般不需要发它，只有服务端要用时间做签名校验时才需要。"
        )
        self.headers_hint_label.setWordWrap(True)
        self.headers_hint_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.headers_hint_label)

        # 时间头实时预览（只有真的配了时间头才显示）
        self.time_preview_label = QLabel("")
        self.time_preview_label.setWordWrap(True)
        self.time_preview_label.setStyleSheet("color: #4a90d9; font-size: 11px;")
        self.time_preview_label.setVisible(False)
        layout.addWidget(self.time_preview_label)
        self._time_timer = QTimer(self)
        self._time_timer.setInterval(1000)
        self._time_timer.timeout.connect(self._refresh_time_preview)
        self._time_timer.start()

        # 敏感头提示（默认隐藏，填了 cookie/token 之类才显示）
        self.headers_sensitive_label = QLabel("")
        self.headers_sensitive_label.setWordWrap(True)
        self.headers_sensitive_label.setStyleSheet(
            "color: #b8860b; font-size: 11px; background: #fffbe6; "
            "border: 1px solid #ffe58f; padding: 4px;"
        )
        self.headers_sensitive_label.setVisible(False)
        layout.addWidget(self.headers_sensitive_label)

        # 分隔线
        layout.addWidget(self._create_separator())

        # 请求体（仅 POST/PUT/PATCH 生效）
        body_header = QHBoxLayout()
        body_header.addWidget(QLabel("📦 请求体 (仅 POST / PUT / PATCH 生效):"))
        body_header.addStretch()
        self.body_hint_label = QLabel("")
        self.body_hint_label.setStyleSheet("color: #888; font-size: 11px;")
        body_header.addWidget(self.body_hint_label)
        layout.addLayout(body_header)

        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText(
            '留空则不带请求体。支持 JSON（如 {"a": 1}）或普通文本/表单串（如 a=1&b=2）。'
        )
        self.body_edit.setMaximumHeight(90)
        self.body_edit.textChanged.connect(self._refresh_body_hint)
        layout.addWidget(self.body_edit)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 初始检测
        self.on_path_changed(self.path_edit.text())
        self._refresh_body_hint()

    def _create_separator(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: #ddd;")
        return sep

    def on_path_changed(self, text):
        """路径文本变化时检测花括号参数"""
        # 使用正则提取所有 {xxx} 中的参数名
        pattern = re.compile(r'\{(\w+)\}')
        found_params = pattern.findall(text)

        # 去重保持顺序
        seen = set()
        unique_params = []
        for p in found_params:
            if p not in seen:
                seen.add(p)
                unique_params.append(p)

        # 更新参数表格
        self._sync_param_table(unique_params)
        self.param_count_label.setText(
            f"检测到 {len(unique_params)} 个参数" if unique_params else "未检测到参数"
        )

    def _sync_param_table(self, param_names):
        existing_values = {}
        existing_types = {}
        existing_notes = {}
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if name_item:
                name = name_item.text()
                existing_values[name] = self._get_param_value(row)
                existing_types[name] = self._get_param_type(row)
                existing_notes[name] = self._get_param_note(row)  # 新增

        self.param_table.setRowCount(0)

        for pname in param_names:
            row = self.param_table.rowCount()
            self.param_table.insertRow(row)

            # 参数名
            name_item = QTableWidgetItem(pname)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setFont(QFont("Consolas", 10))
            self.param_table.setItem(row, 0, name_item)

            # 类型下拉框
            type_combo = QComboBox()
            type_combo.addItems(["string", "path", "int", "float", "bool"])
            type_combo.setCurrentText(existing_types.get(pname, "string"))
            self.param_table.setCellWidget(row, 1, type_combo)

            # 值输入框
            value_edit = QLineEdit()
            value_edit.setPlaceholderText(f"输入 {pname} 的值...")
            if pname in existing_values:
                value_edit.setText(existing_values[pname])
            self.param_table.setCellWidget(row, 2, value_edit)

            # 注释输入框
            note_edit = QLineEdit()
            note_edit.setPlaceholderText("参数注释...")
            if pname in existing_notes:
                note_edit.setText(existing_notes[pname])
            self.param_table.setCellWidget(row, 3, note_edit)

            # 删除按钮（列索引改为4）
            del_btn = QPushButton("×")
            del_btn.setMaximumWidth(30)
            del_btn.setStyleSheet("color: #d9534f; font-weight: bold; border: none;")
            del_btn.setToolTip("移除此参数（将从路径中删除）")
            del_btn.clicked.connect(lambda checked, p=pname: self._remove_param(p))
            self.param_table.setCellWidget(row, 4, del_btn)

    def _get_param_note(self, row):
        widget = self.param_table.cellWidget(row, 3)
        if widget and isinstance(widget, QLineEdit):
            return widget.text()
        return ''

    def _get_param_value(self, row):
        widget = self.param_table.cellWidget(row, 2)
        if widget and isinstance(widget, QLineEdit):
            return widget.text()
        return ''

    def _get_param_type(self, row):
        widget = self.param_table.cellWidget(row, 1)
        if widget and isinstance(widget, QComboBox):
            return widget.currentText()
        return 'string'

    def _remove_param(self, param_name):
        """从路径中移除指定参数的花括号标记"""
        current_path = self.path_edit.text()
        # 移除 {param_name}
        new_path = re.sub(r'\{' + re.escape(param_name) + r'\}', '', current_path)
        # 清理多余斜杠
        new_path = re.sub(r'/+', '/', new_path)
        new_path = new_path.rstrip('/')
        if not new_path.startswith('/'):
            new_path = '/' + new_path
        self.path_edit.setText(new_path)
        # on_path_changed 会自动触发更新表格

    def add_header_row(self):
        self._append_header_row('', '')

    def _append_header_row(self, key, value):
        """追加一行请求头。空值会在界面上显示为灰字（= 不发送该头）。"""
        row = self.headers_table.rowCount()
        self.headers_table.blockSignals(True)
        try:
            self.headers_table.insertRow(row)
            self.headers_table.setItem(row, 0, QTableWidgetItem(str(key or '')))
            self._set_value_cell(row, key, value)
            del_btn = QPushButton("×")
            del_btn.setMaximumWidth(30)
            del_btn.setStyleSheet("color: #d9534f; font-weight: bold; border: none;")
            del_btn.setToolTip("移除此请求头")
            del_btn.clicked.connect(lambda: self._remove_header_row(del_btn))
            self.headers_table.setCellWidget(row, 2, del_btn)
        finally:
            self.headers_table.blockSignals(False)
        self._refresh_header_style()
        self._refresh_time_preview()
        return row

    def _set_value_cell(self, row, key, value):
        """按键名决定值栏形态：时间头 → 时区下拉框；其余 → 普通文本格。

        时区下拉框的选中项会被收集成 ${DATE:时区} 占位符，所以配置 schema 与
        .wbt 格式都不用改，老配置（值是普通字符串）也不受影响。
        """
        text = str(value or '')
        if is_time_header(key):
            self.headers_table.setItem(row, 1, QTableWidgetItem(text))
            self.headers_table.setCellWidget(row, 1, self._make_time_combo(text))
        else:
            if isinstance(self.headers_table.cellWidget(row, 1), QComboBox):
                self.headers_table.removeCellWidget(row, 1)
            self.headers_table.setItem(row, 1, QTableWidgetItem(text))

    def _make_time_combo(self, value):
        """给时间头造一个时区下拉框，初值从已有占位符里解析。"""
        combo = QComboBox()
        for label, tz_key in TIMEZONE_CHOICES:
            combo.addItem(label, tz_key)
        m = _DATE_PLACEHOLDER_RE.match(str(value or '').strip())
        want = DEFAULT_TIMEZONE_KEY
        if m and m.group(1).upper() != 'TIMESTAMP':
            want = (m.group(2) or DEFAULT_TIMEZONE_KEY).strip()
        idx = combo.findData(want)
        if idx < 0:
            # 手工编辑过的配置里可能有列表外的时区，保留它而不是悄悄改掉
            combo.addItem(f'{want}（配置中的时区）', want)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
        combo.setMinimumWidth(150)
        combo.currentIndexChanged.connect(self._refresh_header_style)
        combo.currentIndexChanged.connect(self._refresh_time_preview)
        return combo

    def _rebuild_value_cells(self):
        """键名变化后重建值栏：文本格 ←→ 时区下拉框。"""
        self.headers_table.blockSignals(True)
        try:
            for row in range(self.headers_table.rowCount()):
                key_item = self.headers_table.item(row, 0)
                key = key_item.text().strip() if key_item else ''
                combo = self.headers_table.cellWidget(row, 1)
                val_item = self.headers_table.item(row, 1)
                if is_time_header(key):
                    if not isinstance(combo, QComboBox):
                        # 从文本格切到下拉框：沿用当前值（可能是 ${DATE:...} 或一个时间字符串）
                        self._set_value_cell(row, key, val_item.text() if val_item else '')
                else:
                    if isinstance(combo, QComboBox):
                        tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                        self._set_value_cell(row, key, f'${{DATE:{tz}}}')
        finally:
            self.headers_table.blockSignals(False)

    def _refresh_time_preview(self):
        """把时间头当前会生成的值实时显示出来（每秒刷新）。"""
        try:
            rows = []
            for row in range(self.headers_table.rowCount()):
                combo = self.headers_table.cellWidget(row, 1)
                if not isinstance(combo, QComboBox):
                    continue
                key_item = self.headers_table.item(row, 0)
                key = key_item.text().strip() if key_item else ''
                if not key:
                    continue
                tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                rows.append(f'{key}: {make_date_value(tz)}   [{timezone_label(tz)}]')
            if rows:
                self.time_preview_label.setText(
                    '🕒 发出请求时会实时填入：' + '    '.join(rows))
                self.time_preview_label.setVisible(True)
            else:
                self.time_preview_label.setVisible(False)
        except RuntimeError:
            pass

    def _merge_headers(self, new_headers):
        """把一批请求头并进表格：键名不区分大小写，同名的覆盖原值，新的追加。"""
        merged = 0
        existing = {k.lower(): k for k in self._collect_headers()}
        for key, value in (new_headers or {}).items():
            old = existing.get(key.lower())
            if old is not None:
                for row in range(self.headers_table.rowCount()):
                    key_item = self.headers_table.item(row, 0)
                    if key_item and key_item.text().strip() == old:
                        self.headers_table.blockSignals(True)
                        try:
                            self._set_value_cell(row, key, value)
                        finally:
                            self.headers_table.blockSignals(False)
                        break
            else:
                self._append_header_row(key, value)
                existing[key.lower()] = key
            merged += 1
        self._refresh_header_style()
        self._refresh_time_preview()
        return merged

    def _bulk_paste_headers(self):
        """批量粘贴导入请求头。"""
        dlg = BulkHeaderPasteDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_headers = dlg.get_headers()
        if not new_headers:
            return
        self._merge_headers(new_headers)

    def _import_cookie(self):
        """🍪 导入 Cookie —— 把任意形式的 cookie 文本合成一条 cookie 头。"""
        current = ''
        for k, v in self._collect_headers().items():
            if str(k).strip().lower() == 'cookie':
                current = v
                break
        dlg = CookieImportDialog(self, base_url=getattr(self, '_base_url', '') or '',
                                 current=current)
        if dlg.exec() != QDialog.Accepted:
            return
        value = dlg.get_cookie_value()
        if not value:
            return
        self._merge_headers({'cookie': value})
        note = '（存的是 ${ENV:%s} 占位符，运行时才取真值）' % dlg.get_env_name() \
            if dlg.get_env_name() else '（明文保存，注意别外发 .wbt）'
        QMessageBox.information(
            self, 'Cookie 已导入',
            f'已写入 1 条 cookie 请求头：\n{describe_cookie(value)}\n{note}')

    def _remove_header_row(self, btn):
        for row in range(self.headers_table.rowCount()):
            if self.headers_table.cellWidget(row, 2) == btn:
                self.headers_table.removeRow(row)
                break
        self._refresh_sensitive_warning()

    def _on_header_item_changed(self, item):
        """请求头单元格内容变化 → 刷新灰字、敏感提示、时间头形态。"""
        if item.column() == 1:
            self._refresh_header_style()
            self._refresh_time_preview()
        elif item.column() == 0:
            # 键名变了：值栏要在「普通文本格」和「时区下拉框」之间切换
            self._rebuild_value_cells()
            self._refresh_header_style()
            self._refresh_sensitive_warning()
            self._refresh_time_preview()

    def _refresh_header_style(self):
        """值留空的请求头用暗灰字提示「这一行不会发送」。

        颜色必须跟随当前调色板取：程序全局用的是暗色主题（Text 为白色），
        早期版本在这里写死 #000，结果值栏是黑字、在深灰底上几乎看不见。
        """
        base_text = QColor(self.headers_table.palette().color(QPalette.Text))
        dim = QColor(base_text)
        dim.setAlpha(110)
        self.headers_table.blockSignals(True)
        try:
            for row in range(self.headers_table.rowCount()):
                combo = self.headers_table.cellWidget(row, 1)
                if isinstance(combo, QComboBox):
                    tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                    combo.setToolTip('每次请求前实时生成：\n' + make_date_value(tz)
                                     + '\n时区: ' + timezone_label(tz))
                    continue
                val_item = self.headers_table.item(row, 1)
                if not val_item:
                    continue
                if val_item.text().strip() == '':
                    val_item.setForeground(QBrush(dim))
                    val_item.setToolTip('值留空 = 不发送该头（可用于屏蔽内置默认头）')
                else:
                    # 传一个空 QBrush = 取消显式前景色，交回调色板默认色
                    val_item.setForeground(QBrush())
                    val_item.setToolTip('')
        finally:
            self.headers_table.blockSignals(False)
        self._refresh_sensitive_warning()

    def _collect_headers(self):
        """从表格收集请求头（保留空值，语义 = 显式不发送）。

        时间头的值栏是时区下拉框，这里把选中项还原成 ${DATE:时区} 占位符。
        """
        headers = {}
        for row in range(self.headers_table.rowCount()):
            key_item = self.headers_table.item(row, 0)
            if not key_item or not key_item.text().strip():
                continue
            key = key_item.text().strip()
            combo = self.headers_table.cellWidget(row, 1)
            if isinstance(combo, QComboBox):
                tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                headers[key] = f'${{DATE:{tz}}}'
            else:
                val_item = self.headers_table.item(row, 1)
                headers[key] = (val_item.text() if val_item else '')
        return headers

    def _refresh_sensitive_warning(self):
        """填了 cookie / token 之类就醒目提示：会被明文写进配置文件。"""
        try:
            headers = self._collect_headers()
        except Exception:
            return
        sensitive = [k for k in headers if is_sensitive_header(k)]
        if sensitive:
            names = '、'.join(sensitive[:6]) + (' 等' if len(sensitive) > 6 else '')
            self.headers_sensitive_label.setText(
                f"⚠️ 检测到敏感请求头：{names}。这些值会随图源配置一起保存到磁盘（"
                f"配置文件为加密存储，但导出/分享配置、或流程文件 .wbt 里可能是明文）。"
                f"建议改用 ${{ENV:环境变量名}} 占位符，运行时从系统环境变量取值。"
            )
            self.headers_sensitive_label.setVisible(True)
        else:
            self.headers_sensitive_label.setVisible(False)

    def _refresh_body_hint(self):
        method = self.method_combo.currentText().upper()
        text = self.body_edit.toPlainText().strip()
        if method not in ('POST', 'PUT', 'PATCH'):
            self.body_hint_label.setText(f"当前方法 {method} 不会发送请求体")
            self.body_hint_label.setStyleSheet("color: #c0392b; font-size: 11px;")
        elif text:
            self.body_hint_label.setText(f"将发送 {len(text)} 字符")
            self.body_hint_label.setStyleSheet("color: #888; font-size: 11px;")
        else:
            self.body_hint_label.setText("")
            self.body_hint_label.setStyleSheet("color: #888; font-size: 11px;")

    def validate_and_accept(self):
        path = self.path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "输入错误", "请输入子端口路径")
            return
        if not path.startswith('/'):
            self.path_edit.setText('/' + path)
        self.accept()

    def apply_data(self, data):
        self.method_combo.setCurrentText(data.get('method', 'GET'))
        self.path_edit.setText(data.get('path', '/'))
        self.desc_edit.setText(data.get('description', ''))

        # 恢复参数
        params = data.get('parameters', [])
        for p in params:
            for row in range(self.param_table.rowCount()):
                name_item = self.param_table.item(row, 0)
                if name_item and name_item.text() == p.get('name', ''):
                    type_combo = self.param_table.cellWidget(row, 1)
                    if type_combo and isinstance(type_combo, QComboBox):
                        type_combo.setCurrentText(p.get('type', 'string'))
                    value_edit = self.param_table.cellWidget(row, 2)
                    if value_edit and isinstance(value_edit, QLineEdit):
                        value_edit.setText(p.get('value', ''))
                    note_edit = self.param_table.cellWidget(row, 3)
                    if note_edit and isinstance(note_edit, QLineEdit):
                        note_edit.setText(p.get('note', ''))
                    break

        # 恢复请求头
        headers = data.get('headers', {})
        for k, v in headers.items():
            self._append_header_row(k, v)

        # 恢复请求体
        self.body_edit.setPlainText(data.get('body', '') or '')

        # 触发路径检测
        self.on_path_changed(self.path_edit.text())
        self._refresh_header_style()
        self._refresh_body_hint()

    def get_data(self):
        params = []
        for row in range(self.param_table.rowCount()):
            name_item = self.param_table.item(row, 0)
            if name_item:
                name = name_item.text()
                type_combo = self.param_table.cellWidget(row, 1)
                value_edit = self.param_table.cellWidget(row, 2)
                note_edit = self.param_table.cellWidget(row, 3)
                params.append({
                    'name': name,
                    'type': type_combo.currentText() if type_combo else 'string',
                    'value': value_edit.text() if value_edit else '',
                    'note': note_edit.text() if note_edit else '',
                    'location': 'path'
                })

        headers = self._collect_headers()

        return {
            'id': str(uuid.uuid4())[:8],
            'method': self.method_combo.currentText(),
            'path': self.path_edit.text().strip(),
            'description': self.desc_edit.text().strip(),
            'parameters': params,
            'headers': headers,
            'body': self.body_edit.toPlainText(),
            'last_response': ''
        }


# ==================== 原有配置加密类保持不变 ====================

class ConfigEncryptor:
    """配置文件加密解密工具类"""
    
    def __init__(self, password: str = None):
        self.password = password or self._get_default_password()
        self.salt = b'aliyun_config_salt_'
        
    def _get_default_password(self) -> str:
        try:
            import socket
            import getpass
            machine_info = f"{socket.gethostname()}_{getpass.getuser()}_aliyun_config"
            return hashlib.md5(machine_info.encode()).hexdigest()[:32]
        except:
            return "default_aliyun_config_password_2024"
    
    def _derive_key(self) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.password.encode()))
        return key
    
    def encrypt_data(self, data: str) -> str:
        try:
            key = self._derive_key()
            fernet = Fernet(key)
            encrypted_data = fernet.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted_data).decode()
        except Exception as e:
            global_logger.error(f"数据加密失败: {str(e)}")
            raise
    
    def decrypt_data(self, encrypted_data: str) -> str:
        try:
            key = self._derive_key()
            fernet = Fernet(key)
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted_data = fernet.decrypt(encrypted_bytes)
            return decrypted_data.decode()
        except Exception as e:
            global_logger.error(f"数据解密失败: {str(e)}")
            raise
    
    def encrypt_config_file(self, input_file: str, output_file: str = None) -> bool:
        if output_file is None:
            output_file = input_file
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                config_data = f.read()
            encrypted_data = self.encrypt_data(config_data)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(encrypted_data)
            global_logger.info(f"配置文件加密成功: {input_file} -> {output_file}")
            return True
        except Exception as e:
            global_logger.error(f"配置文件加密失败: {str(e)}")
            return False
    
    def decrypt_config_file(self, input_file: str, output_file: str = None) -> bool:
        if output_file is None:
            output_file = input_file
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                encrypted_data = f.read()
            decrypted_data = self.decrypt_data(encrypted_data)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(decrypted_data)
            global_logger.info(f"配置文件解密成功: {input_file} -> {output_file}")
            return True
        except Exception as e:
            global_logger.error(f"配置文件解密失败: {str(e)}")
            return False


class APITestWorker(QThread):
    """API测试工作线程"""
    test_completed = Signal(str, bool, str)
    
    def __init__(self, api_name, api_key, parent=None):
        super().__init__(parent)
        self.api_name = api_name
        self.api_key = api_key
        self.running = True
    
    def run(self):
        try:
            if self.api_name == "saucenao":
                result = self.test_saucenao()
            elif self.api_name == "serpapi":
                result = self.test_serpapi()
            else:
                result = (False, f"未知的API类型: {self.api_name}")
            self.test_completed.emit(self.api_name, result[0], result[1])
        except Exception as e:
            self.test_completed.emit(self.api_name, False, f"测试异常: {str(e)}")
    
    def test_saucenao(self):
        if not self.api_key:
            return (False, "API密钥为空")
        try:
            test_image_url = "https://saucenao.com/images/static/banner.gif"
            params = {
                "url": test_image_url,
                "output_type": 2,
                "api_key": self.api_key,
                "db": 999,
                "numres": 1
            }
            response = requests.get("https://saucenao.com/search.php", params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "results" in data:
                    return (True, f"API测试成功! 找到 {len(data['results'])} 个结果")
                else:
                    return (False, f"API响应异常: {data.get('message', '未知错误')}")
            else:
                return (False, f"HTTP错误: {response.status_code}")
        except Exception as e:
            return (False, f"请求失败: {str(e)}")
    
    def test_serpapi(self):
        if not self.api_key:
            return (False, "API密钥为空")
        try:
            params = {
                "engine": "google",
                "q": "test",
                "api_key": self.api_key
            }
            response = requests.get("https://serpapi.com/search", params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "search_metadata" in data:
                    return (True, "API测试成功! 搜索请求已完成")
                else:
                    return (False, f"API响应异常: {data.get('error', '未知错误')}")
            else:
                return (False, f"HTTP错误: {response.status_code}")
        except Exception as e:
            return (False, f"请求失败: {str(e)}")
    
    def stop(self):
        self.running = False


class AliyunTestWorker(QThread):
    """阿里云连接测试工作线程"""
    test_completed = Signal(dict)
    
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.running = True
    
    def run(self):
        try:
            if not HAS_ALIYUN:
                self.test_completed.emit({'success': False, 'message': '阿里云客户端模块不可用'})
                return
            initialized = initialize_aliyun_services(
                access_key_id=self.config['access_key_id'],
                access_key_secret=self.config['access_key_secret'],
                endpoint=self.config['endpoint'],
                bucket_name=self.config['bucket_name'],
                region_id=self.config['region_id']
            )
            if initialized:
                result = test_aliyun_connection()
                self.test_completed.emit(result)
            else:
                self.test_completed.emit({'success': False, 'message': '阿里云服务初始化失败'})
        except Exception as e:
            self.test_completed.emit({'success': False, 'message': f'测试过程中发生错误: {str(e)}'})
    
    def stop(self):
        self.running = False


class CSVConfigParser:
    """CSV配置文件解析器"""
    
    @staticmethod
    def parse_csv_file(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                for delimiter in [',', ';', '\t']:
                    try:
                        file.seek(0)
                        reader = csv.DictReader(file, delimiter=delimiter)
                        rows = list(reader)
                        if rows and CSVConfigParser.validate_csv_data(rows[0]):
                            return CSVConfigParser.extract_config(rows[0])
                    except Exception:
                        continue
            return CSVConfigParser.parse_csv_manually(file_path)
        except Exception as e:
            global_logger.error(f"CSV文件解析失败: {str(e)}")
            return None
    
    @staticmethod
    def validate_csv_data(row):
        old_format_valid = ('access_key_id' in row and row['access_key_id'] and
                           'access_key_secret' in row and row['access_key_secret'])
        new_format_valid = ('AccessKeyId' in row and row['AccessKeyId'] and
                           'AccessKeySecret' in row and row['AccessKeySecret'])
        return old_format_valid or new_format_valid
    
    @staticmethod
    def extract_config(row):
        config = {
            'access_key_id': '',
            'access_key_secret': '',
            'endpoint': 'oss-cn-hangzhou.aliyuncs.com',
            'bucket_name': '',
            'region_id': 'cn-hangzhou'
        }
        if 'AccessKeyId' in row and row['AccessKeyId']:
            config['access_key_id'] = row.get('AccessKeyId', '').strip()
            config['access_key_secret'] = row.get('AccessKeySecret', '').strip()
            config['bucket_name'] = row.get('BucketName', '').strip()
            config['endpoint'] = 'oss-cn-hangzhou.aliyuncs.com'
            config['region_id'] = 'cn-hangzhou'
        elif 'access_key_id' in row and row['access_key_id']:
            config['access_key_id'] = row.get('access_key_id', '').strip()
            config['access_key_secret'] = row.get('access_key_secret', '').strip()
            config['endpoint'] = row.get('endpoint', 'oss-cn-hangzhou.aliyuncs.com').strip()
            config['bucket_name'] = row.get('bucket_name', '').strip()
            config['region_id'] = row.get('region_id', 'cn-hangzhou').strip()
        return config
    
    @staticmethod
    def parse_csv_manually(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            for delimiter in [',', ';', '\t']:
                lines = content.strip().split('\n')
                if len(lines) < 2:
                    continue
                headers = [h.strip() for h in lines[0].split(delimiter)]
                key_id_idx = -1
                key_secret_idx = -1
                bucket_idx = -1
                for i, header in enumerate(headers):
                    header_lower = header.lower()
                    if (any(x in header_lower for x in ['access', 'key']) and
                        any(x in header_lower for x in ['id', 'keyid'])):
                        key_id_idx = i
                    elif (any(x in header_lower for x in ['access', 'key']) and
                          'secret' in header_lower):
                        key_secret_idx = i
                    elif any(x in header_lower for x in ['bucket', '存储桶', 'bucketname']):
                        bucket_idx = i
                if key_id_idx == -1 or key_secret_idx == -1:
                    continue
                data_line = lines[1].split(delimiter)
                if len(data_line) <= max(key_id_idx, key_secret_idx):
                    continue
                config = {
                    'access_key_id': data_line[key_id_idx].strip(),
                    'access_key_secret': data_line[key_secret_idx].strip(),
                    'endpoint': 'oss-cn-hangzhou.aliyuncs.com',
                    'bucket_name': data_line[bucket_idx].strip() if bucket_idx != -1 and bucket_idx < len(data_line) else '',
                    'region_id': 'cn-hangzhou'
                }
                if config['access_key_id'] and config['access_key_secret']:
                    return config
            return None
        except Exception as e:
            global_logger.error(f"手动解析CSV文件失败: {str(e)}")
            return None


class DragDropListWidget(QListWidget):
    """支持拖放的文件列表控件"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDrop)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if file_path.lower().endswith('.csv'):
                    if hasattr(self.parent(), 'handle_csv_dropped'):
                        self.parent().handle_csv_dropped(file_path)
        else:
            event.ignore()


# ==================== 主配置对话框（扩展版） ====================

class APIConfigDialog(QDialog):
    """API 和云服务配置对话框（包含图源配置）"""
    
    config_saved = Signal()  # 配置保存后触发，供端口画板刷新
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 和云服务配置")
        self.setMinimumSize(800, 650)
        
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "./data/api_config.json")
        self.config = self.load_config()
        
        # 图源配置相关状态
        self.current_image_source_index = -1
        self.current_endpoint_index = -1
        self.image_source_tabs = None  # 图源配置内部的子选项卡
        self.endpoint_list_widgets = {}  # 每个API入口对应的子端口列表
        self.endpoint_detail_widgets = {}  # 每个API入口对应的详情区域
        
        self.setup_ui()
        self.apply_config()
        
        self.test_workers = {}
        self.setup_auto_sync()
        self.setup_oss_status_display()
        self.update_oss_status_signal.connect(self.update_oss_status)
        self.encryptor = self.create_encryptor()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.tab_widget = QTabWidget()
        
        self.api_tab = self.create_api_tab()
        self.tab_widget.addTab(self.api_tab, "API 配置")
        
        self.aliyun_tab = self.create_aliyun_tab()
        self.tab_widget.addTab(self.aliyun_tab, "阿里云配置")
        
        # ========== 新增：图源配置选项卡 ==========
        self.image_source_tab = self.create_image_source_tab()
        self.tab_widget.addTab(self.image_source_tab, "图源配置")
        
        layout.addWidget(self.tab_widget)
        
        self.status_label = QLabel()
        self.status_label.setStyleSheet("padding: 5px; background-color: #f0f0f0; border: 1px solid #ccc;")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        
        button_layout = QHBoxLayout()
        
        self.save_btn = QPushButton("保存所有配置")
        self.save_btn.clicked.connect(self.save_config)
        button_layout.addWidget(self.save_btn)
        
        self.test_all_btn = QPushButton("测试所有连接")
        self.test_all_btn.clicked.connect(self.test_all_connections)
        button_layout.addWidget(self.test_all_btn)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)

        debug_btn = QPushButton("调试配置")
        debug_btn.clicked.connect(self.debug_config_file)
        button_layout.addWidget(debug_btn)

    # ==================== 图源配置相关方法 ====================
    def _export_response_json(self):
        """导出当前响应文本为JSON文件"""
        text = self._ep_response_text.toPlainText().strip()
        if not text or text == "（尚未调试）":
            QMessageBox.warning(self, "导出失败", "没有可导出的响应内容")
            return
        # 验证是否为有效JSON（可选）
        try:
            json.loads(text)
        except json.JSONDecodeError:
            reply = QMessageBox.question(
                self, "格式不标准",
                "响应文本不是有效的JSON，仍然导出吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出响应JSON", "response.json", "JSON Files (*.json)"
        )
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                QMessageBox.information(self, "导出成功", f"响应已成功导出到:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "导出失败", f"写入文件时出错: {str(e)}")

    def create_image_source_tab(self):
        """创建图源配置选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("📷 API入口列表:"))

        self.add_source_btn = QPushButton("➕ 新建API入口")
        self.add_source_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745; color: white; border-radius: 4px;
                padding: 6px 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #218838; }
        """)
        self.add_source_btn.clicked.connect(self.add_image_source)
        toolbar.addWidget(self.add_source_btn)

        self.edit_source_btn = QPushButton("✏️ 编辑入口")
        self.edit_source_btn.clicked.connect(self.edit_image_source)
        self.edit_source_btn.setEnabled(False)
        toolbar.addWidget(self.edit_source_btn)

        # ---- 导入 / 分享入口：与「已保存流程图」的「导入图纸」同一套思路 ----
        self.import_source_btn = QPushButton("📥 导入入口")
        self.import_source_btn.setToolTip(
            "从 .apientry.json 文件导入 API 入口（可一次多选多个）。\n"
            "别人用「📤 分享入口」导出的文件直接拿来导入就能用；\n"
            "也兼容整套配置文件（含 image_sources 的 JSON）。\n"
            "重名会自动加 (2)/(3)… 后缀。")
        self.import_source_btn.clicked.connect(self.import_image_sources)
        toolbar.addWidget(self.import_source_btn)

        self.share_source_btn = QPushButton("📤 分享入口")
        self.share_source_btn.setToolTip(
            "把当前选中的 API 入口导出成 .apientry.json 文件，直接发给别人即可。\n"
            "文件里含入口名、基础 URL 与它的全部子端口（描述 / 参数 / 注释 / 请求头 / 请求体）。")
        self.share_source_btn.clicked.connect(self.share_image_source)
        self.share_source_btn.setEnabled(False)
        toolbar.addWidget(self.share_source_btn)

        self.delete_source_btn = QPushButton("🗑️ 删除入口")
        self.delete_source_btn.setStyleSheet("color: #d9534f;")
        self.delete_source_btn.clicked.connect(self.delete_image_source)
        self.delete_source_btn.setEnabled(False)
        toolbar.addWidget(self.delete_source_btn)

        toolbar.addStretch()

        # ---- 新手引导：遮罩 + 聚光灯 + 交互式推进（与端口画板同一套引擎）----
        self.tour_btn = QPushButton("🎓 新手引导")
        self.tour_btn.setToolTip(
            "带高亮遮罩的交互式引导：该点的地方直接点，做完会自动继续。\n"
            "随时可按 Esc 退出；以后想重看就点这里。")
        self.tour_btn.clicked.connect(self._start_tour)
        toolbar.addWidget(self.tour_btn)

        layout.addLayout(toolbar)

        # 说明文字
        hint = QLabel("提示：每个API入口可包含多个子端口（路径），点击下方子选项卡切换。选中API入口后可编辑或删除。")
        hint.setStyleSheet("color: #888; font-size: 11px; padding: 4px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # API入口子选项卡（在"图源配置"内排列分布）
        self.image_source_tabs = QTabWidget()
        self.image_source_tabs.setTabsClosable(False)
        self.image_source_tabs.currentChanged.connect(self.on_image_source_tab_changed)
        layout.addWidget(self.image_source_tabs, 1)

        # 空状态提示
        self.empty_source_label = QLabel("暂无API入口，点击\"➕ 新建API入口\"按钮添加")
        self.empty_source_label.setAlignment(Qt.AlignCenter)
        self.empty_source_label.setStyleSheet("color: #aaa; font-size: 14px; padding: 40px;")
        layout.addWidget(self.empty_source_label)

        return tab

    def add_image_source(self):
        """新建API入口"""
        dialog = NewImageSourceDialog(self)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            sources = self.config.get('image_sources', [])
            sources.append({
                'name': data['name'],
                'base_url': data['base_url'],
                'endpoints': []
            })
            self.config['image_sources'] = sources
            self._refresh_image_source_tabs()
            # 选中新添加的
            self.image_source_tabs.setCurrentIndex(len(sources) - 1)
            global_logger.info(f"新增图源API入口: {data['name']}")

    def edit_image_source(self):
        """编辑当前选中的API入口"""
        idx = self.image_source_tabs.currentIndex()
        if idx < 0:
            return
        sources = self.config.get('image_sources', [])
        if idx >= len(sources):
            return
        existing = sources[idx]
        dialog = NewImageSourceDialog(self, existing_data=existing)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            sources[idx]['name'] = data['name']
            sources[idx]['base_url'] = data['base_url']
            self.config['image_sources'] = sources
            self._refresh_image_source_tabs()
            self.image_source_tabs.setCurrentIndex(idx)
            global_logger.info(f"编辑图源API入口: {data['name']}")

    def delete_image_source(self):
        """删除当前选中的API入口"""
        idx = self.image_source_tabs.currentIndex()
        if idx < 0:
            return
        sources = self.config.get('image_sources', [])
        if idx >= len(sources):
            return
        name = sources[idx]['name']
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除API入口 \"{name}\" 及其所有子端口吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            del sources[idx]
            self.config['image_sources'] = sources
            self._refresh_image_source_tabs()
            global_logger.info(f"删除图源API入口: {name}")

    # ==================== API 入口的导入 / 分享 ====================
    # 与「已保存流程图」的「导入图纸」同一套思路：一个入口 = 一个可分享的文件。
    ENTRY_FILE_SUFFIX = '.apientry.json'

    @staticmethod
    def _extract_entries_from_file(data):
        """从导入文件里取出 API 入口列表（返回 [] 表示这不是入口文件）。

        兼容三种形态：
          ① 本功能导出的 {"kind":..., "entry":{...}}
          ② 裸入口 {"name":..., "base_url":..., "endpoints":[...]}
          ③ 整套配置 {"image_sources":[...]}  → 全部当作入口导入
        """
        raw_list = []
        if not isinstance(data, dict):
            return []
        if isinstance(data.get('entry'), dict):
            raw_list.append(data['entry'])
        elif isinstance(data.get('image_sources'), list):
            raw_list.extend(s for s in data['image_sources'] if isinstance(s, dict))
        elif 'base_url' in data or 'endpoints' in data:
            raw_list.append(data)
        out = []
        for raw in raw_list:
            eps = raw.get('endpoints')
            out.append({
                'name': str(raw.get('name') or ''),
                'base_url': str(raw.get('base_url') or ''),
                'endpoints': [dict(ep) for ep in (eps or []) if isinstance(ep, dict)],
            })
        return out

    @staticmethod
    def _unique_entry_name(name, existing):
        """重名时自动加 (2)/(3)…，避免导入后两个入口同名分不清。"""
        name = str(name or '').strip() or '导入的入口'
        if name not in existing:
            return name
        i = 2
        while f"{name} ({i})" in existing:
            i += 1
        return f"{name} ({i})"

    def _entry_file_payload(self, source):
        """把一个 API 入口打包成可分享的字典。"""
        return {
            'kind': 'tianji.api_entry',
            'version': 1,
            'exported_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'entry': {
                'name': str(source.get('name') or ''),
                'base_url': str(source.get('base_url') or ''),
                'endpoints': [dict(ep) for ep in (source.get('endpoints') or [])
                              if isinstance(ep, dict)],
            },
        }

    def share_image_source(self):
        """分享入口：把当前选中的 API 入口导出成 .apientry.json。"""
        idx = self.image_source_tabs.currentIndex() if self.image_source_tabs else -1
        sources = self.config.get('image_sources', [])
        if idx < 0 or idx >= len(sources):
            QMessageBox.information(self, "分享入口", "请先在上面的列表里选中一个 API 入口。")
            return
        src = sources[idx]
        safe = re.sub(r'[\\/:*?"<>|]+', '_', str(src.get('name') or 'api_entry')).strip()
        default = os.path.join(os.path.expanduser('~'),
                               (safe or 'api_entry') + self.ENTRY_FILE_SUFFIX)
        path, _ = QFileDialog.getSaveFileName(
            self, "分享 API 入口", default,
            f"API 入口文件 (*{self.ENTRY_FILE_SUFFIX});;JSON 文件 (*.json)")
        if not path:
            return
        if not path.lower().endswith('.json'):
            path += self.ENTRY_FILE_SUFFIX
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._entry_file_payload(src), f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.critical(self, "分享失败", f"写入文件失败:\n{e}")
            return
        _n = len(src.get('endpoints') or [])
        QMessageBox.information(
            self, "分享入口",
            f"已导出 API 入口「{src.get('name')}」（{_n} 个子端口）：\n{path}\n\n"
            "把这个文件发给别人，对方用「📥 导入入口」即可直接用。")
        global_logger.info(f"分享图源API入口: {src.get('name')} -> {path}")

    def import_image_sources(self):
        """导入入口：从 .apientry.json 导入 API 入口（可多选）。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入 API 入口", '',
            f"API 入口文件 (*{self.ENTRY_FILE_SUFFIX});;JSON 文件 (*.json);;所有文件 (*.*)")
        if not paths:
            return
        sources = self.config.get('image_sources', [])
        existing = {str(s.get('name') or '') for s in sources}
        first_new = len(sources)
        ok_n, fail = 0, []
        for p in paths:
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                entries = self._extract_entries_from_file(data)
                if not entries:
                    fail.append(f"{os.path.basename(p)}：不是 API 入口文件")
                    continue
                for entry in entries:
                    entry['name'] = self._unique_entry_name(entry.get('name'), existing)
                    existing.add(entry['name'])
                    sources.append(entry)
                    ok_n += 1
            except Exception as e:
                fail.append(f"{os.path.basename(p)}：{e}")
        if ok_n:
            self.config['image_sources'] = sources
            self._refresh_image_source_tabs()
            try:
                self.image_source_tabs.setCurrentIndex(first_new)
            except Exception:
                pass
        _msg = f"成功导入 {ok_n} 个 API 入口。"
        if fail:
            _msg += "\n\n以下文件未能导入：\n" + "\n".join(fail[:8])
            if len(fail) > 8:
                _msg += f"\n… 另有 {len(fail) - 8} 个"
        if fail and not ok_n:
            QMessageBox.warning(self, "导入入口", _msg)
        else:
            QMessageBox.information(self, "导入入口", _msg)
        if ok_n:
            global_logger.info(f"导入图源API入口 {ok_n} 个")

    def on_image_source_tab_changed(self, index):
        """API入口子选项卡切换"""
        has_selection = index >= 0
        self.edit_source_btn.setEnabled(has_selection)
        self._set_share_enabled(has_selection)
        self.delete_source_btn.setEnabled(has_selection)
        self.current_image_source_index = index
        self.current_endpoint_index = -1

    def _set_share_enabled(self, on):
        """「分享入口」只在选中了某个入口时可用（控件可能还没建出来，容错）。"""
        try:
            self.share_source_btn.setEnabled(bool(on))
        except Exception:
            pass

    # ---------------- 新手引导（图源配置版，与端口画板同一套引擎） ----------------
    def _maybe_auto_tour(self, _tries=0):
        """首次打开「图源配置」时自动弹一次引导。

        · 只在"确实没看过"时弹（标记同样落在 `data/ui_state.json`，键是 image_source_v1）。
        · 窗口还没显示出来就等一会儿再试 —— 引导要高亮真实控件，没显示时量不到几何。
        · `PORT_PANEL_NO_TOUR=1` 可关掉自动弹（自动化/回归脚本要设）。
        """
        if os.environ.get('PORT_PANEL_NO_TOUR') == '1':
            return
        try:
            from tour_layer import tour_seen
            from tour_script_image_source import TOUR_KEY
        except Exception:
            return
        if tour_seen(TOUR_KEY):
            return
        try:
            if not self.isVisible():
                if _tries < 12:
                    QTimer.singleShot(400, lambda: self._maybe_auto_tour(_tries + 1))
                return
        except RuntimeError:
            return
        self._start_tour()

    def _start_tour(self):
        """开一次「图源配置」新手引导。首次打开自动调，之后点 🎓 新手引导 重看。"""
        cur = getattr(self, '_tour', None)
        if cur is not None:
            try:
                if cur.active:
                    return                     # 已经在跑了，别叠两层遮罩
            except RuntimeError:
                self._tour = None
        try:
            from tour_layer import GuidedTour
            from tour_script_image_source import TOUR_KEY, image_source_tour_steps
        except Exception as e:                                  # noqa: BLE001
            QMessageBox.warning(self, '新手引导', f'引导模块不可用：{e}')
            return
        try:
            self._tour = GuidedTour(image_source_tour_steps(self), self)
            self._tour.finished.connect(
                lambda done: self._on_tour_finished(TOUR_KEY, done))
            self._tour.start()
        except Exception as e:                                  # noqa: BLE001
            self._tour = None
            QMessageBox.critical(self, '新手引导', f'引导启动失败：{e}')

    def _on_tour_finished(self, key, completed):
        """引导收工：走完或跳过都记成"看过"，下次不再自动弹。"""
        try:
            from tour_layer import mark_tour_seen
            mark_tour_seen(key, True)
        except Exception:
            pass

    def _refresh_image_source_tabs(self):
        """刷新图源配置内部的子选项卡"""
        # 断开信号避免触发
        self.image_source_tabs.blockSignals(True)
        # 清除所有子选项卡
        while self.image_source_tabs.count() > 0:
            self.image_source_tabs.removeTab(0)

        self.endpoint_list_widgets.clear()
        self.endpoint_detail_widgets.clear()

        sources = self.config.get('image_sources', [])
        for src_idx, source in enumerate(sources):
            tab_content = self._create_source_tab_content(src_idx, source)
            self.image_source_tabs.addTab(tab_content, source['name'])

        self.image_source_tabs.blockSignals(False)

        # 显示/隐藏空状态
        has_sources = len(sources) > 0
        self.empty_source_label.setVisible(not has_sources)
        self.image_source_tabs.setVisible(has_sources)
        self.edit_source_btn.setEnabled(has_sources and self.image_source_tabs.currentIndex() >= 0)
        self.delete_source_btn.setEnabled(has_sources and self.image_source_tabs.currentIndex() >= 0)
        self._set_share_enabled(has_sources and self.image_source_tabs.currentIndex() >= 0)

    def _create_source_tab_content(self, src_idx, source):
        """为单个API入口创建子选项卡内容"""
        content = QWidget()
        layout = QVBoxLayout(content)

        # 基础URL信息栏
        info_bar = QHBoxLayout()
        info_label = QLabel(f"🔗 基础URL: <b>{source['base_url']}</b>")
        info_label.setTextFormat(Qt.RichText)
        info_bar.addWidget(info_label)
        info_bar.addStretch()

        # 添加子端口按钮
        add_ep_btn = QPushButton("➕ 添加子端口")
        add_ep_btn.setStyleSheet("""
            QPushButton {
                background-color: #007bff; color: white; border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #0069d9; }
        """)
        add_ep_btn.clicked.connect(lambda checked, si=src_idx: self.add_endpoint(si))
        info_bar.addWidget(add_ep_btn)
        # 挂到 self 上：新手引导要高亮它（`_ep_*` 那套按"当前入口页"存的写法，
        # 这里跟着走 —— 每个入口页重建时会被覆盖成该页的那个按钮）
        self._ep_add_btn = add_ep_btn
        layout.addLayout(info_bar)

        # 使用分割器：上部分子端口列表，下部分详情
        splitter = QSplitter(Qt.Vertical)

        # 子端口列表
        list_widget = QListWidget()
        list_widget.setMinimumHeight(80)
        list_widget.setMaximumHeight(150)          # ← 新增：防止无限拉伸
        list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        list_widget.customContextMenuRequested.connect(
            lambda pos, si=src_idx, lw=list_widget: self._endpoint_context_menu(pos, si, lw)
        )
        list_widget.currentRowChanged.connect(
            lambda row, si=src_idx: self._on_endpoint_selected(si, row)
        )
        self._populate_endpoint_list(list_widget, source)
        self.endpoint_list_widgets[src_idx] = list_widget
        splitter.addWidget(list_widget)

        # 子端口详情区域
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 8, 0, 0)

        

        # 详情标题
        detail_header = QLabel("📝 子端口详情（从列表中选择一个子端口进行查看/编辑）")
        detail_header.setStyleSheet("color: #666; font-size: 12px;")
        detail_layout.addWidget(detail_header)

        # 详情内容区
        self._endpoint_detail_stack = {}
        detail_content = QWidget()
        detail_content_layout = QVBoxLayout(detail_content)
        detail_content_layout.setContentsMargins(0, 4, 0, 0)

        # 方法+路径
        ep_info_row = QHBoxLayout()
        self._ep_method_label = QLabel("GET")
        self._ep_method_label.setStyleSheet("font-weight: bold; color: #007bff; font-size: 14px;")
        ep_info_row.addWidget(self._ep_method_label)
        self._ep_path_label = QLabel("/")
        self._ep_path_label.setStyleSheet("font-family: Consolas; font-size: 13px; color: #333;")
        ep_info_row.addWidget(self._ep_path_label, 1)
        detail_layout.addLayout(ep_info_row)

        # 注释
        self._ep_desc_label = QLabel("")
        self._ep_desc_label.setStyleSheet("color: #999; font-style: italic; font-size: 12px;")
        self._ep_desc_label.setWordWrap(True)
        detail_layout.addWidget(self._ep_desc_label)

        # 参数表格
        param_label = QLabel("📌 参数:")
        param_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        detail_layout.addWidget(param_label)

        self._ep_param_table = QTableWidget(0, 4)
        self._ep_param_table.setHorizontalHeaderLabels(["参数名", "类型", "值", "注释"])
        self._ep_param_table.horizontalHeader().setStretchLastSection(True)
        self._ep_param_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._ep_param_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._ep_param_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._ep_param_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._ep_param_table.setColumnWidth(1, 100)
        self._ep_param_table.setMinimumHeight(60)
        self._ep_param_table.setMaximumHeight(200)
        self._ep_param_table.verticalHeader().setVisible(False)
        detail_layout.addWidget(self._ep_param_table)

        # 请求头摘要（只读；敏感值打码）
        headers_label = QLabel("🔧 请求头:")
        headers_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        detail_layout.addWidget(headers_label)

        self._ep_headers_label = QLabel("")
        self._ep_headers_label.setWordWrap(True)
        self._ep_headers_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._ep_headers_label.setStyleSheet(
            "color: #555; font-size: 11px; font-family: Consolas, monospace; "
            "background: #f8f9fa; border: 1px solid #e9ecef; padding: 4px;"
        )
        detail_layout.addWidget(self._ep_headers_label)

        # Cookie 状态（需要登录态的接口全靠它；同一行放验证按钮）
        cookie_row = QHBoxLayout()
        cookie_row.setContentsMargins(0, 0, 0, 0)
        self._ep_cookie_label = QLabel("")
        self._ep_cookie_label.setWordWrap(True)
        self._ep_cookie_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._ep_cookie_label.setStyleSheet(
            "color: #555; font-size: 11px; background: #fffdf5; "
            "border: 1px solid #f0e6c8; padding: 4px;"
        )
        cookie_row.addWidget(self._ep_cookie_label, 1)
        self._ep_cookie_verify_btn = QPushButton("🔎 验证 Cookie")
        self._ep_cookie_verify_btn.setMaximumWidth(130)
        self._ep_cookie_verify_btn.setToolTip(
            "拿当前的请求头真打一次这个子接口：\n"
            "· 200 → Cookie 有效\n"
            "· 401/403 → 会再发一次「不带 Cookie」的对照请求，\n"
            "  用来区分「Cookie 失效」和「接口本身就不通」\n"
            "登录态接口在整合器里报 401 时，先点这里排掉 Cookie 的问题。")
        self._ep_cookie_verify_btn.setEnabled(False)
        self._ep_cookie_verify_btn.clicked.connect(self._verify_current_cookie)
        cookie_row.addWidget(self._ep_cookie_verify_btn)
        detail_layout.addLayout(cookie_row)

        # 操作按钮行
        btn_row = QHBoxLayout()
        self._ep_edit_btn = QPushButton("✏️ 编辑子端口")
        self._ep_edit_btn.clicked.connect(lambda: self._edit_current_endpoint())
        self._ep_edit_btn.setEnabled(False)
        btn_row.addWidget(self._ep_edit_btn)

        self._ep_debug_btn = QPushButton("🚀 调试")
        self._ep_debug_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffc107; color: #333; border-radius: 4px;
                padding: 6px 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #e0a800; }
        """)
        self._ep_debug_btn.clicked.connect(lambda: self._debug_current_endpoint())
        self._ep_debug_btn.setEnabled(False)
        btn_row.addWidget(self._ep_debug_btn)

        self._ep_delete_btn = QPushButton("🗑️ 删除")
        self._ep_delete_btn.setStyleSheet("color: #d9534f;")
        self._ep_delete_btn.clicked.connect(lambda: self._delete_current_endpoint())
        self._ep_delete_btn.setEnabled(False)
        btn_row.addWidget(self._ep_delete_btn)
        btn_row.addStretch()
        detail_layout.addLayout(btn_row)

        # 响应区域
        resp_header_layout = QHBoxLayout()
        resp_label = QLabel("📥 响应 (Responses):")
        resp_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        resp_header_layout.addWidget(resp_label)
        resp_header_layout.addStretch()
        self._ep_export_btn = QPushButton("📤 导出JSON")
        self._ep_export_btn.setMaximumWidth(120)
        self._ep_export_btn.setStyleSheet("padding: 4px 8px;")
        self._ep_export_btn.clicked.connect(lambda: self._export_response_json())
        self._ep_export_btn.setEnabled(False)
        resp_header_layout.addWidget(self._ep_export_btn)
        detail_layout.addLayout(resp_header_layout)

        self._ep_response_text = QTextEdit()
        self._ep_response_text.setReadOnly(True)
        self._ep_response_text.setPlaceholderText("调试后的JSON响应将显示在这里...")
        self._ep_response_text.setMaximumHeight(150)
        self._ep_response_text.setFont(QFont("Consolas", 10))
        self._ep_response_text.setStyleSheet("background-color: #f8f9fa; border: 1px solid #ddd; color: #000000;")
        detail_layout.addWidget(self._ep_response_text)

        # 状态栏
        self._ep_status_label = QLabel("")
        self._ep_status_label.setStyleSheet("font-size: 11px; color: #888;")
        detail_layout.addWidget(self._ep_status_label)

        # 上一次调试的请求/响应头与耗时（默认折叠）
        self._ep_debug_info_toggle = QPushButton("🔍 上一次调试详情（实际发出的请求头 / 响应头 / 耗时）")
        self._ep_debug_info_toggle.setCheckable(True)
        self._ep_debug_info_toggle.setChecked(False)
        self._ep_debug_info_toggle.setStyleSheet(
            "text-align: left; border: none; color: #007bff; font-size: 11px; padding: 2px;"
        )
        self._ep_debug_info_toggle.setVisible(False)
        self._ep_debug_info_toggle.toggled.connect(self._toggle_debug_info)
        detail_layout.addWidget(self._ep_debug_info_toggle)

        self._ep_debug_info = QTextEdit()
        self._ep_debug_info.setReadOnly(True)
        self._ep_debug_info.setMaximumHeight(150)
        self._ep_debug_info.setFont(QFont("Consolas", 9))
        self._ep_debug_info.setStyleSheet(
            "background-color: #f8f9fa; border: 1px solid #ddd; color: #333;"
        )
        self._ep_debug_info.setVisible(False)
        detail_layout.addWidget(self._ep_debug_info)

        detail_layout.addStretch()

        # 存储详情引用
        self.endpoint_detail_widgets[src_idx] = {
            'widget': detail_widget,
            'method_label': self._ep_method_label,
            'path_label': self._ep_path_label,
            'desc_label': self._ep_desc_label,
            'param_table': self._ep_param_table,
            'headers_label': self._ep_headers_label,
            'cookie_label': self._ep_cookie_label,
            'cookie_verify_btn': self._ep_cookie_verify_btn,
            'edit_btn': self._ep_edit_btn,
            'debug_btn': self._ep_debug_btn,
            'delete_btn': self._ep_delete_btn,
            'response_text': self._ep_response_text,
            'status_label': self._ep_status_label,
            'debug_info_toggle': self._ep_debug_info_toggle,
            'debug_info': self._ep_debug_info,
            'export_btn': self._ep_export_btn,
            'current_endpoint_index': -1
        }

        splitter.addWidget(detail_widget)
        splitter.setStretchFactor(0, 0)   # 列表不随窗口拉伸
        splitter.setStretchFactor(1, 1)   # 详情区域可拉伸
        splitter.setSizes([120, 400])     # 初始分配：上120px 下400px

        layout.addWidget(splitter, 1)
        return content

    def _populate_endpoint_list(self, list_widget, source):
        """填充子端口列表"""
        list_widget.clear()
        for ep in source.get('endpoints', []):
            method = ep.get('method', 'GET')
            path = ep.get('path', '/')
            desc = ep.get('description', '')
            display = f"[{method}] {path}"
            if desc:
                display += f" — {desc[:40]}"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, ep.get('id', ''))
            list_widget.addItem(item)

    def _endpoint_context_menu(self, pos, src_idx, list_widget):
        """子端口列表右键菜单"""
        item = list_widget.itemAt(pos)
        menu = QMenu(self)
        if item:
            edit_action = menu.addAction("✏️ 编辑")
            debug_action = menu.addAction("🚀 调试")
            menu.addSeparator()
            delete_action = menu.addAction("🗑️ 删除")
            action = menu.exec(list_widget.mapToGlobal(pos))
            if action == edit_action:
                self._edit_endpoint_by_index(src_idx, list_widget.row(item))
            elif action == debug_action:
                self._debug_endpoint_by_index(src_idx, list_widget.row(item))
            elif action == delete_action:
                self._delete_endpoint_by_index(src_idx, list_widget.row(item))
        else:
            add_action = menu.addAction("➕ 添加子端口")
            action = menu.exec(list_widget.mapToGlobal(pos))
            if action == add_action:
                self.add_endpoint(src_idx)

    def add_endpoint(self, src_idx):
        """添加子端口"""
        sources = self.config.get('image_sources', [])
        base_url = sources[src_idx].get('base_url', '') if src_idx < len(sources) else ''
        dialog = EndpointEditDialog(self, base_url=base_url)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            sources = self.config.get('image_sources', [])
            if src_idx < len(sources):
                sources[src_idx].setdefault('endpoints', []).append(data)
                self.config['image_sources'] = sources
                self._refresh_single_source_tab(src_idx)
                global_logger.info(f"添加子端口: [{data['method']}] {data['path']}")

    def _on_endpoint_selected(self, src_idx, row):
        """子端口列表选中"""
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        endpoints = sources[src_idx].get('endpoints', [])
        if row < 0 or row >= len(endpoints):
            self._clear_endpoint_detail(src_idx)
            return

        ep = endpoints[row]
        detail = self.endpoint_detail_widgets.get(src_idx)
        # 安全结束表格编辑状态，防止删除控件时崩溃
        table = detail.get('param_table')
        if table and table.state() == QAbstractItemView.EditingState:
            table.closePersistentEditor(table.currentIndex())
        if not detail:
            return

        detail['current_endpoint_index'] = row
        detail['method_label'].setText(ep.get('method', 'GET'))
        # 根据方法设置颜色
        method_colors = {'GET': '#28a745', 'POST': '#007bff', 'DELETE': '#d9534f', 'PUT': '#fd7e14', 'PATCH': '#6f42c1'}
        detail['method_label'].setStyleSheet(
            f"font-weight: bold; color: {method_colors.get(ep.get('method', 'GET'), '#007bff')}; font-size: 14px;"
        )
        detail['path_label'].setText(ep.get('path', '/'))
        detail['desc_label'].setText(ep.get('description', '（无注释）'))
        detail['edit_btn'].setEnabled(True)
        detail['debug_btn'].setEnabled(True)
        detail['delete_btn'].setEnabled(True)

        # 填充参数表格
        params = ep.get('parameters', [])
        detail['param_table'].setRowCount(0)
        for p in params:
            r = detail['param_table'].rowCount()
            detail['param_table'].insertRow(r)

            # 参数名（只读）
            name_item = QTableWidgetItem(p.get('name', ''))
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            detail['param_table'].setItem(r, 0, name_item)

            # 类型（可修改下拉框）
            type_combo = QComboBox()
            type_combo.addItems(["string", "path", "int", "float", "bool"])
            type_combo.setCurrentText(p.get('type', 'string'))
            detail['param_table'].setCellWidget(r, 1, type_combo)

            # 值
            detail['param_table'].setItem(r, 2, QTableWidgetItem(p.get('value', '')))

            # 注释
            detail['param_table'].setItem(r, 3, QTableWidgetItem(p.get('note', '')))

        # 请求头摘要（敏感值打码，只显示键与长度）
        headers = ep.get('headers', {}) or {}
        if headers:
            detail['headers_label'].setText(describe_headers(headers))
        else:
            detail['headers_label'].setText(
                '（无自定义请求头；实际发送时会带上内置默认头 '
                'User-Agent / Accept: application/json / Accept-Language）'
            )

        # Cookie 状态（需要登录态的接口会返回 401，先在这里确认）
        self._refresh_cookie_label(detail, headers)
        detail['cookie_verify_btn'].setEnabled(True)

        # 上一次调试详情默认折叠、并清空旧内容（避免串到别的子端口上）
        detail['debug_info'].clear()
        detail['debug_info'].setVisible(False)
        detail['debug_info_toggle'].setChecked(False)
        detail['debug_info_toggle'].setVisible(False)

        # 显示上次响应
        last_resp = ep.get('last_response', '')
        detail['response_text'].setPlainText(last_resp if last_resp else '（尚未调试）')
        body_text = (ep.get('body', '') or '').strip()
        detail['status_label'].setText(
            f"已选中子端口 #{row + 1} | 参数数量: {len(params)}"
            + f" | 请求头: {len(headers)} 个"
            + (f" | 请求体: {len(body_text)} 字符" if body_text else "")
        )

        self.current_endpoint_index = row
        last_resp = ep.get('last_response', '')
        detail['export_btn'].setEnabled(bool(last_resp))

    def _clear_endpoint_detail(self, src_idx):
        """清除子端口详情"""
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail:
            return
        detail['current_endpoint_index'] = -1
        detail['method_label'].setText('-')
        detail['method_label'].setStyleSheet("font-weight: bold; color: #aaa; font-size: 14px;")
        detail['path_label'].setText('/')
        detail['desc_label'].setText('')
        detail['edit_btn'].setEnabled(False)
        detail['debug_btn'].setEnabled(False)
        detail['delete_btn'].setEnabled(False)
        detail['param_table'].setRowCount(0)
        detail['headers_label'].setText('')
        detail['cookie_label'].setText('')
        detail['cookie_verify_btn'].setEnabled(False)
        detail['debug_info'].clear()
        detail['debug_info'].setVisible(False)
        detail['debug_info_toggle'].setChecked(False)
        detail['debug_info_toggle'].setVisible(False)
        detail['response_text'].clear()
        detail['status_label'].setText('')
        detail['export_btn'].setEnabled(False)
        self.current_endpoint_index = -1

    def _toggle_debug_info(self, checked):
        """展开/收起「上一次调试详情」。"""
        src_idx = self.image_source_tabs.currentIndex()
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail:
            return
        detail['debug_info'].setVisible(bool(checked))

    # ---------------- Cookie 状态与验证 ----------------

    @staticmethod
    def _refresh_cookie_label(detail, headers):
        """刷新详情面板里的 Cookie 状态行。"""
        if not detail or 'cookie_label' not in detail:
            return
        text = cookie_status_text(headers)
        label = detail['cookie_label']
        label.setText(text)
        if text.startswith('🍪 Cookie: 未配置'):
            label.setStyleSheet(
                "color: #8a6d3b; font-size: 11px; background: #fffdf5; "
                "border: 1px solid #f0e6c8; padding: 4px;")
        elif '空值' in text:
            label.setStyleSheet(
                "color: #8a6d3b; font-size: 11px; background: #fff8f5; "
                "border: 1px solid #f0d8c8; padding: 4px;")
        else:
            label.setStyleSheet(
                "color: #2e7d32; font-size: 11px; background: #f6fdf7; "
                "border: 1px solid #cfe9d3; padding: 4px;")

    @staticmethod
    def _build_endpoint_url(source, ep):
        """按子端口定义拼出可直接请求的完整 URL（路径参数用配置值替换）。"""
        path = ep.get('path', '/')
        for p in ep.get('parameters', []) or []:
            placeholder = '{' + str(p.get('name', '')) + '}'
            if placeholder in path:
                path = path.replace(placeholder, str(p.get('value', '')))
        return (source.get('base_url', '') or '') + path

    def _verify_current_cookie(self):
        """详情面板「🔎 验证 Cookie」按钮。"""
        src_idx = self.image_source_tabs.currentIndex()
        detail = self.endpoint_detail_widgets.get(src_idx)
        ep_idx = detail.get('current_endpoint_index', -1) if detail else -1
        if ep_idx is None or ep_idx < 0:
            return
        self._verify_cookie_by_index(src_idx, ep_idx)

    def _verify_cookie_by_index(self, src_idx, ep_idx):
        """真打一次子接口验证 Cookie。

        带 Cookie 成功 → 直接下结论。
        带 Cookie 失败（401/403）→ 再发一次**不带 Cookie** 的对照请求：
          · 对照也失败 → 接口本身就不通 / Cookie 没被接受
          · 对照成功   → 说明接口不需要登录态，是这条 Cookie 把请求搞坏了
        """
        self._save_ui_params_to_config(src_idx, ep_idx)
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        source = sources[src_idx]
        endpoints = source.get('endpoints', [])
        if ep_idx >= len(endpoints):
            return
        ep = endpoints[ep_idx]
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail:
            return

        headers = dict(ep.get('headers', {}) or {})
        cookie_val = ''
        for k, v in headers.items():
            if str(k).strip().lower() == 'cookie':
                cookie_val = str(v or '').strip()
                break

        full_url = self._build_endpoint_url(source, ep)

        if not cookie_val:
            detail['cookie_label'].setText(
                '⚠️ 这个子端口还没配 cookie 请求头 —— 需要登录态的接口一定会返回 401。\n'
                '点上面的「🍪 导入 Cookie」把浏览器里的 cookie 粘进来再验证。')
            self._refresh_cookie_label(detail, headers)
            return

        # 配的是 ${ENV:名} 而环境变量又没设 —— 最常见的一次性失败，先点出来
        _m = _ENV_PLACEHOLDER_RE.match(cookie_val)
        if _m and not os.environ.get(_m.group(1)):
            detail['cookie_label'].setText(
                f'⚠️ Cookie 存的是 ${{ENV:{_m.group(1)}}}，但环境变量 '
                f'{_m.group(1)} 当前没有设置 —— 这次请求不会带上 Cookie。\n'
                '在「🍪 导入 Cookie」里重新粘一次并点「💾 立即生效（本程序）」拿到 setx 命令，\n'
                '粘到终端执行一次，再回来点「📥 重新读取系统环境变量」即可，不必重启。')
            detail['cookie_label'].setStyleSheet('color: #8a6d3b; font-size: 11px; '
                                                 'background: #fffdf5; '
                                                 'border: 1px solid #f0e6c8; padding: 4px;')
            return

        detail['cookie_label'].setText(
            f'⏳ 正在验证… {full_url}\n{describe_cookie(cookie_val)}')
        detail['cookie_verify_btn'].setEnabled(False)
        self._start_cookie_probe(src_idx, ep_idx, full_url, headers,
                                 with_cookie=True, note=describe_cookie(cookie_val))

    def _start_cookie_probe(self, src_idx, ep_idx, full_url,
                            headers, with_cookie, first_meta=None, note=''):
        """发一次探测请求。with_cookie=False 时用作对照。"""
        method = 'GET'
        probe_headers = dict(headers or {})
        if not with_cookie:
            probe_headers = {k: v for k, v in probe_headers.items()
                             if str(k).strip().lower() != 'cookie'}

        worker = EndpointDebugWorker(method, full_url, probe_headers, {}, None, self)
        worker.debug_completed.connect(
            lambda resp, code, err, meta, si=src_idx, ei=ep_idx,
                   wc=with_cookie, fm=first_meta, nt=note,
                   u=full_url, hs=headers:
            self._handle_cookie_probe(si, ei, resp, code, err, meta,
                                      wc, fm, nt, u, hs))
        worker.start()
        if not hasattr(self, '_verify_workers'):
            self._verify_workers = []
        self._verify_workers.append(worker)
        # 只留最近几次，避免线程对象越攒越多
        if len(self._verify_workers) > 6:
            self._verify_workers = self._verify_workers[-6:]

    def _handle_cookie_probe(self, src_idx, ep_idx, response_text, status_code,
                             error_msg, meta, with_cookie, first_meta, note,
                             full_url='', headers=None):
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail:
            return
        meta = meta or {}
        try:
            meta['status_code'] = status_code
        except Exception:
            pass
        code = int(status_code or 0)

        if not with_cookie:
            # 对照请求返回了：合起来给结论
            ok_code = int((first_meta or {}).get('status_code') or 0)
            if code in (401, 403):
                verdict = (f'❌ Cookie 没被接受（带 Cookie → {ok_code}，不带 Cookie → {code}）。\n'
                           '   Cookie 可能已过期、不完整，或少了 cf_clearance 这类风控 cookie。\n'
                           '   建议重新从浏览器 Request Headers 里整条复制一次再导入。')
                color = '#c62828'
            else:
                verdict = (f'⚠️ 不带 Cookie 反而通了（{code}），说明这个接口不需要登录态，\n'
                           f'   是配置的 Cookie 本身导致被拒（带 Cookie → {ok_code}）。\n'
                           '   把 cookie 请求头删掉再试，或用「🔎 验证 Cookie」重测。')
                color = '#8a6d3b'
            detail['cookie_label'].setText(verdict)
            detail['cookie_label'].setStyleSheet(f'color: {color}; font-size: 11px; '
                                                 'background: #fdf6f6; '
                                                 'border: 1px solid #f0d8d8; padding: 4px;')
            detail['status_label'].setText(f"Cookie 验证完成 | 带 Cookie: {ok_code} | 对照: {code}")
            detail['cookie_verify_btn'].setEnabled(True)
            self._show_probe_detail(detail, '（对照请求）', meta, first_meta)
            return

        # 带 Cookie 的那一次
        if error_msg:
            detail['cookie_label'].setText(
                f'⚠️ 验证请求失败：{error_msg}\n（网络/代理问题，不是 Cookie 的问题，稍后再试）')
            detail['cookie_label'].setStyleSheet('color: #8a6d3b; font-size: 11px; '
                                                 'background: #fffdf5; '
                                                 'border: 1px solid #f0e6c8; padding: 4px;')
            detail['cookie_verify_btn'].setEnabled(True)
            self._show_probe_detail(detail, '', meta, None)
            return

        if code == 200:
            detail['cookie_label'].setText(
                f'✅ Cookie 有效（HTTP 200，返回 {len(response_text or "")} 字节）。\n'
                f'{note}\n'
                '这个子端口的登录态没问题，整合器里可以放心用。')
            detail['cookie_label'].setStyleSheet('color: #2e7d32; font-size: 11px; '
                                                 'background: #f6fdf7; '
                                                 'border: 1px solid #cfe9d3; padding: 4px;')
            detail['status_label'].setText(f"✅ Cookie 验证通过 | 状态码: 200 | "
                                           f"通道: {'curl_cffi' if meta.get('used_curl') else 'requests'}")
            detail['cookie_verify_btn'].setEnabled(True)
            self._show_probe_detail(detail, '', meta, None)
            return

        if code in (401, 403):
            detail['cookie_label'].setText(
                f'⏳ 带 Cookie 返回 {code}，正在发一次「不带 Cookie」的对照请求，\n'
                '用来区分是 Cookie 失效还是接口本身不通…')
            self._start_cookie_probe(src_idx, ep_idx, full_url, headers or {},
                                     with_cookie=False, first_meta=meta)
            return

        detail['cookie_label'].setText(
            f'⚠️ 带 Cookie 请求返回 {code}（既不是 200 也不是 401/403）。\n'
            '接口通了但响应异常，看下面的响应内容；这种一般是路径/参数写错了。')
        detail['cookie_label'].setStyleSheet('color: #8a6d3b; font-size: 11px; '
                                             'background: #fffdf5; '
                                             'border: 1px solid #f0e6c8; padding: 4px;')
        detail['status_label'].setText(f"Cookie 验证：状态码 {code}")
        detail['cookie_verify_btn'].setEnabled(True)
        self._show_probe_detail(detail, '', meta, None)

    @staticmethod
    def _show_probe_detail(detail, prefix, meta, first_meta):
        """把探测请求的详情塞进「上一次调试详情」折叠区，并展开。"""
        try:
            chunks = []
            if prefix:
                chunks.append(f'=== 本次为{prefix} ===')
            chunks.append(APIConfigDialog._format_debug_info(meta))
            if first_meta:
                chunks.append('=== 对照：带 Cookie 的那一次 ===')
                chunks.append(APIConfigDialog._format_debug_info(first_meta))
            info = '\n'.join(c for c in chunks if c)
            if not info:
                return
            detail['debug_info'].setPlainText(info)
            detail['debug_info_toggle'].setVisible(True)
            detail['debug_info_toggle'].setChecked(True)
            detail['debug_info'].setVisible(True)
        except Exception:
            pass

    # 响应头里"值得先看"的键（按重要性排；★ 表示与状态码/登录态直接相关）
    _KEY_RESPONSE_HEADERS = (
        'set-cookie', 'www-authenticate', 'location', 'retry-after',
        'cf-mitigated', 'cf-ray', 'cf-cache-status', 'x-cache-status',
        'content-type', 'content-length', 'content-encoding',
        'vary', 'cache-control', 'server', 'date', 'expires',
    )
    _STAR_RESPONSE_HEADERS = ('set-cookie', 'www-authenticate', 'location',
                              'retry-after', 'cf-mitigated')

    @staticmethod
    def _diagnose_response(meta) -> list:
        """按状态码 + 实际发出的头 + 响应头，给出人话诊断。"""
        out = []
        code = int(meta.get('status_code') or 0)
        sent = {str(k).lower(): v for k, v in (meta.get('sent_headers') or {}).items()}
        rh = {str(k).lower(): v for k, v in (meta.get('response_headers') or {}).items()}
        has_cookie = str(sent.get('cookie') or '').strip() != ''
        has_auth = str(sent.get('authorization') or '').strip() != ''

        if code in (401, 403) and not has_cookie and not has_auth:
            out.append(f'返回 {code}，而本次请求没有带 Cookie / Authorization。'
                       '"收藏夹 / 我的 / 关注"这类接口基本都要求登录态 —— '
                       '请把浏览器里该站点的 Cookie 整条复制进来（键名 cookie）。')
        elif code in (401, 403) and has_cookie:
            out.append(f'已经带了 Cookie 但仍然被拒（{code}）—— Cookie 可能已过期、'
                       '不完整，或缺少同站其它 Cookie（session / cf_clearance 要一起带）。')
        if 'www-authenticate' in rh:
            out.append(f"服务器要求认证（www-authenticate: {rh['www-authenticate']}）"
                       "，可能需要 Authorization 头或 Bearer Token。")
        if 'set-cookie' in rh:
            out.append('服务器下发了 Set-Cookie —— 部分接口要求把下发的 Cookie 回填到后续请求。')
        if 'cookie' in str(rh.get('vary', '')).lower():
            out.append('响应头 vary 含 Cookie：同一 URL 会随 Cookie 返回不同内容，'
                       '不登录只能拿到公开数据、或直接被拒。')
        if 'retry-after' in rh:
            out.append(f"被限流，服务器要求等待 {rh['retry-after']} 秒后重试"
                       "（轮询这个接口时记得放慢）。")
        if 'cf-mitigated' in rh or ('cf-ray' in rh and code in (403, 429, 503)):
            out.append('命中 Cloudflare 风控（cf-ray / cf-mitigated）—— '
                       '可再点一次调试让它走 curl_cffi 通道，或检查请求头是否过于"不像浏览器"。')
        if 'location' in rh and code in (301, 302, 303, 307, 308):
            out.append(f"服务器重定向到：{rh['location']}")
        if not out and 200 <= code < 300:
            out.append('请求成功，未发现需要处理的异常特征。')
        return out

    @staticmethod
    def _format_debug_info(meta) -> str:
        """把 worker 回传的 meta 渲染成可读文本（敏感头打码）。"""
        if not meta:
            return ''
        lines = []

        via = 'curl_cffi (impersonate=chrome124)' if meta.get('used_curl') else 'requests'
        lines.append(f"耗时: {meta.get('elapsed_ms', 0)} ms    通道: {via}")

        attempts = meta.get('attempts') or []
        if len(attempts) > 1:
            chain = ' → '.join(
                f"{a.get('via')}:{a.get('code') or 'ERR'}" for a in attempts
            )
            lines.append(f"尝试链: {chain}")

        final_url = meta.get('final_url')
        if final_url:
            lines.append(f"最终URL: {final_url}")

        # ---- 响应诊断放最前面：用户最需要看的就是"为什么没通" ----
        diagnosis = APIConfigDialog._diagnose_response(meta)
        if diagnosis:
            lines.append('')
            lines.append('--- 诊断 ---')
            for d in diagnosis:
                lines.append(f'· {d}')

        missing = meta.get('missing_env') or []
        if missing:
            lines.append(f"⚠️ 未取到的环境变量: {', '.join(missing)}（对应请求头已按空值发送）")

        dynamic = meta.get('dynamic_headers') or []
        if dynamic:
            lines.append('🕒 实时时间头本次实际生成的值：')
            for d in dynamic:
                lines.append(f'   {d}')

        if not meta.get('curl_available'):
            lines.append("提示: 未安装 curl_cffi，遇到 Cloudflare 类风控无法自动降级")

        sent = meta.get('sent_headers') or {}
        lines.append('')
        lines.append(f'--- 实际发出的请求头 ({len(sent)}) ---')
        for k, v in sent.items():
            lines.append(f'{k}: {mask_header_value(k, v)}')

        # ---- 响应头：相关键排前面并打星，其余按原顺序跟在后面 ----
        rh = meta.get('response_headers') or {}
        lines.append('')
        lines.append(f'--- 响应头 ({len(rh)}) ---')
        if rh:
            lower_map = {}
            for k in rh:
                lower_map.setdefault(str(k).lower(), k)
            shown = set()
            starred = False
            for want in APIConfigDialog._KEY_RESPONSE_HEADERS:
                real = lower_map.get(want)
                if real is None or real in shown:
                    continue
                shown.add(real)
                mark = '★' if want in APIConfigDialog._STAR_RESPONSE_HEADERS else ' '
                if mark == '★':
                    starred = True
                lines.append(f'{mark} {real}: {rh[real]}')
            for k, v in rh.items():
                if k in shown:
                    continue
                lines.append(f'  {k}: {v}')
            if starred:
                lines.append('  （★ = 与状态码 / 登录态直接相关，优先看这几条）')
        else:
            lines.append('  （没有拿到响应头 —— 通常是连接阶段就失败了）')

        return '\n'.join(lines)

    def _edit_current_endpoint(self):
        """编辑当前选中的子端口"""
        src_idx = self.image_source_tabs.currentIndex()
        if src_idx < 0:
            return
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail or detail['current_endpoint_index'] < 0:
            return
        self._edit_endpoint_by_index(src_idx, detail['current_endpoint_index'])

    def _edit_endpoint_by_index(self, src_idx, ep_idx):
        """按索引编辑子端口"""
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        endpoints = sources[src_idx].get('endpoints', [])
        if ep_idx >= len(endpoints):
            return
        existing = endpoints[ep_idx]
        dialog = EndpointEditDialog(self, existing_data=existing,
                                    base_url=sources[src_idx].get('base_url', ''))
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            data['id'] = existing.get('id', str(uuid.uuid4())[:8])
            data['last_response'] = existing.get('last_response', '')
            endpoints[ep_idx] = data
            self.config['image_sources'] = sources
            self._refresh_single_source_tab(src_idx)
            # 重新选中
            list_widget = self.endpoint_list_widgets.get(src_idx)
            if list_widget and ep_idx < list_widget.count():
                list_widget.setCurrentRow(ep_idx)

    def _save_ui_params_to_config(self, src_idx, ep_idx):
        """将主页参数表格中的当前值保存到对应 endpoint 的配置中"""
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        endpoints = sources[src_idx].get('endpoints', [])
        if ep_idx >= len(endpoints):
            return
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail:
            return

        param_table = detail['param_table']
        params = []
        for row in range(param_table.rowCount()):
            name_item = param_table.item(row, 0)
            if name_item:
                name = name_item.text()
                type_combo = param_table.cellWidget(row, 1)
                value_item = param_table.item(row, 2)
                note_item = param_table.item(row, 3)
                params.append({
                    'name': name,
                    'type': type_combo.currentText() if type_combo else 'string',
                    'value': value_item.text() if value_item else '',
                    'note': note_item.text() if note_item else '',
                    'location': 'path'
                })
        endpoints[ep_idx]['parameters'] = params

    def _debug_current_endpoint(self):
        """调试当前选中的子端口"""
        src_idx = self.image_source_tabs.currentIndex()
        if src_idx < 0:
            return
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail or detail['current_endpoint_index'] < 0:
            return
        self._debug_endpoint_by_index(src_idx, detail['current_endpoint_index'])

    def _debug_endpoint_by_index(self, src_idx, ep_idx):
        """按索引调试子端口"""
        self._save_ui_params_to_config(src_idx, ep_idx)
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        source = sources[src_idx]
        endpoints = source.get('endpoints', [])
        if ep_idx >= len(endpoints):
            return

        ep = endpoints[ep_idx]
        base_url = source['base_url']
        path = ep.get('path', '/')
        method = ep.get('method', 'GET')
        params = ep.get('parameters', [])
        headers = ep.get('headers', {}) or {}
        body = ep.get('body', '') or ''

        # 构建URL：替换路径参数
        url_path = path
        for p in params:
            placeholder = '{' + p['name'] + '}'
            value = p.get('value', '')
            if placeholder in url_path:
                url_path = url_path.replace(placeholder, value)

        full_url = base_url + url_path

        # 更新状态
        detail = self.endpoint_detail_widgets.get(src_idx)
        if detail:
            wait_msg = [f"⏳ 正在调试...", f"URL: {full_url}"]
            if headers:
                wait_msg.append(f"请求头: {describe_headers(headers)}")
            if body and method in ('POST', 'PUT', 'PATCH'):
                wait_msg.append(f"请求体: {len(body)} 字符")
            detail['response_text'].setPlainText('\n'.join(wait_msg))
            detail['status_label'].setText("调试中...")
            detail['debug_btn'].setEnabled(False)

        # 启动调试线程（首次走 requests；被 403/风控拦下会自动降级 curl_cffi）
        worker = EndpointDebugWorker(method, full_url, headers, {}, body, self)
        worker.debug_completed.connect(
            lambda resp, code, err, meta, si=src_idx, ei=ep_idx: self._handle_debug_result(si, ei, resp, code, err, meta)
        )
        worker.start()
        # 保存引用
        if not hasattr(self, '_debug_workers'):
            self._debug_workers = {}
        self._debug_workers[(src_idx, ep_idx)] = worker

    def _handle_debug_result(self, src_idx, ep_idx, response_text, status_code, error_msg, meta=None):
        """处理调试结果"""
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        endpoints = sources[src_idx].get('endpoints', [])
        if ep_idx >= len(endpoints):
            return

        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail:
            return

        meta = meta or {}
        # 交给 _format_debug_info 做响应诊断用（它只收一个参数，保持签名不变）
        try:
            meta['status_code'] = status_code
        except Exception:
            pass
        via = 'curl_cffi' if meta.get('used_curl') else 'requests'

        if error_msg:
            display = f"❌ 调试失败\n状态码: {status_code}\n错误: {error_msg}"
            detail['response_text'].setPlainText(display)
            detail['status_label'].setText(
                f"调试失败 | 状态码: {status_code} | 通道: {via} | {meta.get('elapsed_ms', 0)} ms"
            )
        else:
            # 尝试美化JSON
            try:
                parsed = json.loads(response_text)
                formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
            except:
                formatted = response_text
            detail['response_text'].setPlainText(formatted)
            detail['status_label'].setText(
                f"✅ 调试成功 | 状态码: {status_code} | 通道: {via} | {meta.get('elapsed_ms', 0)} ms"
            )
            # 保存响应
            endpoints[ep_idx]['last_response'] = formatted
            self.config['image_sources'] = sources
            detail['export_btn'].setEnabled(True)

        # 请求头/响应头/耗时详情（折叠区，默认不展开）
        info = self._format_debug_info(meta)
        if info:
            detail['debug_info'].setPlainText(info)
            detail['debug_info_toggle'].setVisible(True)
            detail['debug_info_toggle'].setChecked(False)
            detail['debug_info'].setVisible(False)
            # 被风控拦下或降级重试过，自动展开，省得用户以为「就是没数据」
            attempts = meta.get('attempts') or []
            if len(attempts) > 1 or status_code in (401, 403, 429, 503):
                detail['debug_info_toggle'].setChecked(True)
                detail['debug_info'].setVisible(True)

        detail['debug_btn'].setEnabled(True)

        # 清理worker引用
        if hasattr(self, '_debug_workers'):
            self._debug_workers.pop((src_idx, ep_idx), None)

    def _delete_current_endpoint(self):
        """删除当前选中的子端口"""
        src_idx = self.image_source_tabs.currentIndex()
        if src_idx < 0:
            return
        detail = self.endpoint_detail_widgets.get(src_idx)
        if not detail or detail['current_endpoint_index'] < 0:
            return
        self._delete_endpoint_by_index(src_idx, detail['current_endpoint_index'])

    def _delete_endpoint_by_index(self, src_idx, ep_idx):
        """按索引删除子端口"""
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            return
        endpoints = sources[src_idx].get('endpoints', [])
        if ep_idx >= len(endpoints):
            return
        ep = endpoints[ep_idx]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除子端口 [{ep.get('method', 'GET')}] {ep.get('path', '/')} 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            del endpoints[ep_idx]
            self.config['image_sources'] = sources
            self._refresh_single_source_tab(src_idx)
            global_logger.info(f"删除子端口: [{ep.get('method', 'GET')}] {ep.get('path', '/')}")

    def _refresh_single_source_tab(self, src_idx):
        """刷新单个API入口子选项卡"""
        sources = self.config.get('image_sources', [])
        if src_idx >= len(sources):
            self._refresh_image_source_tabs()
            return

        source = sources[src_idx]
        # 更新子选项卡标题
        self.image_source_tabs.setTabText(src_idx, source['name'])

        # 更新子端口列表
        list_widget = self.endpoint_list_widgets.get(src_idx)
        if list_widget:
            self._populate_endpoint_list(list_widget, source)

        # 清除详情
        self._clear_endpoint_detail(src_idx)

    # ==================== 原有方法保持不变 ====================

    def debug_config_file(self):
        """调试配置文件状态"""
        try:
            if not os.path.exists(self.config_file):
                QMessageBox.information(self, "调试信息", "配置文件不存在")
                return
            with open(self.config_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            info = f"配置文件: {self.config_file}\n"
            info += f"文件大小: {len(content)} 字符\n"
            info += f"是否加密: {self._looks_like_encrypted(content)}\n"
            if content:
                info += f"前100字符: {content[:100]}\n"
                if self._looks_like_encrypted(content):
                    encryptor = self.create_encryptor()
                    if encryptor:
                        try:
                            decrypted = encryptor.decrypt_data(content)
                            info += f"解密成功: {len(decrypted)} 字符\n"
                            info += f"解密预览: {decrypted[:200]}\n"
                        except Exception as e:
                            info += f"解密失败: {str(e)}\n"
            QMessageBox.information(self, "配置文件调试信息", info)
        except Exception as e:
            QMessageBox.critical(self, "调试错误", f"调试失败: {str(e)}")

    def create_encryptor(self):
        if not HAS_CRYPTOGRAPHY:
            global_logger.warning("加密库不可用，配置文件将以明文存储")
            return None
        try:
            import socket
            import getpass
            machine_info = f"{socket.gethostname()}_{getpass.getuser()}_aliyun_config"
            password = hashlib.md5(machine_info.encode()).hexdigest()[:32]
            global_logger.info("使用机器绑定密码创建加密器")
            return ConfigEncryptor(password)
        except Exception as e:
            global_logger.warning(f"创建机器绑定加密器失败，使用默认密码: {str(e)}")
            try:
                return ConfigEncryptor()
            except Exception as e2:
                global_logger.error(f"创建默认加密器也失败: {str(e2)}")
                return None
    
    def load_config(self):
        if not os.path.exists(self.config_file):
            global_logger.info(f"配置文件不存在: {self.config_file}")
            return self._get_default_config()
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            global_logger.info(f"配置文件内容长度: {len(content)} 字符")
            if not content:
                global_logger.warning("配置文件为空")
                return self._get_default_config()
            if self._looks_like_encrypted(content):
                global_logger.info("检测到加密配置文件，尝试解密...")
                encryptor = self.create_encryptor()
                if encryptor:
                    try:
                        decrypted_content = encryptor.decrypt_data(content)
                        global_logger.info(f"解密成功，内容长度: {len(decrypted_content)} 字符")
                        if decrypted_content.strip():
                            config = json.loads(decrypted_content)
                            global_logger.info("成功解密并解析配置文件")
                            return self._ensure_image_sources(config)
                        else:
                            global_logger.error("解密后的内容为空")
                            return self._get_default_config()
                    except json.JSONDecodeError as e:
                        global_logger.error(f"解密内容不是有效的JSON: {str(e)}")
                        return self._handle_corrupted_config(decrypted_content)
                    except Exception as e:
                        global_logger.error(f"配置文件解密失败: {str(e)}")
                        return self._load_plain_config()
                else:
                    global_logger.warning("加密器不可用，尝试明文读取")
                    return self._load_plain_config()
            else:
                global_logger.info("检测到明文配置文件")
                return self._load_plain_config()
        except Exception as e:
            global_logger.error(f"加载配置失败: {e}")
            return self._get_default_config()

    def _ensure_image_sources(self, config):
        """确保配置中包含image_sources字段"""
        if 'image_sources' not in config:
            config['image_sources'] = []
        return config

    def _get_default_config(self):
        return {
            "saucenao": "",
            "serpapi": "",
            "saucenao_cookie": "",
            "aliyun_access_key_id": "",
            "aliyun_access_key_secret": "",
            "aliyun_endpoint": "oss-cn-hangzhou.aliyuncs.com",
            "aliyun_bucket": "",
            "aliyun_region": "cn-hangzhou",
            "image_sources": []
        }

    def _handle_corrupted_config(self, decrypted_content: str) -> dict:
        global_logger.warning("配置文件可能已损坏，尝试修复...")
        try:
            if decrypted_content.startswith('\ufeff'):
                decrypted_content = decrypted_content[1:]
                global_logger.info("已去除BOM头")
            if decrypted_content.strip():
                config = json.loads(decrypted_content)
                global_logger.info("修复成功，配置文件有效")
                return self._ensure_image_sources(config)
        except:
            pass
        global_logger.error("无法修复配置文件，创建默认配置")
        self._backup_corrupted_config(decrypted_content)
        return self._get_default_config()

    def _backup_corrupted_config(self, content: str):
        try:
            backup_file = self.config_file + '.corrupted.backup'
            with open(backup_file, 'w', encoding='utf-8') as f:
                f.write(content)
            global_logger.info(f"已备份损坏的配置文件到: {backup_file}")
        except Exception as e:
            global_logger.error(f"备份损坏配置文件失败: {str(e)}")
    
    def _looks_like_encrypted(self, content: str) -> bool:
        try:
            base64.b64decode(content)
            return len(content) > 50
        except:
            return False
    
    def _load_plain_config(self):
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self._encrypt_and_save_config(config)
            global_logger.info("已自动加密明文配置文件")
            return self._ensure_image_sources(config)
        except Exception as e:
            global_logger.error(f"加载明文配置失败: {e}")
            return self._get_default_config()
    
    def _encrypt_and_save_config(self, config):
        # 备份文件经 secure_store 密文落盘（明文仅内存），杜绝明文泄漏。
        # 与 MONITORED_FILES 加密同步保持一致：未解锁时仅写内存缓存、不落盘。
        try:
            backup_file = self.config_file + '.plain.backup'
            if secure_store is not None:
                plain_bytes = json.dumps(config, indent=4, ensure_ascii=False).encode('utf-8')
                secure_store.set_plaintext(backup_file, plain_bytes)
                global_logger.info(f"已创建密文备份: {backup_file}")
            else:
                global_logger.warning("secure_store 不可用，跳过配置文件备份")
        except Exception as e:
            global_logger.warning(f"创建密文备份失败: {e}")
        if not HAS_CRYPTOGRAPHY:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            global_logger.warning("加密库不可用，配置文件以明文保存")
            return
        encryptor = self.create_encryptor()
        if encryptor:
            try:
                json_str = json.dumps(config, indent=4, ensure_ascii=False)
                global_logger.info(f"准备加密的JSON内容长度: {len(json_str)} 字符")
                encrypted_data = encryptor.encrypt_data(json_str)
                global_logger.info(f"加密后的数据长度: {len(encrypted_data)} 字符")
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    f.write(encrypted_data)
                global_logger.info("配置文件已加密保存")
                self._verify_encrypted_config()
            except Exception as e:
                global_logger.error(f"加密保存配置失败: {e}")
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
                global_logger.warning("加密失败，配置文件以明文保存")
        else:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            global_logger.warning("加密器创建失败，配置文件以明文保存")

    def _verify_encrypted_config(self):
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                encrypted_content = f.read().strip()
            encryptor = self.create_encryptor()
            if encryptor and encrypted_content:
                decrypted_content = encryptor.decrypt_data(encrypted_content)
                if decrypted_content.strip():
                    json.loads(decrypted_content)
                    global_logger.info("加密配置文件验证成功")
                else:
                    global_logger.error("加密配置文件验证失败：解密后内容为空")
            else:
                global_logger.warning("跳过加密配置文件验证")
        except Exception as e:
            global_logger.error(f"加密配置文件验证失败: {str(e)}")

    def setup_auto_sync(self):
        if hasattr(self, 'aliyun_tab'):
            sync_layout = QHBoxLayout()
            self.auto_sync_check = QCheckBox("启用自动文件同步")
            self.auto_sync_check.setChecked(True)
            sync_layout.addWidget(self.auto_sync_check)
            self.auto_sync_interval = QComboBox()
            self.auto_sync_interval.addItems(["每5分钟", "每15分钟", "每30分钟", "每1小时", "每2小时"])
            self.auto_sync_interval.setCurrentIndex(1)
            sync_layout.addWidget(QLabel("检测间隔:"))
            sync_layout.addWidget(self.auto_sync_interval)
            sync_layout.addStretch()
            self.check_sync_btn = QPushButton("立即检测文件同步")
            self.check_sync_btn.clicked.connect(self.check_file_sync)
            sync_layout.addWidget(self.check_sync_btn)
    
    def check_file_sync(self):
        if not HAS_ALIYUN:
            QMessageBox.warning(self, "错误", "阿里云客户端不可用")
            return
        try:
            from user import EventViewerDialog
            temp_viewer = EventViewerDialog(self)
            events = temp_viewer.scan_for_sync_events()
            if events:
                reply = QMessageBox.information(
                    self, "发现文件同步事件",
                    f"发现 {len(events)} 个需要同步的文件事件。是否打开事件查看器？",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.open_event_viewer()
            else:
                QMessageBox.information(self, "检测完成", "所有文件都是最新的，无需同步。")
        except Exception as e:
            QMessageBox.warning(self, "检测失败", f"文件同步检测失败: {str(e)}")
    
    def setup_oss_status_display(self):
        if hasattr(self, 'aliyun_tab'):
            status_layout = QHBoxLayout()
            self.oss_status_label = QLabel("OSS状态: 未知")
            self.oss_status_label.setStyleSheet("font-weight: bold; padding: 5px;")
            status_layout.addWidget(self.oss_status_label)
            self.test_oss_btn = QPushButton("测试OSS连接")
            self.test_oss_btn.clicked.connect(self.test_oss_connection)
            status_layout.addWidget(self.test_oss_btn)
            status_layout.addStretch()
            self.update_oss_status()

    def update_oss_status(self):
        try:
            from aliyun_client import ensure_oss_initialized, get_global_aliyun_client
            if ensure_oss_initialized():
                client = get_global_aliyun_client()
                if client.connected:
                    self.oss_status_label.setText("✅ OSS状态: 已连接")
                    self.oss_status_label.setStyleSheet("color: green; font-weight: bold; padding: 5px;")
                else:
                    self.oss_status_label.setText("⚠️ OSS状态: 连接异常")
                    self.oss_status_label.setStyleSheet("color: orange; font-weight: bold; padding: 5px;")
            else:
                self.oss_status_label.setText("❌ OSS状态: 未配置")
                self.oss_status_label.setStyleSheet("color: red; font-weight: bold; padding: 5px;")
        except Exception as e:
            self.oss_status_label.setText(f"❌ OSS状态: 错误 - {str(e)}")
            self.oss_status_label.setStyleSheet("color: red; font-weight: bold; padding: 5px;")

    def test_oss_connection(self):
        try:
            from aliyun_client import ensure_oss_initialized, get_global_aliyun_client
            self.save_aliyun_config()
            if ensure_oss_initialized():
                client = get_global_aliyun_client()
                result = client.test_connection()
                if result['success']:
                    QMessageBox.information(self, "成功", "OSS连接测试成功！")
                else:
                    QMessageBox.warning(self, "失败", f"OSS连接测试失败: {result.get('message', '未知错误')}")
            else:
                QMessageBox.warning(self, "失败", "OSS客户端初始化失败，请检查配置")
            self.update_oss_status()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"测试连接时发生错误: {str(e)}")

    def save_aliyun_config(self):
        aliyun_config = {
            "aliyun_access_key_id": self.access_key_id_edit.text().strip(),
            "aliyun_access_key_secret": self.access_key_secret_edit.text().strip(),
            "aliyun_endpoint": self.endpoint_edit.text().strip(),
            "aliyun_bucket": self.bucket_edit.text().strip(),
            "aliyun_region": self.region_edit.text().strip()
        }
        self.config.update(aliyun_config)
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            from aliyun_client import get_global_aliyun_client
            client = get_global_aliyun_client()
            if (aliyun_config["aliyun_access_key_id"] and aliyun_config["aliyun_access_key_secret"]):
                client.set_config(
                    aliyun_config["aliyun_access_key_id"],
                    aliyun_config["aliyun_access_key_secret"],
                    aliyun_config["aliyun_endpoint"],
                    aliyun_config["aliyun_bucket"],
                    aliyun_config["aliyun_region"]
                )
                if client.initialize_oss_client():
                    import threading
                    def test_in_background():
                        client.test_connection()
                        self.update_oss_status_signal.emit()
                    threading.Thread(target=test_in_background, daemon=True).start()
            QMessageBox.information(self, "成功", "阿里云配置已保存")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存阿里云配置失败: {e}")

    update_oss_status_signal = Signal()

    def open_event_viewer(self):
        try:
            from user import EventViewerDialog
            dialog = EventViewerDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开事件查看器: {str(e)}")
    
    def create_api_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        saucenao_group = QGroupBox("SauceNAO API")
        saucenao_layout = QGridLayout(saucenao_group)
        saucenao_layout.addWidget(QLabel("API 密钥:"), 0, 0)
        self.saucenao_key_edit = QLineEdit()
        self.saucenao_key_edit.setPlaceholderText("输入 SauceNAO API 密钥")
        saucenao_layout.addWidget(self.saucenao_key_edit, 0, 1)
        self.saucenao_test_btn = QPushButton("测试")
        self.saucenao_test_btn.clicked.connect(lambda: self.test_api("saucenao", self.saucenao_key_edit.text()))
        saucenao_layout.addWidget(self.saucenao_test_btn, 0, 2)
        saucenao_layout.addWidget(QLabel("状态:"), 1, 0)
        self.saucenao_status = QLabel("未配置")
        saucenao_layout.addWidget(self.saucenao_status, 1, 1)
        saucenao_layout.addWidget(QLabel("Cookie (用于自动获取密钥):"), 2, 0)
        self.saucenao_cookie_edit = QLineEdit()
        self.saucenao_cookie_edit.setPlaceholderText("输入从浏览器获取的Cookie字符串")
        saucenao_layout.addWidget(self.saucenao_cookie_edit, 2, 1)
        self.saucenao_cookie_test_btn = QPushButton("测试Cookie")
        self.saucenao_cookie_test_btn.clicked.connect(lambda: self.test_cookie("saucenao", self.saucenao_cookie_edit.text()))
        saucenao_layout.addWidget(self.saucenao_cookie_test_btn, 2, 2)
        layout.addWidget(saucenao_group)
        
        serpapi_group = QGroupBox("SerpAPI (Google 图片搜索)")
        serpapi_layout = QGridLayout(serpapi_group)
        serpapi_layout.addWidget(QLabel("API 密钥:"), 0, 0)
        self.serpapi_key_edit = QLineEdit()
        self.serpapi_key_edit.setPlaceholderText("输入 SerpAPI 密钥")
        serpapi_layout.addWidget(self.serpapi_key_edit, 0, 1)
        self.serpapi_test_btn = QPushButton("测试")
        self.serpapi_test_btn.clicked.connect(lambda: self.test_api("serpapi", self.serpapi_key_edit.text()))
        serpapi_layout.addWidget(self.serpapi_test_btn, 0, 2)
        serpapi_layout.addWidget(QLabel("状态:"), 1, 0)
        self.serpapi_status = QLabel("未配置")
        serpapi_layout.addWidget(self.serpapi_status, 1, 1)
        layout.addWidget(serpapi_group)
        
        other_group = QGroupBox("其他 API 配置")
        other_layout = QVBoxLayout(other_group)
        other_layout.addWidget(QLabel("预留其他 API 配置位置"))
        layout.addWidget(other_group)
        layout.addStretch()
        return tab
    
    def create_aliyun_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        if not HAS_ALIYUN:
            error_label = QLabel("阿里云客户端模块不可用\n\n请确保已安装以下依赖:\n"
                               "pip install oss2\n"
                               "pip install aliyun-python-sdk-core\n"
                               "pip install aliyun-python-sdk-ecs")
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: #d9534f; padding: 20px;")
            layout.addWidget(error_label)
            return tab
        
        csv_group = QGroupBox("CSV文件导入配置")
        csv_layout = QVBoxLayout(csv_group)
        drop_label = QLabel("拖放CSV文件到这里或点击选择文件")
        drop_label.setAlignment(Qt.AlignCenter)
        drop_label.setStyleSheet("""
            QLabel {
                border: 2px dashed #ccc;
                border-radius: 10px;
                padding: 10px;
                background-color: #f9f9f9;
                color: #666;
            }
            QLabel:hover {
                border-color: #007bff;
                background-color: #f0f8ff;
            }
        """)
        drop_label.setAcceptDrops(True)
        drop_label.dragEnterEvent = self.dragEnterEvent
        drop_label.dragMoveEvent = self.dragMoveEvent
        drop_label.dropEvent = self.dropEvent
        csv_layout.addWidget(drop_label)
        
        file_btn_layout = QHBoxLayout()
        self.select_csv_btn = QPushButton("选择CSV文件")
        self.select_csv_btn.clicked.connect(self.select_csv_file)
        file_btn_layout.addWidget(self.select_csv_btn)
        self.clear_csv_btn = QPushButton("清除配置")
        self.clear_csv_btn.clicked.connect(self.clear_aliyun_config)
        file_btn_layout.addWidget(self.clear_csv_btn)
        csv_layout.addLayout(file_btn_layout)
        
        recent_label = QLabel("最近使用的CSV文件:")
        csv_layout.addWidget(recent_label)
        self.recent_files_list = DragDropListWidget()
        self.recent_files_list.setMaximumHeight(80)
        self.recent_files_list.itemDoubleClicked.connect(self.on_recent_file_selected)
        csv_layout.addWidget(self.recent_files_list)
        layout.addWidget(csv_group)
        
        config_group = QGroupBox("阿里云配置")
        config_layout = QFormLayout(config_group)
        self.oss_status_indicator = QLabel("❓ 未知")
        self.oss_status_indicator.setStyleSheet("font-weight: bold;")
        config_layout.addRow("OSS服务状态:", self.oss_status_indicator)
        self.access_key_id_edit = QLineEdit()
        self.access_key_id_edit.setPlaceholderText("输入AccessKey ID")
        self.access_key_id_edit.setEchoMode(QLineEdit.Password)
        config_layout.addRow("AccessKey ID:", self.access_key_id_edit)
        self.access_key_secret_edit = QLineEdit()
        self.access_key_secret_edit.setPlaceholderText("输入AccessKey Secret")
        self.access_key_secret_edit.setEchoMode(QLineEdit.Password)
        config_layout.addRow("AccessKey Secret:", self.access_key_secret_edit)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("例如: oss-cn-hangzhou.aliyuncs.com")
        self.endpoint_edit.setText("oss-cn-hangzhou.aliyuncs.com")
        config_layout.addRow("OSS Endpoint:", self.endpoint_edit)
        self.bucket_edit = QLineEdit()
        self.bucket_edit.setPlaceholderText("输入存储桶名称")
        config_layout.addRow("Bucket名称:", self.bucket_edit)
        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText("例如: cn-hangzhou")
        self.region_edit.setText("cn-hangzhou")
        config_layout.addRow("区域ID:", self.region_edit)
        layout.addWidget(config_group)
        
        test_group = QGroupBox("连接测试")
        test_layout = QVBoxLayout(test_group)
        
        # ---------- 阿里云连接测试行 ----------
        test_btn_layout = QHBoxLayout()
        self.aliyun_test_btn = QPushButton("测试阿里云连接")
        self.aliyun_test_btn.clicked.connect(self.test_aliyun_connection)
        test_btn_layout.addWidget(self.aliyun_test_btn)
        self.aliyun_save_btn = QPushButton("保存阿里云配置")
        self.aliyun_save_btn.clicked.connect(self.save_aliyun_config)
        test_btn_layout.addWidget(self.aliyun_save_btn)
        test_layout.addLayout(test_btn_layout)
        
        self.aliyun_progress_bar = QProgressBar()
        self.aliyun_progress_bar.setVisible(False)
        test_layout.addWidget(self.aliyun_progress_bar)
        
        self.aliyun_test_result = QTextEdit()
        self.aliyun_test_result.setMaximumHeight(100)
        self.aliyun_test_result.setReadOnly(True)
        self.aliyun_test_result.setPlaceholderText("阿里云连接测试结果将显示在这里...")
        test_layout.addWidget(self.aliyun_test_result)
        
        # ---------- 本地同步服务控制行 ----------
        sync_group = QGroupBox("本地同步服务 (localhost)")
        sync_group.setMinimumHeight(120)
        sync_layout = QVBoxLayout(sync_group)
        
        
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("监听端口:"))
        self.sync_port_edit = QLineEdit()
        self.sync_port_edit.setPlaceholderText("9876")
        self.sync_port_edit.setMaximumWidth(80)
        self.sync_port_edit.setText("9876")
        port_row.addWidget(self.sync_port_edit)
        port_row.addStretch()
        
        self.sync_check_btn = QPushButton("启动/检查服务")
        self.sync_check_btn.clicked.connect(self.check_local_sync)
        port_row.addWidget(self.sync_check_btn)
        sync_layout.addLayout(port_row)
        
        self.sync_status_label = QLabel("服务未启动")
        self.sync_status_label.setStyleSheet("color: #888;")
        sync_layout.addWidget(self.sync_status_label)
        
        test_layout.addWidget(sync_group)
        layout.addWidget(test_group)
        
        info_label = QLabel(
            "注意！您的CSV文件在阿里云用户中每次只能创建一次！请妥善保存！\n"
            "CSV文件格式说明:\n"
            "CSV文件应包含以下列: access_key_id, access_key_secret, endpoint, bucket_name, region_id\n"
            "支持拖放操作，也可以点击选择文件按钮\n\n"
            "配置说明:\n"
            "1. 访问阿里云控制台获取AccessKey\n"
            "2. 创建OSS存储桶并获取Endpoint\n"
            "3. 确保ECS实例所在区域与配置一致"
        )
        info_label.setStyleSheet("padding: 10px; background-color: #fff3cd; border: 1px solid #ffeeba; color: #856404;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        self.load_recent_files()
        return tab
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if file_path.lower().endswith('.csv'):
                    self.handle_csv_file(file_path)
                    break
    
    def select_csv_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择阿里云配置CSV文件", "", "CSV文件 (*.csv);;所有文件 (*.*)")
        if file_path:
            self.handle_csv_file(file_path)
    
    def handle_csv_file(self, file_path):
        try:
            config = CSVConfigParser.parse_csv_file(file_path)
            if config:
                self.access_key_id_edit.setText(config['access_key_id'])
                self.access_key_secret_edit.setText(config['access_key_secret'])
                self.endpoint_edit.setText(config['endpoint'])
                self.bucket_edit.setText(config['bucket_name'])
                self.region_edit.setText(config['region_id'])
                self.add_to_recent_files(file_path)
                self.aliyun_test_result.setPlainText(f"✅ CSV文件导入成功!\n文件: {os.path.basename(file_path)}\n配置已自动填充到表单中")
                QMessageBox.information(self, "成功", "CSV文件配置导入成功!")
                global_logger.info(f"CSV文件配置导入成功: {file_path}")
            else:
                QMessageBox.warning(self, "导入失败", "无法解析CSV文件，请检查文件格式和内容")
                global_logger.warning(f"CSV文件解析失败: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"处理CSV文件时发生错误: {str(e)}")
            global_logger.error(f"处理CSV文件错误: {str(e)}")
    
    def add_to_recent_files(self, file_path):
        recent_files = self.config.get('aliyun_recent_files', [])
        if file_path in recent_files:
            recent_files.remove(file_path)
        recent_files.insert(0, file_path)
        recent_files = recent_files[:10]
        self.config['aliyun_recent_files'] = recent_files
        self.update_recent_files_list()
    
    def load_recent_files(self):
        self.update_recent_files_list()
    
    def update_recent_files_list(self):
        self.recent_files_list.clear()
        recent_files = self.config.get('aliyun_recent_files', [])
        for file_path in recent_files:
            if os.path.exists(file_path):
                item = QListWidgetItem(os.path.basename(file_path))
                item.setData(Qt.UserRole, file_path)
                self.recent_files_list.addItem(item)
    
    def on_recent_file_selected(self, item):
        file_path = item.data(Qt.UserRole)
        if os.path.exists(file_path):
            self.handle_csv_file(file_path)
        else:
            QMessageBox.warning(self, "文件不存在", "选择的文件不存在，已从列表中移除")
            self.remove_missing_recent_files()
    
    def remove_missing_recent_files(self):
        recent_files = self.config.get('aliyun_recent_files', [])
        recent_files = [f for f in recent_files if os.path.exists(f)]
        self.config['aliyun_recent_files'] = recent_files
        self.update_recent_files_list()
    
    def clear_aliyun_config(self):
        reply = QMessageBox.question(self, "确认清除", "确定要清除所有阿里云配置吗？", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.access_key_id_edit.clear()
            self.access_key_secret_edit.clear()
            self.endpoint_edit.setText("oss-cn-hangzhou.aliyuncs.com")
            self.bucket_edit.clear()
            self.region_edit.setText("cn-hangzhou")
            self.aliyun_test_result.clear()
            QMessageBox.information(self, "成功", "阿里云配置已清除")
    
    def apply_config(self):
        self.saucenao_key_edit.setText(self.config.get("saucenao", ""))
        self.saucenao_status.setText("已配置" if self.config.get("saucenao") else "未配置")
        self.saucenao_cookie_edit.setText(self.config.get("saucenao_cookie", ""))
        self.serpapi_key_edit.setText(self.config.get("serpapi", ""))
        self.serpapi_status.setText("已配置" if self.config.get("serpapi") else "未配置")
        if HAS_ALIYUN:
            self.access_key_id_edit.setText(self.config.get("aliyun_access_key_id", ""))
            self.access_key_secret_edit.setText(self.config.get("aliyun_access_key_secret", ""))
            self.endpoint_edit.setText(self.config.get("aliyun_endpoint", "oss-cn-hangzhou.aliyuncs.com"))
            self.bucket_edit.setText(self.config.get("aliyun_bucket", ""))
            self.region_edit.setText(self.config.get("aliyun_region", "cn-hangzhou"))
        # 刷新图源配置
        self._refresh_image_source_tabs()
    
    def save_config(self):
        self.config.update({
            "saucenao": self.saucenao_key_edit.text().strip(),
            "serpapi": self.serpapi_key_edit.text().strip(),
            "saucenao_cookie": self.saucenao_cookie_edit.text().strip(),
            "aliyun_access_key_id": self.access_key_id_edit.text().strip(),
            "aliyun_access_key_secret": self.access_key_secret_edit.text().strip(),
            "aliyun_endpoint": self.endpoint_edit.text().strip(),
            "aliyun_bucket": self.bucket_edit.text().strip(),
            "aliyun_region": self.region_edit.text().strip()
        })
        try:
            self._encrypt_and_save_config(self.config)
            self.saucenao_status.setText("已配置" if self.config["saucenao"] else "未配置")
            self.serpapi_status.setText("已配置" if self.config["serpapi"] else "未配置")
            QMessageBox.information(self, "成功", "所有配置已加密保存（含图源配置）")
            global_logger.info("所有配置已加密保存（含图源配置）")
            # 通知端口画板刷新
            self.config_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败: {e}")
            global_logger.error(f"保存配置失败: {e}")
    
    def test_api(self, api_name, api_key):
        if api_name == "saucenao":
            self.saucenao_status.setText("测试中...")
            self.saucenao_status.setStyleSheet("color: #856404;")
            self.saucenao_test_btn.setEnabled(False)
        elif api_name == "serpapi":
            self.serpapi_status.setText("测试中...")
            self.serpapi_status.setStyleSheet("color: #856404;")
            self.serpapi_test_btn.setEnabled(False)
        worker = APITestWorker(api_name, api_key, self)
        worker.test_completed.connect(self.handle_api_test_result)
        worker.start()
        self.test_workers[api_name] = worker
    
    def test_aliyun_connection(self):
        if not HAS_ALIYUN:
            QMessageBox.warning(self, "错误", "阿里云客户端模块不可用")
            return
        config = {
            'access_key_id': self.access_key_id_edit.text().strip(),
            'access_key_secret': self.access_key_secret_edit.text().strip(),
            'endpoint': self.endpoint_edit.text().strip(),
            'bucket_name': self.bucket_edit.text().strip(),
            'region_id': self.region_edit.text().strip()
        }
        if not config['access_key_id'] or not config['access_key_secret']:
            QMessageBox.warning(self, "输入错误", "请输入AccessKey ID和Secret")
            return
        self.aliyun_progress_bar.setVisible(True)
        self.aliyun_test_btn.setEnabled(False)
        self.aliyun_test_result.clear()
        self.aliyun_test_result.setPlainText("正在测试阿里云连接...")
        self.aliyun_test_thread = AliyunTestWorker(config, self)
        self.aliyun_test_thread.test_completed.connect(self.handle_aliyun_test_result)
        self.aliyun_test_thread.start()
    
    def handle_api_test_result(self, api_name, success, message):
        if api_name == "saucenao":
            if success:
                self.saucenao_status.setText("✓ 测试成功")
                self.saucenao_status.setStyleSheet("color: #155724;")
            else:
                self.saucenao_status.setText("✗ 测试失败")
                self.saucenao_status.setStyleSheet("color: #721c24;")
            self.saucenao_test_btn.setEnabled(True)
        elif api_name == "serpapi":
            if success:
                self.serpapi_status.setText("✓ 测试成功")
                self.serpapi_status.setStyleSheet("color: #155724;")
            else:
                self.serpapi_status.setText("✗ 测试失败")
                self.serpapi_status.setStyleSheet("color: #721c24;")
            self.serpapi_test_btn.setEnabled(True)
        if success:
            self.status_label.setText(f"{api_name} API测试成功: {message}")
            self.status_label.setStyleSheet("padding: 5px; background-color: #d4edda; border: 1px solid #c3e6cb; color: #155724;")
            global_logger.info(f"{api_name} API测试成功: {message}")
        else:
            self.status_label.setText(f"{api_name} API测试失败: {message}")
            self.status_label.setStyleSheet("padding: 5px; background-color: #f8d7da; border: 1px solid #f5c6cb; color: #721c24;")
            global_logger.warning(f"{api_name} API测试失败: {message}")
        self.status_label.setVisible(True)
    
    def handle_aliyun_test_result(self, result):
        self.aliyun_progress_bar.setVisible(False)
        self.aliyun_test_btn.setEnabled(True)
        if result['success']:
            result_lines = [f"✅ 阿里云连接测试成功！"]
            if result.get('oss_connected'):
                result_lines.append(f"OSS连接: 成功")
            else:
                if not result.get('oss_service_available', True):
                    result_lines.append(f"OSS连接: 服务未开通")
                else:
                    result_lines.append(f"OSS连接: 失败")
            if result.get('ecs_connected'):
                result_lines.append(f"ECS连接: 成功")
            else:
                result_lines.append(f"ECS连接: 失败")
            result_lines.append(f"消息: {result.get('message', '')}")
            result_lines.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result.get('timestamp', time.time())))}")
            if not result.get('oss_service_available', True):
                result_lines.append("\n📋 OSS服务开通指引:")
                result_lines.append("1. 登录阿里云控制台 (https://oss.console.aliyun.com)")
                result_lines.append("2. 点击'立即开通'对象存储服务")
                result_lines.append("3. 根据提示完成开通流程")
                result_lines.append("4. 创建存储桶(Bucket)")
                result_lines.append("5. 返回此对话框更新存储桶名称")
            result_text = "\n".join(result_lines)
            self.aliyun_test_result.setPlainText(result_text)
            if result.get('oss_connected'):
                QMessageBox.information(self, "成功", "阿里云连接测试成功！OSS和ECS服务均正常。")
            else:
                if not result.get('oss_service_available', True):
                    reply = QMessageBox.information(self, "连接成功但OSS未开通",
                        "ECS连接成功，但对象存储服务(OSS)尚未开通。\n\n是否要查看开通指引？",
                        QMessageBox.Yes | QMessageBox.No)
                    if reply == QMessageBox.Yes:
                        self.show_oss_activation_guide()
                else:
                    QMessageBox.warning(self, "部分成功", "ECS连接成功，但OSS连接失败。请检查OSS配置。")
            global_logger.info("阿里云连接测试完成")
        else:
            result_text = (
                f"❌ 阿里云连接测试失败！\n\n"
                f"错误信息: {result.get('message', '未知错误')}\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result.get('timestamp', time.time())))}\n\n"
                f"请检查:\n"
                f"1. AccessKey ID 和 Secret 是否正确\n"
                f"2. 网络连接是否正常\n"
                f"3. 存储桶名称和区域是否正确"
            )
            self.aliyun_test_result.setPlainText(result_text)
            QMessageBox.warning(self, "失败", "阿里云连接测试失败，请检查配置。")
            global_logger.warning(f"阿里云连接测试失败: {result.get('message', '未知错误')}")

    def show_oss_activation_guide(self):
        guide_dialog = QDialog(self)
        guide_dialog.setWindowTitle("OSS服务开通指引")
        guide_dialog.setMinimumSize(600, 400)
        layout = QVBoxLayout(guide_dialog)
        title_label = QLabel("阿里云对象存储服务(OSS)开通指引")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #1890ff;")
        layout.addWidget(title_label)
        steps_text = QTextEdit()
        steps_text.setReadOnly(True)
        steps_text.setHtml("""
        <h3>开通对象存储服务(OSS)步骤:</h3>
        <ol>
        <li><b>访问OSS控制台</b><br>
            打开 <a href="https://oss.console.aliyun.com">https://oss.console.aliyun.com</a><br>
            或登录阿里云控制台后搜索"对象存储OSS"</li><br>
        <li><b>开通服务</b><br>
            - 如果您是首次使用，系统会提示"立即开通"<br>
            - 点击"立即开通"按钮<br>
            - 阅读并同意服务协议<br>
            - 等待系统开通服务（通常立即生效）</li><br>
        <li><b>创建存储桶(Bucket)</b><br>
            - 开通后点击"创建Bucket"<br>
            - 填写Bucket名称（全局唯一）<br>
            - 选择区域（建议与ECS实例相同区域）<br>
            - 其他设置保持默认或根据需求调整</li><br>
        <li><b>获取配置信息</b><br>
            - Bucket名称：您创建的存储桶名称<br>
            - Endpoint：根据区域自动生成，格式为 oss-区域.aliyuncs.com<br>
            - Region：存储桶所在区域</li><br>
        <li><b>返回配置对话框</b><br>
            - 在此对话框中更新存储桶名称<br>
            - 重新测试连接</li>
        </ol>
        <h3>注意事项:</h3>
        <ul>
        <li>OSS服务按实际使用量收费，新用户有免费额度</li>
        <li>Bucket名称全局唯一，不能与其他用户重复</li>
        <li>建议选择与ECS实例相同的区域以减少网络延迟</li>
        <li>开通后可能需要几分钟时间生效</li>
        </ul>
        """)
        layout.addWidget(steps_text)
        button_layout = QHBoxLayout()
        open_console_btn = QPushButton("打开OSS控制台")
        open_console_btn.clicked.connect(lambda: self.open_url("https://oss.console.aliyun.com"))
        button_layout.addWidget(open_console_btn)
        copy_config_btn = QPushButton("复制配置说明")
        copy_config_btn.clicked.connect(lambda: self.copy_to_clipboard(steps_text.toPlainText()))
        button_layout.addWidget(copy_config_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(guide_dialog.accept)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
        guide_dialog.exec()

    def open_url(self, url):
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开浏览器: {str(e)}")

    def copy_to_clipboard(self, text):
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(self, "成功", "配置说明已复制到剪贴板")
    
    def test_all_connections(self):
        if self.saucenao_key_edit.text().strip():
            self.test_api("saucenao", self.saucenao_key_edit.text())
        if self.serpapi_key_edit.text().strip():
            self.test_api("serpapi", self.serpapi_key_edit.text())
        if (HAS_ALIYUN and self.access_key_id_edit.text().strip() and self.access_key_secret_edit.text().strip()):
            self.test_aliyun_connection()
        if (not self.saucenao_key_edit.text().strip() and
            not self.serpapi_key_edit.text().strip() and
            (not HAS_ALIYUN or not self.access_key_id_edit.text().strip())):
            QMessageBox.information(self, "提示", "请先配置服务密钥再进行测试")
    
    def test_cookie(self, api_name, cookie_str):
        if api_name != "saucenao" or not cookie_str:
            return
        test_url = "https://saucenao.com/user.php"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0'}
        try:
            cookie_dict = {}
            for part in cookie_str.split(';'):
                part = part.strip()
                if '=' in part:
                    k, v = part.split('=', 1)
                    cookie_dict[k] = v
            response = requests.get(test_url, headers=headers, cookies=cookie_dict, timeout=10)
            if response.status_code == 200:
                if "logout" in response.text.lower():
                    QMessageBox.information(self, "成功", "Cookie有效，登录状态正常。")
                else:
                    QMessageBox.warning(self, "警告", "Cookie可能已失效或无法验证登录状态。")
            else:
                QMessageBox.critical(self, "错误", f"HTTP错误: {response.status_code}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"测试失败: {str(e)}")
    
    def closeEvent(self, event):
        for worker in self.test_workers.values():
            if worker.isRunning():
                worker.stop()
                worker.wait()
        if hasattr(self, 'aliyun_test_thread') and self.aliyun_test_thread.isRunning():
            self.aliyun_test_thread.stop()
            self.aliyun_test_thread.wait()
        # 停止调试worker
        if hasattr(self, '_debug_workers'):
            for worker in self._debug_workers.values():
                if worker.isRunning():
                    worker.stop()
                    worker.wait()
        super().closeEvent(event)
    
    def get_config(self):
        return self.config
    
    def check_local_sync(self):
        """启动本地同步服务并进行连接检查"""
        try:
            port = int(self.sync_port_edit.text().strip() or 9876)
        except ValueError:
            QMessageBox.warning(self, "输入错误", "端口号必须为整数")
            return
        
        # 尝试启动服务
        server = get_local_sync_server()
        if not server.running:
            success = server.start(port)
            if not success:
                self.sync_status_label.setText("❌ 服务启动失败")
                self.sync_status_label.setStyleSheet("color: red;")
                return
        
        # 检查连接
        result = server.check_connection()
        if result['success']:
            self.sync_status_label.setText(f"✅ 服务运行中 (端口 {port})")
            self.sync_status_label.setStyleSheet("color: green;")
            QMessageBox.information(self, "连接成功", "本地同步服务已启动并正常运行")
        else:
            self.sync_status_label.setText("❌ 服务不可达")
            self.sync_status_label.setStyleSheet("color: red;")
            QMessageBox.warning(self, "连接失败", f"本地同步服务未响应: {result.get('message', '')}")


# ==================== 独立窗口：图源配置 ====================
class ImageSourceConfigDialog(APIConfigDialog):
    """**独立窗口**的「图源配置」——与「API 和云服务配置」里的图源配置是**同一套代码**。

    存在的意义：端口画板要能单独打开图源配置而不必先开「API 和云服务配置」；
    将来把端口画板抽成独立工具时，也不用拖着 API / 阿里云那两页走。

    为什么用继承而不是另写一份：这样那 30 多个图源相关方法（新建/编辑/删除入口、
    子端口增删改、参数表、Cookie 验证、调试、导入导出…）**原样继承**，
    行为与「API 和云服务配置」里的那一页**逐字一致**，不存在"抄一份、改着改着就不一样"的风险。

    只重写三处与另外两页强绑定的方法：

    ====================  ==========================================================
    ``setup_ui()``        只放图源配置这一页 + 自己的「保存 / 关闭」按钮行
    ``apply_config()``    原实现会去写 API / 阿里云页的控件（这里没有那些控件）
    ``save_config()``     同上，只保存（整个配置文件，图源与其它页共用一个文件）
    ====================  ==========================================================

    ``setup_auto_sync()`` / ``setup_oss_status_display()`` 原本就带
    ``if hasattr(self, 'aliyun_tab')`` 守卫，这里没建 ``aliyun_tab``，
    所以它们自动变成空操作 —— **不需要重写**。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 黑夜模式：独立窗口没有主程序那样的父窗口可继承，显式套一份与主程序相同的深色主题
        try:
            from dark_theme import apply_dark_theme
            apply_dark_theme(self)
        except Exception:
            pass
        self.setWindowTitle("图源配置")
        self.setMinimumSize(920, 660)
        # 首次打开自动弹一次新手引导（看过就不再打扰；延后一点等窗口真的显示出来）
        self._tour = None
        QTimer.singleShot(900, self._maybe_auto_tour)

    # ---------- 重写 1/3：只建图源这一页 ----------
    def setup_ui(self):
        layout = QVBoxLayout(self)

        self.image_source_tab = self.create_image_source_tab()
        layout.addWidget(self.image_source_tab, 1)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            "padding: 5px; background-color: #f0f0f0; border: 1px solid #ccc;")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        button_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存图源配置")
        self.save_btn.clicked.connect(self.save_config)
        button_layout.addWidget(self.save_btn)
        button_layout.addStretch()
        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    # ---------- 重写 2/3：只刷新图源 ----------
    def apply_config(self):
        """只刷新图源列表（不碰 API / 阿里云页的控件）。"""
        self._refresh_image_source_tabs()

    # ---------- 重写 3/3：只保存 ----------
    def save_config(self):
        """保存到与「API 和云服务配置」**同一个**配置文件（图源只是其中一段）。"""
        try:
            self._encrypt_and_save_config(self.config)
            QMessageBox.information(self, "成功", "图源配置已加密保存")
            global_logger.info("图源配置已加密保存（独立窗口）")
            self.config_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存配置失败: {e}")
            global_logger.error(f"保存配置失败: {e}")