# img_server.py
import sys, json, re, uuid, time, os, csv, pickle, shutil, hashlib, threading, tempfile, math, subprocess
from typing import List, Dict, Any, Optional
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False
from PySide6.QtWidgets import (
    QDialog, QTreeWidget, QTreeWidgetItem, QGraphicsScene, QGraphicsView,
    QGraphicsItem, QGraphicsPathItem, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit, QSpinBox,
    QDoubleSpinBox, QComboBox, QCheckBox, QGraphicsProxyWidget, QApplication, QMenu,
    QDialogButtonBox, QMessageBox, QWidget, QFormLayout, QGroupBox, QTextEdit,
    QGraphicsRectItem, QInputDialog, QFileDialog, QToolButton, QAbstractItemView,
    QScrollArea, QFrame, QTabWidget, QTabBar, QGraphicsObject, QRubberBand, QProgressBar,
    QGraphicsPixmapItem, QGraphicsTextItem, QStackedWidget, QStylePainter, QStyleOptionTab, QStyle,
    QColorDialog, QTableWidget, QTableWidgetItem, QHeaderView, QGridLayout,
)
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QPoint, Signal, QThread, QMimeData, QTimer, QLineF,
    QSizeF, QEvent, QObject, QRect, QSize, QPropertyAnimation, QEasingCurve
)
from PySide6.QtGui import (
    QPainter, QPen, QColor, QDrag, QPainterPath, QFont, QBrush, QTransform,
    QWheelEvent, QKeyEvent, QTextCursor, QPixmap, QPalette, QCursor
)

from portpanel.core.paths import project_root

from portpanel.ui.image_source import (APIConfigDialog, ImageSourceConfigDialog,
                               build_browser_headers,
                               resolve_env_in_headers, resolve_header_placeholders,
                               mask_header_value, describe_headers, is_sensitive_header,
                               BulkHeaderPasteDialog, is_time_header, make_date_value,
                               timezone_label, TIMEZONE_CHOICES, DEFAULT_TIMEZONE_KEY,
                               CookieImportDialog, describe_cookie,
                               describe_headers_sent, env_status_text,
                               reload_env_from_system)

# ================= 加强 HTTP 请求（支持 curl_cffi 回退） =================
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    curl_requests = None

# 下载超时：元组 (连接超时, 读取超时)。
# 连接超时 15s 快速失败；读取超时 300s 是"每次读取之间的间隔"，不是总时长，
# 因此远地区/慢速大文件只要持续有数据就不会被误杀。
DOWNLOAD_TIMEOUT = (15, 300)
# 下载失败重试：每个文件最多尝试次数（含首次），间隔 1 秒防止被认定为爬虫
DOWNLOAD_MAX_ATTEMPTS = 3
DOWNLOAD_RETRY_DELAY = 1.0

# ============ 下载模式 ============
def _project_root():
    """项目根目录 —— 也就是 `data/` 所在的那一层。

    ⚠ 拆包（2026-10-11）之后**不能**再用 `dirname(__file__)` 了：
    本模块从仓库根搬进了 `portpanel/ui/`，`dirname(__file__)` 变成包内目录，
    于是 data/logs/temp 全被写到了 `portpanel/ui/` 下面 —— 表现是
    「程序读不到 data/api_config.json、读不到 data/node_svg/*.svg（悬浮不出内容）、
    日志也另起一份」。

    为什么不写死"往上走几层"：源码运行是 `<root>/portpanel/ui/x.py`（3 层），
    PyInstaller 打包后是 `<app>/_internal/portpanel/ui/x.py`（3 层但根是 _internal），
    两种布局的根都能靠"谁下面有 data/"认出来，比数层数稳。
    """
    from portpanel.core.paths import project_root
    return project_root()


def _project_root_legacy():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, 'data')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 兜底：<root>/portpanel/ui/x.py -> <root>
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


DL_MODE_MEMORY = 'memory'    # 小文件：下载到内存
DL_MODE_CHUNKED = 'chunked'  # 大文件：Range 分块并行下载到临时文件
DL_MODE_STREAM = 'stream'    # 超大文件：流式写入 .dlpack 临时文件（低内存）

# 阈值（字节）
DL_SMALL_IMAGE_MAX = 1 * 1024 * 1024     # 图片 < 1MB 视为小文件
DL_SMALL_OTHER_MAX = 10 * 1024 * 1024    # 其他文件 < 10MB 视为小文件
DL_CHUNK_MIN = 10 * 1024 * 1024          # >= 10MB 进入分块下载
DL_STREAM_MIN = 200 * 1024 * 1024        # >= 200MB 流式写临时文件（防内存暴涨）
DL_CHUNK_SIZE = 4 * 1024 * 1024          # 每个分块 4MB
DL_MAX_CHUNK_WORKERS = 4                 # 单文件分块线程数
DL_BATCH_WORKERS = 4                     # 并行下载的文件数（小文件并行）
DL_TMP_SUFFIX = '.dlpack'                # 临时下载文件后缀
DL_TMP_DIR = os.path.join(
    _project_root(), 'temp', 'dlpack') if '__file__' in globals() else 'temp/dlpack'


# ================= 大轮询「自增」模式 =================
# 勾选后该组不再按固定轮数执行：从第 1 轮开始逐轮 +1（同步喂给本组的步进器），
# 每轮结束后判定终止条件（本组下载层报错 / 本组数据库框数据已走完一整圈），
# 触发即结束该组；未触发则一直递增到 AUTO_POLL_MAX 为止（再多请用拨盘手动指定）。
# 注意：它必须定义在 TRANSFER_SPECS 之前 —— 仪表盘里「全局自增」的提示语会引用它。
AUTO_POLL_MAX = 99                                      # 自增轮询的递增上限（含）

# ================= 数据传递（速率 / 频次）参数 =================
# 仪表盘（TransferDashboard）与 .wbt 里 `transfer` 键的**权威定义**。
# 硬约束：每一项的 default 都必须等于「引入本功能之前的模块常量」——
# 用户没动过任何一项时，整条下载链路的行为必须与改动前逐字节一致。
_MB = 1024 * 1024

TRANSFER_SPECS = [
    # ---- 并发与频次：直接决定「多快、多频繁」 ----
    dict(key='batch_workers', group='并发与频次', label='并行下载文件数', unit='个',
         kind='int', default=DL_BATCH_WORKERS, lo=1, hi=32, step=1, dec=0,
         tip='同时下载的文件数（对目标站的请求频次影响最大）。\n'
             '调大＝更快但更容易触发风控 / DDoS 防护；调小＝更慢更温和。'),
    dict(key='retry_attempts', group='并发与频次', label='单文件重试次数', unit='次',
         kind='int', default=DOWNLOAD_MAX_ATTEMPTS, lo=1, hi=10, step=1, dec=0,
         tip='每个文件最多尝试几次（含首次）。1＝不重试。'),
    dict(key='retry_delay', group='并发与频次', label='重试间隔', unit='秒',
         kind='float', default=DOWNLOAD_RETRY_DELAY, lo=0.0, hi=60.0, step=0.1, dec=1,
         tip='失败后等多久再重试。'),
    dict(key='wait_time', group='并发与频次', label='元素间等待', unit='秒',
         kind='float', default=0.5, lo=0.0, hi=60.0, step=0.1, dec=1,
         mirror='wait_time_spin',
         tip='每个元素收发完成后、走向下一连线元素前的等待间隔。\n'
             '与工具栏上的「元素间等待」是同一项，两边双向同步。'),
    dict(key='poll_count', group='并发与频次', label='大轮询次数', unit='次',
         kind='int', default=1, lo=1, hi=9999, step=1, dec=0, mirror='poll_count_spin',
         tip='整张图纸完整跑一遍算一次轮询。与工具栏拨盘是同一项。'),
    dict(key='poll_auto', group='并发与频次', label='全局自增轮询', unit='',
         kind='bool', default=False, mirror='poll_auto_chk',
         tip='勾选后，不属于任何类框的全局元素不按拨盘固定轮数跑，\n'
             f'而是从 1 递增到 {AUTO_POLL_MAX}，遇下载层报错或数据走完即停。'),
    # ---- 超时 ----
    dict(key='conn_timeout', group='超时', label='连接超时', unit='秒',
         kind='float', default=DOWNLOAD_TIMEOUT[0], lo=1.0, hi=120.0, step=1.0, dec=0,
         tip='与目标站建立连接的最长等待时间。'),
    dict(key='read_timeout', group='超时', label='读取超时', unit='秒',
         kind='float', default=DOWNLOAD_TIMEOUT[1], lo=10.0, hi=1800.0, step=10.0, dec=0,
         tip='两次数据到达之间的最长等待（**不是总时长**），\n'
             '所以慢速大文件只要持续有数据就不会被误杀。'),
    dict(key='api_timeout', group='超时', label='API 请求超时', unit='秒',
         kind='float', default=DOWNLOAD_TIMEOUT[1], lo=10.0, hi=1800.0, step=10.0, dec=0,
         tip='接口（JSON / HTML）请求的读取超时；文件下载走上面的「读取超时」。'),
    # ---- 分块与阈值 ----
    dict(key='chunk_workers', group='分块与阈值', label='单文件分块并发', unit='个',
         kind='int', default=DL_MAX_CHUNK_WORKERS, lo=1, hi=16, step=1, dec=0,
         tip='大文件 Range 分块的并行线程数。\n'
             '与「并行下载文件数」相乘才是真正的总并发上限。'),
    dict(key='chunk_size', group='分块与阈值', label='分块大小', unit='MB',
         kind='float', default=DL_CHUNK_SIZE / _MB, lo=1.0, hi=64.0, step=1.0, dec=0,
         tip='每个 Range 分块的字节数：调小＝请求更碎更频繁，调大＝单次请求更大。'),
    dict(key='small_image', group='分块与阈值', label='图片小文件阈值', unit='MB',
         kind='float', default=DL_SMALL_IMAGE_MAX / _MB, lo=0.1, hi=20.0, step=0.1, dec=1,
         tip='小于该值的图片直接下到内存，达到或超过则走分块下载。'),
    dict(key='small_other', group='分块与阈值', label='其他小文件阈值', unit='MB',
         kind='float', default=DL_SMALL_OTHER_MAX / _MB, lo=0.1, hi=100.0, step=0.5, dec=1,
         tip='小于该值的非图片文件直接下到内存，达到或超过则走分块下载。'),
    dict(key='stream_min', group='分块与阈值', label='流式写盘阈值', unit='MB',
         kind='float', default=DL_STREAM_MIN / _MB, lo=10.0, hi=2048.0, step=10.0, dec=0,
         tip='达到或超过该值改用流式写临时文件（低内存），避免内存暴涨。'),
]

TRANSFER_DEFAULTS = {s['key']: s['default'] for s in TRANSFER_SPECS}
TRANSFER_KEYS = tuple(s['key'] for s in TRANSFER_SPECS)
TRANSFER_GROUPS = tuple(dict.fromkeys(s['group'] for s in TRANSFER_SPECS))
TRANSFER_SPEC_BY_KEY = {s['key']: s for s in TRANSFER_SPECS}


def _transfer_coerce(spec, value):
    """按规格把任意输入规整成合法值（越界夹紧、类型转换、异常回落默认）。"""
    try:
        kind = spec['kind']
        if kind == 'bool':
            return bool(value)
        if kind == 'int':
            return max(int(spec['lo']), min(int(spec['hi']), int(round(float(value)))))
        return max(float(spec['lo']), min(float(spec['hi']), float(value)))
    except Exception:
        return spec['default']


class TransferConfig:
    """**一次运行用的数据传递参数快照（不可变）**。

    为什么不直接读模块级常量：这些参数要随图纸走，而且要给「以后多张图纸
    并发下载」留路 —— 只有把快照**显式交给每一个 worker**（而不是让 worker
    在运行时回头去读某个全局变量），不同图纸之间才不会互相串参数。
    为此所有下游只接收本对象，绝不读全局。
    """

    __slots__ = TRANSFER_KEYS

    def __init__(self, values=None):
        values = values if isinstance(values, dict) else {}
        for spec in TRANSFER_SPECS:
            k = spec['key']
            setattr(self, k, _transfer_coerce(spec, values.get(k, spec['default'])))

    # ---------- 构造 ----------
    @classmethod
    def defaults(cls):
        return cls()

    @classmethod
    def from_dict(cls, d):
        return cls(d)

    # ---------- 序列化 ----------
    def to_dict(self):
        return {k: getattr(self, k) for k in TRANSFER_KEYS}

    def is_default(self):
        return self.to_dict() == TRANSFER_DEFAULTS

    def diff(self):
        """返回所有与默认值不同的项：{key: (默认, 当前)}。"""
        out = {}
        for k in TRANSFER_KEYS:
            v = getattr(self, k)
            if v != TRANSFER_DEFAULTS[k]:
                out[k] = (TRANSFER_DEFAULTS[k], v)
        return out

    def copy(self, **override):
        d = self.to_dict()
        d.update(override)
        return TransferConfig(d)

    # ---------- 派生量（下游真正用的形态） ----------
    @property
    def timeout(self):
        """文件下载用 (连接超时, 读取超时) 元组。"""
        return (float(self.conn_timeout), float(self.read_timeout))

    @property
    def api_timeout_tuple(self):
        """接口请求用超时元组：连接沿用 conn_timeout，读取用 api_timeout。"""
        return (min(float(self.conn_timeout), float(self.api_timeout)),
                float(self.api_timeout))

    @property
    def chunk_size_bytes(self):
        return max(1, int(round(float(self.chunk_size) * _MB)))

    @property
    def small_image_bytes(self):
        return max(1, int(round(float(self.small_image) * _MB)))

    @property
    def small_other_bytes(self):
        return max(1, int(round(float(self.small_other) * _MB)))

    @property
    def stream_min_bytes(self):
        return max(1, int(round(float(self.stream_min) * _MB)))

    # ---------- 展示 ----------
    def summary(self):
        """仪表盘收起时显示的一行摘要。"""
        return (f"并发 {self.batch_workers} · 等待 {self.wait_time:g}s · "
                f"重试 {self.retry_attempts}×{self.retry_delay:g}s")

    def __repr__(self):
        return f"<TransferConfig {self.summary()}>"


def detect_download_mode(content_type: str, content_length: int, cfg=None) -> str:
    """根据文件类型与大小决定下载模式。cfg 缺省＝默认参数（行为与改动前一致）。"""
    cfg = cfg if isinstance(cfg, TransferConfig) else TransferConfig.defaults()
    if content_length <= 0:
        return DL_MODE_MEMORY  # 未知大小：保守走内存流式
    is_image = content_type.startswith('image/')
    if is_image and content_length < cfg.small_image_bytes:
        return DL_MODE_MEMORY
    if not is_image and content_length < cfg.small_other_bytes:
        return DL_MODE_MEMORY
    if content_length >= cfg.stream_min_bytes:
        return DL_MODE_STREAM
    return DL_MODE_CHUNKED


def _make_robust_request(method, url, timeout=DOWNLOAD_TIMEOUT, max_retries=2, **kwargs):
    """
    发送 HTTP 请求，自动检测 DDoS-Guard / 超时 并降级到 curl_cffi。
    返回 (response_or_None, error_message, used_curl)
    """
    # 完整浏览器 headers
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent',
                       'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/125.0.0.0 Safari/537.36')
    headers.setdefault('Accept',
                       'text/html,application/xhtml+xml,application/xml;q=0.9,'
                       'image/avif,image/webp,image/apng,*/*;q=0.8')
    headers.setdefault('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8')
    headers.setdefault('Accept-Encoding', 'gzip, deflate, br')
    headers.setdefault('Cache-Control', 'no-cache')
    headers.setdefault('Pragma', 'no-cache')
    headers.setdefault('Sec-Ch-Ua',
                       '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"')
    headers.setdefault('Sec-Ch-Ua-Mobile', '?0')
    headers.setdefault('Sec-Ch-Ua-Platform', '"Windows"')
    headers.setdefault('Sec-Fetch-Dest', 'document')
    headers.setdefault('Sec-Fetch-Mode', 'navigate')
    headers.setdefault('Sec-Fetch-Site', 'none')
    headers.setdefault('Sec-Fetch-User', '?1')
    headers.setdefault('Upgrade-Insecure-Requests', '1')
    # 从 URL 推断 Referer
    if 'Referer' not in headers:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        headers.setdefault('Referer', f'{parsed.scheme}://{parsed.netloc}/')
    # 添加 DNT（Do Not Track）
    headers.setdefault('DNT', '1')
    headers.setdefault('Connection', 'keep-alive')

    # ---- 第一轮：标准 requests ----
    try:
        resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
        # 检测 DDoS-Guard 特征
        ddos_guard = False
        for h_name, h_val in resp.headers.items():
            hn = h_name.lower()
            if hn in ('server', 'x-powered-by', 'via') and 'ddos-guard' in h_val.lower():
                ddos_guard = True
                break
            if hn == 'set-cookie' and '__ddg' in h_val:
                ddos_guard = True
                break
        if ddos_guard:
            raise RuntimeError("DDoS-Guard 检测到，降级到 curl_cffi")
        return resp, None, False
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
            RuntimeError) as e:
        err_msg = str(e)
        # ---- 第二轮：curl_cffi 回退 ----
        if HAS_CURL_CFFI:
            # 尝试多种模拟策略 + 重试
            impersonate_opts = ["chrome124", "chrome110", "safari15_5"]
            last_err = None
            for attempt in range(max_retries + 1):
                for imp in impersonate_opts:
                    try:
                        # curl_cffi 额外参数：启用 HTTP/2、禁用 SSL 验证降级探测
                        resp = curl_requests.request(
                            method, url,
                            headers=headers,
                            timeout=timeout,
                            impersonate=imp,
                            # 以下参数帮助绕过部分 WAF
                            allow_redirects=True,
                            **kwargs
                        )
                        return resp, None, True
                    except Exception as ce:
                        last_err = ce
                        continue
                # 所有模拟都失败后，短暂等待再重试
                if attempt < max_retries:
                    time.sleep(2)
            return None, f"curl_cffi 所有模拟重试均失败: {last_err}", True
        else:
            hint = ("；建议安装 curl_cffi 以绕过 DDoS-Guard 防火墙："
                    "pip install curl_cffi") if 'ddos' in err_msg.lower() or 'timeout' in err_msg.lower() else ""
            return None, f"{err_msg}{hint}", False
    except Exception as e:
        return None, str(e), False


def _header_get(headers, name, default=None):
    """大小写不敏感地读取响应头。

    requests 的 CaseInsensitiveDict 与 curl_cffi 0.15 的 Headers 都能
    .get(name) 大小写不敏感；但代码里常有 dict(resp.headers) 转成普通 dict
    （curl_cffi 的键为小写），必须手动小写匹配，否则 content-type /
    content-length 读空 → “未知大小”与 0 字节存盘。
    """
    if headers is None:
        return default
    try:
        v = headers.get(name)
        if v is not None:
            return v
    except Exception:
        pass
    lname = name.lower()
    try:
        for k, v in headers.items():
            if str(k).lower() == lname:
                return v
    except Exception:
        pass
    return default


# ================= API 日志富文本模式（数据处理选项卡） =================
# 折叠规则：两种显示状态（纯文本 / 富文本）共用一个日志窗口，
# 窗口上限 LOG_PAGE_SIZE 行；超过则折叠，滑到上/下边缘时再加载相邻页。
# 富文本按 RICH_BATCH_SIZE 为一批着色，窗口最多容纳 RICH_MAX_BATCHES 批。
LOG_PAGE_SIZE = 1000                                    # 每页（窗口）行数
RICH_BATCH_SIZE = 100                                   # 富文本着色批次粒度
RICH_MAX_BATCHES = max(1, LOG_PAGE_SIZE // RICH_BATCH_SIZE)

# ================= 会话日志落盘（每次启动一个子文件夹，实时记录） =================
_SESSION_LOG = {
    'dir': '',      # 本次启动的日志子文件夹
    'path': '',     # 日志文件完整路径
    'fh': None,     # 打开的文件句柄（行缓冲 + 每次 flush）
    'failed': False,
}


def _session_log_root():
    """日志根目录：<程序目录>/logs（不存在则创建）。"""
    base = (_project_root()
            if '__file__' in globals() else os.getcwd())
    return os.path.join(base, 'logs')


def _session_log_open():
    """懒创建本次启动的日志文件，返回文件句柄（失败返回 None，只尝试一次）。"""
    if _SESSION_LOG['fh'] is not None:
        return _SESSION_LOG['fh']
    if _SESSION_LOG['failed']:
        return None
    try:
        ts = time.strftime('%Y%m%d_%H%M%S')
        run_id = f"run_{ts}"
        d = os.path.join(_session_log_root(), run_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, 'session.log')
        fh = open(p, 'a', encoding='utf-8', buffering=1)
        _SESSION_LOG['dir'] = d
        _SESSION_LOG['path'] = p
        _SESSION_LOG['fh'] = fh
        fh.write("=" * 72 + "\n")
        fh.write("全栈图库管理器 · 数据处理日志（本次启动会话）\n")
        fh.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"日志文件: {p}\n")
        fh.write("=" * 72 + "\n")
        fh.flush()
        return fh
    except Exception:
        _SESSION_LOG['failed'] = True
        return None


def _session_log_write(text):
    """把一条日志实时追加到本次会话的日志文件（每行带时间戳）。自动懒创建。"""
    fh = _session_log_open()
    if fh is None:
        return
    try:
        ts = time.strftime('%H:%M:%S')
        for ln in str(text).split('\n'):
            fh.write(f"[{ts}] {ln}\n")
        fh.flush()
    except Exception:
        pass


def _session_log_close():
    """关闭日志文件（退出时调用；进程直接结束时由操作系统回收）。"""
    fh = _SESSION_LOG.get('fh')
    if fh is not None:
        try:
            fh.write(f"[{time.strftime('%H:%M:%S')}] ---- 会话结束 ----\n")
            fh.flush()
            fh.close()
        except Exception:
            pass
        _SESSION_LOG['fh'] = None

# VS Code 深色主题 JSON 语法着色
_COLOR_COMMENT = '#6a737d'      # 注释（纯文本日志）
_COLOR_DEFAULT = '#d4d4d4'      # 括号/冒号/标点
_COLOR_KEY = '#9cdcfe'          # JSON 键
_COLOR_STRING = '#ce9178'       # JSON 字符串值
_COLOR_NUMBER = '#b5cea8'       # JSON 数字
_COLOR_BOOL = '#569cd6'         # true/false/null
_COLOR_TAG = '#569cd6'          # HTML 标签名（字符串内）
_COLOR_TAG_BRACKET = '#808080'  # 尖括号
_COLOR_ATTR = '#9cdcfe'          # HTML 属性名
_COLOR_ATTR_VAL = '#ce9178'      # HTML 属性值
_BG_COLOR = '#1e1e1e'           # VS Code 深色背景

_EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F'
    '\u2190-\u21FF\u2B50\u2705\u274C\u2757\u2764\u2714\u2716'
    '\u2795\u2796\u2797\u2708-\u270D\u261D\u26A0-\u26A7'
    '\u2B05-\u2B07\u2192\u25B6\u25C0\u23F0-\u23FF\u2B55'
    '\u267B\u26CE\u2702\u2712\u2763\u2934-\u2935]'
)


def _strip_emoji(s):
    """去除日志文本中的 emoji（富文本注释行不显示 emoji）。"""
    try:
        return _EMOJI_RE.sub('', s)
    except Exception:
        return s


def _html_escape(s):
    """HTML 转义；空格转 &nbsp;，防止 QTextEdit 富文本折叠连续空格（保缩进）。"""
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;')
             .replace(' ', '&nbsp;'))


def _html_string_highlight(s):
    """字符串值着色：内容橙色；内嵌 HTML 标签（尖括号）单独着色。"""
    parts = []
    idx = 0
    for m in re.finditer(r'</?[a-zA-Z][a-zA-Z0-9]*[^>]*>', s):
        parts.append(f'<span style="color:{_COLOR_STRING}">{_html_escape(s[idx:m.start()])}</span>')
        tag = m.group(0)
        inner = tag[1:-1]
        parts.append(
            f'<span style="color:{_COLOR_TAG_BRACKET}">&lt;</span>'
            f'<span style="color:{_COLOR_TAG}">{_html_escape(inner)}</span>'
            f'<span style="color:{_COLOR_TAG_BRACKET}">&gt;</span>')
        idx = m.end()
    parts.append(f'<span style="color:{_COLOR_STRING}">{_html_escape(s[idx:])}</span>')
    return ''.join(parts)


_JSON_TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'
    r'|\btrue\b|\bfalse\b|\bnull\b|[{}[\],:]'
)


def _json_string_token_html(tok):
    """JSON 字符串 token（含引号）着色：引号与内容橙色，内嵌 HTML 标签蓝。"""
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        return (f'<span style="color:{_COLOR_STRING}">"</span>'
                + _html_string_highlight(tok[1:-1])
                + f'<span style="color:{_COLOR_STRING}">"</span>')
    return f'<span style="color:{_COLOR_STRING}">{_html_escape(tok)}</span>'


def _json_line_to_html(line):
    """单行 JSON 文本 → VS Code 风格语法着色（键/字符串/数字/布尔/标点）。"""
    out = []
    pos = 0
    n = len(line)
    for m in _JSON_TOKEN_RE.finditer(line):
        out.append(f'<span style="color:{_COLOR_DEFAULT}">{_html_escape(line[pos:m.start()])}</span>')
        tok = m.group(0)
        if tok in '{}[],:':
            out.append(f'<span style="color:{_COLOR_DEFAULT}">{_html_escape(tok)}</span>')
        elif tok in ('true', 'false', 'null'):
            out.append(f'<span style="color:{_COLOR_BOOL}">{tok}</span>')
        elif tok.startswith('"'):
            is_key = (m.end() < n and line[m.end():].lstrip().startswith(':'))
            if is_key:
                out.append(f'<span style="color:{_COLOR_KEY}">{_html_escape(tok)}</span>')
            else:
                out.append(_json_string_token_html(tok))
        else:
            out.append(f'<span style="color:{_COLOR_NUMBER}">{_html_escape(tok)}</span>')
        pos = m.end()
    out.append(f'<span style="color:{_COLOR_DEFAULT}">{_html_escape(line[pos:])}</span>')
    return ''.join(out)


def _json_to_html(obj):
    """把解析后的 JSON 渲染成树状 HTML：按规范 indent=2 逐行语法着色。

    行数与原始日志中 json.dumps(indent=2) 的 JSON 一致 → 富文本行与日志行 1:1 对应，
    切换回纯文本时可精确跳转到对应行。
    """
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        text = json.dumps(obj)
    lines = []
    for ln in text.split('\n'):
        if ln.strip() == '':
            lines.append(f'<div style="color:{_COLOR_DEFAULT}">&nbsp;</div>')
        else:
            lines.append(f'<div style="color:{_COLOR_DEFAULT}">{_json_line_to_html(ln)}</div>')
    return '\n'.join(lines)


def _comment_line_html(line):
    """普通日志行 → 灰色注释（去 emoji）。"""
    return f'<div style="color:{_COLOR_COMMENT}">{_html_escape(_strip_emoji(line))}</div>'


def _try_json_block(lines, i, n):
    """尝试从 lines[i] 行内第一个 { / [ 开始组装 JSON 块。

    支持裸 JSON（行首 { / [）与带标记前缀（如 "参数: {"、"响应头: {"）。
    返回 (prefix, end_idx, obj, partial_end)：
      - 完整解析成功:   (prefix, j, obj, None)
      - 括号未闭合或解析失败（截断/损坏）: (prefix, scan_end, None, scan_end)
      - 不是 JSON 起点:  (None, None, None, None)
    截断部分也返回扫描范围，供调用方按行级语法着色（不因截断而丢色）。
    """
    line = lines[i]
    pos = -1
    for p, ch in enumerate(line):
        if ch in '{[':
            pos = p
            break
    if pos < 0:
        return None, None, None, None
    prefix = line[:pos]
    # 前缀必须为空（裸 JSON）或以 : 结尾（如 "参数: "），否则不视为 JSON 起点
    if prefix.strip() and not prefix.rstrip().endswith(':'):
        return None, None, None, None
    depth = 0
    in_str = False
    esc = False
    closed = False
    j = i
    while j < n:
        s = lines[j]
        start_ch = pos if j == i else 0
        for ch in s[start_ch:]:
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in '{[':
                    depth += 1
                elif ch in '}]':
                    depth -= 1
                    if depth <= 0:
                        closed = True
                        break
        if closed:
            break
        j += 1
    if not closed:
        # 括号未闭合（内容截断）：起点到日志末尾都按 JSON 内容处理（逐行着色）
        return prefix, n - 1, None, n - 1
    parts = [line[pos:]]
    for k in range(i + 1, j + 1):
        parts.append(lines[k])
    try:
        obj = json.loads('\n'.join(parts))
    except Exception:
        # 括号闭合但解析失败（尾逗号/截断损坏）：按截断处理，逐行着色
        return prefix, j, None, j
    return prefix, j, obj, None


def _iter_log_blocks(lines, start, n):
    """把 lines[start:n] 切分为逻辑块（JSON 块 / HTML 块 / 注释行）。

    yield (block_start, block_end_exclusive, kind, payload)：
      kind='json'         payload=(prefix, obj)            完整 JSON → 树状
      kind='json_partial' payload=(prefix, raw_lines)     截断/损坏 JSON → 逐行着色
      kind='json_line'    payload=日志行                   孤立 JSON 残留行（如 ], "key":）→ 逐行着色
      kind='html'         payload=已格式化的 HTML 片段
      kind='comment'      payload=日志行文本
    """
    i = start
    while i < n:
        prefix, j, obj, partial = _try_json_block(lines, i, n)
        if obj is not None:
            yield (i, j + 1, 'json', (prefix, obj))
            i = j + 1
            continue
        if partial is not None:
            # 截断/损坏的 JSON：块内每行都按 JSON 行级着色（不丢色）
            yield (i, partial + 1, 'json_partial', (prefix, lines[i:partial + 1]))
            i = partial + 1
            continue
        raw, j = _try_html_block(lines, i, n)
        if raw is not None:
            html_out = _format_html_to_html(raw)
            if html_out is not None:
                yield (i, j + 1, 'html', html_out)
                i = j + 1
                continue
        if _is_json_mid_line(lines[i]):
            # 无 { / [ 起点的孤立 JSON 残留行（如截断后的 "]"、"\"key\":"）
            yield (i, i + 1, 'json_line', lines[i])
            i += 1
            continue
        yield (i, i + 1, 'comment', lines[i])
        i += 1


def _render_block_html(kind, payload):
    """把逻辑块渲染为 HTML 片段（块内部可为多行 div）。"""
    if kind == 'json':
        prefix, obj = payload
        frag = _json_to_html(obj)
        if prefix and prefix.strip():
            # 带标记前缀（如 "参数: "）：前缀保留为灰色注释
            return _comment_line_html(prefix.rstrip()) + '\n' + frag
        return frag
    if kind == 'json_partial':
        prefix, raw_lines = payload
        parts = []
        if prefix and prefix.strip():
            parts.append(_comment_line_html(prefix.rstrip()))
        for ln in raw_lines:
            if ln.strip() == '':
                parts.append(f'<div style="color:{_COLOR_DEFAULT}">&nbsp;</div>')
            else:
                parts.append(f'<div style="color:{_COLOR_DEFAULT}">{_json_line_to_html(ln)}</div>')
        return '\n'.join(parts)
    if kind == 'json_line':
        ln = payload
        if ln.strip() == '':
            return f'<div style="color:{_COLOR_DEFAULT}">&nbsp;</div>'
        return f'<div style="color:{_COLOR_DEFAULT}">{_json_line_to_html(ln)}</div>'
    if kind == 'html':
        return payload
    return _comment_line_html(payload)


def _render_log_batch_html(lines, start, end, cache=None):
    """把 lines[start:end) 渲染成 HTML 块。

    返回 (html, line_map)；line_map: {block_start_log_line: block_index_in_html}，
    供富文本滚动定位（把日志行映射到渲染后的块号）。
    cache: {block_start_log_line: html_fragment}，命中块直接复用（着色缓存，
    避免窗口滑动时重复解析/重绘导致 JSON 着色丢失）。
    """
    blocks = []
    line_map = {}
    n = len(lines)
    for bs, be, kind, payload in _iter_log_blocks(lines, start, n):
        if bs >= end:
            break
        line_map[bs] = len(blocks)
        frag = None
        if cache is not None:
            frag = cache.get(bs)
        if frag is None:
            frag = _render_block_html(kind, payload)
            if cache is not None:
                cache[bs] = frag
        blocks.append(frag)
    return '\n'.join(blocks), line_map


class _HtmlFormatter(HTMLParser):
    """把 HTML 文本解析并重排为缩进 + 着色的行片段（每行片段由外层包 <div>）。

    块级标签独占一行并嵌套缩进，内联标签（a/span/img/input 等）保持行内，
    标签名/属性名/属性值/尖括号按 VS Code 配色。
    """
    _INLINE_TAGS = {
        'a', 'span', 'b', 'i', 'em', 'strong', 'small', 'sub', 'sup', 'u',
        'img', 'input', 'button', 'label', 'option', 'select', 'textarea',
        'br', 'code', 'abbr', 'cite', 'data', 'mark', 'q', 'time', 'var', 'wbr',
    }

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.lines = []   # 已完成的行（span 片段）
        self.cur = []     # 当前行累积片段
        self.depth = 0
        self._stack = []  # 标签块级/内联栈

    def _flush(self):
        if self.cur:
            self.lines.append(''.join(self.cur))
            self.cur = []

    def _indent_html(self):
        return '&nbsp;' * (self.depth * 2)

    def _tag_html(self, tag, attrs, close=False, selfclose=False):
        parts = [
            f'<span style="color:{_COLOR_TAG_BRACKET}">&lt;{"/" if close else ""}</span>',
            f'<span style="color:{_COLOR_TAG}">{_html_escape(tag)}</span>',
        ]
        for idx_a, (k, v) in enumerate(attrs):
            if idx_a > 0:
                parts.append('&nbsp;')  # 属性间空格（span 间空白会被富文本折叠）
            parts.append(f'<span style="color:{_COLOR_ATTR}">{_html_escape(k)}</span>')
            if v is not None:
                parts.append(f'<span style="color:{_COLOR_DEFAULT}">=</span>')
                parts.append(f'<span style="color:{_COLOR_ATTR_VAL}">"{_html_escape(v)}"</span>')
        if selfclose:
            parts.append(f'<span style="color:{_COLOR_TAG_BRACKET}">/&gt;</span>')
        else:
            parts.append(f'<span style="color:{_COLOR_TAG_BRACKET}">&gt;</span>')
        return ''.join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._INLINE_TAGS:
            self.cur.append(self._tag_html(tag, attrs))
            self._stack.append(False)
        else:
            self._flush()
            self.cur.append(self._indent_html() + self._tag_html(tag, attrs))
            self._flush()
            self.depth += 1
            self._stack.append(True)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        self.cur.append(self._tag_html(tag, attrs, selfclose=True))

    def handle_endtag(self, tag):
        tag = tag.lower()
        is_block = self._stack.pop() if self._stack else (tag not in self._INLINE_TAGS)
        if is_block:
            self.depth = max(0, self.depth - 1)
            self._flush()
            self.cur.append(self._indent_html() + self._tag_html(tag, [], close=True))
            self._flush()
        else:
            self.cur.append(self._tag_html(tag, [], close=True))

    def handle_data(self, data):
        t = re.sub(r'\s+', ' ', data)
        if t.strip():
            self.cur.append(f'<span style="color:{_COLOR_DEFAULT}">{_html_escape(t)}</span>')

    def handle_entityref(self, name):
        self.cur.append(f'<span style="color:{_COLOR_STRING}">&amp;{name};</span>')

    def handle_charref(self, name):
        self.cur.append(f'<span style="color:{_COLOR_STRING}">&amp;#{name};</span>')

    def handle_comment(self, data):
        self._flush()
        self.cur.append(self._indent_html())
        self.cur.append(
            f'<span style="color:{_COLOR_COMMENT}">&lt;!--{_html_escape(data)}--&gt;</span>')
        self._flush()

    def handle_decl(self, decl):
        self._flush()
        self.cur.append(f'<span style="color:{_COLOR_BOOL}">&lt;!{_html_escape(decl)}&gt;</span>')
        self._flush()


def _format_html_to_html(raw):
    """格式化 HTML 文本 → 缩进着色的 HTML 块；解析失败返回 None。"""
    f = _HtmlFormatter()
    try:
        f.feed(raw)
        f.close()
    except Exception:
        return None
    if not f.lines:
        return None
    return '\n'.join(
        f'<div style="color:{_COLOR_DEFAULT}">{ln}</div>' for ln in f.lines)


def _try_html_block(lines, i, n):
    """尝试从 lines[i]（以 < 开头）收集 HTML 块直到 </html>。

    返回 (raw_html, end_idx)；无法识别返回 (None, None)。
    """
    if not lines[i].lstrip().startswith('<'):
        return None, None
    j = i
    while j < n:
        if '</html>' in lines[j].lower():
            break
        j += 1
    end = min(j, n - 1)
    raw = '\n'.join(lines[i:end + 1])
    return raw, end


_JSON_KEY_RE = re.compile(r'^"[^"]*"\s*:')


def _is_json_mid_line(s):
    """判断一行是否可能是 JSON 块内部行（键值/闭合/数组元素），用于窗口边界回溯。"""
    s = s.strip()
    if not s:
        return False
    if s.startswith(('}', ']')):
        return True
    if _JSON_KEY_RE.match(s):
        return True
    if s.startswith('"') and (s.endswith(',') or s.endswith('"')):
        return True
    if re.match(r'^-?\d', s) or s in ('true', 'false', 'null'):
        return True
    return False


def _snap_window_start(lines, start):
    """若 start 落在 JSON/HTML 块中间，回溯到该块起点（保证 JSON 树完整着色缩进）。

    从 start 向前扫描：JSON/HTML 中间行继续回溯，遇到空行、普通日志行或块起点
    （裸 { [ 或 "xxx: {"）即停。
    """
    if start <= 0:
        return 0
    n = len(lines)
    i = start
    while i > 0:
        prev = i - 1
        # 1) 从 prev 能识别覆盖到 start 的 JSON 块（含截断）→ prev 是跨边界块起点
        p, j, obj, partial = _try_json_block(lines, prev, n)
        if (obj is not None or partial is not None) and j >= start:
            return prev
        # 2) 从 prev 能识别覆盖到 start 的 HTML 块 → prev 是跨边界块起点
        raw, j = _try_html_block(lines, prev, n)
        if raw is not None and j >= start:
            return prev
        # 3) prev 是 JSON/HTML 中间行 → 继续回溯
        s = lines[prev].strip()
        if not s:
            break
        if _is_json_mid_line(s) or s.startswith('<'):
            i -= 1
            continue
        # 4) 普通日志行 → 停
        break
    return i


def _is_log_block_boundary(lines, idx, lookback=200):
    """idx 是否落在 JSON/HTML 块边界上（可安全作为增量渲染的起点）。

    与 _snap_window_start 的区别：后者为了让窗口边界好看会**额外回溯**（只要
    上一行"长得像" JSON 中间行就继续退），用于展示；本函数只回答一个问题 ——
    **有没有一个块从 idx 之前开始、一直跨过 idx**。有则 idx 落在块内部，
    增量渲染会把该块的尾巴当成新块重复画一遍。

    判定方式：向前最多回看 lookback 行，用 _iter_log_blocks 实际使用的两个块
    识别原语（_try_json_block / _try_html_block）逐行试探，只要有一行的块覆盖
    到 idx 即判否。lookback 取 200 行足以覆盖任何正常日志块。
    """
    if idx <= 0:
        return True
    n = len(lines)
    lo = max(0, idx - max(1, int(lookback)))
    for k in range(idx - 1, lo - 1, -1):
        try:
            _p, j, obj, partial = _try_json_block(lines, k, n)
            if (obj is not None or partial is not None) and j >= idx:
                return False
            raw, j2 = _try_html_block(lines, k, n)
            if raw is not None and j2 >= idx:
                return False
        except Exception:
            return False
    return True


class FlowTabBar(QTabBar):
    """流程图选项卡专用 TabBar：已更改的选项卡标题以 * 前缀 + 斜体橙色区分。

    实现：paintEvent **完全自绘**（官方同款：initStyleOption + drawControl(CE_TabBarTab)），
    dirty 选项卡直接修改 QStyleOptionTab 的字体（斜体）与调色板文本色（橙），
    一次绘制完成，绝无"底层文本 + 上层文本"两层重叠。
    注意：QSS 里不能写 color（会覆盖自绘文本色）。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dirty_tabs = {}

    def set_tab_dirty(self, idx, dirty):
        dirty = bool(dirty)
        if self._dirty_tabs.get(idx) != dirty:
            self._dirty_tabs[idx] = dirty
            self.update()

    def _on_tab_removed(self, idx):
        """移除选项卡后重排 dirty 标记索引（位于其后的索引前移一位）。"""
        new = {}
        for i, d in self._dirty_tabs.items():
            if i == idx:
                continue
            new[i - 1 if i > idx else i] = d
        self._dirty_tabs = new

    def paintEvent(self, ev):
        # 背景/边框交给 QSS（CE_TabBarTabShape，含 selected/hover 状态），
        # 文本与关闭按钮**自绘** —— QSS 的 label 文本色会覆盖 palette 导致
        # "斜体橙字盖在正常字上"，所以文本绝不走 QStyleSheetStyle 的 label 绘制。
        # 注：PySide6 6.9 裁剪了 CE_TabBarTabCloseButton/SE_TabBarTabCloseButton
        # 常量，关闭按钮自绘右上角 ×（C++ 层点击命中不受影响）。
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        st = self.style()
        for i in range(self.count()):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, i)
            # 1) 背景/边框（QSS 规则自动处理 普通/selected/hover）
            st.drawControl(QStyle.CE_TabBarTabShape, opt, p, self)
            # 2) 关闭按钮（自绘 ×，位置与 C++ 命中区域一致）
            if self.tabsClosable():
                r = opt.rect
                cx = r.right() - 11
                cy = r.center().y()
                p.setPen(QPen(QColor('#9aa7b5'), 1))
                p.drawLine(cx - 3, cy - 3, cx + 3, cy + 3)
                p.drawLine(cx - 3, cy + 3, cx + 3, cy - 3)
            # 3) 文本自绘：有未保存更改 → 斜体橙（含 * 前缀，由 _update_tab_title 设置）
            text_r = opt.rect.adjusted(4, 0, -22 if self.tabsClosable() else -4, 0)
            if self._dirty_tabs.get(i):
                f = QFont(self.font())
                f.setItalic(True)
                p.setFont(f)
                p.setPen(QColor('#ffb454'))
            else:
                p.setFont(self.font())
                p.setPen(QColor('#cfd8e3'))
            p.drawText(text_r, Qt.AlignCenter, self.tabText(i))
        p.end()


class LogTabBar(QTabBar):
    """数据处理选项卡专用 TabBar：富文本模式时仅 index 0 画绿色边框（不影响其他 tab）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_rich = False

    def set_log_rich(self, v):
        v = bool(v)
        if self._log_rich != v:
            self._log_rich = v
            self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._log_rich and self.count() > 0:
            r = self.tabRect(0)
            p = QPainter(self)
            p.setPen(QPen(QColor('#4ec9b0'), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            p.end()


# ================= 日志追加去抖参数 =================
# 纯文本日志的批量刷写节拍：逐行 insertText 实测 ≈1.2 ms/行（3000 行 3.7 s）——
# 合并到一次批量插入后只触发一次文档排版。
PLAIN_FLUSH_MS = 60        # 去抖间隔
PLAIN_FLUSH_MAX = 400      # 缓冲到这个行数就立刻冲刷（爆发式输出时不让缓冲无界增长）


class LogTextEdit(QTextEdit):
    """带日志行拦截的只读日志框。

    append()/clear() 交给 owner 统一处理：owner 维护权威行列表 _log_lines，
    并按"日志窗口"（LOG_PAGE_SIZE 行）渲染纯文本/富文本两种视图。
    纯文本内容永远是权威，富文本只是其带色视图。
    """
    def __init__(self, owner=None, parent=None):
        super().__init__(parent)
        self._owner = owner
        self.setReadOnly(True)
        self.setPlaceholderText("执行流程后，此处显示 API 请求与响应内容...")
        self.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")

    def append(self, text):
        # 只上报，不直接写入文档：由 owner 按窗口渲染（超过一页自动折叠）
        text = str(text)
        if self._owner is not None:
            self._owner._on_log_append(text)
            return
        super().append(text)

    def clear(self):
        if self._owner is not None:
            self._owner._on_log_clear()
            return
        super().clear()


# ================= 后台下载线程（避免 UI 卡死） =================
class DownloadWorker(QThread):
    """单个文件的智能下载器（后台线程），支持三种模式：

    - DL_MODE_MEMORY : 小文件（图片<1MB，其他<10MB）下载到内存，返回 bytes（含断点续传）
    - DL_MODE_CHUNKED: 大文件（>=10MB）Range 分块并行下载到 .dlpack 临时文件，返回路径
    - DL_MODE_STREAM : 超大文件（>=200MB）流式写入 .dlpack 临时文件（低内存），返回路径

    所有模式支持取消（stop()）、实时进度。临时文件由调用方负责转储与清理。
    """
    # progress(file_id, 已下载字节, 总字节)
    progress = Signal(int, int, int)
    # done(file_id, result, is_tmp_path, used_curl, error_msg, content_type)
    # result: 内存模式为 response 对象；文件模式为临时文件路径(.dlpack)
    done = Signal(int, object, bool, object, object, str)

    def __init__(self, method, url, headers=None, timeout=None,
                 file_id=0, parent=None, cfg=None):
        super().__init__(parent)
        self.method = method.upper()
        self.url = url
        self.headers = headers or {}
        # 传递参数快照：本 worker 只认这一份，绝不回头读模块级常量，
        # 这样将来多张图纸并发下载时各自的并发/间隔/超时互不干扰。
        self.cfg = cfg if isinstance(cfg, TransferConfig) else TransferConfig.defaults()
        # timeout 缺省时用快照里的 (连接超时, 读取超时)；显式传入则覆盖（供特殊调用方使用）
        self.timeout = timeout if timeout is not None else self.cfg.timeout
        self._file_id = file_id
        self._stop = False
        self._resps = []  # 所有活动响应（用于取消时统一中断）

    def _extract_socket(self, resp):
        """提取响应底层的 socket 对象（用于跨线程中断 recv）。

        注意：不能跨线程调用 resp.close()——它会与 worker 线程的
        BufferedReader 读锁竞争，导致主线程永久阻塞（卡在"正在取消"）。
        直接关闭底层 socket 才能立即唤醒阻塞的 recv。
        """
        import socket as _sm
        try:
            raw = getattr(resp, 'raw', None)
            if raw is None:
                return None
            fp = getattr(raw, '_fp', None) or getattr(raw, 'fp', None)
            if fp is None:
                return None
            inner = getattr(fp, 'fp', None) or fp
            if hasattr(inner, '_sock'):
                return inner._sock
            if hasattr(inner, 'raw') and isinstance(inner.raw, _sm.socket):
                return inner.raw
            if isinstance(inner, _sm.socket):
                return inner
            if hasattr(inner, 'fileno'):
                try:
                    return _sm.socket(fileno=inner.fileno())
                except Exception:
                    return None
        except Exception:
            pass
        return None

    def stop(self):
        self._stop = True
        # 跨线程中断：关闭所有活动响应的底层 socket 唤醒阻塞的 recv。
        # 绝不要调用 resp.close()（会因 BufferedReader 锁竞争阻塞主线程）。
        for r in list(self._resps):
            sock = self._extract_socket(r)
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _register_resp(self, resp):
        self._resps.append(resp)

    def _interruptible_sleep(self, secs):
        """可中断的休眠；返回 False 表示用户已取消（调用方应立即退出）"""
        end = time.time() + secs
        while time.time() < end:
            if self._stop:
                return False
            time.sleep(0.1)
        return True

    # ---------- 模式1：小文件下载到内存（含断点续传） ----------
    def _stream_to_memory(self, request_fn, used_curl):
        """流式下载到内存，带断点续传。

        request_fn(method, url, headers, timeout) -> response
        返回 (response, error_msg)；response 为 None 表示失败。
        任何路径下用户取消（self._stop）都必须立即返回，绝不发起续传重试。
        """
        max_attempts = self.cfg.retry_attempts  # 首次 + 重试（默认 3 次，间隔 1 秒）
        received = 0
        total = 0
        chunks = []
        last_err = None

        for attempt in range(max_attempts):
            if self._stop:
                return None, "已取消"

            req_headers = dict(self.headers)
            # 已收到部分数据 → 断点续传
            if received > 0:
                req_headers['Range'] = f'bytes={received}-'

            try:
                resp = request_fn(self.method, self.url, req_headers, self.timeout)
            except Exception as e:
                last_err = str(e)
                if self._stop:
                    return None, "已取消"
                if attempt < max_attempts - 1:
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        return None, "已取消"
                    continue
                return None, last_err

            self._register_resp(resp)
            status = resp.status_code
            if status == 206 and received > 0:
                total = received + int(_header_get(resp.headers, 'Content-Length', 0) or 0)
            elif status == 200 and received > 0:
                received = 0
                chunks = []
                total = int(_header_get(resp.headers, 'Content-Length', 0) or 0)
            elif status == 206 and received == 0:
                # 无 Range 请求却返回 206：服务器只给了部分内容，视为失败重试
                last_err = "服务器返回 206 部分内容"
                try:
                    resp.close()
                except Exception:
                    pass
                if attempt < max_attempts - 1:
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        return None, "已取消"
                    continue
                return None, last_err
            else:
                _st = resp.status_code
                if _st >= 400:
                    # 服务器明确失败（403 等）：不把错误页正文当内容、不做无谓重试
                    try:
                        resp.close()
                    except Exception:
                        pass
                    if _st == 403:
                        return None, f"HTTP 403 {resp.reason or ''}(服务器拒绝/无此内容,已跳过)"
                    return None, f"HTTP {_st} {resp.reason or ''}(服务器拒绝,已跳过)"
                total = int(_header_get(resp.headers, 'Content-Length', 0) or 0)

            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self._stop:
                        try:
                            resp.close()
                        except Exception:
                            pass
                        return None, "已取消"
                    if chunk:
                        chunks.append(chunk)
                        received += len(chunk)
                        self.progress.emit(self._file_id, received, total)
                # 完整性校验：声明了总大小但没下满 → 视为失败重试
                if total > 0 and received < total:
                    last_err = f"下载不完整: {received}/{total}"
                    try:
                        resp.close()
                    except Exception:
                        pass
                    if attempt < max_attempts - 1:
                        if not self._interruptible_sleep(self.cfg.retry_delay):
                            return None, "已取消"
                        continue
                    return None, last_err
                _resp_bytes = b''.join(chunks)
                resp._content = _resp_bytes
                try:
                    # curl_cffi 的 Response.content 是普通实例属性（非 property），
                    # 只设 _content 无效，必须同时写 content，否则存盘读到 b''
                    resp.content = _resp_bytes
                except Exception:
                    # requests 的 content 是只读 property，赋值会抛错，忽略即可
                    pass
                return resp, None
            except Exception as e:
                last_err = str(e)
                try:
                    resp.close()
                except Exception:
                    pass
                if self._stop:
                    return None, "已取消"
                if attempt < max_attempts - 1:
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        return None, "已取消"
                    continue
                return None, last_err

        return None, last_err or "未知错误"

    # ---------- 模式2：大文件 Range 分块并行下载到临时文件 ----------
    def _chunked_download(self, request_fn, used_curl, content_length):
        """Range 分块并行下载到 .dlpack 临时文件。

        返回 (tmp_path, error)。error == '__NO_RANGE__' 表示服务器不支持 Range，
        调用方应降级为流式写入。
        """
        import threading as _th
        from concurrent.futures import ThreadPoolExecutor
        try:
            os.makedirs(DL_TMP_DIR, exist_ok=True)
        except Exception:
            pass
        tmp_path = os.path.join(DL_TMP_DIR, f"dl_{uuid.uuid4().hex}{DL_TMP_SUFFIX}")
        try:
            with open(tmp_path, 'wb') as f:
                f.truncate(content_length)
        except Exception as e:
            return None, f"临时文件创建失败: {e}"

        # 划分分块
        ranges = []
        _csz = self.cfg.chunk_size_bytes
        for start in range(0, content_length, _csz):
            ranges.append((start, min(start + _csz, content_length)))
        if not ranges:
            return None, "文件大小异常"

        lock = _th.Lock()
        received = [0]
        stop_err = [None]

        def fetch(seg):
            start, end = seg
            hdrs = dict(self.headers)
            hdrs['Range'] = f'bytes={start}-{end - 1}'
            try:
                r = request_fn(self.method, self.url, hdrs, self.timeout)
            except Exception as e:
                with lock:
                    if stop_err[0] is None:
                        stop_err[0] = str(e)
                return
            self._register_resp(r)
            if r.status_code != 206:
                with lock:
                    if stop_err[0] is None:
                        stop_err[0] = '__NO_RANGE__'
                try:
                    r.close()
                except Exception:
                    pass
                return
            offset = start
            try:
                # 每个分块线程用独立文件句柄 + seek（Windows 无 os.pwrite）
                with open(tmp_path, 'r+b') as f:
                    f.seek(offset)
                    for chunk in r.iter_content(chunk_size=65536):
                        if self._stop:
                            try:
                                r.close()
                            except Exception:
                                pass
                            return
                        if chunk:
                            f.write(chunk)
                            offset += len(chunk)
                            with lock:
                                received[0] += len(chunk)
                            self.progress.emit(self._file_id, received[0], content_length)
            except Exception as e:
                with lock:
                    if stop_err[0] is None:
                        stop_err[0] = str(e)
            finally:
                try:
                    r.close()
                except Exception:
                    pass

        executor = ThreadPoolExecutor(max_workers=min(self.cfg.chunk_workers, len(ranges)))
        futures = [executor.submit(fetch, seg) for seg in ranges]
        cancelled = False
        try:
            # 等待所有分块完成；取消时关闭活动响应并立即退出（不等待卡住的连接线程）
            while any(not f.done() for f in futures):
                if self._stop:
                    cancelled = True
                    for r in list(self._resps):
                        sock = self._extract_socket(r)
                        if sock is not None:
                            try:
                                sock.close()
                            except Exception:
                                pass
                    time.sleep(0.5)
                    break
                time.sleep(0.05)
        finally:
            if cancelled:
                # 不等待卡住的 daemon 线程（它们会自行超时退出）
                executor.shutdown(wait=False)
            else:
                executor.shutdown(wait=True)
        if not cancelled:
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass

        if self._stop:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return None, "已取消"

        with lock:
            err = stop_err[0]
        if err == '__NO_RANGE__':
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return None, '__NO_RANGE__'
        if err:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return None, err
        # 完整性校验
        try:
            actual = os.path.getsize(tmp_path)
        except Exception:
            actual = -1
        if actual != content_length:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return None, f"分块下载不完整: {actual}/{content_length}"
        return tmp_path, None

    # ---------- 模式3：超大文件流式写入临时文件（低内存，含断点续传） ----------
    def _stream_to_file(self, request_fn, used_curl, content_length=0):
        """流式写入 .dlpack 临时文件（超大文件 / 服务器不支持 Range 时）。

        返回 (tmp_path, error)。
        """
        try:
            os.makedirs(DL_TMP_DIR, exist_ok=True)
        except Exception:
            pass
        tmp_path = os.path.join(DL_TMP_DIR, f"dl_{uuid.uuid4().hex}{DL_TMP_SUFFIX}")
        received = 0
        total = content_length
        last_err = None
        max_attempts = self.cfg.retry_attempts

        for attempt in range(max_attempts):
            if self._stop:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return None, "已取消"

            req_headers = dict(self.headers)
            if received > 0:
                req_headers['Range'] = f'bytes={received}-'

            try:
                r = request_fn(self.method, self.url, req_headers, self.timeout)
            except Exception as e:
                last_err = str(e)
                if self._stop:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    return None, "已取消"
                if attempt < max_attempts - 1:
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                        return None, "已取消"
                    continue
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return None, last_err

            self._register_resp(r)
            # 206 续传：总大小 = 已下载 + 剩余
            if r.status_code == 206 and received > 0:
                total = received + int(_header_get(r.headers, 'Content-Length', 0) or 0)
            elif r.status_code == 200 and received > 0:
                received = 0
                total = int(_header_get(r.headers, 'Content-Length', 0) or 0)
            elif r.status_code == 206 and received == 0:
                # 无 Range 请求却返回 206：服务器只给了部分内容，视为失败重试
                last_err = "服务器返回 206 部分内容"
                try:
                    r.close()
                except Exception:
                    pass
                if attempt < max_attempts - 1:
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                        return None, "已取消"
                    continue
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return None, last_err

            if r.status_code >= 400:
                # 服务器明确失败（403 等）：删除已建的临时文件，不把错误页当内容
                try:
                    r.close()
                except Exception:
                    pass
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                if r.status_code == 403:
                    return None, f"HTTP 403 {r.reason or ''}(服务器拒绝/无此内容,已跳过)"
                return None, f"HTTP {r.status_code} {r.reason or ''}(服务器拒绝,已跳过)"

            try:
                with open(tmp_path, 'ab' if received > 0 else 'wb') as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if self._stop:
                            try:
                                r.close()
                            except Exception:
                                pass
                            try:
                                os.remove(tmp_path)
                            except Exception:
                                pass
                            return None, "已取消"
                        if chunk:
                            f.write(chunk)
                            received += len(chunk)
                            self.progress.emit(self._file_id, received, total)
                # 完整性校验：声明了总大小但没写满 → 视为失败重试
                if total > 0 and received < total:
                    last_err = f"下载不完整: {received}/{total}"
                    try:
                        r.close()
                    except Exception:
                        pass
                    if attempt < max_attempts - 1:
                        if not self._interruptible_sleep(self.cfg.retry_delay):
                            try:
                                os.remove(tmp_path)
                            except Exception:
                                pass
                            return None, "已取消"
                        continue
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    return None, last_err
            except Exception as e:
                last_err = str(e)
                try:
                    r.close()
                except Exception:
                    pass
                if self._stop:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    return None, "已取消"
                if attempt < max_attempts - 1:
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                        return None, "已取消"
                    continue
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return None, last_err

            # 成功
            return tmp_path, None

        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return None, last_err or "未知错误"

    def run(self):
        # 构造浏览器 headers（用户自定义头优先；空值 = 显式不发该头）
        # 默认值由 api_config_dialog.build_browser_headers 统一提供，
        # 与图源配置里的「调试」按钮共用同一套，避免两处默认头不一致。
        self.headers = build_browser_headers(self.headers)

        # 确保超时为元组 (连接, 读取)；旧代码可能传标量
        t = self.timeout
        if not isinstance(t, (tuple, list)):
            t = (min(15, t), max(120, t))
        self.timeout = t

        def _req_requests(method, url, hdrs, timeout):
            return requests.request(method, url, headers=hdrs,
                                    timeout=timeout, stream=True)

        def _req_curl(method, url, hdrs, timeout):
            return curl_requests.request(
                method, url, headers=hdrs, timeout=timeout,
                stream=True, impersonate="chrome124")

        # ---- 探测：获取 Content-Type / Content-Length / Accept-Ranges ----
        probe = None
        probe_used_curl = False
        probe_err = None
        try:
            probe = _req_requests(self.method, self.url, self.headers, self.timeout)
            self._register_resp(probe)
        except Exception as e:
            probe_err = str(e)
        if probe is None and HAS_CURL_CFFI:
            try:
                probe = _req_curl(self.method, self.url, self.headers, self.timeout)
                self._register_resp(probe)
                probe_used_curl = True
            except Exception as e:
                probe_err = str(e)
        if probe is None:
            self.done.emit(self._file_id, None, False, probe_used_curl,
                           probe_err or "请求失败", '')
            return

        # ---- 403 专门处理：不产生临时文件、不做无谓重试，直接跳过 ----
        # requests 探测命中 403（常见于 DDoS-Guard / UA 风控 / 源站确无该内容）时，
        # 不再把 403 错误页正文当作"文件内容"读入并转储/存盘。
        # 若装有 curl_cffi，先用浏览器指纹再探测一次（可绕过部分 DDoS-Guard 误拦）；
        # 若仍 403 或失败 → 判定服务端确实没有该内容，直接跳过该项。
        force_curl = False
        if probe.status_code == 403:
            _reason = probe.reason or ''
            if HAS_CURL_CFFI and not probe_used_curl:
                _p2 = None
                _replaced = False
                try:
                    _p2 = _req_curl(self.method, self.url, self.headers, self.timeout)
                    self._register_resp(_p2)
                    if _p2.status_code < 400:
                        # curl 通道可正常访问（如 DDoS-Guard 只拦 requests 指纹）
                        try:
                            probe.close()
                        except Exception:
                            pass
                        probe = _p2
                        probe_used_curl = True
                        force_curl = True
                        _replaced = True
                    else:
                        try:
                            _p2.close()
                        except Exception:
                            pass
                except Exception:
                    if _p2 is not None:
                        try:
                            _p2.close()
                        except Exception:
                            pass
                if not _replaced:
                    # curl 通道同样被拒/失败 → 服务端确实无此内容 → 跳过（无临时文件）
                    try:
                        probe.close()
                    except Exception:
                        pass
                    self.done.emit(self._file_id, None, False, True,
                                   f"HTTP 403 {_reason}(服务器拒绝/无此内容,已跳过)", '')
                    return
            else:
                # 无 curl_cffi 或探测已走 curl：requests 的 403 无法再降级 → 直接跳过
                try:
                    probe.close()
                except Exception:
                    pass
                self.done.emit(self._file_id, None, False, probe_used_curl,
                               f"HTTP 403 {_reason}(服务器拒绝/无此内容,已跳过)", '')
                return

        content_type = (_header_get(probe.headers, 'Content-Type', '') or '').lower()
        content_length = int(_header_get(probe.headers, 'Content-Length', 0) or 0)
        accept_ranges = (_header_get(probe.headers, 'Accept-Ranges', '') or '').lower()
        # 关闭探测响应，后续按模式重新请求（逻辑统一，支持断点续传）
        try:
            probe.close()
        except Exception:
            pass

        mode = detect_download_mode(content_type, content_length, self.cfg)

        # ---------- 小文件：内存 ----------
        if mode == DL_MODE_MEMORY:
            if force_curl:
                # 403 已由 curl 通道接管：直接走 curl，不再向 requests 发起注定失败的请求
                resp, err = self._stream_to_memory(_req_curl, True)
            else:
                resp, err = self._stream_to_memory(_req_requests, False)
            if err is None:
                self.done.emit(self._file_id, resp, False, force_curl, None, content_type)
                return
            if err == "已取消":
                self.done.emit(self._file_id, None, False, force_curl, "已取消", content_type)
                return
            if HAS_CURL_CFFI and not force_curl:
                resp, err2 = self._stream_to_memory(_req_curl, True)
                if err2 is None:
                    self.done.emit(self._file_id, resp, False, True, None, content_type)
                    return
                if err2 == "已取消":
                    self.done.emit(self._file_id, None, False, True, "已取消", content_type)
                    return
                self.done.emit(self._file_id, None, False, True, err2, content_type)
                return
            self.done.emit(self._file_id, None, False, force_curl, err, content_type)
            return

        # ---------- 大文件 / 超大文件：临时文件模式 ----------
        tmp_path = None
        err = None

        def _do_chunked(req_fn, curl_flag):
            nonlocal tmp_path, err
            tp, e = self._chunked_download(req_fn, curl_flag, content_length)
            if e == '__NO_RANGE__':
                tp, e = self._stream_to_file(req_fn, curl_flag, content_length)
            tmp_path, err = tp, e

        # 403 判定为"服务器无此内容"：不再重试、不再写 .dlpack 临时文件
        primary_fn = _req_curl if force_curl else _req_requests
        primary_is_curl = force_curl
        if mode == DL_MODE_CHUNKED and 'bytes' in accept_ranges and content_length > 0:
            # 分块下载：失败自动重试（最多 3 次，间隔 1 秒）；403 例外不重试
            for _attempt in range(self.cfg.retry_attempts):
                if self._stop:
                    err = "已取消"
                    break
                _do_chunked(primary_fn, primary_is_curl)
                if tmp_path is not None or err in (None, "已取消"):
                    break
                if err and ('HTTP 403' in err or '已跳过' in err):
                    break  # 服务端明确拒绝/无此内容 → 直接失败，不硬下
                if not self._interruptible_sleep(self.cfg.retry_delay):
                    err = "已取消"
                    break
        else:
            tmp_path, err = self._stream_to_file(primary_fn, primary_is_curl, content_length)

        # 失败且未取消 → curl_cffi 回退（同样最多 3 次，间隔 1 秒）
        if (tmp_path is None and err and err != "已取消"
                and HAS_CURL_CFFI and not probe_used_curl):
            if mode == DL_MODE_CHUNKED and 'bytes' in accept_ranges and content_length > 0:
                for _attempt in range(self.cfg.retry_attempts):
                    if self._stop:
                        err = "已取消"
                        break
                    _do_chunked(_req_curl, True)
                    if tmp_path is not None or err in (None, "已取消"):
                        break
                    if err and ('HTTP 403' in err or '已跳过' in err):
                        break  # 403：服务端无此内容 → 不继续回退重试
                    if not self._interruptible_sleep(self.cfg.retry_delay):
                        err = "已取消"
                        break
            else:
                tmp_path, err = self._stream_to_file(_req_curl, True, content_length)

        if err == "已取消":
            self.done.emit(self._file_id, None, False, probe_used_curl, "已取消", content_type)
            return
        if tmp_path is not None:
            self.done.emit(self._file_id, tmp_path, True, probe_used_curl, None, content_type)
            return
        self.done.emit(self._file_id, None, False, probe_used_curl,
                       err or "未知错误", content_type)


# ================= 后台存盘（纯文件 I/O，不阻塞下载与 UI） =================
def _resolve_rule_json_value(data, path):
    """从 JSON 数据中按路径解析值（支持 $.a.b[0].c 语法）。纯函数，供后台线程使用。"""
    if data is None or not path:
        return None
    p = str(path).strip()
    if not p:
        return None
    if not p.startswith('$'):
        p = '$' + p
    segments = []
    body = p[2:] if p.startswith('$.') else p
    if not body:
        return None
    for part in body.split('.'):
        if '[' in part and part.endswith(']'):
            name, idx_str = part[:-1].split('[', 1)
            try:
                segments.append((name, int(idx_str)))
            except ValueError:
                segments.append((name, None))
        else:
            segments.append((part, None))
    cur = data
    for name, idx in segments:
        if isinstance(cur, list):
            if idx is not None and 0 <= idx < len(cur):
                cur = cur[idx]
            elif not name and idx is not None:
                return None
            else:
                # 列表：取首个元素继续
                if cur:
                    cur = cur[0]
                else:
                    return None
        if isinstance(cur, dict):
            if name and name in cur:
                cur = cur[name]
                # 同一段内若有索引且值为列表，取对应元素（如 $.tags[1]）
                if idx is not None and isinstance(cur, list):
                    if 0 <= idx < len(cur):
                        cur = cur[idx]
                    else:
                        return None
            elif not name and idx is not None:
                # 数组下标段（$[0]）：数据已是数组元素时跳过，继续按后续键解析
                pass
            else:
                return None
        else:
            if name or idx is not None:
                return None
    return cur


# Windows 保留设备名（用作文件夹名会失败）：CON/PRN/AUX/NUL/COM1-9/LPT1-9
_WINDOWS_RESERVED_NAMES = (
    {'con', 'prn', 'aux', 'nul'}
    | {f'com{i}' for i in range(1, 10)}
    | {f'lpt{i}' for i in range(1, 10)}
)


def _sync_rule_widget_processed(container_node, part_id, dp_node):
    """把上游 DP 的处理结果显示到规则拼合文本栏（命名预览）。

    模块级函数：供 NodeScene._handle_rule_part_drop 调用。
    """
    cw = (getattr(container_node, '_rule_widgets', None) or {}).get(part_id)
    if cw is None:
        return
    proc = getattr(dp_node, 'processed_data', '')
    if proc:
        cw.edit.setText(proc)
        cw.part_value = proc
        cw.edit.setToolTip(
            f"路径: {getattr(dp_node, 'source_path', '')}\n处理后: {proc}")
        cw.clear_btn.show()


def _sanitize_folder_segment(s):
    """清洗文件夹名片段：屏蔽 Windows 非法字符与控制字符、规避保留设备名。

    防止规则值里的 ? : * < > | / \\ 等导致 os.makedirs 失败，也防止 CON/NUL
    等 Windows 保留名、超长路径导致创建失败。
    """
    # 非法字符 + 控制字符（0x00-0x1F、0x7F）→ 下划线
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', str(s)).strip().strip('.')
    if not s:
        return 'x'
    # Windows 保留设备名（含扩展名形式如 CON.txt）→ 加前缀规避
    if s.split('.')[0].lower() in _WINDOWS_RESERVED_NAMES:
        s = '_' + s
    # 截断超长片段，避免路径过长
    if len(s) > 80:
        s = s[:80]
    return s or 'x'


def _compute_rule_folder_name(rule_config, rule_data):
    """根据规则计算子文件夹名：创建时机值 + 时间(可选) + 文本拼合段（按顺序）。"""
    segs = []
    # 创建时机值（分组标识）
    cp = (rule_config or {}).get('creation_path', '')
    if cp:
        val = _resolve_rule_json_value(rule_data, cp)
        if (val is not None and not isinstance(val, (dict, list))
                and str(val) not in ('', 'None')):
            segs.append(_sanitize_folder_segment(val))
    # 时间规则（标准 UTC）
    if (rule_config or {}).get('time_enabled'):
        import datetime
        fmt = (rule_config or {}).get('time_format', 'yyyy-mm-dd')
        py_fmt = fmt.replace('yyyy', '%Y').replace('mm', '%m').replace('dd', '%d')
        segs.append(datetime.datetime.now(datetime.timezone.utc).strftime(py_fmt))
    # 文本拼合段（有序）
    for pd in (rule_config or {}).get('parts', []):
        src = pd.get('source_path', '')
        text = pd.get('text', '')
        seg = None
        if src:
            val = _resolve_rule_json_value(rule_data, src)
            if (val is not None and not isinstance(val, (dict, list))
                    and str(val) not in ('', 'None')):
                seg = str(val)
                # 上游数据处理框：对该段源值应用其正则/替换规则，
                # 使"数据处理结果输入文本栏"参与文件夹命名
                regex = pd.get('regex', '')
                if regex:
                    try:
                        seg = re.sub(regex, pd.get('replacement', ''), seg)
                    except Exception:
                        pass  # 正则错误时保留原值
        if seg is None and text:
            seg = text
        if seg:
            segs.append(_sanitize_folder_segment(seg))
    if not segs:
        segs.append('download')
    return '_'.join(segs)


def _apply_rule_storage(rule_config, rule_data, storage_path):
    """规则模式：计算子文件夹名并创建，返回 (子目录绝对路径, ok, msg)。"""
    if not (rule_config or {}).get('enabled'):
        return storage_path, True, ""
    folder = _compute_rule_folder_name(rule_config, rule_data)
    sub = os.path.join(storage_path, folder)
    try:
        os.makedirs(sub, exist_ok=True)
        return sub, True, folder
    except Exception as e:
        return storage_path, False, f"创建规则文件夹失败: {e}"


# ================= 下载清单（manifest）去重 =================
# 以内容寻址路径（file.path / 下载 URL 路径）为主键记录已下载文件，
# 配合大小校验判断是否需重新下载。
# 清单文件存放在各下载根目录下（<storage>/download_manifest.json），
# 更新数据时可第一时间读取对应文件夹的校对内容。
_manifest_lock = threading.Lock()

# 下载前是否做本地 sha256 完整性复核（仅当清单里存了 sha256 时生效）。
# 只比大小会漏掉「大小相同但内容被截断/损坏/替换」的文件；开启后，命中清单的
# 每一项都会把本地文件整读一遍算 sha256，用 I/O 换正确性。
# 代价参考：一个几百 MB 的图片库，一轮跳过检查合计约 1 秒量级；
# 若库特别大且确认磁盘可靠，可置 False 退回「只比大小」。
MANIFEST_SHA256_VERIFY = True
# 计算 sha256 的分块大小（1 MiB），避免大文件一次性读进内存
MANIFEST_SHA256_CHUNK = 1 << 20


def _manifest_file(storage_path):
    return os.path.join(storage_path or '', 'download_manifest.json')


def _load_manifest(storage_path):
    """读取指定下载根目录的清单；文件不存在或损坏时返回空清单。"""
    try:
        with open(_manifest_file(storage_path), 'r', encoding='utf-8') as f:
            m = json.load(f)
        if isinstance(m, dict) and isinstance(m.get('files'), dict):
            return m
    except Exception:
        pass
    return {'version': 1, 'files': {}}


def _save_manifest(m, storage_path):
    try:
        if storage_path:
            os.makedirs(storage_path, exist_ok=True)
        fp = _manifest_file(storage_path)
        tmp = fp + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        os.replace(tmp, fp)
    except Exception:
        pass


def manifest_get(storage_path, source_key):
    """查询指定下载根目录清单中某来源路径的记录；不存在返回 None。"""
    if not source_key:
        return None
    with _manifest_lock:
        m = _load_manifest(storage_path)
        return m['files'].get(str(source_key))


def _file_sha256(path, chunk=MANIFEST_SHA256_CHUNK):
    """本地文件的 sha256 十六进制摘要（小写）；读取失败返回 None。"""
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def manifest_check(storage_path, source_key, verify_sha256=None):
    """判定清单里的记录能否让我们跳过这次下载，并给出作废原因。

    返回 (skip, reason)：
      - 无记录          → (False, '')      首次下载，正常放行
      - 记录有效        → (True,  '')
      - 记录作废        → (False, '原因')  需要重新下载；reason 可直接写进日志

    verify_sha256 为 None 时取模块开关 MANIFEST_SHA256_VERIFY。
    """
    if not source_key or not storage_path:
        return False, ''
    entry = manifest_get(storage_path, source_key)
    if not entry:
        return False, ''
    local_rel = entry.get('local_path', '') or ''
    if not local_rel:
        return False, '清单记录不完整'
    fp = os.path.join(storage_path, local_rel)
    try:
        if not os.path.isfile(fp):
            return False, '本地文件不存在'
        rec_size = entry.get('size')
        if rec_size:
            try:
                cur_size = os.path.getsize(fp)
            except Exception:
                return False, '无法读取文件大小'
            if cur_size != rec_size:
                return False, f'大小不符 {cur_size}≠{rec_size}'
        verify = MANIFEST_SHA256_VERIFY if verify_sha256 is None else bool(verify_sha256)
        rec_sha = str(entry.get('sha256') or '').strip().lower()
        if verify and rec_sha:
            cur_sha = _file_sha256(fp)
            if cur_sha is None:
                return False, 'sha256 复核读取失败'
            if cur_sha != rec_sha:
                return False, f'sha256 不符 {cur_sha[:12]}…≠{rec_sha[:12]}…'
        return True, ''
    except Exception as e:
        return False, f'复核异常: {e}'


def manifest_should_skip(storage_path, source_key, verify_sha256=None):
    """已下载过且本地文件有效（存在 + 大小一致 + sha256 复核一致）→ True。"""
    ok, _why = manifest_check(storage_path, source_key, verify_sha256)
    return ok


def manifest_add(source_key, storage_path, local_rel, size, sha256=None):
    """下载成功后写入对应下载根目录的清单。主线程调用（内部有锁，线程安全）。"""
    if not source_key or not storage_path:
        return
    try:
        import datetime as _dt
        with _manifest_lock:
            m = _load_manifest(storage_path)
            m['files'][str(source_key)] = {
                'local_path': local_rel,
                'size': size,
                'sha256': sha256 or '',
                'downloaded_at': _dt.datetime.now().isoformat(timespec='seconds'),
            }
            _save_manifest(m, storage_path)
    except Exception:
        pass


def _io_store_data(data, api_url, content_type, storage_path,
                   rule_config=None, rule_data=None):
    """纯文件 I/O：计算文件名并写盘。后台线程调用，不碰 UI。

    rule_config / rule_data：规则模式配置与当前轮询 JSON，用于生成子文件夹。
    返回 (ok, msg, final_path)。
    """
    if not storage_path or not os.path.isdir(storage_path):
        return False, "路径未设置", None, ''
    # 空内容守卫：下载失败（404 等）时上层会交出 data=None，
    # 旧代码直接走到 hashlib.sha256(None) 抛 TypeError，
    # 被 StoreWorker 兜底成「存储失败: object supporting the buffer API required」，
    # 再被 _on_store_done 当成容器故障终止整条流程。此处提前拦下并标记为「跳过」。
    if data is None:
        return False, "⏸ 无内容可存储（下载失败或空响应），已跳过", None, ''
    if rule_config and rule_config.get('enabled'):
        storage_path, ok, msg = _apply_rule_storage(rule_config, rule_data, storage_path)
        if not ok:
            return False, msg, None, ''
    import urllib.parse
    base = os.path.basename(urllib.parse.urlparse(api_url).path) or f"dl_{int(time.time())}"
    is_json = content_type.startswith('application/json') or (not content_type and api_url.endswith('.json'))
    ext = ".json" if is_json else (os.path.splitext(base)[1] or ".bin")
    # 计算内容字节（JSON 规范化序列化；其余按原始字节）
    if is_json:
        content_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
    else:
        content_bytes = data.encode('utf-8') if isinstance(data, str) else data
    try:
        free = shutil.disk_usage(storage_path).free
        if len(content_bytes) > free:
            return False, f"文件 {len(content_bytes)/1024:.0f}KB 超过剩余 {free/1024:.0f}KB", None, ''
    except Exception:
        pass
    # 内容寻址命名：以内容 SHA-256 作为文件名（非哈希命名自动转换）
    sha = hashlib.sha256(content_bytes).hexdigest()
    fn = f"{sha}{ext}"
    fp = os.path.join(storage_path, fn)
    if os.path.exists(fp):
        try:
            if os.path.getsize(fp) == len(content_bytes):
                return True, f"已存在: {fn}", fp, sha
        except Exception:
            pass
        # 同名但大小不符：覆盖重写
    try:
        if is_json:
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(content_bytes.decode('utf-8'))
        else:
            with open(fp, 'wb') as f:
                f.write(content_bytes)
        return True, f"已存: {fn}", fp, sha
    except Exception as e:
        return False, f"存储失败: {e}", None, ''


def _io_store_data_from_file(tmp_path, api_url, content_type, storage_path,
                             rule_config=None, rule_data=None):
    """纯文件 I/O：从 .dlpack 临时文件转储。后台线程调用，不碰 UI。

    rule_config / rule_data：规则模式配置与当前轮询 JSON，用于生成子文件夹。
    返回 (ok, msg, final_path)。成功后删除临时文件（清空缓存）。
    """
    if not storage_path or not os.path.isdir(storage_path):
        return False, "路径未设置", None, ''
    if rule_config and rule_config.get('enabled'):
        storage_path, ok, msg = _apply_rule_storage(rule_config, rule_data, storage_path)
        if not ok:
            return False, msg, None, ''
    if not tmp_path or not os.path.isfile(tmp_path):
        return False, "临时文件不存在", None, ''
    try:
        size = os.path.getsize(tmp_path)
    except Exception as e:
        return False, f"读取临时文件失败: {e}", None, ''
    try:
        free = shutil.disk_usage(storage_path).free
        if size > free:
            return False, f"文件 {size/1024:.0f}KB 超过剩余 {free/1024:.0f}KB", None, ''
    except Exception:
        pass
    import urllib.parse
    base = os.path.basename(urllib.parse.urlparse(api_url).path) or f"dl_{int(time.time())}"
    is_json = content_type.startswith('application/json') or (not content_type and api_url.endswith('.json'))
    ext = ".json" if is_json else (os.path.splitext(base)[1] or ".bin")
    try:
        # 内容寻址命名：以内容 SHA-256 作为文件名（非哈希命名自动转换）
        # 二进制大文件分块读入哈希，避免整文件载入内存
        if is_json:
            with open(tmp_path, 'rb') as src:
                data = json.load(src)
            content_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
            sha = hashlib.sha256(content_bytes).hexdigest()
            size = len(content_bytes)
        else:
            h = hashlib.sha256()
            with open(tmp_path, 'rb') as src:
                for blk in iter(lambda: src.read(1 << 20), b''):
                    h.update(blk)
            sha = h.hexdigest()
        fn = f"{sha}{ext}"
        fp = os.path.join(storage_path, fn)
        if os.path.exists(fp):
            try:
                if os.path.getsize(fp) == size:
                    return True, f"已存在: {fn}", fp, sha
            except Exception:
                pass
            # 同名但大小不符：覆盖重写
        if is_json:
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(content_bytes.decode('utf-8'))
        else:
            shutil.copyfile(tmp_path, fp)
        return True, f"已存: {fn}", fp, sha
    except Exception as e:
        return False, f"存储失败: {e}", None, ''
    finally:
        try:
            os.remove(tmp_path)  # 处理完成后清理临时缓存文件
        except Exception:
            pass


class StoreWorker(QThread):
    """后台存盘线程：执行文件 I/O，不阻塞下载调度与 UI。

    下载完成一个文件后，把写盘任务交给 StoreWorker 后台执行，
    主线程立即继续处理其他下载（下载与存盘并行流水线）。
    """
    done = Signal(int, bool, str, str, str)  # (task_id, ok, msg, final_path, sha256)

    def __init__(self, task_id, io_func, io_args, parent=None):
        super().__init__(parent)
        self._task_id = task_id
        self._io_func = io_func
        self._io_args = io_args

    def run(self):
        try:
            res = self._io_func(*self._io_args)
        except Exception as e:
            res = (False, f"存储失败: {e}", None, '')
        ok, msg, fp = res[0], res[1], res[2]
        sha = res[3] if len(res) > 3 else ''
        try:
            self.done.emit(self._task_id, ok, msg, fp or '', sha)
        except Exception:
            pass


# ================= 工具函数 =================
def parse_curly_params(template: str) -> List[str]:
    seen = set()
    result = []
    for m in re.finditer(r'\{(\w+)\}', template):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result

def build_json_path(item: QTreeWidgetItem) -> str:
    parts = []
    while item:
        key = item.text(0)
        parent = item.parent()
        if parent and isinstance(parent.data(0, Qt.UserRole), int):
            idx = parent.indexOfChild(item)
            parts.append(f"[{idx}]")
        else:
            parts.append(key)
        item = parent
    return '$.' + '.'.join(reversed(parts))

def is_array_path(path: str) -> bool:
    return bool(re.search(r'\[\d+\]', path))

# ================= JSON 树组件 =================
class JSONTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["键", "值", "类型"])
        self.setDragEnabled(False)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setColumnWidth(0, 180)
        self.setColumnWidth(1, 200)
        self.setColumnWidth(2, 60)
        self.setMinimumHeight(100)

    def populate(self, data, parent_item=None):
        if parent_item is None:
            self.clear()
            parent_item = self.invisibleRootItem()
        if isinstance(data, dict):
            for key, value in data.items():
                item = QTreeWidgetItem()
                item.setText(0, str(key))
                self._set_item_value(item, value)
                parent_item.addChild(item)
                if isinstance(value, (dict, list)):
                    self.populate(value, item)
        elif isinstance(data, list):
            for i, elem in enumerate(data):
                item = QTreeWidgetItem()
                item.setText(0, f"[{i}]")
                self._set_item_value(item, elem)
                parent_item.addChild(item)
                if isinstance(elem, (dict, list)):
                    self.populate(elem, item)
        else:
            item = QTreeWidgetItem()
            item.setText(0, str(data))
            item.setText(1, str(data))
            item.setText(2, type(data).__name__)
            parent_item.addChild(item)

    def _set_item_value(self, item, value):
        if isinstance(value, (dict, list)):
            item.setText(1, f"({len(value)} 项)")
            item.setText(2, type(value).__name__)
        else:
            item.setText(1, str(value))
            item.setText(2, type(value).__name__)

class SourceJSONTreeWidget(QTreeWidget):
    """可拖拽的源JSON树"""
    drag_started = Signal(object)  # 拖拽开始，携带数据来源的 API 节点
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["键", "值", "类型"])
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setColumnWidth(0, 180)
        self.setColumnWidth(1, 200)
        self.setColumnWidth(2, 60)
        self.setMinimumHeight(150)
        self._drag_start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_start_pos is not None:
            if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
                super().mouseMoveEvent(event)
                return
            item = self.currentItem()
            if item:
                path = build_json_path(item)
                value = item.text(1)
                val_type = item.text(2)
                # 打包 path + value + type 为 JSON 传给接收方
                payload = json.dumps({
                    'path': path,
                    'value': value,
                    'type': val_type
                })
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-jsonpath", payload.encode())
                drag.setMimeData(mime)
                # 通知来源 API 节点（供拖入容器/拼合栏时确定 DP 上游）
                try:
                    self.drag_started.emit(getattr(self, '_source_node', None))
                except Exception:
                    pass
                drag.exec(Qt.CopyAction)
                self._drag_start_pos = None
                return
        super().mouseMoveEvent(event)

    def populate(self, data, parent_item=None):
        if parent_item is None:
            self.clear()
            parent_item = self.invisibleRootItem()
        if isinstance(data, dict):
            for key, value in data.items():
                item = QTreeWidgetItem()
                item.setText(0, str(key))
                self._set_item_value(item, value)
                parent_item.addChild(item)
                if isinstance(value, (dict, list)):
                    self.populate(value, item)
        elif isinstance(data, list):
            for i, elem in enumerate(data):
                item = QTreeWidgetItem()
                item.setText(0, f"[{i}]")
                self._set_item_value(item, elem)
                parent_item.addChild(item)
                if isinstance(elem, (dict, list)):
                    self.populate(elem, item)
        else:
            item = QTreeWidgetItem()
            item.setText(0, str(data))
            item.setText(1, str(data))
            item.setText(2, type(data).__name__)
            parent_item.addChild(item)

    def _set_item_value(self, item, value):
        if isinstance(value, (dict, list)):
            item.setText(1, f"({len(value)} 项)")
            item.setText(2, type(value).__name__)
        else:
            item.setText(1, str(value))
            item.setText(2, type(value).__name__)

# ================= 参数编辑组件 =================
class ParamEditWidget(QWidget):
    value_changed = Signal(str, str)
    data_cleared = Signal(str)

    def __init__(self, param_name, initial_value="", parent=None):
        super().__init__(parent)
        self.param_name = param_name
        self.source_path = ""
        self.sequential = False
        self.siblings_count = 0
        self.current_index = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        name_label = QLabel(param_name)
        name_label.setFixedWidth(60)
        name_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(name_label)

        self.edit = QLineEdit(initial_value)
        self.edit.setPlaceholderText(f"{{{param_name}}}")
        self.edit.setAcceptDrops(True)
        self.edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.edit)

        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setFixedSize(20, 20)
        self.clear_btn.setStyleSheet("color: red; font-weight: bold; border: none;")
        self.clear_btn.clicked.connect(self.clear_binding)
        self.clear_btn.hide()
        layout.addWidget(self.clear_btn)

        self.index_combo = QComboBox()
        self.index_combo.setFixedWidth(60)
        self.index_combo.currentIndexChanged.connect(self._on_combo_changed)
        self.index_combo.hide()
        layout.addWidget(self.index_combo)

        self.seq_btn = QToolButton()
        self.seq_btn.setFixedSize(20, 20)
        self.seq_btn.setStyleSheet("background-color: green; border-radius: 10px;")
        self.seq_btn.setToolTip("顺序读取")
        self.seq_btn.clicked.connect(self._toggle_sequential)
        self.seq_btn.hide()
        layout.addWidget(self.seq_btn)

        self.edit.installEventFilter(self)
        self._update_readonly()

    def eventFilter(self, obj, event):
        if obj == self.edit:
            if event.type() == QEvent.DragEnter:
                if event.mimeData().hasFormat("application/x-jsonpath"):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop:
                if event.mimeData().hasFormat("application/x-jsonpath"):
                    raw = str(event.mimeData().data("application/x-jsonpath"), 'utf-8')
                    try:
                        payload = json.loads(raw)
                        path = payload.get('path', '')
                        value = payload.get('value', '')
                    except (json.JSONDecodeError, TypeError):
                        path = raw
                        value = path
                    self.set_json_path(path, display_value=value)
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)

    def set_json_path(self, path, display_value=None):
        """绑定 JSON 路径，优先显示实际值内容"""
        if display_value and display_value != path:
            self.edit.setText(display_value)
            self.edit.setToolTip(f"📎 绑定路径: {path}")
        else:
            self.edit.setText(path)
            self.edit.setToolTip("")
        self.source_path = path
        self.clear_btn.show()
        if is_array_path(path):
            self.sequential = True
            self.seq_btn.show()
            self.index_combo.show()
        else:
            self.sequential = False
            self.seq_btn.hide()
            self.index_combo.hide()
        self._update_readonly()
        self._emit_change()

    def clear_binding(self):
        self.edit.clear()
        self.edit.setToolTip("")
        self.source_path = ""
        self.sequential = False
        self.current_index = 0
        self.clear_btn.hide()
        self.seq_btn.hide()
        self.index_combo.hide()
        self.edit.setReadOnly(False)
        self._emit_change()
        self.data_cleared.emit(self.param_name)

    def _on_text_changed(self, text):
        if not text and self.source_path:
            # 手动编辑/回删清空文本：仅解除绑定显示状态，不触发节点删除
            # （只有点击 ✕ 按钮的 clear_binding 才会发出 data_cleared 删除 DP 节点）
            self.source_path = ""
            self.sequential = False
            self.current_index = 0
            self.edit.setToolTip("")
            self.clear_btn.hide()
            self.seq_btn.hide()
            self.index_combo.hide()
            self.edit.setReadOnly(False)
            self._emit_change()
        else:
            self._update_readonly()
            self._emit_change()

    def _update_readonly(self):
        self.edit.setReadOnly(self.edit.text().startswith('$.'))

    def _emit_change(self):
        self.value_changed.emit(self.param_name, self.edit.text())

    def _toggle_sequential(self):
        self.sequential = not self.sequential

    def _on_combo_changed(self, idx):
        self.current_index = idx

# ================= 拼合信息栏组件 =================
class ConcatEditWidget(QWidget):
    """字符串拼合编辑组件：接收拖入的数据，带有 ↑ 上移按钮"""
    value_changed = Signal(str, str)  # (id, new_value)
    move_up_requested = Signal(str)   # 请求上移

    def __init__(self, concat_id="", anchor_param="", parent=None):
        super().__init__(parent)
        self.concat_id = concat_id
        self.source_path = ""
        self.concat_value = ""
        self.anchor_param = anchor_param  # 关联的原信息栏参数名

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 2, 2, 2)  # 左侧留出偏移
        layout.setSpacing(4)

        # "拼合" 标签
        self.name_label = QLabel("+")
        self.name_label.setFixedWidth(30)
        self.name_label.setStyleSheet(
            "color: #e5a53b; font-weight: bold; font-size: 14px; background: transparent;"
        )
        layout.addWidget(self.name_label)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("拖入信息以拼合...")
        self.edit.setAcceptDrops(True)
        self.edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.edit)

        # 清除按钮
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setFixedSize(20, 20)
        self.clear_btn.setStyleSheet("color: red; font-weight: bold; border: none;")
        self.clear_btn.clicked.connect(self.clear_binding)
        self.clear_btn.hide()
        layout.addWidget(self.clear_btn)

        # ↑ 上移按钮
        self.up_btn = QToolButton()
        self.up_btn.setText("↑")
        self.up_btn.setFixedSize(22, 22)
        self.up_btn.setStyleSheet(
            "color: #55aaff; font-weight: bold; font-size: 14px; border: 1px solid #3a6a9a; "
            "border-radius: 3px; background: #1a2a3a;"
        )
        self.up_btn.setToolTip("向上移动拼合位置")
        self.up_btn.clicked.connect(lambda: self.move_up_requested.emit(self.concat_id))
        layout.addWidget(self.up_btn)

        self.edit.installEventFilter(self)

    def _on_text_changed(self, text):
        self.concat_value = text
        self.value_changed.emit(self.concat_id, text)

    def eventFilter(self, obj, event):
        if obj == self.edit:
            if event.type() == QEvent.DragEnter:
                if event.mimeData().hasFormat("application/x-jsonpath"):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop:
                if event.mimeData().hasFormat("application/x-jsonpath"):
                    raw = str(event.mimeData().data("application/x-jsonpath"), 'utf-8')
                    try:
                        payload = json.loads(raw)
                        path = payload.get('path', '')
                        value = payload.get('value', '')
                    except (json.JSONDecodeError, TypeError):
                        path = raw
                        value = raw
                    self.set_drop_data(path, value)
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)

    def set_drop_data(self, path, display_value=None):
        """接收拖入的 JSON 路径数据"""
        self.source_path = path
        show = display_value if display_value and display_value != path else path
        self.edit.setText(show)
        self.edit.setToolTip(f"📎 路径: {path}")
        self.concat_value = show
        self.clear_btn.show()

    def clear_binding(self):
        self.edit.clear()
        self.edit.setToolTip("")
        self.source_path = ""
        self.concat_value = ""
        self.clear_btn.hide()

    def get_value(self) -> str:
        return self.concat_value

class RulePartWidget(QWidget):
    """容器规则拼合文本部件：可拖入数据（source_path 绑定）或输入固定文本，支持上移排序。

    与 ConcatEditWidget 机制一致：左侧输入点（端口）用于连线，拖入 JSON 路径后绑定
    轮询数据中的字段，运行时从当前轮询 JSON 解析值参与文件夹命名。
    """
    value_changed = Signal(str, str)   # (part_id, new_value)
    move_up_requested = Signal(str)    # 请求上移

    def __init__(self, part_id="", parent=None):
        super().__init__(parent)
        self.part_id = part_id
        self.source_path = ""     # 绑定的 JSON 路径（如 $.title）
        self.part_value = ""      # 当前文本值
        self.anchor_param = ""    # 可选：关联的上游数据源参数名

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 2, 2, 2)  # 左侧留出偏移（输入点）
        layout.setSpacing(4)

        # "文本" 标签（输入点标记）
        self.name_label = QLabel("T")
        self.name_label.setFixedWidth(30)
        self.name_label.setStyleSheet(
            "color: #e5a53b; font-weight: bold; font-size: 13px; background: transparent;"
        )
        layout.addWidget(self.name_label)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("拖入信息以命名，或输入固定文本...")
        self.edit.setAcceptDrops(True)
        self.edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.edit)

        # 清除绑定按钮
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setFixedSize(20, 20)
        self.clear_btn.setStyleSheet("color: red; font-weight: bold; border: none;")
        self.clear_btn.clicked.connect(self.clear_binding)
        self.clear_btn.hide()
        layout.addWidget(self.clear_btn)

        # ↑ 上移按钮（调整拼合顺序）
        self.up_btn = QToolButton()
        self.up_btn.setText("↑")
        self.up_btn.setFixedSize(22, 22)
        self.up_btn.setStyleSheet(
            "color: #55aaff; font-weight: bold; font-size: 14px; border: 1px solid #3a6a9a; "
            "border-radius: 3px; background: #1a2a3a;"
        )
        self.up_btn.setToolTip("向上移动拼合位置")
        self.up_btn.clicked.connect(lambda: self.move_up_requested.emit(self.part_id))
        layout.addWidget(self.up_btn)

        self.edit.installEventFilter(self)

    def _on_text_changed(self, text):
        self.part_value = text
        self.value_changed.emit(self.part_id, text)

    def eventFilter(self, obj, event):
        if obj == self.edit:
            if event.type() == QEvent.DragEnter:
                if event.mimeData().hasFormat("application/x-jsonpath"):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Drop:
                if event.mimeData().hasFormat("application/x-jsonpath"):
                    raw = str(event.mimeData().data("application/x-jsonpath"), 'utf-8')
                    try:
                        payload = json.loads(raw)
                        path = payload.get('path', '')
                        value = payload.get('value', '')
                    except (json.JSONDecodeError, TypeError):
                        path = raw
                        value = raw
                    self.set_drop_data(path, value)
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)

    def set_drop_data(self, path, display_value=None):
        """接收拖入的 JSON 路径数据"""
        self.source_path = path
        show = display_value if display_value and display_value != path else path
        self.edit.setText(show)
        self.edit.setToolTip(f"路径: {path}")
        self.part_value = show
        self.clear_btn.show()

    def clear_binding(self):
        self.edit.clear()
        self.edit.setToolTip("")
        self.source_path = ""
        self.part_value = ""
        self.clear_btn.hide()

    def get_value(self) -> str:
        return self.part_value

class _RuleCreationFilter(QObject):
    """创建时机输入框的拖入过滤器：接收 JSON 路径作为创建时机"""
    path_set = Signal(str)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.DragEnter:
            if event.mimeData().hasFormat("application/x-jsonpath"):
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Drop:
            if event.mimeData().hasFormat("application/x-jsonpath"):
                raw = str(event.mimeData().data("application/x-jsonpath"), 'utf-8')
                try:
                    payload = json.loads(raw)
                    path = payload.get('path', '')
                except (json.JSONDecodeError, TypeError):
                    path = raw
                self.path_set.emit(path)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

# ================= 输出框垂直缩放手柄（输出选项卡中的输出列表） =================
# ================= 下载条目：目标路径可视 + 点击直达 =================
# 测试钩子：非 None 时 _reveal_in_explorer 改为调用它（避免自动化测试真的弹出资源管理器）
_REVEAL_HOOK = None


def _reveal_in_explorer(path):
    """在资源管理器里定位 path：文件 → 打开父目录并选中它；目录 → 直接打开。

    返回 (ok, 说明文本)。绝不抛异常（打不开只记日志）。
    """
    if not path:
        return False, '未记录文件位置'
    p = os.path.normpath(str(path))
    if _REVEAL_HOOK is not None:
        try:
            return _REVEAL_HOOK(p)
        except Exception as e:
            return False, f'{type(e).__name__}: {e}'
    try:
        if os.path.isfile(p):
            if os.name == 'nt':
                # 经典写法 explorer /select,"完整路径"：打开父目录并选中该文件
                subprocess.Popen(f'explorer /select,"{p}"')
            else:
                subprocess.Popen(['xdg-open', os.path.dirname(p)])
            return True, p
        if os.path.isdir(p):
            if os.name == 'nt':
                os.startfile(p)  # noqa: S606
            else:
                subprocess.Popen(['xdg-open', p])
            return True, p
    except Exception as e:
        return False, f'打开失败 {type(e).__name__}: {e}'
    return False, f'路径不存在: {p}'


class ClickableProgressBar(QProgressBar):
    """可点击的进度条：下载完成后点它 → 资源管理器直接跳到该文件的位置。"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target_file = ''    # 存盘后的最终文件完整路径
        self._target_dir = ''     # 尚未存盘时的目标文件夹（根目录/子文件夹）

    def _target_path(self):
        return self._target_file or self._target_dir

    def set_target(self, file_path='', fallback_dir=''):
        """记录点击目标；两者都为空时进度条退化为普通进度条（无手型光标）。"""
        if file_path:
            self._target_file = file_path
        if fallback_dir:
            self._target_dir = fallback_dir
        tp = self._target_path()
        self.setCursor(Qt.PointingHandCursor if tp else Qt.ArrowCursor)
        self.setToolTip(f'📂 点击直达: {tp}' if tp else '')

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._target_path():
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ClickableLabel(QLabel):
    """可点击的标签（下载目标信息行）。"""
    clicked = Signal()

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _DownloadTarget:
    """下载条目的「目标位置」显示与点击直达。

    - set_dir(root, sub, filename)：存盘前就知道的落位（根目录 / 规则子文件夹 / 文件名）
    - set_file(fp)：存盘完成后记下最终文件，让进度条与信息行都可点击直达
    - 点击进度条或信息行 → 资源管理器定位到该文件（未完成时定位到目标文件夹）
    """

    def __init__(self, bar, label=None, logger=None):
        self.bar = bar
        self.label = label
        self.log = logger
        self.root = ''        # 容器根目录（可能是 storage_path/dyn_root，绝对路径）
        self.sub = ''         # 规则子文件夹名（无规则时为空）
        self.filename = ''
        self.file_path = ''   # 最终文件绝对路径
        if isinstance(bar, ClickableProgressBar):
            bar.clicked.connect(self.reveal)
        if isinstance(label, ClickableLabel):
            label.clicked.connect(self.reveal)
        if label is not None:
            label.setWordWrap(True)
            label.hide()

    # ---------- 对外 ----------
    def set_dir(self, root='', sub='', filename=''):
        if root:
            self.root = str(root)
        self.sub = str(sub or '')
        if filename:
            self.filename = str(filename)
        self._refresh()

    def set_file(self, fp):
        if not fp:
            return
        self.file_path = str(fp)
        self.filename = os.path.basename(self.file_path)
        if not self.root:
            self.root = os.path.dirname(self.file_path)
        self._refresh()

    def reveal(self):
        """点击进度条/信息行：资源管理器直达文件（未完成时到目标文件夹）。"""
        target = self.file_path
        if not target and self.root:
            target = os.path.join(self.root, self.sub) if self.sub else self.root
        if not target:
            if self.log:
                self.log('  ⓘ 该下载项还没有可定位的目标位置')
            return
        ok, info = _reveal_in_explorer(target)
        if self.log:
            self.log((f'  📂 已定位: {info}' if ok else f'  ⚠ 定位失败: {info}'))

    # ---------- 内部 ----------
    def _refresh(self):
        if self.label is not None:
            root_txt = self.root or '（未确定）'
            sub_txt = self.sub or '（无 · 直接存入根目录）'
            name_txt = self.filename or '（下载中…）'
            self.label.setText(
                f"📁 根目录: {root_txt}\n"
                f"📂 子文件夹: {sub_txt}\n"
                f"📄 文件: {name_txt}")
            self.label.setToolTip(
                f"根目录: {root_txt}\n子文件夹: {sub_txt}\n文件: {name_txt}\n"
                f"（点击可在资源管理器中定位）")
            self.label.show()
        if isinstance(self.bar, ClickableProgressBar):
            fallback = ''
            if self.root:
                fallback = os.path.join(self.root, self.sub) if self.sub else self.root
            self.bar.set_target(file_path=self.file_path, fallback_dir=fallback)


def _detach_and_delete(widget):
    """把控件立刻脱离父控件，再交给 deleteLater() 释放。

    为什么不能只写 widget.deleteLater()：
      deleteLater() 只投递一个 DeferredDelete 事件，而 Qt **仅在回到事件循环外层时**
      才处理它；流程执行期间主线程长时间只跑 QApplication.processEvents()，
      DeferredDelete 一直排在队里不执行 ⇒ 该控件仍然是父控件的子控件、仍然会被绘制，
      但它已不在布局里、几何被冻结在最后一次的位置 ⇒ 与新控件**逐像素重叠绘制**。
      （输出选项卡里"好几个下载进度条重叠到一块、图层出错"就是这个原因。）
    setParent(None) 会立即把控件从父控件摘除并隐藏，绘制当场停止；随后的 deleteLater()
    只负责回收内存，晚一点执行也无所谓。
    """
    if widget is None:
        return
    try:
        widget.setParent(None)
    except Exception:
        try:
            widget.hide()
        except Exception:
            pass
    try:
        widget.deleteLater()
    except Exception:
        pass


class _OutputResizeHandle(QFrame):
    """输出列表（输出选项卡）中单个输出框底部的垂直拖拽手柄：拖动改变该框高度。"""
    def __init__(self, block, parent=None):
        super().__init__(parent)
        self._block = block
        self.setFixedHeight(14)
        self.setCursor(Qt.SizeVerCursor)
        self.setStyleSheet(
            "QFrame { background: rgba(255,255,255,10); border-top: 1px solid #3a5a3a; }"
        )
        self._dragging = False
        self._start_y = 0.0
        self._start_h = 0

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2
        pen = QPen(QColor(170, 190, 210, 160), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for off in (-3, 0, 3):
            p.drawLine(QPointF(cx - 9, cy + off), QPointF(cx + 9, cy + off))
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._start_y = event.globalPosition().y()
            self._start_h = self._block.height()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            dy = event.globalPosition().y() - self._start_y
            new_h = int(self._start_h + dy)
            # 用布局最小高度做下限：setFixedHeight 会把 widget 的 minimumHeight 改成固定值，
            # 用它做下限会导致只能拉大不能缩小。
            min_h = self._block.minimumSizeHint().height()
            if min_h < 60:
                min_h = 60
            new_h = max(new_h, min_h)
            self._block.setFixedHeight(new_h)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class OutputItem(QFrame):
    """输出条目：标题行（多选复选框 + 钉子 + 标题）+ 内容区。

    - 右键菜单：删除 / 多选
    - 钉子（📌）：固定后不参与 200 条存储上限、不会被自动释放，
      且外框与标题字由绿色（默认描边）变为蓝色提醒
    - 多选模式：条目左侧出现复选框，配合顶部"全选"与底部"删除已选"
    """
    menu_requested = Signal(object, object)  # (item, global_pos)

    def __init__(self, title_html, title_color, parent=None):
        super().__init__(parent)
        self._pinned = False
        self._checked = False
        self._title_color = title_color
        self._base_style = (
            "QFrame { border: 1px solid #3a5a3a; border-radius: 4px; "
            "background: #1e1e1e; margin: 2px 0px; }"
        )
        self._pin_style = (
            "QFrame { border: 2px solid #55aaff; border-radius: 4px; "
            "background: #12263a; margin: 2px 0px; }"
        )
        self.setStyleSheet(self._base_style)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)

        # ---- 顶部行：复选框 + 钉子 + 标题 ----
        top = QHBoxLayout()
        top.setSpacing(4)
        self.checkbox = QCheckBox()
        self.checkbox.setVisible(False)
        self.checkbox.stateChanged.connect(self._on_check_changed)
        top.addWidget(self.checkbox)
        self.pin_btn = QToolButton()
        self.pin_btn.setText("📌")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setToolTip("📌 固定该条目：不计入存储上限、不被自动释放（变蓝提醒）")
        self.pin_btn.setStyleSheet(
            "QToolButton { border:none; font-size:13px; color:#888; }"
            "QToolButton:checked { color:#55aaff; }"
        )
        self.pin_btn.clicked.connect(self._toggle_pin)
        top.addWidget(self.pin_btn)
        self.title_label = QLabel(title_html)
        self.title_label.setObjectName("api_header")
        self.title_label.setWordWrap(True)
        top.addWidget(self.title_label, 1)
        self._layout.addLayout(top)

        self.content_layout = self._layout  # 内容区：子标题/进度条/键值树等追加到此

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._emit_menu)

    # ---- 信号/槽 ----
    def _on_check_changed(self, state):
        self._checked = (state == Qt.Checked)

    def _emit_menu(self, pos):
        self.menu_requested.emit(self, self.mapToGlobal(pos))

    def _toggle_pin(self):
        self._pinned = self.pin_btn.isChecked()
        self._apply_pin_style()

    def set_pinned(self, pinned):
        self._pinned = bool(pinned)
        self.pin_btn.setChecked(self._pinned)
        self._apply_pin_style()

    def _apply_pin_style(self):
        if self._pinned:
            self.setStyleSheet(self._pin_style)
            self.title_label.setStyleSheet(
                "font-weight:bold; color:#55aaff; background:#0f1f33; "
                "padding:3px 8px; border-left:3px solid #55aaff; "
                "border-radius:2px; font-size:11px;"
            )
        else:
            self.setStyleSheet(self._base_style)
            self.title_label.setStyleSheet(
                f"font-weight:bold; color:{self._title_color}; background:#1a2a1a; "
                f"padding:3px 8px; border-left:3px solid {self._title_color}; "
                f"border-radius:2px; font-size:11px;"
            )

    def set_checkbox_visible(self, vis):
        self.checkbox.setVisible(vis)
        if not vis:
            self.checkbox.setChecked(False)


# ================= 数据处理节点 =================
class DataProcessNode(QGraphicsRectItem):
    def __init__(self):
        super().__init__()
        self.setRect(0, 0, 200, 100)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(2)

        self.input_conn = None
        self.output_conn = None
        self.regex = ""
        self.source_data = ""
        self.processed_data = ""
        self.source_path = ""  # JSON 路径绑定来源
        self._csv_source = None  # (CsvDataNode, header) 关联的CSV数据源

        self._build_ui()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    def _build_ui(self):
        if hasattr(self, 'proxy') and self.proxy and self.scene():
            self.scene().removeItem(self.proxy)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.src_label = QLabel("源数据: -")
        self.src_label.setStyleSheet("color: white; background: transparent;")
        self.src_label.setWordWrap(True)
        self.src_label.setTextFormat(Qt.PlainText)  # 关闭富文本：HTML 内容原样显示不渲染
        layout.addWidget(self.src_label)

        # 正则模式行
        regex_layout = QHBoxLayout()
        regex_layout.addWidget(QLabel("正则:"))
        self.regex_edit = QLineEdit()
        self.regex_edit.setPlaceholderText("正则表达式")
        self.regex_edit.textChanged.connect(self._on_regex_changed)
        self.regex_edit.editingFinished.connect(self._on_editing_finished)
        regex_layout.addWidget(self.regex_edit)
        layout.addLayout(regex_layout)

        # 替换字符串行
        replace_layout = QHBoxLayout()
        replace_layout.addWidget(QLabel("替换为:"))
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("替换文本（留空=删除匹配）")
        self.replace_edit.textChanged.connect(self._on_regex_changed)
        self.replace_edit.editingFinished.connect(self._on_editing_finished)
        replace_layout.addWidget(self.replace_edit)
        layout.addLayout(replace_layout)

        self.result_label = QLabel("处理后: -")
        self.result_label.setStyleSheet("color: white; background: transparent;")
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.PlainText)  # 关闭富文本：HTML 内容原样显示不渲染
        layout.addWidget(self.result_label)

        container.setStyleSheet("background: rgba(100,100,100,220); border-radius:4px; color:white;")
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(container)
        self.proxy.setPos(2, 2)

        self._resize_to_fit()

    def _resize_to_fit(self):
        """根据内容自适应大小"""
        if not self.proxy or not self.proxy.widget():
            return
        container = self.proxy.widget()
        container.adjustSize()
        # 给标签文本设置合适的宽度约束
        max_w = max(200, container.sizeHint().width())
        self.src_label.setFixedWidth(max_w - 16)
        self.result_label.setFixedWidth(max_w - 16)
        container.adjustSize()
        size = container.size()
        self.setRect(0, 0, size.width() + 4, size.height() + 4)
        # 更新连线
        scene = self.scene()
        if scene and isinstance(scene, NodeScene):
            scene.update_connections_for_node(self)

    def _push_preview(self):
        """把处理后内容实时推送到下游元素框（预览用，如站点解析识别预览）。"""
        scene = self.scene()
        if scene and isinstance(scene, NodeScene):
            scene.push_preview_downstream(self)

    def set_source_data(self, data: str):
        self.source_data = data
        self.src_label.setText(f"源数据: {data}")
        self.apply_regex()
        self._resize_to_fit()

    def _on_regex_changed(self):
        """实时预览（仅更新结果，不触发 resize）"""
        self.regex = self.regex_edit.text()
        replacement = self.replace_edit.text() if hasattr(self, 'replace_edit') else ''
        if self.regex and self.source_data:
            try:
                result = re.sub(self.regex, replacement, self.source_data)
                self.processed_data = result
            except Exception:
                self.processed_data = "正则错误"
        else:
            self.processed_data = self.source_data
        self.result_label.setText(f"处理后: {self.processed_data}")
        self._push_preview()

    def _on_editing_finished(self):
        """编辑完成时应用正则+自适应大小"""
        self.apply_regex()

    def apply_regex(self):
        self.regex = self.regex_edit.text()
        replacement = self.replace_edit.text() if hasattr(self, 'replace_edit') else ''
        if self.regex and self.source_data:
            try:
                result = re.sub(self.regex, replacement, self.source_data)
                self.processed_data = result
            except Exception:
                self.processed_data = "正则错误"
        else:
            self.processed_data = self.source_data
        self.result_label.setText(f"处理后: {self.processed_data}")
        self._push_preview()
        self._resize_to_fit()

    def inputPort(self):
        return self.mapToScene(self.rect().left(), self.rect().center().y())

    def outputPort(self):
        return self.mapToScene(self.rect().right(), self.rect().center().y())

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(180, 130, 50, 200))
        else:
            painter.setPen(QPen(Qt.black, 2))
            painter.setBrush(QColor(180, 130, 50))
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.setBrush(QColor(100,200,100))
        painter.drawEllipse(QPointF(self.rect().left(), self.rect().center().y()), 5, 5)
        painter.setBrush(QColor(200,100,100))
        painter.drawEllipse(QPointF(self.rect().right(), self.rect().center().y()), 5, 5)


def _safe_eval_arith(expr):
    """安全求值算术表达式（仅 + - * / 与括号、数字、一元正负号）。非法返回 None。"""
    if expr is None:
        return None
    s = str(expr).replace('x', '*').replace('×', '*').replace('X', '*').replace('÷', '/')
    try:
        import ast as _ast
        tree = _ast.parse(s.strip(), mode='eval')
    except SyntaxError:
        return None
    allowed = (_ast.Expression, _ast.BinOp, _ast.Add, _ast.Sub, _ast.Mult, _ast.Div,
               _ast.UnaryOp, _ast.USub, _ast.UAdd, _ast.Constant, _ast.Num)
    for nd in _ast.walk(tree):
        if not isinstance(nd, allowed):
            return None
    try:
        return eval(compile(tree, '<arith>', 'eval'), {'__builtins__': {}}, {})
    except Exception:
        return None


def _to_number(v):
    """把值转为 float；失败返回 0.0（步进器输入强制要求 int/float 数字信号）。"""
    try:
        return float(str(v).strip())
    except Exception:
        return 0.0


def _apply_leading_operator_to_step(formula):
    """公式以运算符开头时，把该运算符作用于步数并加括号。

    示例：
      -1*50      → ({步数}-1)*50
      *2         → ({步数}*2)
      -{title0}  → ({步数}-{title0})
      -(1+2)*3   → ({步数}-(1+2))*3
    """
    s = (formula or '').strip()
    if not s or s[0] not in '+-*/':
        return s
    op = s[0]
    rest = s[1:].lstrip()
    m = re.match(r'^(\{[\w]+?\}|\(.+?\)|[0-9]+(?:\.[0-9]+)?)', rest)
    if not m:
        return s
    operand = m.group(1)
    tail = rest[m.end():]
    return f"({{步数}}{op}{operand}){tail}"


class StepperNode(QGraphicsRectItem):
    """步进器特殊元素框：输出 = 步数 × 步长（或用户公式）。

    - 步数：每次大轮询 +1（与左上角轮询次数同步）
    - 步长：可编辑框，默认数字；有上游数字信号时显示 {title0}、{title1} 引用
    - 输入点可多个（左侧，多条线添加）；输出点唯一（右侧）
    - 多个输入用算术运算符连接运算（示例：输出 = 步数 x ({title0}*{title1}-{key_name0})）
    - 每个轮询步进数 +1，并把输出值立即注入下游元素框（API 参数/拼合栏）参与执行
    """
    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(2)
        self.step_len = 1.0          # 默认步长（无公式时：输出 = 步数 × 步长）
        self.formula = ""            # 用户公式（可含 {步数} / {titleN}）
        self.output_value = 0.0      # 最近一次输出
        self.output_int = False      # 输出类型：True=int（四舍五入取整），False=float
        self._step_count = 1         # 当前步数（最近一次轮询；未运行过为 1，用于实时预览）
        self._inputs = []            # [{'key':'title0','source':'','value':''}]
        self._input_counter = 0
        self._build_ui()

    def _build_ui(self):
        if hasattr(self, 'proxy') and self.proxy and self.scene():
            self.scene().removeItem(self.proxy)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        title = QLabel("🔢 步进器")
        title.setStyleSheet("color:#e5a53b; font-weight:bold; background:transparent;")
        layout.addWidget(title)
        row = QHBoxLayout()
        row.addWidget(QLabel("步长/公式:"))
        self.step_edit = QLineEdit()
        self.step_edit.setPlaceholderText("如 2 或 {步数}*({title0}+{title1})")
        # 先连接再 setText，使初始值也触发实时预览
        self.step_edit.textChanged.connect(self._on_edit_changed)
        self.step_edit.setText(self.formula or str(self.step_len))
        row.addWidget(self.step_edit)
        # 输出类型按钮：int / float（与输出点同侧，右侧）
        self._output_type_btns = []
        for _t, _lbl in (("int", "int"), ("float", "float")):
            b = QPushButton(_lbl)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setFixedWidth(34)
            b.setChecked((_t == "float") != bool(self.output_int))
            b.setStyleSheet(
                "QPushButton { color:#fff; background:#2a4a6a; border:1px solid #4a7aaa; "
                "border-radius:3px; padding:0 4px; font-size:10px; }"
                "QPushButton:checked { background:#55aaff; color:#000; }"
            )
            b.clicked.connect(lambda _=False, t=_t: self._on_output_type_changed(t))
            row.addWidget(b)
            self._output_type_btns.append(b)
        layout.addLayout(row)
        # ---- 输入数据栏：每个输入一行（无连线=可编辑自变量，可直接写数字/公式）----
        self._input_widgets = {}
        hint_lbl = QLabel("输入（无连线时可直接写数字/公式）:")
        hint_lbl.setStyleSheet("color:#aaa; font-size:10px; background:transparent;")
        layout.addWidget(hint_lbl)
        for x in self._inputs:
            self._add_input_row(layout, x)
        add_btn = QPushButton("+ 添加输入")
        add_btn.setStyleSheet(
            "QPushButton { color:#fff; background:#3a5a7a; border:1px dashed #5a8aaa; "
            "border-radius:3px; padding:1px 6px; font-size:10px; }"
            "QPushButton:hover { background:#4a6a8a; }"
        )
        add_btn.clicked.connect(self._on_add_input_clicked)
        layout.addWidget(add_btn)
        self.output_label = QLabel("输出: 0")
        self.output_label.setStyleSheet("color:#55aaff; font-size:11px; background:transparent;")
        layout.addWidget(self.output_label)
        container.setStyleSheet("background: rgba(90,60,15,230); border-radius:4px; color:white;")
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(container)
        self.proxy.setPos(2, 2)
        self.refresh_display()  # 初始值同步到输出标签
        self._resize_to_fit()

    def _add_input_row(self, layout, x):
        """渲染一个输入数据栏：{titleN} 标签 + 输入框（无连线可编辑，有连线只读）。"""
        key = x.get('key', '')
        row = QHBoxLayout()
        lbl = QLabel(f"{{{key}}}")
        lbl.setFixedWidth(60)
        lbl.setStyleSheet("color:#8fd08f; font-size:10px; background:transparent;")
        row.addWidget(lbl)
        edit = QLineEdit()
        edit.setReadOnly(bool(x.get('connected')))
        edit.setPlaceholderText("上游数据（有连线）" if x.get('connected') else "数字或公式（自变量）")
        edit.setToolTip("前方无连线时可直接写数字/公式；有连线时由上游数据提供")
        if x.get('text'):
            edit.setText(str(x['text']))
        edit.textChanged.connect(lambda t, k=key: self._on_input_text_changed(k, t))
        row.addWidget(edit)
        # 右侧 ✕ 键：可选删除该输入（连着的线一并移除）
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setToolTip(f"删除输入 {key}")
        del_btn.setStyleSheet(
            "QPushButton { color:#ff9a9a; background:transparent; border:1px solid #8a4a4a; "
            "border-radius:3px; font-size:10px; }"
            "QPushButton:hover { background:#8a2a2a; color:#fff; }"
        )
        del_btn.clicked.connect(lambda _=False, k=key: self._on_remove_input_clicked(k))
        row.addWidget(del_btn)
        layout.addLayout(row)
        self._input_widgets[key] = edit

    def _on_input_text_changed(self, key, text):
        for x in self._inputs:
            if x.get('key') == key:
                x['text'] = text
                break
        # 编辑输入数据栏后立即预览当前处理数值
        self._recompute_preview()

    def _on_add_input_clicked(self):
        # 按钮事件内不直接重建 proxy（避免销毁被点击控件导致崩溃），延迟执行
        self._pending_add_input = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_add_input)

    def _do_add_input(self):
        if not getattr(self, '_pending_add_input', False):
            return
        self._pending_add_input = False
        self.add_independent_input()
        self._build_ui()
        self._resize_to_fit()

    def _on_output_type_changed(self, t):
        """切换输出类型 int/float：立即重算并刷新预览。"""
        self.output_int = (t == "int")
        # 同步按钮互斥状态（QPushButton autoExclusive 在同父下应已互斥，这里兜底）
        if hasattr(self, '_output_type_btns') and self._output_type_btns:
            for b, _t in zip(self._output_type_btns, ("int", "float")):
                b.blockSignals(True)
                b.setChecked(_t == t)
                b.blockSignals(False)
        self._recompute_preview()

    def _on_remove_input_clicked(self, key):
        # 按钮事件内不直接重建 proxy（避免销毁被点击控件导致崩溃），延迟执行
        self._pending_remove_input = key
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_remove_input)

    def _do_remove_input(self):
        """彻底删除一个输入：先移除连到它的所有连线，再从 _inputs 中删除并重建 UI。"""
        key = getattr(self, '_pending_remove_input', None)
        if not key:
            return
        self._pending_remove_input = None
        scene = self.scene()
        if scene is not None and isinstance(scene, NodeScene):
            for conn in list(scene.connections):
                if conn.end_obj == self and conn.end_param == key:
                    scene.removeItem(conn)
                    scene.connections.remove(conn)
            scene._save_undo()
        self._inputs = [x for x in self._inputs if x.get('key') != key]
        # 计数器不回退，避免后续新增 key 冲突
        self._input_counter = max(self._input_counter, len(self._inputs))
        self._update_step_hint()
        self._build_ui()
        self._resize_to_fit()

    def _update_step_hint(self):
        if not hasattr(self, 'step_edit') or not self.step_edit:
            return
        hints = [f'{{{x["key"]}}}' for x in self._inputs]
        if hints:
            self.step_edit.setPlaceholderText("公式可用: " + '、'.join(hints))
        else:
            self.step_edit.setPlaceholderText("如 2 或 {步数}*({title0}+{title1})")

    def _resize_to_fit(self):
        if not self.proxy or not self.proxy.widget():
            return
        container = self.proxy.widget()
        container.adjustSize()
        w = container.sizeHint().width()
        self.step_edit.setFixedWidth(max(130, w - 30))
        container.adjustSize()
        size = container.size()
        self.setRect(0, 0, size.width() + 4, size.height() + 4)
        scene = self.scene()
        if scene and isinstance(scene, NodeScene):
            scene.update_connections_for_node(self)

    def _on_edit_changed(self, text):
        # 纯数字（含负数）→ 步长；否则当作公式
        try:
            if re.fullmatch(r'[+-]?\d+(\.\d+)?', (text or '').strip()):
                self.step_len = float(text)
                self.formula = ""
            else:
                self.formula = text
        except Exception:
            pass
        # 编辑后立即预览当前处理数值
        self._recompute_preview()

    def _recompute_preview(self):
        """编辑后立即预览：步数取当前步数，自变量取数据栏值，连线输入取 0/上次值。"""
        try:
            iv = {}
            for x in self._inputs:
                if x.get('connected'):
                    iv[x['key']] = x.get('value', 0) or 0
                else:
                    iv[x['key']] = self.resolve_independent_input(x['key'])
            self.compute(self._step_count, iv)
        except Exception:
            pass

    def refresh_display(self):
        if hasattr(self, 'output_label') and self.output_label:
            self.output_label.setText(f"输出: {self.output_value}")

    # ---------- 端口 ----------
    def inputPort(self):
        return self.mapToScene(self.rect().left(), self.rect().center().y())

    def outputPort(self):
        return self.mapToScene(self.rect().right(), self.rect().center().y())

    def input_port(self, idx):
        n = max(1, len(self._inputs) + 1)
        y = self.rect().top() + (idx + 1) * self.rect().height() / n
        return self.mapToScene(self.rect().left(), y)

    def input_port_for_key(self, key):
        for i, x in enumerate(self._inputs):
            if x.get('key') == key:
                return self.input_port(i)
        return self.inputPort()

    # ---------- 输入管理 ----------
    def register_input(self):
        """新增一个输入（连线连到步进器时调用），返回输入 key。"""
        key = f"title{self._input_counter}"
        self._input_counter += 1
        self._inputs.append({'key': key, 'source': '', 'value': 0.0,
                             'connected': True, 'text': ''})
        self._update_step_hint()
        self._build_ui()
        self._resize_to_fit()
        return key

    def add_independent_input(self):
        """新增一个自变量输入（无上游连线，数据栏可直接写数字/公式）。"""
        key = f"title{self._input_counter}"
        self._input_counter += 1
        self._inputs.append({'key': key, 'source': '', 'value': 0.0,
                             'connected': False, 'text': ''})
        self._update_step_hint()
        return key

    def remove_input(self, key):
        """断开一个输入：保留该输入并转为可编辑自变量（不删除数据栏）。"""
        for x in self._inputs:
            if x.get('key') == key:
                x['connected'] = False
                x['source'] = ''
                break
        self._update_step_hint()
        self._build_ui()
        self._resize_to_fit()

    def resolve_independent_input(self, key):
        """无连线的输入 → 自变量：读取数据栏中的数字或公式。"""
        text = ''
        for x in self._inputs:
            if x.get('key') == key:
                text = str(x.get('text', '') or '').strip()
                break
        if not text:
            return 0.0
        try:
            if re.fullmatch(r'[+-]?\d+(\.\d+)?', text):
                return float(text)
            if text[0] in '+-*/':
                # 运算符开头的公式按 (步数±操作数)… 处理，例如 -1*50 → (步数-1)*50
                expr = _apply_leading_operator_to_step(text) \
                    .replace('{步数}', str(self._step_count))
                r = _safe_eval_arith(expr)
                return float(r) if r is not None else 0.0
            r = _safe_eval_arith(text)
            return float(r) if r is not None else 0.0
        except Exception:
            return 0.0

    # ---------- 计算 ----------
    def set_input_values(self, values):
        for x in self._inputs:
            x['value'] = values.get(x['key'], 0)

    def compute(self, step_count, input_values):
        """计算输出：输出 = 步数 × 步长（或公式求值）。

        公式以运算符开头时按 _apply_leading_operator_to_step 处理，
        例如 -1*50 → (步数-1)*50。同时记录当前步数供实时预览使用。
        主公式为空时：若存在填了“公式”（非纯数字）的自变量数据栏，
        取第一个作为输出公式（例：title0 填 -1*50 → 输出=(步数-1)*50）。
        """
        self._step_count = step_count
        self.set_input_values(input_values)
        expr = (self.formula or '').strip()
        if not expr:
            # 主公式为空 → 优先用“填了公式”的自变量数据栏作为输出公式
            for x in self._inputs:
                if x.get('connected'):
                    continue
                t = str(x.get('text', '') or '').strip()
                if t and not re.fullmatch(r'[+-]?\d+(\.\d+)?', t):
                    expr = t
                    break
        if not expr:
            s = f"{step_count}*{self.step_len}"
        else:
            s = _apply_leading_operator_to_step(expr)
        s = s.replace('{步数}', str(step_count)).replace('{step}', str(step_count)) \
             .replace('{STEP}', str(step_count))
        for i in range(len(input_values)):
            s = s.replace(f'{{title{i}}}', str(input_values.get(f'title{i}', 0))) \
                 .replace(f'{{key_name{i}}}', str(input_values.get(f'title{i}', 0)))
        try:
            if re.fullmatch(r'[+-]?\d+(\.\d+)?', s.strip()):
                # 公式替换后只剩纯数字：直接作为输出值（不再次乘步数，避免重复计数）
                self.output_value = float(s)
            else:
                r = _safe_eval_arith(s)
                if r is not None:
                    self.output_value = float(r)
        except Exception:
            pass
        # 输出类型：int 时四舍五入取整
        if self.output_int:
            self.output_value = int(round(self.output_value))
        self.refresh_display()
        return self.output_value

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(120, 80, 20, 200))
        else:
            painter.setPen(QPen(Qt.black, 2))
            painter.setBrush(QColor(120, 80, 20))
        painter.drawRoundedRect(self.rect(), 4, 4)
        # 输出点（右侧，唯一）
        painter.setBrush(QColor(200, 100, 100))
        out = self.mapFromScene(self.outputPort())
        painter.drawEllipse(out, 6, 6)
        # 输入点（左侧，每个输入一个）
        painter.setBrush(QColor(100, 200, 100))
        for i in range(len(self._inputs)):
            p = self.mapFromScene(self.input_port(i))
            painter.drawEllipse(p, 6, 6)
        # 主输入点（左侧中心，总入口）
        painter.setBrush(QColor(100, 200, 100, 120))
        painter.drawEllipse(QPointF(self.rect().left(), self.rect().center().y()), 4, 4)

# ================= 图纸节点（拖入图片） =================
_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.ico',
               '.svg', '.tif', '.tiff'}

def _is_image_file(path):
    try:
        return os.path.splitext((path or '').lower())[1] in _IMAGE_EXTS
    except Exception:
        return False


def _is_wbt_file(path):
    """是否为流程图(.wbt)文件 —— 图纸拖入的第二类来源。"""
    try:
        return (path or '').lower().endswith('.wbt')
    except Exception:
        return False


class ImageNode(QGraphicsRectItem):
    """图纸节点：显示一张图片（图纸/素材）。

    - 从文件管理器拖入图片 → 预先渲染原图虚影（保持比例）判断绘制大小 → 松开绘制
    - 可拖动、可框选进类、可删除；保存时把图片内嵌(base64)进流程图，随时可调用
    """
    def __init__(self, image_path='', pixmap=None, display_w=200):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(2)
        self.image_path = image_path or ''
        self._pixmap = pixmap
        self._display_w = max(60, int(display_w or 200))
        if self._pixmap is None and self.image_path:
            self.load_image()
        self._resize_to_fit()

    def load_image(self):
        if self.image_path:
            try:
                pm = QPixmap(self.image_path)
                if not pm.isNull():
                    self._pixmap = pm
            except Exception:
                pass

    def to_base64(self, max_side=1024):
        """把图片缩放后内嵌为 base64 PNG（保证流程图可移植）。"""
        import base64
        from PySide6.QtCore import QBuffer, QByteArray
        if not self._pixmap or self._pixmap.isNull():
            return ''
        pm = self._pixmap
        if max(pm.width(), pm.height()) > max_side:
            pm = pm.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        pm.save(buf, 'PNG')
        buf.close()
        try:
            return base64.b64encode(bytes(ba)).decode('ascii')
        except Exception:
            return ''

    def from_base64(self, b64):
        import base64
        from PySide6.QtCore import QByteArray
        try:
            raw = base64.b64decode(b64 or '')
            pm = QPixmap()
            pm.loadFromData(QByteArray(raw), 'PNG')
            if not pm.isNull():
                self._pixmap = pm
        except Exception:
            pass

    def set_pixmap(self, pm):
        self._pixmap = pm
        self._resize_to_fit()

    def _resize_to_fit(self):
        if self._pixmap and not self._pixmap.isNull():
            h = max(40, int(self._display_w * self._pixmap.height() / max(1, self._pixmap.width())))
            self.setRect(0, 0, self._display_w, h)
        else:
            self.setRect(0, 0, self._display_w, 120)
        scene = self.scene()
        if scene and isinstance(scene, NodeScene):
            scene.update_connections_for_node(self)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        r = self.rect()
        painter.setBrush(QColor(24, 34, 54))
        painter.setPen(QPen(QColor(120, 160, 210), 2))
        painter.drawRect(r)
        if self._pixmap and not self._pixmap.isNull():
            inner = r.adjusted(3, 3, -3, -3)
            pw, ph = self._pixmap.width(), self._pixmap.height()
            scale = min(inner.width() / pw, inner.height() / ph) if pw and ph else 1.0
            dw, dh = pw * scale, ph * scale
            tx = int(inner.x() + (inner.width() - dw) / 2)
            ty = int(inner.y() + (inner.height() - dh) / 2)
            painter.drawPixmap(tx, ty, int(dw), int(dh), self._pixmap)
        else:
            painter.setPen(QColor(150, 160, 180))
            painter.drawText(r, Qt.AlignCenter, "图纸\n(拖入图片文件)")
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3))
            painter.drawRect(r)


# ================= 类框（可视化图层类嵌套方案） =================
def _color_to_str(c):
    """颜色序列化为 #AARRGGBB。"""
    try:
        return c.name(QColor.HexArgb)
    except Exception:
        return '#1c6cd22a'


def _color_from_str(s, default=None):
    """颜色反序列化（#AARRGGBB）。"""
    if not s:
        return QColor(default) if default is not None else QColor(28, 108, 210, 42)
    c = QColor()
    c.setNamedColor(str(s))
    if c.isValid():
        return c
    return QColor(default) if default is not None else QColor(28, 108, 210, 42)


class ClassColorDialog(QDialog):
    """类框自定义颜色：背景色 + 边框色（背景保留毛玻璃半透明）。"""
    def __init__(self, bg, border, parent=None):
        super().__init__(parent)
        self.setWindowTitle("类框颜色")
        self.setModal(True)
        self._bg = QColor(bg)
        self._border = QColor(border)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(QLabel("背景色:"))
        self.btn_bg = QPushButton()
        self.btn_bg.setFixedSize(64, 30)
        self.btn_bg.setToolTip("点击选择类框背景色（毛玻璃）")
        self.btn_bg.clicked.connect(self._pick_bg)
        row.addWidget(self.btn_bg)
        row.addSpacing(16)
        row.addWidget(QLabel("边框色:"))
        self.btn_border = QPushButton()
        self.btn_border.setFixedSize(64, 30)
        self.btn_border.setToolTip("点击选择类框边框色")
        self.btn_border.clicked.connect(self._pick_border)
        row.addWidget(self.btn_border)
        lay.addLayout(row)
        tip = QLabel("提示：背景为毛玻璃半透明效果，所选颜色会轻微淡化。")
        tip.setStyleSheet("color:#888; font-size:11px;")
        lay.addWidget(tip)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._refresh()

    def _refresh(self):
        self.btn_bg.setStyleSheet(
            f"background-color:{self._bg.name()}; border:1px solid #777; border-radius:3px;")
        self.btn_border.setStyleSheet(
            f"background-color:{self._border.name()}; border:1px solid #777; border-radius:3px;")

    def _pick_bg(self):
        c = QColorDialog.getColor(self._bg, self, "选择类框背景色")
        if c.isValid():
            self._bg = c
            self._refresh()

    def _pick_border(self):
        c = QColorDialog.getColor(self._border, self, "选择类框边框色")
        if c.isValid():
            self._border = c
            self._refresh()

    def colors(self):
        return QColor(self._bg), QColor(self._border)


class ClassNode(QGraphicsRectItem):
    """类框：把一组元素框打包成一个类，拥有独立的类大轮询次数与触发/输出信号。

    - 第一个创建的类框为 main：类名固定 "main"、不可更改、无输入触发点、运行时必定触发
    - 非 main 类框：左侧输入触发点、右侧输出触发点；输入未触发时不执行内部内容（静默）
    - 每个类框有自己独立的大轮询次数；被框选的步进器跟随该类轮询次数
    - 框选内容背景淡色透明玻璃着色、圆角纯色边框、边贴合方格网格（20px）
    - 左上角类名文本框 + 该类大轮询次数及调整按钮；右上角 ✕ 取消整合
    """
    GRID = 20          # 方格网格对齐步长（与场景小网格一致）
    MARGIN = 30        # 比成员总范围多出的外边距
    _id_counter = 0    # 类实例自增 ID（序列化/恢复用）

    def __init__(self, class_name=None, is_main=False, poll_count=1, members=None,
                 poll_auto=False):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)  # 拖动类框 → 成员整体平移
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(-1)  # 置于连线(z=0)/成员(z=2)之下：类玻璃与选中高亮不遮挡内容（头部代理有效z=5仍在顶层）
        self.class_name = class_name or ('main' if is_main else 'class1')
        self._is_main = bool(is_main)
        self.poll_count = max(1, int(poll_count or 1))
        # 自增轮询：勾选后本类不按 poll_count 固定轮数跑，而是从 1 递增到 AUTO_POLL_MAX，
        # 直到本类下载层报错或本类所辖数据库框数据走完一整圈为止。
        self.poll_auto = bool(poll_auto)

        self._members = list(members or [])
        self._header_proxy = None
        self._suppress_refit = False  # 拖动类框期间禁止贴合（防循环）
        self._prev_pos = QPointF(0, 0)
        # ---- 自定义框选色（毛玻璃背景 + 边框，可改，随 .wbt 保存） ----
        self._bg_color = QColor(28, 108, 210, 42)      # 默认玻璃背景
        self._border_color = QColor(90, 170, 255, 235)  # 默认边框
        ClassNode._id_counter += 1
        self._class_id = ClassNode._id_counter
        self._build_header()
        self._refit()
        self._prev_pos = self.pos()

    # ---------- 头部控件：类名 + 类轮询次数 + 调整按钮 + ✕ ----------
    def _build_header(self):
        if self._header_proxy is not None:
            try:
                if self._header_proxy.scene():
                    self._header_proxy.scene().removeItem(self._header_proxy)
            except Exception:
                pass
            self._header_proxy = None
        hw = QWidget()
        hw.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(hw)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)

        self._name_edit = QLineEdit(self.class_name)
        self._name_edit.setFixedWidth(96)
        self._name_edit.setToolTip("类名（main 类固定不可更改）")
        if self._is_main:
            self._name_edit.setReadOnly(True)
            self._name_edit.setText('main')
        self._name_edit.setStyleSheet(
            "QLineEdit { background:#1a3a5a; color:#ffffff; border:1px solid #4a6a9a; "
            "border-radius:3px; font-weight:bold; padding:2px 5px; }")
        self._name_edit.editingFinished.connect(self._on_name_changed)
        lay.addWidget(self._name_edit)

        lbl = QLabel("轮询:")
        lbl.setStyleSheet("color:#cfe4ff; font-size:11px; background:transparent;")
        lay.addWidget(lbl)

        self._poll_edit = QSpinBox()
        self._poll_edit.setRange(1, 9999)
        self._poll_edit.setValue(self.poll_count)
        self._poll_edit.setFixedWidth(50)
        self._poll_edit.setToolTip("该类大轮询次数（被框选内容脱离 UI 左上角全局轮询控制）")
        self._poll_edit.valueChanged.connect(self._on_poll_changed)
        lay.addWidget(self._poll_edit)

        self._btn_minus = QPushButton("−")
        self._btn_minus.setFixedSize(20, 20)
        self._btn_minus.setToolTip("减少该类轮询次数")
        self._btn_minus.clicked.connect(
            lambda: self._poll_edit.setValue(max(1, self._poll_edit.value() - 1)))
        lay.addWidget(self._btn_minus)

        self._btn_plus = QPushButton("+")
        self._btn_plus.setFixedSize(20, 20)
        self._btn_plus.setToolTip("增加该类轮询次数")
        self._btn_plus.clicked.connect(
            lambda: self._poll_edit.setValue(self._poll_edit.value() + 1))
        lay.addWidget(self._btn_plus)

        self._poll_auto_chk = QCheckBox("自增")
        self._poll_auto_chk.setChecked(self.poll_auto)
        self._poll_auto_chk.setToolTip(
            f"自增轮询：不按上面拨盘的固定轮数跑，而是从第 1 轮开始逐轮 +1，\n"
            f"直到本类下载层报错（HTTP 404 等）或本类所辖数据库框数据走完一整圈；\n"
            f"最多递增到 {AUTO_POLL_MAX} 轮（再多请取消勾选、用拨盘手动指定）")
        self._poll_auto_chk.setStyleSheet(
            "QCheckBox { color:#cfe4ff; font-size:11px; background:transparent; }")
        self._poll_auto_chk.toggled.connect(self._on_poll_auto_changed)
        lay.addWidget(self._poll_auto_chk)
        self._poll_edit.setEnabled(not self.poll_auto)

        lay.addStretch(1)

        self._btn_color = QPushButton("🎨")
        self._btn_color.setFixedSize(22, 20)
        self._btn_color.setToolTip("自定义类框背景/边框颜色（毛玻璃）")
        self._btn_color.clicked.connect(self._on_color_clicked)
        lay.addWidget(self._btn_color)

        self._btn_x = QPushButton("✕")
        self._btn_x.setFixedSize(20, 20)
        self._btn_x.setToolTip("取消该类整合（内部内容保持不变，重新跟踪全局大轮询）")
        self._btn_x.clicked.connect(self._on_remove_clicked)
        lay.addWidget(self._btn_x)

        self._header_proxy = QGraphicsProxyWidget(self)
        self._header_proxy.setWidget(hw)
        self._header_proxy.setZValue(6)  # 头部在成员之上可点击

    # ---------- 信号 ----------
    def _on_name_changed(self):
        if self._is_main:
            self._name_edit.setText('main')
            self.class_name = 'main'
            return
        name = (self._name_edit.text() or '').strip() or 'class'
        self._name_edit.setText(name)
        self.class_name = name
        self._update_member_tooltips()

    def _on_poll_changed(self, v):
        self.poll_count = max(1, int(v))

    def _on_poll_auto_changed(self, checked):
        """自增轮询开关（勾选后 poll_count 仅作显示，实际轮数由运行时递增决定）。"""
        self.poll_auto = bool(checked)
        self._poll_edit.setEnabled(not self.poll_auto)

    def _on_remove_clicked(self):
        scene = self.scene()
        if scene is not None and hasattr(scene, 'remove_class'):
            # 延迟执行：按钮事件内销毁自身代理会崩溃
            QTimer.singleShot(0, lambda s=scene, cc=self: s.remove_class(cc))

    def _on_color_clicked(self):
        """自定义类框背景/边框颜色（保持毛玻璃半透明）。"""
        dlg = ClassColorDialog(self._bg_color, self._border_color)
        if dlg.exec() != QDialog.Accepted:
            return
        bg, border = dlg.colors()
        bg.setAlpha(45)       # 毛玻璃：背景半透明
        border.setAlpha(235)
        self._bg_color = bg
        self._border_color = border
        self.update()
        scene = self.scene()
        if scene is not None and hasattr(scene, '_save_undo'):
            scene._save_undo()

    def set_as_regular(self):
        """把 main 类转为普通类（粘贴出重复 main 时用）。"""
        self._is_main = False
        if self.class_name == 'main':
            self.class_name = 'main_copy'
        if hasattr(self, '_name_edit') and self._name_edit:
            self._name_edit.setReadOnly(False)
            self._name_edit.setText(self.class_name)

    # ---------- 成员管理 ----------
    def add_member(self, node):
        if node in self._members:
            return
        old = getattr(node, '_class', None)
        if old is not None and old is not self and node in getattr(old, '_members', []):
            old._members.remove(node)
        node._class = self
        self._members.append(node)
        try:
            node.setToolTip(f"类: {self.class_name}")
        except Exception:
            pass

    def remove_member(self, node):
        if node in self._members:
            self._members.remove(node)
        if getattr(node, '_class', None) is self:
            node._class = None
        try:
            node.setToolTip('')
        except Exception:
            pass

    def _update_member_tooltips(self):
        for m in self._members:
            try:
                m.setToolTip(f"类: {self.class_name}")
            except Exception:
                pass

    # ---------- 端口：左输入触发点 / 右输出触发点 ----------
    def inputPort(self):
        return self.mapToScene(self.rect().left(), self.rect().center().y())

    def outputPort(self):
        return self.mapToScene(self.rect().right(), self.rect().center().y())

    # ---------- 贴合：按成员范围 + 网格对齐 ----------
    def _refit(self):
        if not self._members:
            self.setRect(0, 0, 280, 140)
            self._position_header()
            return
        rect = QRectF()
        for m in self._members:
            try:
                if m.scene() is None:
                    continue
                rect = rect.united(m.sceneBoundingRect())
            except Exception:
                continue
        if rect.isNull():
            rect = QRectF(self.pos(), QSizeF(280, 140))
        rect = rect.adjusted(-self.MARGIN, -self.MARGIN, self.MARGIN, self.MARGIN)
        g = self.GRID
        left = math.floor(rect.left() / g) * g
        top = math.floor(rect.top() / g) * g
        right = math.ceil(rect.right() / g) * g
        bottom = math.ceil(rect.bottom() / g) * g
        self._suppress_refit = True
        try:
            self.setPos(QPointF(left, top))
            self.setRect(QRectF(0, 0, max(g, right - left), max(g, bottom - top)))
        finally:
            self._suppress_refit = False
        self._prev_pos = QPointF(left, top)
        self._position_header()

    def _position_header(self):
        if self._header_proxy is None or self._header_proxy.widget() is None:
            return
        try:
            w = max(140, int(self.rect().width() - 24))
            self._header_proxy.widget().setFixedWidth(w)
        except Exception:
            pass
        self._header_proxy.setPos(12, 12)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            # 拖动类框 → 所有成员同步平移（成员已选中的由 Qt 一并移动，避免二次位移）
            if (not getattr(self, '_suppress_refit', False)
                    and getattr(self, '_members', None)
                    and value != self._prev_pos):
                delta = QPointF(value) - self._prev_pos
                self._suppress_refit = True
                try:
                    for m in list(self._members):
                        try:
                            if m.scene() is not None and not m.isSelected():
                                m.setPos(m.pos() + delta)
                        except Exception:
                            pass
                finally:
                    self._suppress_refit = False
                self._prev_pos = QPointF(value)
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    # ---------- 绘制 ----------
    def paint(self, painter, option, widget=None):
        r = self.rect()
        # 淡色透明玻璃背景（自定义颜色，仍为毛玻璃半透明）
        painter.setBrush(self._bg_color)
        painter.setPen(QPen(self._border_color, 2))
        painter.drawRoundedRect(r, 14, 14)
        # 输入触发点（左侧；main 无输入点）
        if not self._is_main:
            painter.setBrush(QColor(255, 190, 90))
            inp = self.mapFromScene(self.inputPort())
            painter.drawEllipse(inp, 6, 6)
        # 输出触发点（右侧）
        painter.setBrush(QColor(120, 230, 120))
        out = self.mapFromScene(self.outputPort())
        painter.drawEllipse(out, 6, 6)
        # 选中：透明玻璃高亮（不遮挡成员与连线）+ 高亮边框
        if self.isSelected():
            painter.setBrush(QColor(40, 140, 255, 50))
            painter.setPen(QPen(QColor(0, 200, 255), 3))
            painter.drawRoundedRect(r, 14, 14)


# ================= 站点解析节点（元素框） =================
class SiteParserNode(QGraphicsRectItem):
    """站点解析元素框：输入 URL → 识别站点 → 解析器下载 → 存入下游容器根文件夹。

    - 顶部状态行：已识别: 未识别 / MEGA / ...
    - 中间 URL 输入预览（可编辑；若输入点有上游连线，运行时以上游文本为准）
    - 左侧输入点（接收文本内容作为 URL）；右侧输出点（连到容器框根文件夹）
    - 执行时以 [GET] 形式出现在「输出」选项卡，带实时进度条
    """
    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(2)
        self.url = ""            # 当前 URL（预览框文本）
        self.parser_name = "未识别"
        self.input_text = ""     # 上游输入点传入的文本（有连线时）
        self._executed_url = ""  # 已成功解析的 URL（多轮去重：同一 URL 不重复下载）
        self._executed_ok = False
        self._last_ok = False
        self._last_msg = ""
        self._last_size = 0
        self._build_ui()

    def _build_ui(self):
        if hasattr(self, 'proxy') and self.proxy and self.scene():
            self.scene().removeItem(self.proxy)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        title = QLabel("🌐 站点解析")
        title.setStyleSheet("color:#55aaff; font-weight:bold; background:transparent;")
        layout.addWidget(title)

        self.status_label = QLabel("已识别: 未识别")
        self.status_label.setStyleSheet("color:#e5a53b; font-size:11px; background:transparent;")
        layout.addWidget(self.status_label)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("输入站点链接，如 https://mega.nz/...")
        self.url_edit.setText(self.url)
        self.url_edit.textChanged.connect(self._on_url_changed)
        layout.addWidget(self.url_edit)

        self.info_label = QLabel("输出点连到容器框根文件夹")
        self.info_label.setStyleSheet("color:#aaa; font-size:10px; background:transparent;")
        layout.addWidget(self.info_label)

        container.setStyleSheet("background: rgba(20,60,110,230); border-radius:4px; color:white;")
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(container)
        self.proxy.setPos(2, 2)
        self._resize_to_fit()

    def _on_url_changed(self, text):
        self.url = text
        self._update_parser_status()
        self._resize_to_fit()  # 输入数据时同步贴合边框，防止内容区溢出

    def _has_input_connection(self):
        """输入点是否有上游连线。"""
        scene = self.scene()
        if scene is None:
            return False
        return any(getattr(c, 'end_obj', None) is self
                   for c in getattr(scene, 'connections', ()))

    def set_input_text(self, text):
        """上游输入点实时推入的文本（预览用）：更新识别状态。"""
        self.input_text = (text or '').strip()
        self._update_parser_status()

    def _update_parser_status(self):
        """根据当前 URL 识别站点并更新状态行。

        有输入点连线时以上游推送的文本为准（实时预览下游处理结果），
        否则用预览框里手填的 URL。
        """
        try:
            from site_parser import manager
            probe = self.input_text if self._has_input_connection() else self.get_url()
            p = manager.detect(probe)
            self.parser_name = p.name if p else "未识别"
        except Exception:
            self.parser_name = "未识别"
        self._set_status_text(f"已识别: {self.parser_name}")

    def _set_status_text(self, text):
        if hasattr(self, 'status_label') and self.status_label:
            self.status_label.setText(text)

    def get_url(self):
        if hasattr(self, 'url_edit') and self.url_edit:
            return self.url_edit.text().strip()
        return self.url

    def set_url(self, url):
        self.url = url or ""
        if hasattr(self, 'url_edit') and self.url_edit:
            if self.url_edit.text() != self.url:
                self.url_edit.blockSignals(True)
                self.url_edit.setText(self.url)
                self.url_edit.blockSignals(False)
        self._update_parser_status()
        self._resize_to_fit()

    def _resize_to_fit(self):
        if not self.proxy or not self.proxy.widget():
            return
        container = self.proxy.widget()
        # 固定输入框与容器最大宽度：长 URL 输入时内容不再撑破边框（横向滚动）
        if hasattr(self, 'url_edit') and self.url_edit:
            self.url_edit.setFixedWidth(220)
        container.setMaximumWidth(230)
        container.adjustSize()
        size = container.size()
        self.setRect(0, 0, size.width() + 4, size.height() + 4)
        scene = self.scene()
        if scene and isinstance(scene, NodeScene):
            scene.update_connections_for_node(self)

    # ---------- 端口 ----------
    def inputPort(self):
        return self.mapToScene(self.rect().left(), self.rect().center().y())

    def outputPort(self):
        return self.mapToScene(self.rect().right(), self.rect().center().y())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(20, 90, 160, 200))
        else:
            painter.setPen(QPen(Qt.black, 2))
            painter.setBrush(QColor(20, 90, 160))
        painter.drawRoundedRect(self.rect(), 4, 4)
        # 输出点（右侧）
        painter.setBrush(QColor(200, 100, 100))
        out = self.mapFromScene(self.outputPort())
        painter.drawEllipse(out, 6, 6)
        # 输入点（左侧）
        painter.setBrush(QColor(100, 200, 100))
        inp = self.mapFromScene(self.inputPort())
        painter.drawEllipse(inp, 6, 6)


# ================= CSV/XLSX 数据库节点（分块懒加载+滑动窗口） =================
class CsvDataNode(QGraphicsRectItem):
    """数据库节点：加载 CSV/XLSX 文件，只读展示，每列可输出连线。
    采用分块懒加载 + 滑动窗口策略：
    - 块大小 = 21 行
    - 最多缓存 3 个块，加载新块时释放最旧的块
    """
    def __init__(self, file_path=""):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(2)

        self.file_path = file_path
        self.headers = []              # 表头列表
        self._row_count = 0            # 文件总行数
        self.current_row = 0           # 当前行索引
        self.start_row = 0             # 起始行
        self.proxy = None
        self._title_label = None       # 标题行（输入端口的对齐基准）

        # ---------- 写入（上游数据流入本表） ----------
        # ingest_mode: upsert=按主键更新/追加 | append=纯追加 | replace=清空重写 | refresh=只重读不写
        self.ingest_mode = 'upsert'
        self.key_column = ''           # 主键列名，空表示自动探测
        self._ingest_depth = 0         # 重入保护（写入 → 刷新 → 下游 → 再写回）
        self._last_ingest_text = ''    # 最近一次写入结果的摘要，显示在节点底部
        self._ingest_backed_up = False # 本轮是否已备份原文件（每轮只备份一次）
        # 本次运行中被上游写过内容的行号（"新鲜行"）。
        # 运行期间只在这些行之间循环，绝不回头读取运行前就有的老行——
        # 老行只作为图纸占位符存在，不再参与轮询。为空表示"静态表"，行为不变。
        self._fresh_rows = set()

        # ---------- 分块缓存 ----------
        self.CHUNK_SIZE = 21           # 每块行数
        self.MAX_CHUNKS = 3            # 最多缓存块数
        self._chunks = {}              # {chunk_index: [{header:val, ...}, ...]}
        self._chunk_load_order = []    # 块加载顺序，用于淘汰
        self._cached_values = {}       # {chunk_index: {header: [val,...]}} 预构建的列值缓存

        self._load_headers_and_count(file_path)
        self._ensure_chunk(0)          # 初始加载第 0 块
        self._build_ui()

    # ================== 文件扫描（仅读表头+统计行数） ==================
    def _load_headers_and_count(self, path):
        """读取表头和总行数，不加载数据行"""
        if not path or not os.path.exists(path):
            self.headers = ["未加载"]
            self._row_count = 1
            self._put_dummy_data()
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.csv':
                self._scan_csv(path)
            elif ext in ('.xlsx', '.xls'):
                self._scan_xlsx(path)
            else:
                raise ValueError(f"不支持的文件格式: {ext}")
        except Exception as e:
            self.headers = ["错误"]
            self._row_count = 1
            self._put_dummy_data()

    def _put_dummy_data(self):
        self._chunks[0] = [{h: "" for h in self.headers}]
        self._rebuild_chunk_value_cache(0)
        self._chunk_load_order = [0]

    def _scan_csv(self, path):
        with open(path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            self.headers = reader.fieldnames or []
            count = 0
            for _ in reader:
                count += 1
            self._row_count = count
        if not self.headers:
            self.headers = ["无数据"]
            self._row_count = 1

    def _scan_xlsx(self, path):
        if not HAS_OPENPYXL:
            raise ImportError("缺少 openpyxl 库，请执行: pip install openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            raise ValueError("工作簿中没有工作表")
        rows_iter = ws.iter_rows(values_only=True)
        headers_row = next(rows_iter, None)
        if not headers_row:
            self.headers = ["空文件"]
            self._row_count = 1
            wb.close()
            return
        self.headers = [str(h) if h is not None else f"列{i}" for i, h in enumerate(headers_row)]
        count = 0
        for _ in rows_iter:
            count += 1
        self._row_count = count
        wb.close()

    # ================== 分块加载 ==================
    def _chunk_index(self, row_idx):
        return row_idx // self.CHUNK_SIZE

    def _chunk_start(self, chunk_idx):
        return chunk_idx * self.CHUNK_SIZE

    def _chunk_end(self, chunk_idx):
        return min((chunk_idx + 1) * self.CHUNK_SIZE, self._row_count)

    def _ensure_chunk(self, chunk_idx):
        """确保指定块已加载，必要时加载并淘汰旧块"""
        if chunk_idx in self._chunks:
            return
        max_chunk = (self._row_count - 1) // self.CHUNK_SIZE if self._row_count > 0 else 0
        if chunk_idx < 0 or chunk_idx > max_chunk:
            return
        # 淘汰：超过 MAX_CHUNKS 时删除最旧块
        while len(self._chunks) >= self.MAX_CHUNKS:
            oldest = self._chunk_load_order.pop(0)
            if oldest in self._chunks:
                del self._chunks[oldest]
                self._cached_values.pop(oldest, None)
        # 加载新块
        ext = os.path.splitext(self.file_path)[1].lower() if self.file_path else ''
        try:
            if ext == '.csv':
                rows = self._load_chunk_csv(chunk_idx)
            elif ext in ('.xlsx', '.xls'):
                rows = self._load_chunk_xlsx(chunk_idx)
            else:
                rows = []
        except Exception:
            rows = []
        if rows:
            self._chunks[chunk_idx] = rows
            self._rebuild_chunk_value_cache(chunk_idx)
            self._chunk_load_order.append(chunk_idx)

    def _load_chunk_csv(self, chunk_idx):
        """从 CSV 读取第 chunk_idx 块数据"""
        start = self._chunk_start(chunk_idx)
        end = self._chunk_end(chunk_idx)
        target_count = end - start
        result = []
        with open(self.file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i < start:
                    continue
                if i >= end:
                    break
                result.append(dict(row))
        return result

    def _load_chunk_xlsx(self, chunk_idx):
        """从 XLSX 读取第 chunk_idx 块数据"""
        start = self._chunk_start(chunk_idx)
        end = self._chunk_end(chunk_idx)
        wb = openpyxl.load_workbook(self.file_path, read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            wb.close()
            return []
        rows_iter = ws.iter_rows(values_only=True)
        next(rows_iter, None)  # 跳过表头
        result = []
        for i, row in enumerate(rows_iter):
            if i < start:
                continue
            if i >= end:
                break
            row_dict = {}
            for ci, h in enumerate(self.headers):
                val = row[ci] if ci < len(row) else ""
                row_dict[h] = str(val) if val is not None else ""
            result.append(row_dict)
        wb.close()
        return result

    def _rebuild_chunk_value_cache(self, chunk_idx):
        """为指定块构建列值缓存 {header: [val,...]}"""
        rows = self._chunks.get(chunk_idx, [])
        cache = {}
        for h in self.headers:
            cache[h] = [row.get(h, "") for row in rows]
        self._cached_values[chunk_idx] = cache

    # ================== 值访问接口 ==================
    def get_current_value(self, header):
        """获取当前行指定表头的值"""
        idx = self.current_row
        ck = self._chunk_index(idx)
        self._ensure_chunk(ck)
        chunk_cache = self._cached_values.get(ck, {})
        vals = chunk_cache.get(header, [])
        offset = idx - self._chunk_start(ck)
        if 0 <= offset < len(vals):
            return vals[offset]
        return ""

    def get_all_values_for_header(self, header):
        """获取指定表头在所有已加载块中的值列表（用于行选择对话框）"""
        result = []
        # 先收集所有已加载块的缓存
        for ck in sorted(self._chunks.keys()):
            cache = self._cached_values.get(ck, {})
            result.extend(cache.get(header, []))
        # 还要看总行数是否超出已加载范围，补占位标记
        loaded_count = sum(len(self._chunks.get(ck, [])) for ck in self._chunks)
        if loaded_count < self._row_count:
            result.append(None)  # 占位符表示"还有更多"
        return result, loaded_count

    def set_start_row(self, row_index):
        max_row = max(0, self._row_count - 1) if self._row_count > 0 else 0
        self.start_row = max(0, min(row_index, max_row))
        self.current_row = self.start_row

    def advance_row(self):
        """轮询时推进到下一行，在整个文件行范围内循环"""
        if self._row_count > 0:
            self.current_row = (self.current_row + 1) % self._row_count
            self._sync_combos_to_row(self.current_row)

    def reset_row(self):
        self.current_row = self.start_row
        self._sync_combos_to_row(self.current_row)

    def _sync_combos_to_row(self, idx):
        self._set_row(idx)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    # ================== UI ==================
    def _build_ui(self):
        if self.proxy and self.scene():
            self.scene().removeItem(self.proxy)
            self.proxy = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 标题栏
        fname = os.path.basename(self.file_path) if self.file_path else "未选择文件"
        title = QLabel(f"[Bucket:  {fname}]")
        title.setStyleSheet("font-weight: bold; color: #fff; background: transparent;")
        self._title_label = title          # 输入端口的纵向对齐基准（同 ContainerNode）
        layout.addWidget(title)

        # 行信息 + 行导航
        nav_layout = QHBoxLayout()
        row_info = QLabel(f"共 {self._row_count} 行 · 当前第 {self.current_row + 1} 行")
        row_info.setStyleSheet("color: #aaf; font-size: 11px; background: transparent;")
        self._row_info_label = row_info
        nav_layout.addWidget(row_info)
        nav_layout.addStretch()

        # 行选择 spinbox（范围为总行数）
        self._row_spin = QSpinBox()
        spin_max = max(1, self._row_count)
        self._row_spin.setRange(1, spin_max)
        self._row_spin.setValue(self.current_row + 1)
        self._row_spin.setFixedWidth(70)
        self._row_spin.valueChanged.connect(self._on_row_spin_changed)
        nav_layout.addWidget(QLabel("行:"))
        nav_layout.addWidget(self._row_spin)
        layout.addLayout(nav_layout)

        # 起始行选择
        row_sel_layout = QHBoxLayout()
        row_sel_layout.addWidget(QLabel("起始行:"))
        self.start_row_spin = QSpinBox()
        self.start_row_spin.setRange(1, spin_max)
        self.start_row_spin.setValue(self.start_row + 1)
        self.start_row_spin.valueChanged.connect(self._on_start_row_changed)
        row_sel_layout.addWidget(self.start_row_spin)
        layout.addLayout(row_sel_layout)

        # ---------- 写入设置（上游数据流入本表时使用） ----------
        # 注意：QComboBox 在 QGraphicsProxyWidget 里弹窗会失效（同 ContainerNode 模式选择），
        #       这里一律用 QPushButton + QMenu 的既有写法。
        _INGEST_LABELS = [("追加·按主键更新", 'upsert'),
                          ("追加·不去重", 'append'),
                          ("覆盖·清空重写", 'replace'),
                          ("不写入·只重读文件", 'refresh')]
        ingest_layout = QHBoxLayout()
        ingest_layout.addWidget(QLabel("写入:"))
        self._ingest_mode_btn = QPushButton(self._ingest_mode_label())
        self._ingest_mode_btn.setStyleSheet(
            "QPushButton { color:#fff; background:#3a6a4a; border:1px solid #5a9a6a; "
            "border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#4a7a5a; }"
        )
        self._ingest_mode_btn.setToolTip(
            "上游元素框（接口 / 数据处理 / 站点解析）把数据流进本表时的写入方式：\n"
            "  • 追加·按主键更新：已存在的行按主键列更新，新行追加（不会重复）\n"
            "  • 追加·不去重：每次直接往末尾追加\n"
            "  • 覆盖·清空重写：清空数据行后重写（表头保留）\n"
            "  • 不写入·只重读文件：不改文件，只重新读取并刷新显示\n"
            "写入前会自动把原文件备份到 temp/csv_ingest_backup/\n"
            "注意：Excel 用 openpyxl 重写数据行，单元格格式/公式不会保留。"
        )
        self._ingest_mode_btn.clicked.connect(self._on_ingest_mode_clicked)
        ingest_layout.addWidget(self._ingest_mode_btn, 1)
        layout.addLayout(ingest_layout)

        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("主键列:"))
        self._key_column_btn = QPushButton(self._key_column_label())
        self._key_column_btn.setStyleSheet(
            "QPushButton { color:#fff; background:#3a6a4a; border:1px solid #5a9a6a; "
            "border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#4a7a5a; }"
        )
        self._key_column_btn.setToolTip(
            "「追加·按主键更新」模式下用于判断“同一行”的列。\n"
            "自动探测顺序：id → public_id → relation_id → name → key → hash → url → path → 第一列"
        )
        self._key_column_btn.clicked.connect(self._on_key_column_clicked)
        key_layout.addWidget(self._key_column_btn, 1)
        layout.addLayout(key_layout)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #555;")
        layout.addWidget(sep)

        # 列表式布局：左=表头，右=当前值（只读QLineEdit）+ ▼按钮
        self._header_labels = {}
        self._value_edits = {}
        self._row_btns = {}

        for h in self.headers:
            row_w = QWidget()
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(2, 1, 2, 1)
            rl.setSpacing(4)

            key_label = QLabel(h)
            key_label.setFixedWidth(80)
            key_label.setStyleSheet("color: #eee; font-weight: bold; background: transparent;")
            key_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rl.addWidget(key_label)

            cur_val = self.get_current_value(h)
            val_edit = QLineEdit(cur_val)
            val_edit.setReadOnly(True)
            val_edit.setStyleSheet(
                "QLineEdit { color: #fff; background: #2a5a8a; border: 1px solid #4a8aba; "
                "border-radius: 3px; padding: 2px 4px; }"
            )
            val_edit.setMinimumWidth(120)
            rl.addWidget(val_edit, 1)
            self._value_edits[h] = val_edit

            btn = QPushButton("▼")
            btn.setFixedSize(22, 22)
            btn.setStyleSheet(
                "QPushButton { color: #fff; background: #3a6a9a; border: 1px solid #5a8aba; "
                "border-radius: 3px; font-size: 10px; }"
                "QPushButton:hover { background: #4a7aaa; }"
            )
            btn.setToolTip(f"选择 {h} 的值所在行")
            btn.clicked.connect(lambda checked, hd=h: self._show_row_picker(hd))
            rl.addWidget(btn)
            self._row_btns[h] = btn

            layout.addWidget(row_w)

        # 最近一次写入结果（上游数据流入后显示在这里）
        self._ingest_status_label = QLabel(self._last_ingest_text or "等待上游数据…")
        self._ingest_status_label.setWordWrap(True)
        self._ingest_status_label.setStyleSheet(
            "color: #8fd; font-size: 11px; background: transparent;")
        layout.addWidget(self._ingest_status_label)

        container.setStyleSheet("background: rgba(40, 80, 120, 220); border-radius: 4px; color: white;")
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(container)
        self.proxy.setPos(2, 2)

        container.adjustSize()
        size = container.size()
        self.setRect(0, 0, size.width() + 4, size.height() + 4)

    def _show_row_picker(self, header):
        """点击 ▼ 按钮 → 弹出独立对话框，显示该列所有值供选择（支持分块懒加载）"""
        dialog = QDialog()
        dialog.setWindowTitle(f"选择行 - {header}")
        dialog.setMinimumSize(320, 250)
        dialog.resize(380, 350)
        dlg_layout = QVBoxLayout(dialog)

        list_widget = QListWidget()
        self._populate_row_list(list_widget, header)
        list_widget.setCurrentRow(self.current_row)
        list_widget.itemDoubleClicked.connect(lambda it: dialog.done(it.data(Qt.UserRole) if it.data(Qt.UserRole) is not None else -2))
        # 检测滚动到底部 → 加载更多
        list_widget.verticalScrollBar().valueChanged.connect(
            lambda val: self._on_row_list_scroll(list_widget, header, val)
        )
        dlg_layout.addWidget(list_widget)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        def on_accept():
            item = list_widget.currentItem()
            if item and item.data(Qt.UserRole) is not None:
                dialog.done(item.data(Qt.UserRole))
            else:
                dialog.done(-1)
        btn_box.accepted.connect(on_accept)
        btn_box.rejected.connect(dialog.reject)
        dlg_layout.addWidget(btn_box)

        # 居中
        screen = QApplication.primaryScreen()
        if screen:
            sc = screen.geometry().center()
            dialog.move(sc.x() - dialog.width() // 2, sc.y() - dialog.height() // 2)

        result = dialog.exec()
        if result >= 0:
            self._set_row(result)
            self._sync_start_row(result)

    def _populate_row_list(self, list_widget, header):
        """向列表控件填充已加载块的行数据（带占位符）"""
        list_widget.clear()
        # 收集已加载块
        for ck in sorted(self._chunks.keys()):
            cache = self._cached_values.get(ck, {})
            vals = cache.get(header, [])
            base_row = self._chunk_start(ck)
            for i, v in enumerate(vals):
                item = QListWidgetItem(f"行{base_row + i + 1}: {v}")
                item.setData(Qt.UserRole, base_row + i)
                list_widget.addItem(item)
        # 如果还有未加载的行，加"载入更多"占位
        loaded = sum(len(self._chunks.get(ck, [])) for ck in self._chunks)
        if loaded < self._row_count:
            more_item = QListWidgetItem("── 下拉载入更多 ──")
            more_item.setData(Qt.UserRole, None)  # None = 占位
            more_item.setFlags(Qt.NoItemFlags)
            list_widget.addItem(more_item)

    def _on_row_list_scroll(self, list_widget, header, scroll_val):
        """检测滚动条是否接近底部 → 自动加载下一块"""
        scrollbar = list_widget.verticalScrollBar()
        if scrollbar.maximum() <= 0:
            return
        # 当滚动到 85% 位置时触发加载
        if scroll_val < scrollbar.maximum() * 0.85:
            return
        # 找到已加载的总行数
        loaded = sum(len(self._chunks.get(ck, [])) for ck in self._chunks)
        if loaded >= self._row_count:
            return
        # 计算需要加载的块
        next_chunk = self._chunk_index(loaded)
        self._ensure_chunk(next_chunk)
        # 重新填充列表并保持滚动位置
        current_top_item = list_widget.itemAt(0, 0)
        current_top_row = list_widget.row(current_top_item) if current_top_item else 0
        self._populate_row_list(list_widget, header)
        # 恢复近似滚动位置
        if current_top_row < list_widget.count():
            list_widget.scrollToItem(list_widget.item(current_top_row), QAbstractItemView.PositionAtTop)

    def _set_row(self, idx):
        """设置当前行并同步更新所有 UI 及下游连线元素框"""
        if idx < 0 or idx >= self._row_count:
            return
        self.current_row = idx
        ck = self._chunk_index(idx)
        self._ensure_chunk(ck)
        # 更新所有值显示
        for h, edit in self._value_edits.items():
            val = self.get_current_value(h)
            edit.setText(val)
        # 同步下游连线元素框
        self._sync_downstream()
        # 更新行号
        self._row_spin.blockSignals(True)
        self._row_spin.setValue(idx + 1)
        self._row_spin.blockSignals(False)
        if hasattr(self, '_row_info_label') and self._row_info_label:
            self._row_info_label.setText(
                f"共 {self._row_count} 行 · 当前第 {idx + 1} 行"
            )

    def _sync_downstream(self):
        """将当前行数据推送到下游连线（API 参数 / 站点解析识别预览等）"""
        if not self.scene() or not hasattr(self.scene(), 'connections'):
            return
        for conn in list(self.scene().connections):
            if conn.start_obj != self:
                continue
            # 直接 CsvDataNode → APINode
            if isinstance(conn.end_obj, APINode) and conn.start_param:
                h = conn.start_param
                if h in self.headers:
                    val = self.get_current_value(h)
                    pname = conn.end_param
                    for p in conn.end_obj.params:
                        if p['name'] == pname:
                            p['value'] = val
                            if pname in conn.end_obj.param_widgets:
                                conn.end_obj.param_widgets[pname].edit.setText(val)
                            break
            # CsvDataNode → DataProcessNode（更新 DP 源数据 → 实时推送下游预览）
            elif isinstance(conn.end_obj, DataProcessNode):
                h = conn.start_param
                if h and h in self.headers:
                    val = self.get_current_value(h)
                    conn.end_obj.set_source_data(val)
                # 同时保留 CsvDataNode → DataProcessNode → APINode 的直接参数填充
                for c2 in list(self.scene().connections):
                    if c2.start_obj == conn.end_obj and isinstance(c2.end_obj, APINode):
                        pname = c2.end_param
                        for p in c2.end_obj.params:
                            if p['name'] == pname:
                                p['value'] = val
                                if pname in c2.end_obj.param_widgets:
                                    c2.end_obj.param_widgets[pname].edit.setText(val)
                                break
            # 直接 CsvDataNode → SiteParserNode（识别预览实时更新）
            elif isinstance(conn.end_obj, SiteParserNode):
                h = conn.start_param
                if h and h in self.headers:
                    conn.end_obj.set_input_text(self.get_current_value(h))

    def _sync_start_row(self, idx):
        """同步更新起始行和起始行 spinbox"""
        self.start_row = idx
        if hasattr(self, 'start_row_spin') and self.start_row_spin:
            self.start_row_spin.blockSignals(True)
            self.start_row_spin.setValue(idx + 1)
            self.start_row_spin.blockSignals(False)

    def _on_row_spin_changed(self, val):
        self._set_row(val - 1)

    def _on_start_row_changed(self, val):
        self.set_start_row(val - 1)

    def rebuild_after_load(self):
        self._build_ui()

    # ================== 上游数据流入本表（数据库框的输入端） ==================
    # 设计要点（与既有约定保持一致）：
    #  • 写入模式选择用 QPushButton + QMenu —— QComboBox 在 QGraphicsProxyWidget 里弹窗会失效
    #  • 写入不改变 existing 结构：表头 = 原表头 + 新出现的键（新键追加到右侧）
    #  • 单元格按"键名 → 值"落位，"起始行为键名，下面为内容"
    #  • 写入前把原文件备份到 temp/csv_ingest_backup/（每轮只备份一次）
    #  • 写完立即 reload_from_file()，这就是"数据流进 csv 表时触发刷新"
    _INGEST_MODES = (('upsert', '追加·按主键更新'),
                     ('append', '追加·不去重'),
                     ('replace', '覆盖·清空重写'),
                     ('refresh', '不写入·只重读文件'))
    _KEY_COLUMN_CANDIDATES = ('id', 'public_id', 'relation_id', 'name',
                              'key', 'hash', 'url', 'path')

    def _ingest_mode_label(self):
        for val, label in self._INGEST_MODES:
            if val == self.ingest_mode:
                return label
        return self._INGEST_MODES[0][1]

    def _key_column_label(self):
        return self.key_column or "自动探测"

    def _on_ingest_mode_clicked(self):
        """写入模式选择（QMenu，同 ContainerNode._on_mode_clicked 的写法）"""
        menu = QMenu()
        for val, label in self._INGEST_MODES:
            act = menu.addAction(label)
            act.setData(val)
            act.setCheckable(True)
            act.setChecked(val == self.ingest_mode)
        chosen = menu.exec(self._ingest_mode_btn.mapToGlobal(
            self._ingest_mode_btn.rect().bottomLeft()))
        if chosen is not None:
            val = chosen.data()
            if val and val != self.ingest_mode:
                self.ingest_mode = val
                self._ingest_mode_btn.setText(self._ingest_mode_label())
                self.update()

    def _on_key_column_clicked(self):
        """主键列选择（QMenu）"""
        menu = QMenu()
        act = menu.addAction("自动探测")
        act.setData('')
        act.setCheckable(True)
        act.setChecked(not self.key_column)
        for h in self.headers:
            if not h or h in ("未加载", "无数据", "错误"):
                continue
            a = menu.addAction(h)
            a.setData(h)
            a.setCheckable(True)
            a.setChecked(h == self.key_column)
        chosen = menu.exec(self._key_column_btn.mapToGlobal(
            self._key_column_btn.rect().bottomLeft()))
        if chosen is not None:
            val = chosen.data() or ''
            if val != self.key_column:
                self.key_column = val
                self._key_column_btn.setText(self._key_column_label())

    def _set_ingest_status(self, text):
        self._last_ingest_text = text or ''
        lbl = getattr(self, '_ingest_status_label', None)
        if lbl is not None:
            try:
                lbl.setText(self._last_ingest_text)
            except RuntimeError:
                pass
        self.update()

    @staticmethod
    def _cell_text(v):
        """把 JSON 值转成适合落进表格单元格的内容"""
        if v is None:
            return ''
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float, str)):
            return v
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)

    @staticmethod
    def _extract_rows(payload):
        """把一份响应 JSON 归一化成 [{键: 值}, ...]；拿不到就返回 []"""
        if payload is None:
            return []
        if isinstance(payload, dict):
            # 二进制/错误/纯文本包装体不是表格数据
            if '_binary' in payload or 'error' in payload or 'raw' in payload:
                return []
            best = None
            for v in payload.values():
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    if best is None or len(v) > len(best):
                        best = v
            if best is not None:
                return [dict(x) for x in best]
            return [dict(payload)]
        if isinstance(payload, list):
            if not payload:
                return []
            if all(isinstance(x, dict) for x in payload):
                return [dict(x) for x in payload]
            return [{'value': x} for x in payload]
        return []

    def _detect_key_column(self, columns):
        """自动探测主键列：id → public_id → … → 第一列"""
        lower = {}
        for c in columns:
            if isinstance(c, str) and c:
                lower.setdefault(c.lower(), c)
        for cand in self._KEY_COLUMN_CANDIDATES:
            if cand in lower:
                return lower[cand]
        return columns[0] if columns else ''

    def _read_file_raw(self):
        """读回原始 (表头列表, 数据行列表)；空文件返回 ([], [])"""
        path = self.file_path
        ext = os.path.splitext(path)[1].lower()
        if ext == '.csv':
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                all_rows = [r for r in csv.reader(f)]
            if not all_rows:
                return [], []
            return [str(x) for x in all_rows[0]], [list(r) for r in all_rows[1:]]
        if ext in ('.xlsx', '.xls'):
            if not HAS_OPENPYXL:
                raise RuntimeError("未安装 openpyxl，无法读写 Excel")
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                ws = wb.active
                all_rows = []
                for r in ws.iter_rows(values_only=True):
                    all_rows.append([('' if c is None else c) for c in r])
            finally:
                wb.close()
            if not all_rows:
                return [], []
            return [str(x) for x in all_rows[0]], [list(r) for r in all_rows[1:]]
        raise RuntimeError(f"不支持的文件格式: {ext}")

    def _backup_file_once(self):
        """本轮第一次写入前，把原文件备份到 temp/csv_ingest_backup/"""
        if self._ingest_backed_up:
            return ''
        self._ingest_backed_up = True
        if not self.file_path or not os.path.exists(self.file_path):
            return ''
        try:
            root = _project_root()
            bdir = os.path.join(root, 'temp', 'csv_ingest_backup')
            os.makedirs(bdir, exist_ok=True)
            dst = os.path.join(
                bdir, f"{os.path.basename(self.file_path)}."
                      f"{time.strftime('%Y%m%d_%H%M%S')}.bak")
            shutil.copy2(self.file_path, dst)
            return dst
        except Exception:
            return ''

    def _write_file_raw(self, headers, rows):
        """把 (表头, 数据行) 写回文件；CSV 走临时文件原子替换，xlsx 用 openpyxl 重写数据行"""
        path = self.file_path
        ext = os.path.splitext(path)[1].lower()
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)

        if ext == '.csv':
            tmp = path + '.tmp_ingest'
            with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(headers)
                for r in rows:
                    w.writerow(r)
            os.replace(tmp, path)
            return

        if ext == '.xlsx':
            if not HAS_OPENPYXL:
                raise RuntimeError("未安装 openpyxl，无法写入 xlsx")
            tmp = path + '.tmp_ingest.xlsx'
            try:
                wb = (openpyxl.load_workbook(path)
                      if os.path.exists(path) else openpyxl.Workbook())
                ws = wb.active
            except Exception:
                wb = openpyxl.Workbook()
                ws = wb.active
            # 清掉旧数据行，保留表头行（以及其它工作表）
            if ws.max_row and ws.max_row > 1:
                ws.delete_rows(2, ws.max_row - 1)
            for c, h in enumerate(headers, 1):
                ws.cell(row=1, column=c, value=h)
            for r, row in enumerate(rows, 2):
                for c, v in enumerate(row, 1):
                    if v != '':
                        ws.cell(row=r, column=c, value=v)
            wb.save(tmp)
            wb.close()
            os.replace(tmp, path)
            return

        raise RuntimeError(f"不支持写入的文件格式: {ext}（Excel 请用 .xlsx）")

    def reload_from_file(self):
        """重新读取文件并刷新界面：表头 / 行数 / 分块缓存全部作废重建。
        这就是"数据流进 csv 表时也触发刷新"的落点。"""
        self._chunks = {}
        self._chunk_load_order = []
        self._cached_values = {}
        try:
            self._load_headers_and_count(self.file_path)
        except Exception:
            pass
        if self._row_count <= 0:
            self._row_count = 1
        if self.current_row >= self._row_count:
            self.current_row = 0
        if self.start_row >= self._row_count:
            self.start_row = 0
        try:
            self._ensure_chunk(0)
        except Exception:
            pass
        self._build_ui()
        # 内容变了 → 同步给下游（接口参数 / 数据处理 / 站点解析预览）
        try:
            sc = self.scene()
            if sc is not None and hasattr(sc, 'update_connections_for_node'):
                sc.update_connections_for_node(self)
        except Exception:
            pass
        try:
            self._sync_downstream()
        except Exception:
            pass
        self.update()

    def ingest_json(self, payload, source_label=''):
        """上游元素框把一份 JSON 结果送进本表：按写入模式落盘 + 刷新显示。
        返回 (ok, 摘要)。任何异常都不外抛，避免打断数据流。"""
        if self._ingest_depth > 0:
            return (False, '正在写入中，已忽略重复触发')
        if not self.file_path:
            self._set_ingest_status('⚠ 未选择数据文件，无法写入')
            return (False, '未选择数据文件')

        rows = self._extract_rows(payload)
        if not rows:
            self._set_ingest_status('⚠ 上游数据里没有可写入的键值行')
            return (False, '无可写入的行')

        self._ingest_depth += 1
        try:
            mode = self.ingest_mode

            # 只重读，不写文件
            if mode == 'refresh':
                self.reload_from_file()
                txt = (f"🔄 已重新读取 {os.path.basename(self.file_path)}"
                       f"（共 {self._row_count} 行）"
                       + (f" · 源:{source_label}" if source_label else ''))
                self._set_ingest_status(txt)
                return (True, txt)

            # 1) 列集合 = 原表头 + 新出现的键（新键追加到右侧）
            try:
                old_headers, old_rows = self._read_file_raw()
            except Exception as e:
                old_headers, old_rows = [], []
                if os.path.exists(self.file_path):
                    self._set_ingest_status(f"⚠ 原文件读取失败，按新表重建：{e}")

            incoming_cols = []
            for r in rows:
                for k in r.keys():
                    if k not in incoming_cols:
                        incoming_cols.append(k)

            headers = [h for h in old_headers if h != '']
            for c in incoming_cols:
                if c not in headers:
                    headers.append(c)
            if not headers:
                self._set_ingest_status('⚠ 没有可用列名，已放弃写入')
                return (False, '没有可用列名')

            def _to_cells(d):
                return [self._cell_text(d.get(h, '')) for h in headers]

            key_col = self.key_column or self._detect_key_column(headers)
            key_idx = headers.index(key_col) if key_col in headers else None

            def _pad(r):
                r = list(r)
                if len(r) < len(headers):
                    r += [''] * (len(headers) - len(r))
                elif len(r) > len(headers):
                    r = r[:len(headers)]
                return r

            # 2) 合并
            written = []      # 本次真正写入了内容的行号 → 供"新鲜行"判定使用
            if mode == 'replace':
                new_rows = [_to_cells(d) for d in rows]
                added, updated = len(new_rows), 0
                written = list(range(len(new_rows)))
            elif mode == 'append':
                new_rows = [_pad(r) for r in old_rows]
                written = list(range(len(new_rows), len(new_rows) + len(rows)))
                new_rows += [_to_cells(d) for d in rows]
                added, updated = len(rows), 0
            else:  # upsert
                new_rows = [_pad(r) for r in old_rows]
                index = {}
                if key_idx is not None:
                    for i, r in enumerate(new_rows):
                        k = str(r[key_idx]) if key_idx < len(r) else ''
                        if k != '' and k not in index:
                            index[k] = i
                added = updated = 0
                for d in rows:
                    cells = _to_cells(d)
                    k = str(cells[key_idx]) if key_idx is not None else ''
                    if k and k in index:
                        new_rows[index[k]] = cells
                        written.append(index[k])
                        updated += 1
                    else:
                        if k:
                            index[k] = len(new_rows)
                        written.append(len(new_rows))
                        new_rows.append(cells)
                        added += 1

            # 3) 落盘 + 刷新显示
            backup = self._backup_file_once()
            self._write_file_raw(headers, new_rows)
            # ---- 新鲜行登记 + 游标吸附 ----
            # 下游元素框随后读到的必须是本轮的"新鲜行"；游标若停在图纸自带的老占位上，
            # 就把它挪到第一行新鲜数据，否则下游会拿着老数据去跑。
            if written:
                self._fresh_rows |= set(written)
                if self.current_row not in self._fresh_rows:
                    self.current_row = min(self._fresh_rows)
            self.reload_from_file()

            if mode == 'replace':
                head = f"✅ 覆盖写入 {len(rows)} 行"
            else:
                head = f"✅ 新增 {added} 行"
                if updated:
                    head += f" / 更新 {updated} 行"
            txt = (f"{head} · 主键[{key_col or '—'}] · 共 {self._row_count} 行"
                   + (f" · 源:{source_label}" if source_label else ''))
            if backup:
                txt += f" · 已备份 {os.path.basename(backup)}"
            self._set_ingest_status(txt)
            return (True, txt)
        except Exception as e:
            self._set_ingest_status(f"⚠ 写入失败：{e}")
            return (False, str(e))
        finally:
            self._ingest_depth -= 1

    # ---------- 输入端口（上游元素框把数据流进本表；对齐到标题行中心） ----------
    def inputPort(self) -> QPointF:
        """左侧输入端口：与标题行等高（同 ContainerNode），视觉上表示数据入口"""
        title = getattr(self, '_title_label', None)
        if title is not None and self.proxy is not None:
            try:
                container = self.proxy.widget()
                widget_pos = title.mapTo(container, QPointF(0, title.height() // 2))
                scene_pos = self.proxy.mapToScene(widget_pos)
                return QPointF(self.scenePos().x() + self.rect().left(), scene_pos.y())
            except Exception:
                pass
        return self.mapToScene(self.rect().left(), self.rect().center().y())

    # ---------- 输出端口（每列表头对应一个输出，对齐到该行中心） ----------
    def output_port_for_header(self, header) -> QPointF:
        if header not in self._value_edits:
            return QPointF()
        edit = self._value_edits[header]
        container = self.proxy.widget()
        edit_pos = edit.mapTo(container, QPointF(edit.width(), edit.height() // 2))
        scene_pos = self.proxy.mapToScene(edit_pos)
        return QPointF(self.scenePos().x() + self.rect().right(), scene_pos.y())

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(40, 80, 120, 200))
        else:
            painter.setPen(QPen(Qt.white, 2))
            painter.setBrush(QColor(40, 80, 120))
        painter.drawRoundedRect(self.rect(), 6, 6)

        # 输入端口（绿色，与 ContainerNode 的输入点约定一致）
        in_port = self.inputPort()
        if not in_port.isNull():
            painter.setBrush(QColor(100, 200, 100))
            painter.setPen(QPen(Qt.white, 1))
            painter.drawEllipse(self.mapFromScene(in_port), 7, 7)

        # 输出端口（每列表头一个，黄色）
        painter.setPen(Qt.NoPen)
        for h in self.headers:
            port = self.output_port_for_header(h)
            if port.isNull():
                continue
            local_port = self.mapFromScene(port)
            painter.setBrush(QColor(255, 200, 50))
            painter.drawEllipse(local_port, 6, 6)

# ================= 容器框（数据终端节点） =================
class ContainerNode(QGraphicsRectItem):
    """容器框：接收 GET 文件/数据并存储到本地，无输出端口"""
    def __init__(self, storage_path=""):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(2)

        self.storage_path = storage_path
        # ---- 根目录输入端（运行时状态，不写进 .wbt）----
        # 没接上游时：storage_path 本身就是根目录（老行为不变）。
        # 接了上游并拿到名字时：storage_path 退化为「上级目录」，
        # 实际根目录 = storage_path/<上游给的文件夹名>（不存在则自动创建）。
        self.dyn_root = ''
        self.stored_bytes = 0
        self.total_files = 0
        self._latest_filename = ""
        self.proxy = None
        # ---- 规则模式状态（本地存储模式下可用）----
        self._rule_enabled = False           # 规则模式开关（右 = true）
        self._rule_creation_path = ''        # 创建时机 JSON 路径（如 $.id / $.user）
        self._rule_time_enabled = False      # 时间规则开关
        self._rule_time_format = 'yyyy-mm-dd'  # 时间格式（固定，UTC）
        self._rule_parts = []                # 拼合文本段（有序）：[{'id','text','source_path'}]
        self._rule_part_counter = 0          # 拼合段自增 ID
        self._rule_widgets = {}              # {part_id: RulePartWidget}
        self._rule_creation_edit = None
        self._rule_time_toggle = None
        self._rule_time_label = None
        self._rule_toggle = None

        self._build_ui()

    # ---------- 工具 ----------
    def _truncate_path(self, p, max_len=32):
        if len(p) <= max_len:
            return p
        return "..." + p[-(max_len - 3):]

    def _update_node_size(self):
        if not self.proxy:
            return
        c = self.proxy.widget()
        c.adjustSize()
        self.setRect(0, 0, c.width() + 4, c.height() + 4)
        s = self.scene()
        if s and isinstance(s, NodeScene):
            s.update_connections_for_node(self)

    # ---------- 路径 ----------
    def set_storage_path(self, path):
        self.storage_path = path
        txt = self._truncate_path(path) if path else "选择存储路径"
        if hasattr(self, '_path_btn') and self._path_btn:
            self._path_btn.setText(txt)
            self._path_btn.setToolTip(path if path else "")
        self._refresh_display()

    # ---------- 根目录输入端 ----------
    def active_storage_path(self):
        """本次运行实际写入用的根目录。

        - 没接「根目录输入点」，或上游本轮没给值 → 直接返回 storage_path（原行为）。
        - 接了且拿到名字 → storage_path 当「上级目录」，实际根目录 = storage_path/<名字>；
          上级目录里已经有同名文件夹就不重复创建，直接往里写（makedirs exist_ok）。
        """
        base = (self.storage_path or '').strip()
        name = (self.dyn_root or '').strip()
        if not base or not name:
            return base
        safe = name.replace('\\', '/').strip('/').replace('..', '_')
        for ch in '<>:"|?*':
            safe = safe.replace(ch, '_')
        parts = [p for p in safe.split('/') if p not in ('', '.')]
        if not parts:
            return base
        return os.path.join(base, *parts)

    def ensure_active_storage_path(self):
        """取实际根目录并确保它存在：不存在则创建，已存在则直接用。"""
        p = self.active_storage_path()
        if p and not os.path.isdir(p):
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                pass
        return p

    # ---------- 磁盘 ----------
    def _get_disk_info(self):
        # 接了「根目录输入点」时按实际根目录算（同一分区，仅路径存在性不同）
        _p = self.active_storage_path() or self.storage_path
        if not _p or not os.path.isdir(_p):
            return None, None, None
        try:
            u = shutil.disk_usage(_p)
            return u.free, u.used, u.total
        except:
            return None, None, None

    def _refresh_display(self):
        if not hasattr(self, '_info_label') or not self._info_label:
            return
        lines = []
        free, used, total = self._get_disk_info()
        if free is not None:
            lines.append(f"磁盘: {used/1024**3:.1f}GB / {total/1024**3:.1f}GB")
            lines.append(f"  可用 {free/1024**3:.1f}GB ({free*100//total}%)")
        if self._latest_filename:
            lines.append(f"最新: {self._latest_filename}")
        if self.total_files > 0:
            lines.append(f"已存 {self.total_files} 文件 ({self.stored_bytes/1024:.0f}KB)")
        self._info_label.setText("\n".join(lines) if lines else "等待存储...")
        self._update_node_size()

    # ---------- 空间检查 ----------
    def check_space(self, needed=0):
        free, used, total = self._get_disk_info()
        if free is None:
            return True, ""
        if needed > 0:
            if needed > free:
                return False, f"文件 {needed/1024:.0f}KB 超过剩余 {free/1024:.0f}KB"
        elif used >= total * 0.99:
            return False, "磁盘已达 99%"
        return True, ""

    # ---------- 存储 ----------
    def store_data(self, resp_data, api_url, content_type=""):
        if not self.storage_path or not os.path.isdir(self.storage_path):
            return False, "路径未设置"
        ok, msg = self.check_space(len(resp_data))
        if not ok:
            return False, msg
        import urllib.parse
        base = os.path.basename(urllib.parse.urlparse(api_url).path) or f"dl_{int(time.time())}"
        is_json = content_type.startswith('application/json') or (not content_type and api_url.endswith('.json'))
        ext = ".json" if is_json else (os.path.splitext(base)[1] or ".bin")
        name = os.path.splitext(base)[0]
        fn = f"{name}{ext}"
        fp = os.path.join(self.storage_path, fn)
        c = 1
        while os.path.exists(fp):
            fn = f"{name}_{c}{ext}"; fp = os.path.join(self.storage_path, fn); c += 1
        try:
            if is_json:
                with open(fp, 'w', encoding='utf-8') as f:
                    json.dump(resp_data, f, indent=2, ensure_ascii=False)
            else:
                d = resp_data.encode('utf-8') if isinstance(resp_data, str) else resp_data
                with open(fp, 'wb') as f:
                    f.write(d)
            self.stored_bytes += os.path.getsize(fp)
            self.total_files += 1
            self._latest_filename = fn
            self._refresh_display()
            return True, f"已存: {fn}"
        except Exception as e:
            return False, f"存储失败: {e}"

    def store_data_from_file(self, tmp_path, api_url, content_type=""):
        """从 .dlpack 临时文件转储到存储目录（大文件，避免加载进内存）。

        返回 (ok, msg, final_path)。成功后自动删除临时文件。
        """
        if not self.storage_path or not os.path.isdir(self.storage_path):
            return False, "路径未设置", None
        if not tmp_path or not os.path.isfile(tmp_path):
            return False, "临时文件不存在", None
        try:
            size = os.path.getsize(tmp_path)
        except Exception as e:
            return False, f"读取临时文件失败: {e}", None
        ok, msg = self.check_space(size)
        if not ok:
            return False, msg, None

        import urllib.parse
        base = os.path.basename(urllib.parse.urlparse(api_url).path) or f"dl_{int(time.time())}"
        is_json = content_type.startswith('application/json') or (not content_type and api_url.endswith('.json'))
        ext = ".json" if is_json else (os.path.splitext(base)[1] or ".bin")
        name = os.path.splitext(base)[0]
        fn = f"{name}{ext}"
        fp = os.path.join(self.storage_path, fn)
        c = 1
        while os.path.exists(fp):
            fn = f"{name}_{c}{ext}"; fp = os.path.join(self.storage_path, fn); c += 1
        try:
            if is_json:
                # JSON 大文件：逐行读取并重新序列化（保持缩进）
                with open(tmp_path, 'rb') as src, open(fp, 'w', encoding='utf-8') as dst:
                    data = json.load(src)
                    json.dump(data, dst, indent=2, ensure_ascii=False)
            else:
                shutil.copyfile(tmp_path, fp)
            self.stored_bytes += os.path.getsize(fp)
            self.total_files += 1
            self._latest_filename = fn
            self._refresh_display()
            try:
                os.remove(tmp_path)  # 转储成功后清理临时文件
            except Exception:
                pass
            return True, f"已存: {fn}", fp
        except Exception as e:
            return False, f"存储失败: {e}", None

    # ---------- UI ----------
    def _build_ui(self):
        if self.proxy and self.scene():
            self.scene().removeItem(self.proxy)
            self.proxy = None

        cw = QWidget()
        lo = QVBoxLayout(cw)
        lo.setContentsMargins(4, 4, 4, 4)
        lo.setSpacing(2)

        # 标题
        t = QLabel("容器")
        t.setStyleSheet("font-weight: bold; color: #fff; background: transparent;")
        self._title_label = t
        lo.addWidget(t)

        # 模式选择（QMenu 避免 QGraphicsProxyWidget 中 QComboBox 弹窗失效）
        self._mode_index = 0
        self._mode_labels = ["本地存储", "FTP/SFTP（预留）", "OSS（预留）", "WebDAV（预留）"]
        mr = QHBoxLayout()
        mr.addWidget(QLabel("模式:"))
        self._mode_btn = QPushButton(self._mode_labels[0])
        self._mode_btn.setStyleSheet(
            "QPushButton { color:#fff; background:#3a6a4a; border:1px solid #5a9a6a; "
            "border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#4a7a5a; }"
        )
        self._mode_btn.clicked.connect(self._on_mode_clicked)
        mr.addWidget(self._mode_btn)
        lo.addLayout(mr)

        # ---- 规则模式（仅本地存储模式显示）----
        if self._mode_index == 0:
            self._build_rule_ui(lo)

        # 路径按钮
        bt = self._truncate_path(self.storage_path) if self.storage_path else "选择存储路径"
        self._path_btn = QPushButton(bt)
        self._path_btn.setToolTip(self.storage_path if self.storage_path else "")
        self._path_btn.setStyleSheet(
            "QPushButton { color:#fff; background:#2a6a4a; border:1px solid #4a9a6a; "
            "border-radius:3px; padding:4px 8px; font-weight:bold; }"
            "QPushButton:hover { background:#3a7a5a; }"
        )
        self._path_btn.clicked.connect(self._on_select_path)
        lo.addWidget(self._path_btn)

        # 信息
        self._info_label = QLabel("等待存储...")
        self._info_label.setStyleSheet("color:#add; font-size:11px; background:transparent;")
        self._info_label.setWordWrap(True)
        lo.addWidget(self._info_label)

        self._refresh_display()

        cw.setStyleSheet("background:rgba(30,100,60,220); border-radius:4px; color:white;")
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(cw)
        self.proxy.setPos(2, 2)
        cw.adjustSize()
        self.setRect(0, 0, cw.width() + 4, cw.height() + 4)

    # ---------- 规则模式 ----------
    def _build_rule_ui(self, lo):
        """构建规则模式 UI（本地存储模式下调用）"""
        # 一般 / 规则 左右拨钮开关
        rr = QHBoxLayout()
        rr.addWidget(QLabel("规则:"))
        self._rule_toggle = QPushButton()
        self._rule_toggle.setCheckable(True)
        self._rule_toggle.setChecked(self._rule_enabled)
        self._rule_toggle.setText("规则 ON" if self._rule_enabled else "一般")
        self._rule_toggle.setStyleSheet(
            "QPushButton { color:#fff; background:#3a5a7a; border:1px solid #5a8aaa; "
            "border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:checked { background:#4a8a4a; border:1px solid #6aaa6a; }"
        )
        self._rule_toggle.clicked.connect(lambda: self._on_rule_toggle())
        rr.addWidget(self._rule_toggle)
        lo.addLayout(rr)

        if not self._rule_enabled:
            # 规则区关闭：清理已随 proxy 销毁的 widget 引用，避免悬垂引用
            self._rule_creation_edit = None
            self._rule_time_toggle = None
            self._rule_time_label = None
            self._rule_add_btn = None
            self._rule_widgets = {}
            return

        # ---- 第一行：创建时机（固定信息栏）----
        cr = QHBoxLayout()
        cl = QLabel("创建时机")
        cl.setFixedWidth(50)
        cl.setStyleSheet("color:#e5a53b; font-size:11px; background:transparent;")
        cr.addWidget(cl)
        self._rule_creation_edit = QLineEdit()
        self._rule_creation_edit.setPlaceholderText("拖入 JSON 层级，如 $.id")
        self._rule_creation_edit.setAcceptDrops(True)
        self._rule_creation_edit.setText(self._rule_creation_path)
        self._rule_creation_edit.textChanged.connect(self._on_creation_text_changed)
        if not hasattr(self, '_rule_creation_filter') or not self._rule_creation_filter:
            self._rule_creation_filter = _RuleCreationFilter()
            self._rule_creation_filter.path_set.connect(self._on_creation_path_set)
        self._rule_creation_edit.installEventFilter(self._rule_creation_filter)
        cr.addWidget(self._rule_creation_edit)
        lo.addLayout(cr)

        # ---- 时间规则行 ----
        tr = QHBoxLayout()
        ttl = QLabel("时间规则")
        ttl.setFixedWidth(50)
        ttl.setStyleSheet("color:#e5a53b; font-size:11px; background:transparent;")
        tr.addWidget(ttl)
        self._rule_time_toggle = QPushButton()
        self._rule_time_toggle.setCheckable(True)
        self._rule_time_toggle.setChecked(self._rule_time_enabled)
        self._rule_time_toggle.setText("开" if self._rule_time_enabled else "关")
        self._rule_time_toggle.setStyleSheet(
            "QPushButton { color:#fff; background:#3a5a7a; border:1px solid #5a8aaa; "
            "border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:checked { background:#4a8a4a; border:1px solid #6aaa6a; }"
        )
        self._rule_time_toggle.clicked.connect(lambda: self._on_time_toggle())
        tr.addWidget(self._rule_time_toggle)
        self._rule_time_label = QLabel("UTC " + self._rule_time_format)
        self._rule_time_label.setStyleSheet("color:#aaa; font-size:10px; background:transparent;")
        tr.addWidget(self._rule_time_label)
        tr.addStretch(1)
        lo.addLayout(tr)

        # ---- 拼合文本区 ----
        self._rule_widgets = {}
        for pd in self._rule_parts:
            self._add_rule_part_widget(lo, pd)

        # 添加文本按钮
        self._rule_add_btn = QPushButton("+ 添加文本")
        self._rule_add_btn.setStyleSheet(
            "QPushButton { color:#fff; background:#3a5a7a; border:1px dashed #5a8aaa; "
            "border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#4a6a8a; }"
        )
        self._rule_add_btn.clicked.connect(self._on_add_rule_part)
        lo.addWidget(self._rule_add_btn)

    def _add_rule_part_widget(self, lo, pd):
        """添加单个拼合文本部件到规则区"""
        part_id = pd['id']
        cw = RulePartWidget(part_id)
        cw.edit.setText(pd.get('text', ''))
        cw.part_value = pd.get('text', '')
        cw.source_path = pd.get('source_path', '')
        if cw.source_path:
            cw.edit.setToolTip(f"路径: {cw.source_path}")
            cw.clear_btn.show()
        cw.value_changed.connect(self._on_rule_part_value_changed)
        cw.move_up_requested.connect(self._on_rule_part_move_up)
        lo.addWidget(cw)
        self._rule_widgets[part_id] = cw

    def _on_rule_toggle(self, checked=None):
        """一般 / 规则 拨钮切换（延迟重建，避免在按钮事件中销毁 widget 崩溃）"""
        if checked is None:
            checked = bool(self._rule_toggle.isChecked()) if self._rule_toggle else False
        self._rule_enabled = bool(checked)
        self._pending_rule_toggle = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_rule_toggle)

    def _do_rule_toggle(self):
        if not getattr(self, '_pending_rule_toggle', False):
            return
        self._pending_rule_toggle = False
        self._build_ui()
        self._update_node_size()

    def _on_time_toggle(self, checked=None):
        """时间规则拨钮切换"""
        if checked is None:
            checked = bool(self._rule_time_toggle.isChecked()) if self._rule_time_toggle else False
        self._rule_time_enabled = bool(checked)
        if self._rule_time_toggle:
            self._rule_time_toggle.setText("开" if checked else "关")
            self._rule_time_toggle.setChecked(checked)

    def _on_creation_text_changed(self, text):
        self._rule_creation_path = text

    def _on_creation_path_set(self, path):
        """拖入 JSON 路径设置创建时机"""
        self._rule_creation_path = path
        if self._rule_creation_edit:
            self._rule_creation_edit.setText(path)
            self._rule_creation_edit.setToolTip(f"路径: {path}")
        self._update_node_size()

    def _on_add_rule_part(self):
        """添加文本按钮（延迟重建，避免在按钮事件中销毁 widget 崩溃）"""
        self._rule_part_counter += 1
        pd = {'id': f'rule_{self._rule_part_counter}', 'text': '', 'source_path': ''}
        self._rule_parts.append(pd)
        self._pending_add_rule_part = True
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_add_rule_part)

    def _do_add_rule_part(self):
        if not getattr(self, '_pending_add_rule_part', False):
            return
        self._pending_add_rule_part = False
        self._build_ui()
        self._update_node_size()

    def _on_rule_part_value_changed(self, part_id, value):
        for pd in self._rule_parts:
            if pd['id'] == part_id:
                pd['text'] = value
                break

    def _on_rule_part_move_up(self, part_id):
        """拼合段上移（调整拼合顺序）"""
        self._pending_rule_move_up = part_id
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_rule_part_move_up)

    def _do_rule_part_move_up(self):
        part_id = getattr(self, '_pending_rule_move_up', '')
        if not part_id:
            return
        self._pending_rule_move_up = ''
        for i, pd in enumerate(self._rule_parts):
            if pd['id'] == part_id and i > 0:
                self._rule_parts[i], self._rule_parts[i - 1] = \
                    self._rule_parts[i - 1], self._rule_parts[i]
                self._build_ui()
                self._update_node_size()
                return

    def remove_rule_part(self, part_id):
        """移除指定拼合段（连线删除时调用）"""
        self._rule_parts = [pd for pd in self._rule_parts if pd['id'] != part_id]
        self._build_ui()
        self._update_node_size()

    def rule_part_input_port(self, part_id) -> QPointF:
        """拼合文本段的左侧输入端口位置（场景坐标）"""
        cw = self._rule_widgets.get(part_id)
        if cw and self.proxy:
            container = self.proxy.widget()
            widget_pos = cw.mapTo(container, QPointF(0, cw.height() // 2))
            scene_pos = self.proxy.mapToScene(widget_pos)
            return QPointF(self.scenePos().x() + self.rect().left(), scene_pos.y())
        return self.inputPort()

    def _on_mode_clicked(self):
        """模式选择弹出菜单"""
        menu = QMenu()
        for i, label in enumerate(self._mode_labels):
            action = menu.addAction(label)
            action.setData(i)
            action.setCheckable(True)
            if i == self._mode_index:
                action.setChecked(True)
        chosen = menu.exec(self._mode_btn.mapToGlobal(
            self._mode_btn.rect().bottomLeft()))
        if chosen:
            self._mode_index = chosen.data()
            self._mode_btn.setText(self._mode_labels[self._mode_index])
            # 延迟重建（仍在菜单事件栈内，直接销毁 proxy 会崩溃）
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._rebuild_after_mode_change)

    def _rebuild_after_mode_change(self):
        self._build_ui()  # 重建以显示 / 隐藏规则区
        self._update_node_size()

    def _on_select_path(self):
        p = QFileDialog.getExistingDirectory(None, "选择存储文件夹", self.storage_path or "")
        if p:
            self.set_storage_path(p)

    def root_path_input_port(self) -> QPointF:
        """「根目录输入点」位置：与「选择存储路径」按钮同一行、贴着框的左边缘。

        与主输入点（绿色，固定在标题行）分开：这里接收的是"根目录文件夹名"，
        改了它等于把配置的路径降级成上级目录（见 active_storage_path）。
        """
        if getattr(self, '_path_btn', None) and self.proxy:
            container = self.proxy.widget()
            widget_pos = self._path_btn.mapTo(
                container, QPointF(0, self._path_btn.height() // 2))
            scene_pos = self.proxy.mapToScene(widget_pos)
            return QPointF(self.scenePos().x() + self.rect().left(), scene_pos.y())
        return self.inputPort()

    def inputPort(self):
        """左侧输入端口（固定在标题"容器"旁，不随规则文本栏增减而错位）"""
        if hasattr(self, '_title_label') and self._title_label and self.proxy:
            container = self.proxy.widget()
            widget_pos = self._title_label.mapTo(
                container, QPointF(0, self._title_label.height() // 2))
            scene_pos = self.proxy.mapToScene(widget_pos)
            return QPointF(self.scenePos().x() + self.rect().left(), scene_pos.y())
        return self.mapToScene(self.rect().left(), self.rect().center().y())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(30, 100, 60, 200))
        else:
            painter.setPen(QPen(Qt.white, 2))
            painter.setBrush(QColor(30, 100, 60))
        painter.drawRoundedRect(self.rect(), 6, 6)
        # 左侧主输入端口（绿色）
        port = self.inputPort()
        local_port = self.mapFromScene(port)
        painter.setBrush(QColor(100, 200, 100))
        painter.drawEllipse(local_port, 7, 7)
        # 根目录输入端口（青色，与「选择存储路径」同一行；拿到值后更亮）
        rp = self.root_path_input_port()
        lrp = self.mapFromScene(rp)
        painter.setBrush(QColor(80, 230, 255) if self.dyn_root else QColor(70, 170, 200))
        painter.setPen(QPen(Qt.white, 1))
        painter.drawEllipse(lrp, 6, 6)
        painter.setPen(QPen(Qt.white, 2))
        # 规则文本段输入端口（橙色圆点，与 RulePartWidget 左侧输入点对齐）
        for pid in list(getattr(self, '_rule_widgets', {}).keys()):
            p = self.rule_part_input_port(pid)
            lp = self.mapFromScene(p)
            painter.setBrush(QColor(229, 165, 59))
            painter.drawEllipse(lp, 5, 5)

# ================= API 节点 (支持自适应大小) =================
class _APINodeSignals(QObject):
    data_cleared = Signal(str)


def _api_param_is_manual_input(api_node, param_name):
    """API 主输入点是否为「手动输入内容」（无 $. 路径绑定且文本非空）。

    手动输入 = 用户在编辑框直接敲入的内容（从未拖入 $. 路径绑定、非只读绑定态）。
    这类值应「常态保持」：连线 / 轮询都不覆盖它 —— 动态注入内容应进拼合栏承载。
    """
    try:
        pw = (getattr(api_node, 'param_widgets', None) or {}).get(param_name)
        if pw is not None and hasattr(pw, 'edit'):
            txt = str(pw.edit.text() or '').strip()
            if txt and not txt.startswith('$.') and not getattr(pw, 'source_path', ''):
                return True
    except Exception:
        pass
    try:
        for p in (getattr(api_node, 'params', None) or []):
            if p.get('name') == param_name:
                v = str(p.get('value', '') or '').strip()
                if v and not v.startswith('$.') and not str(p.get('source_path', '') or ''):
                    return True
    except Exception:
        pass
    return False


class APINodeHeadersDialog(QDialog):
    """API 节点自定义请求头编辑对话框。

    为什么用对话框而不是节点内联表格：APINode._build_ui 每次都会销毁并重建
    proxy 及其全部子控件，内联编辑控件得写一整套「保存-重建」样板，漏一处就
    「一改参数值请求头就丢」。对话框方案下 self.headers 是唯一真相源。
    """

    def __init__(self, headers=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义请求头")
        self.setMinimumSize(640, 420)
        self._build_ui()
        for k, v in (headers or {}).items():
            self._append_row(k, v)
        self._refresh_style()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        tip = QLabel(
            "这些请求头会随本次请求一起发出，并保存在流程文件里。\n"
            "• 值留空 = 不发送该头（可用来屏蔽内置默认头，例如把 User-Agent 留空）\n"
            "• 值支持 ${ENV:环境变量名} 占位符，运行时从系统环境变量取值，避免密钥明文落盘\n"
            "• 键名是 Date / Time / X-Timestamp 之类时，值栏会变成时区下拉框，每次请求前"
            "实时生成该时区的当前时间\n"
            "• 未填写的头会用内置默认值补齐：User-Agent / Accept / Accept-Language"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(tip)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["键", "值", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 40)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        cookie_btn = QPushButton("🍪 导入 Cookie")
        cookie_btn.setMaximumWidth(130)
        cookie_btn.setToolTip(
            "需要登录态的接口必须先带 Cookie，否则会返回 401。\n"
            "点这里把浏览器里复制到的 Cookie 粘进来 —— 纯值、整行、\n"
            "cURL 命令、Cookie-Editor 导出的 JSON 都能自动认出来，\n"
            "统一合成一条 cookie 请求头。\n"
            "注意：要复制的是 Request Headers 里的 cookie，不是响应头。")
        cookie_btn.clicked.connect(self._import_cookie)
        btn_row.addWidget(cookie_btn)
        paste_btn = QPushButton("📋 批量粘贴")
        paste_btn.setMaximumWidth(130)
        paste_btn.setToolTip("从任意文本批量导入请求头（JSON / curl 命令 / 每行 `键: 值` 等）")
        paste_btn.clicked.connect(self._bulk_paste)
        btn_row.addWidget(paste_btn)
        add_btn = QPushButton("+ 添加请求头")
        add_btn.setMaximumWidth(130)
        add_btn.clicked.connect(lambda: self._append_row('', ''))
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.sensitive_label = QLabel("")
        self.sensitive_label.setWordWrap(True)
        self.sensitive_label.setStyleSheet(
            "color: #b8860b; font-size: 11px; background: #fffbe6; "
            "border: 1px solid #ffe58f; padding: 4px;"
        )
        self.sensitive_label.setVisible(False)
        layout.addWidget(self.sensitive_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_row(self, key, value):
        row = self.table.rowCount()
        self.table.blockSignals(True)
        try:
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(key or '')))
            self._set_value_cell(row, key, value)
            del_btn = QPushButton("×")
            del_btn.setMaximumWidth(30)
            del_btn.setStyleSheet("color: #d9534f; font-weight: bold; border: none;")
            del_btn.setToolTip("移除此请求头")
            del_btn.clicked.connect(lambda: self._remove_row(del_btn))
            self.table.setCellWidget(row, 2, del_btn)
        finally:
            self.table.blockSignals(False)
        self._refresh_style()

    def _set_value_cell(self, row, key, value):
        """时间头的值栏换成时区下拉框，其余仍是普通文本格。"""
        text = str(value or '')
        if is_time_header(key):
            self.table.setItem(row, 1, QTableWidgetItem(text))
            combo = QComboBox()
            for label, tz_key in TIMEZONE_CHOICES:
                combo.addItem(label, tz_key)
            m = re.match(r'^\$\{(DATE|TIME)(?::([^}|]+))?(?:\|[^}]*)?\}$',
                         text.strip(), re.IGNORECASE)
            want = (m.group(2) or DEFAULT_TIMEZONE_KEY).strip() if m else DEFAULT_TIMEZONE_KEY
            idx = combo.findData(want)
            if idx < 0:
                combo.addItem(f'{want}（配置中的时区）', want)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
            combo.setMinimumWidth(150)
            combo.currentIndexChanged.connect(self._refresh_style)
            self.table.setCellWidget(row, 1, combo)
        else:
            if isinstance(self.table.cellWidget(row, 1), QComboBox):
                self.table.removeCellWidget(row, 1)
            self.table.setItem(row, 1, QTableWidgetItem(text))

    def _rebuild_value_cells(self):
        """键名改了 → 在文本格与时区下拉框之间切换值栏。"""
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                key_item = self.table.item(row, 0)
                key = key_item.text().strip() if key_item else ''
                combo = self.table.cellWidget(row, 1)
                val_item = self.table.item(row, 1)
                if is_time_header(key):
                    if not isinstance(combo, QComboBox):
                        self._set_value_cell(row, key, val_item.text() if val_item else '')
                elif isinstance(combo, QComboBox):
                    tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                    self._set_value_cell(row, key, f'${{DATE:{tz}}}')
        finally:
            self.table.blockSignals(False)

    def _remove_row(self, btn):
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 2) == btn:
                self.table.removeRow(row)
                break
        self._refresh_style()

    def _on_item_changed(self, item):
        if item.column() == 0:
            self._rebuild_value_cells()
        self._refresh_style()

    def _merge_headers(self, new_headers):
        """按不区分大小写的键名合并：同名的覆盖原值，新的追加。"""
        existing = {k.lower(): k for k in self.get_headers()}
        for key, value in (new_headers or {}).items():
            old = existing.get(key.lower())
            if old is None:
                self._append_row(key, value)
                existing[key.lower()] = key
                continue
            for row in range(self.table.rowCount()):
                key_item = self.table.item(row, 0)
                if key_item and key_item.text().strip() == old:
                    self.table.blockSignals(True)
                    try:
                        self._set_value_cell(row, key, value)
                    finally:
                        self.table.blockSignals(False)
                    break
        self._refresh_style()

    def _bulk_paste(self):
        """批量粘贴导入（复用图源配置那边的解析器与对话框）。"""
        dlg = BulkHeaderPasteDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_headers = dlg.get_headers()
        if not new_headers:
            return
        self._merge_headers(new_headers)

    def _import_cookie(self):
        """🍪 导入 Cookie（与图源配置里的同名按钮同一套解析逻辑）。"""
        current = ''
        for k, v in self.get_headers().items():
            if str(k).strip().lower() == 'cookie':
                current = v
                break
        dlg = CookieImportDialog(self, base_url='', current=current)
        if dlg.exec() != QDialog.Accepted:
            return
        value = dlg.get_cookie_value()
        if not value:
            return
        self._merge_headers({'cookie': value})
        note = ('（存的是 ${ENV:%s} 占位符，运行时才取真值）' % dlg.get_env_name()) \
            if dlg.get_env_name() else '（明文保存 —— 保存流程后 cookie 会写进 .wbt，注意别外发）'
        QMessageBox.information(
            self, 'Cookie 已导入',
            f'已写入 1 条 cookie 请求头：\n{describe_cookie(value)}\n{note}')

    def _refresh_style(self):
        """值留空的用暗灰字提示「这一行不会发送」。

        颜色必须从当前调色板取：主程序全局是暗色主题（Text 为白色），
        早期版本写死 #000，值栏就成了黑字，在深灰底上几乎看不见。
        """
        base_text = QColor(self.table.palette().color(QPalette.Text))
        dim = QColor(base_text)
        dim.setAlpha(110)
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                combo = self.table.cellWidget(row, 1)
                if isinstance(combo, QComboBox):
                    tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                    combo.setToolTip('每次请求前实时生成：\n' + make_date_value(tz)
                                     + '\n时区: ' + timezone_label(tz))
                    continue
                val_item = self.table.item(row, 1)
                if not val_item:
                    continue
                if val_item.text().strip() == '':
                    val_item.setForeground(QBrush(dim))
                    val_item.setToolTip('值留空 = 不发送该头')
                else:
                    # 空 QBrush = 取消显式前景色，跟随调色板默认色
                    val_item.setForeground(QBrush())
                    val_item.setToolTip('')
        finally:
            self.table.blockSignals(False)

        headers = self.get_headers()
        sensitive = [k for k in headers if is_sensitive_header(k)]
        if sensitive:
            names = '、'.join(sensitive[:6]) + (' 等' if len(sensitive) > 6 else '')
            self.sensitive_label.setText(
                f"⚠️ 检测到敏感请求头：{names}。这些值会随流程文件（data/webtree/*.wbt）"
                f"一起明文保存，分享或拷贝该文件即泄露。强烈建议改用 "
                f"${{ENV:环境变量名}} 占位符。"
            )
            self.sensitive_label.setVisible(True)
        else:
            self.sensitive_label.setVisible(False)

    def get_headers(self):
        headers = {}
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            if not key_item or not key_item.text().strip():
                continue
            key = key_item.text().strip()
            combo = self.table.cellWidget(row, 1)
            if isinstance(combo, QComboBox):
                # 时间头：把时区下拉框的选择还原成占位符，实际时间在发送前才生成
                tz = combo.currentData() or DEFAULT_TIMEZONE_KEY
                headers[key] = f'${{DATE:{tz}}}'
            else:
                val_item = self.table.item(row, 1)
                headers[key] = (val_item.text() if val_item else '')
        return headers


class APINode(QGraphicsRectItem):
    def __init__(self, api_data=None):
        super().__init__()
        self.setRect(0, 0, 220, 120)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(1)

        self.api_ref = api_data or {}
        self.method = "GET"
        self.url_path = "/"
        self.params = []
        self.headers = {}
        self.result_json = None
        self.next_nodes = []
        self.param_widgets = {}
        self.proxy = None
        # 拼合信息栏
        self._concat_widgets = []     # [ConcatEditWidget, ...]
        self._concat_counter = 0      # 自增 ID
        self._concat_spacer = None    # 分隔线 widget

        self._signals = _APINodeSignals()
        self.data_cleared = self._signals.data_cleared
        # 数组迭代状态
        self._array_values = {}       # {param_name: [value1, value2, ...]}
        self._array_index = {}        # {param_name: current_index}
        self._array_rounds = {}       # {param_name: total_rounds_needed}
        # 拼合栏数组迭代状态（拖到拼合栏的数组 source_path 绑定也驱动内轮询）
        self._concat_array_values = {}   # {concat_id: [value1, value2, ...]}
        self._concat_array_index = {}    # {concat_id: current_index}
        self._concat_array_rounds = {}   # {concat_id: total_rounds_needed}
        # 二进制内容标记
        self._binary_content = False
        self._binary_size = 0
        self._binary_type = ""
        self._binary_filename = ""

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene and isinstance(scene, NodeScene):
                scene.update_connections_for_node(self)
        return super().itemChange(change, value)

    def _build_ui(self):
        # 保存现有拼合栏数据（_build_ui 销毁 proxy 时会连带删除其内子 widget）
        saved_concat = []
        for cw in self._concat_widgets:
            saved_concat.append({
                'id': cw.concat_id,
                'value': cw.get_value(),
                'source_path': cw.source_path,
                'anchor_param': getattr(cw, 'anchor_param', ''),
            })
        # 合并尚未重建的待添加拼合栏
        if hasattr(self, '_pending_concat'):
            saved_concat.extend(self._pending_concat)
            self._pending_concat = []
        self._concat_widgets.clear()
        self._concat_counter = max(self._concat_counter, len(saved_concat))

        # 保存现有参数栏的 source_path（避免重建后丢失路径绑定）
        saved_param_paths = {}
        for pname, pw in self.param_widgets.items():
            saved_param_paths[pname] = getattr(pw, 'source_path', '')

        if self.proxy and self.scene():
            self.scene().removeItem(self.proxy)
            self.proxy = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        header = QLabel(f"{self.method} {self.url_path}")
        header.setStyleSheet("font-weight: bold; color: white; background: transparent;")
        header_row.addWidget(header, 1)

        # 请求头入口。做成小按钮而不是内联表格：_build_ui 每次都会销毁并重建 proxy
        # 及其所有子控件，内联编辑控件必须写「保存-重建」样板且极易丢状态；
        # 这里把 self.headers 当唯一真相源，编辑走对话框，重建后按数据重画即可。
        self._headers_btn = QPushButton()
        self._headers_btn.setCursor(Qt.PointingHandCursor)
        self._headers_btn.setMaximumHeight(20)
        self._headers_btn.clicked.connect(self._edit_headers)
        header_row.addWidget(self._headers_btn, 0)
        layout.addLayout(header_row)
        self._refresh_headers_btn()

        param_names = parse_curly_params(self.url_path)
        self.param_widgets.clear()
        for name in param_names:
            initial_val = next((p['value'] for p in self.params if p['name'] == name), '')
            pw = ParamEditWidget(name, initial_val)
            pw.value_changed.connect(self._on_param_changed)
            pw.data_cleared.connect(self._signals.data_cleared)
            pw.value_changed.connect(lambda v, n=name: self.update_node_size())
            pw.data_cleared.connect(lambda n=name: self.update_node_size())
            # 恢复 source_path 绑定（优先从 params 持久化数据，其次从内存 saved_param_paths）
            sp = ''
            for p in self.params:
                if p.get('name') == name and p.get('source_path'):
                    sp = p['source_path']
                    break
            if not sp:
                sp = saved_param_paths.get(name, '')
            if sp:
                pw.source_path = sp
                if sp.startswith('$.'):
                    pw.clear_btn.show()
                    pw._update_readonly()
            layout.addWidget(pw)
            self.param_widgets[name] = pw

        # ---- 拼合信息栏区域 ----
        if saved_concat:
            for cd in saved_concat:
                cw = ConcatEditWidget(concat_id=cd['id'], anchor_param=cd.get('anchor_param', ''))
                cw.value_changed.connect(lambda v, c=cd['id']: self.update_node_size())
                cw.move_up_requested.connect(self._on_concat_move_up)
                if cd['value']:
                    cw.edit.setText(cd['value'])
                    cw.concat_value = cd['value']
                if cd['source_path']:
                    cw.source_path = cd['source_path']
                    cw.clear_btn.show()
                cw.up_btn.setVisible(True)
                cw.up_btn.setEnabled(True)
                # 插入到关联参数栏之后（方便一一对应），如果找不到则追加到末尾
                inserted = False
                anchor = cd.get('anchor_param', '')
                if anchor and anchor in self.param_widgets:
                    for ci in range(layout.count()):
                        w = layout.itemAt(ci).widget()
                        if w is self.param_widgets.get(anchor):
                            layout.insertWidget(ci + 1, cw)
                            inserted = True
                            break
                if not inserted:
                    layout.addWidget(cw)
                self._concat_widgets.append(cw)

        container.setStyleSheet("background: rgba(60,60,60,200); border-radius:4px; color:white;")
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(container)
        self.proxy.setPos(2, 2)

        self.update_node_size()

    def _refresh_headers_btn(self):
        """刷新请求头按钮的文字/配色/悬停提示（不重建 UI）。"""
        btn = getattr(self, '_headers_btn', None)
        if btn is None:
            return
        n = len(self.headers or {})
        btn.setText(f"🔧 {n}" if n else "🔧 请求头")
        if n:
            btn.setStyleSheet(
                "QPushButton { color: #ffd54f; background: rgba(255,213,79,40); "
                "border: 1px solid #ffd54f; border-radius: 3px; padding: 0 4px; "
                "font-size: 10px; }"
                "QPushButton:hover { background: rgba(255,213,79,80); }"
            )
            btn.setToolTip("自定义请求头（点击编辑，敏感值已打码）:\n"
                           + describe_headers(self.headers))
        else:
            btn.setStyleSheet(
                "QPushButton { color: #bbb; background: rgba(255,255,255,20); "
                "border: 1px solid #777; border-radius: 3px; padding: 0 4px; "
                "font-size: 10px; }"
                "QPushButton:hover { background: rgba(255,255,255,50); }"
            )
            btn.setToolTip("点击添加自定义请求头（如 Cookie / Authorization）。\n"
                           "留空值 = 不发送该头；值支持 ${ENV:变量名} 占位符。")

    def _edit_headers(self):
        """打开请求头编辑对话框。只更新按钮，不重建 UI。"""
        view = None
        scene = self.scene()
        if scene is not None and getattr(scene, 'views', None):
            views = scene.views()
            if views:
                view = views[0]
        dlg = APINodeHeadersDialog(self.headers, parent=view)
        if dlg.exec() != QDialog.Accepted:
            return
        self.headers = dlg.get_headers()
        self._refresh_headers_btn()
        scene = self.scene()
        if scene is not None and isinstance(scene, NodeScene):
            scene._save_undo()

    def update_node_size(self):
        if not self.proxy:
            return
        container = self.proxy.widget()
        container.adjustSize()
        size = container.size()
        self.setRect(0, 0, size.width() + 4, size.height() + 4)
        scene = self.scene()
        if scene and isinstance(scene, NodeScene):
            scene.update_connections_for_node(self)

    def set_api(self, method, url_path, params=None, headers=None):
        self.method = method
        self.url_path = url_path
        self.params = params or []
        if headers is not None:
            self.headers = dict(headers)
        self._build_ui()

    def _on_param_changed(self, name, value):
        for p in self.params:
            if p['name'] == name:
                p['value'] = value
                # 同步路径绑定状态：绑定时持久化 source_path，手动输入/清空绑定则清除，
                # 避免运行时数组解析从 params 回退读到残留绑定，把手动输入内容覆盖/回绑。
                pw = (self.param_widgets or {}).get(name)
                if pw is not None:
                    p['source_path'] = getattr(pw, 'source_path', '')
                return

    # ---- 拼合信息栏方法 ----
    def add_concat_field(self, anchor_param=""):
        """添加一个拼合信息栏（收到连线时调用），anchor_param 指定关联的参数名"""
        cid = f"concat_{self._concat_counter}"
        self._concat_counter += 1
        if not hasattr(self, '_pending_concat'):
            self._pending_concat = []
        self._pending_concat.append({
            'id': cid,
            'value': '',
            'source_path': '',
            'anchor_param': anchor_param,
        })
        self._build_ui()
        self.update_node_size()
        return cid

    def remove_concat_field(self, concat_id):
        """移除指定拼合栏"""
        self._concat_widgets = [cw for cw in self._concat_widgets if cw.concat_id != concat_id]
        self._build_ui()
        self.update_node_size()

    def _on_concat_move_up(self, concat_id):
        """拼合栏上移：延迟处理避免在按钮事件中销毁 widget"""
        self._pending_move_up = concat_id
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._do_concat_move_up)

    def _do_concat_move_up(self):
        """延迟执行的拼合上移操作"""
        concat_id = getattr(self, '_pending_move_up', '')
        if not concat_id:
            return
        self._pending_move_up = ''

        for i, cw in enumerate(self._concat_widgets):
            if cw.concat_id == concat_id:
                if i == 0 and self.param_widgets:
                    # ── 与最后一个原信息栏交换数据 ──
                    last_key = list(self.param_widgets.keys())[-1]
                    last_pw = self.param_widgets[last_key]
                    # 交换值
                    cw_val = cw.get_value()
                    pw_val = last_pw.edit.text()
                    cw.edit.setText(pw_val)
                    cw.concat_value = pw_val
                    last_pw.edit.setText(cw_val)
                    # 交换 source_path 和 anchor_param
                    cw.source_path, last_pw.source_path = last_pw.source_path, cw.source_path
                    cw.anchor_param = last_key
                    # 更新连接
                    scene = self.scene()
                    if scene and isinstance(scene, NodeScene):
                        for conn in list(scene.connections):
                            if conn.end_obj == self:
                                if conn.end_param == last_key:
                                    conn.end_param = concat_id
                                    conn._end_fn = lambda n=self, c=concat_id: n.concat_input_port(c)
                                elif conn.end_param == concat_id:
                                    conn.end_param = last_key
                                    conn._end_fn = lambda n=self, p=last_key: n.param_input_port(p)
                        scene.update_connections_for_node(self)
                    self._build_ui()
                    self.update_node_size()
                    return
                if i > 0:
                    # ── 与上面的拼合栏交换 ──
                    self._concat_widgets[i], self._concat_widgets[i - 1] = \
                        self._concat_widgets[i - 1], self._concat_widgets[i]
                    self._build_ui()
                    self.update_node_size()
                    return

    def concat_input_port(self, concat_id) -> QPointF:
        """拼合栏的左侧输入端口位置"""
        for cw in self._concat_widgets:
            if cw.concat_id == concat_id:
                container = self.proxy.widget() if self.proxy else None
                if not container:
                    return QPointF()
                widget_pos = cw.mapTo(container, QPointF(0, cw.height() // 2))
                scene_pos = self.proxy.mapToScene(widget_pos)
                return QPointF(self.scenePos().x() + self.rect().left(), scene_pos.y())
        return QPointF()

    def get_all_concat_values(self) -> list:
        """获取所有拼合栏的值列表"""
        return [cw.get_value() for cw in self._concat_widgets if cw.get_value()]

    def param_input_port(self, param_name) -> QPointF:
        if param_name not in self.param_widgets:
            return QPointF()
        widget = self.param_widgets[param_name]
        container = self.proxy.widget()
        widget_pos = widget.mapTo(container, QPointF(0, widget.height() // 2))
        scene_pos = self.proxy.mapToScene(widget_pos)
        return QPointF(self.scenePos().x() + self.rect().left(), scene_pos.y())

    def outputPort(self):
        return self.mapToScene(self.rect().right(), self.rect().center().y())

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 200, 255), 3, Qt.SolidLine))
            painter.setBrush(QColor(70, 130, 180, 200))
        else:
            painter.setPen(QPen(Qt.black, 2))
            painter.setBrush(QColor(70, 130, 180))
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.setBrush(QColor(200,100,100))
        painter.drawEllipse(QPointF(self.rect().right(), self.rect().center().y()), 7, 7)
        for name in self.param_widgets.keys():
            port = self.param_input_port(name)
            local_port = self.mapFromScene(port)
            painter.setBrush(QColor(100,200,100))
            painter.drawEllipse(local_port, 6, 6)
        # 拼合栏端口（橙色）
        for cw in self._concat_widgets:
            port = self.concat_input_port(cw.concat_id)
            local_port = self.mapFromScene(port)
            painter.setBrush(QColor(229, 165, 59))  # 橙色
            painter.drawEllipse(local_port, 5, 5)
        # 拼合区域端口提示（底部中心偏左）
        if self._concat_widgets:
            painter.setBrush(QColor(229, 165, 59, 80))
            painter.setPen(QPen(QColor(229, 165, 59), 1, Qt.DashLine))
            painter.drawRoundedRect(
                self.rect().left() + 4, self.rect().bottom() - 16,
                self.rect().width() - 8, 12, 3, 3
            )
            painter.setPen(Qt.NoPen)

# ================= 连线 =================
class ConnectionPath(QGraphicsPathItem):
    def __init__(self, start_fn, end_fn, start_obj=None, end_obj=None, end_param=None,
                 start_param=None):
        super().__init__()
        self._start_fn = start_fn
        self._end_fn = end_fn
        self.start_obj = start_obj
        self.end_obj = end_obj
        self.end_param = end_param
        self.start_param = start_param  # 起点附加信息（如 CsvDataNode 的 header）
        self.setPen(QPen(QColor(255,180,50), 3, Qt.DashLine))
        self.setZValue(0)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.update_path()

    def update_path(self):
        p1 = self._start_fn()
        p2 = self._end_fn()
        if not p1 or not p2:
            return
        # 三次贝塞尔曲线：水平偏移为两点间距的一半（至少 50px）
        dx = max(abs(p2.x() - p1.x()) * 0.5, 50)
        cp1 = QPointF(p1.x() + dx, p1.y())
        cp2 = QPointF(p2.x() - dx, p2.y())
        path = QPainterPath()
        path.moveTo(p1)
        path.cubicTo(cp1, cp2, p2)
        self.setPath(path)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(255, 80, 80), 4, Qt.SolidLine))
        else:
            painter.setPen(QPen(QColor(255, 180, 50), 3, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self.path())

    def hoverEnterEvent(self, event):
        self.setPen(QPen(QColor(255, 200, 100), 4, Qt.SolidLine))
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if not self.isSelected():
            self.setPen(QPen(QColor(255, 180, 50), 3, Qt.DashLine))
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 清除其他选中，只选中当前连线
            scene = self.scene()
            if scene:
                for conn in scene.connections:
                    if conn != self:
                        conn.setSelected(False)
            self.setSelected(True)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

# ================= 场景 =================
class NodeScene(QGraphicsScene):
    def __init__(self):
        super().__init__()
        # 画布大小由内容决定（不设固定 sceneRect）：可无限放置元素框，
        # 渲染/滚动缓存只按实际内容占用，不预分配固定大区域。
        # QGraphicsScene 未显式设置 sceneRect 时自动取 itemsBoundingRect。
        self.connections = []
        self.nodes = []
        self._temp_start_fn = None
        self._temp_start_obj = None
        self._temp_line = None
        self._undo_save_cb = None  # 由 FlowEditorDialog 设置
        self._last_output_drag_node = None  # 输出栏最近一次拖拽数据的来源 API 节点
        self.classes = []  # 类框集合（类嵌套方案）

    def _save_undo(self):
        if self._undo_save_cb is not None:
            self._undo_save_cb()

    def add_node(self, node):
        self.addItem(node)
        self.nodes.append(node)
        if isinstance(node, APINode):
            node.data_cleared.connect(lambda param, n=node: self._on_param_data_cleared(n, param))
        self._save_undo()

    def remove_node(self, node):
        # 类框：走 remove_class（成员回到全局，内部内容保留）
        if isinstance(node, ClassNode):
            self.remove_class(node)
            return
        # 删除前把节点从所属类框中摘除（类框自动贴合剩余成员）
        _owner = getattr(node, '_class', None)
        if _owner is not None and node in getattr(_owner, '_members', []):
            _owner._members.remove(node)
            try:
                node.setToolTip('')
            except Exception:
                pass
            _owner._refit()
        # 删除前记录受影响的拼合栏（只记录该节点作为 end_obj 的连线关联的拼合栏）
        affected_concat_ids = set()
        for conn in self.connections:
            if conn.end_obj == node or conn.start_obj == node:
                if isinstance(conn.end_obj, APINode):
                    ep = conn.end_param
                    if ep and ep.startswith('concat_'):
                        affected_concat_ids.add((conn.end_obj, ep))
                    else:
                        for cw in conn.end_obj._concat_widgets:
                            if getattr(cw, 'anchor_param', '') == ep:
                                affected_concat_ids.add((conn.end_obj, cw.concat_id))
                                break
                if isinstance(conn.start_obj, APINode):
                    for cw in conn.start_obj._concat_widgets:
                        affected_concat_ids.add((conn.start_obj, cw.concat_id))
        # 删除与此节点相连的所有连线
        for c in list(self.connections):
            if c.start_obj == node or c.end_obj == node:
                self.removeItem(c)
                self.connections.remove(c)
        if isinstance(node, DataProcessNode):
            self.removeItem(node)
            if node in self.nodes:
                self.nodes.remove(node)
            self._cleanup_concat_fields(affected_concat_ids)
            self._save_undo()
            return
        for conn in list(self.connections):
            if conn.start_obj == node or conn.end_obj == node:
                self.removeItem(conn)
                self.connections.remove(conn)
        self.removeItem(node)
        if node in self.nodes:
            self.nodes.remove(node)
        self._cleanup_concat_fields(affected_concat_ids)
        self._save_undo()

    def remove_connection(self, conn):
        """删除单条连线：仅断开该线，不级联删除上游元素框/拼合栏/其他连线。

        数据流由连线驱动，断线后该线不再向下游节点传递数据。
        """
        # 步进器输入连线断开 → 注销对应输入
        if isinstance(conn.end_obj, StepperNode) and conn.end_param \
                and conn.end_param not in (None, 'main'):
            conn.end_obj.remove_input(conn.end_param)
        self.removeItem(conn)
        if conn in self.connections:
            self.connections.remove(conn)
        self._save_undo()

    def _cleanup_concat_fields(self, affected_set):
        """清理受影响的拼合栏：如果某拼合栏没有 DP 连线了，则移除它及关联的空线"""
        for api_node, concat_id in list(affected_set):
            if concat_id not in [cw.concat_id for cw in api_node._concat_widgets]:
                continue
            # 检查是否还有 DP 节点连接到此拼合栏
            still_connected = False
            for conn in self.connections:
                if conn.end_obj == api_node and conn.end_param == concat_id:
                    still_connected = True
                    break
                if isinstance(conn.end_obj, DataProcessNode):
                    for c2 in self.connections:
                        if c2.start_obj == conn.end_obj and c2.end_obj == api_node and c2.end_param == concat_id:
                            still_connected = True
                            break
            if not still_connected:
                # 移除指向该拼合栏的空线
                for conn in list(self.connections):
                    if conn.end_obj == api_node and conn.end_param == concat_id:
                        self.removeItem(conn)
                        self.connections.remove(conn)
                    elif conn.start_obj == api_node and conn.end_param == concat_id:
                        self.removeItem(conn)
                        self.connections.remove(conn)
                    elif isinstance(conn.end_obj, DataProcessNode):
                        for c2 in list(self.connections):
                            if c2.start_obj == conn.end_obj and c2.end_obj == api_node and c2.end_param == concat_id:
                                self.removeItem(c2)
                                self.connections.remove(c2)
                api_node.remove_concat_field(concat_id)

    def _on_param_data_cleared(self, api_node, param_name):
        affected = set()
        for cw in api_node._concat_widgets:
            affected.add((api_node, cw.concat_id))
        for conn in list(self.connections):
            if conn.end_obj == api_node and conn.end_param == param_name:
                if isinstance(conn.start_obj, DataProcessNode):
                    dp_node = conn.start_obj
                    for c in list(self.connections):
                        if c.start_obj == dp_node or c.end_obj == dp_node:
                            self.removeItem(c)
                            self.connections.remove(c)
                    self.removeItem(dp_node)
                    if dp_node in self.nodes:
                        self.nodes.remove(dp_node)
                break
        self._cleanup_concat_fields(affected)
        self._save_undo()

    def _handle_concat_drop(self, api_node, concat_id, path, value):
        """
        拼合栏拖入数据 → 自动连接上游源元素框。
        - 已有原始连线（来自非 DP 的上游节点）：拆除连线，插入 DP 节点
        - 已有 DP 节点：更新其数据
        """
        # 查找已有 DP 输出连线（DP → 拼合栏）
        existing_dp_conn = None
        for conn in self.connections:
            if conn.end_obj == api_node and conn.end_param == concat_id:
                if isinstance(conn.start_obj, DataProcessNode):
                    existing_dp_conn = conn
                break

        # ---- 情况1：已有 DP 节点 → 更新其显示数据 ----
        if existing_dp_conn:
            dp_node = existing_dp_conn.start_obj
            dp_node.source_path = path
            dp_node.set_source_data(value)
            dp_node.src_label.setToolTip(f"📎 路径: {path}")
            self._save_undo()
            return

        # ---- 查找触发该拼合栏的原始连线 ----
        original_conn = None
        # 优先：直连【该拼合栏】的非 DP 上游连线（例如 API→API 首线手动主输入被
        # 重定向到拼合栏后的那条线）。拖字段进来时把它升级为单一 DP 链路，
        # 避免在已有直连上游的基础上再叠一套 DP 连线造成重复。
        for conn in self.connections:
            if (conn.end_obj == api_node and conn.end_param == concat_id
                    and not isinstance(conn.start_obj, DataProcessNode)):
                original_conn = conn
                break
        # 兜底：该 API 指向主参数的非 DP 连线（兼容旧场景）
        if original_conn is None:
            for conn in self.connections:
                if (conn.end_obj == api_node and not isinstance(conn.start_obj, DataProcessNode)
                        and conn.end_param and not conn.end_param.startswith('concat_')):
                    original_conn = conn
                    break

        # ---- 情况1：已有 DP 节点 → 更新其显示数据 ----
        if existing_dp_conn:
            dp_node = existing_dp_conn.start_obj
            dp_node.source_path = path
            dp_node.set_source_data(value)
            dp_node.src_label.setToolTip(f"📎 路径: {path}")
            self._save_undo()
            return

        # ---- 情况2：有原始上游连线 → 拆除并插入 DP ----
        if original_conn:
            upstream_obj = original_conn.start_obj
            self.removeItem(original_conn)
            self.connections.remove(original_conn)
        else:
            # 输出栏拖入来源：跟随数据来源的 API 节点（未连线也构成完整回路）
            src_node = getattr(self, '_last_output_drag_node', None)
            if src_node is not None and src_node in self.nodes and hasattr(src_node, 'outputPort'):
                upstream_obj = src_node
            else:
                upstream_obj = None

        dp_node = DataProcessNode()
        dp_node.set_source_data(value)
        dp_node.source_path = path
        dp_node.src_label.setToolTip(f"📎 路径: {path}")
        self.addItem(dp_node)
        self.nodes.append(dp_node)

        if upstream_obj:
            # 1) 上游输出 → DP 输入
            if isinstance(upstream_obj, (APINode, DataProcessNode)):
                start_fn = upstream_obj.outputPort
            elif isinstance(upstream_obj, CsvDataNode):
                hd = original_conn.start_param or ''
                start_fn = lambda n=upstream_obj, h=hd: n.output_port_for_header(h)
            else:
                start_fn = upstream_obj.outputPort if hasattr(upstream_obj, 'outputPort') else None
            if start_fn:
                conn1 = ConnectionPath(start_fn, dp_node.inputPort, upstream_obj, dp_node,
                                       start_param=original_conn.start_param if original_conn else None)
                self.addItem(conn1)
                self.connections.append(conn1)
            # DP 输出 → 拼合栏
            end_fn = lambda n=api_node, c=concat_id: n.concat_input_port(c)
            conn2 = ConnectionPath(dp_node.outputPort, end_fn, dp_node, api_node, concat_id)
            self.addItem(conn2)
            self.connections.append(conn2)
            # 定位在连线中间
            mid = (upstream_obj.pos() + api_node.pos()) / 2
            dp_node.setPos(mid - QPointF(100, 50))
        else:
            # 无上游源：仅显示在 API 左侧
            pos = api_node.pos() - QPointF(240, -80)
            dp_node.setPos(pos)
            end_fn = lambda n=api_node, c=concat_id: n.concat_input_port(c)
            conn = ConnectionPath(dp_node.outputPort, end_fn, dp_node, api_node, concat_id)
            self.addItem(conn)
            self.connections.append(conn)
        self._save_undo()
        self._save_undo()

    def _connect_rule_dp_sync(self, dp_node, container_node, part_id):
        """把 DP 正则/替换的编辑变化实时同步到规则拼合文本栏（仅连接一次）。

        用户在 DP 上修改正则/替换后，文本栏立即显示最新处理结果（参与文件夹命名）。
        """
        if getattr(dp_node, '_rule_sync_connected', False):
            return
        dp_node._rule_sync_connected = True
        if hasattr(dp_node, 'regex_edit') and dp_node.regex_edit:
            dp_node.regex_edit.textChanged.connect(
                lambda *a, n=dp_node: _sync_rule_widget_processed(
                    container_node, part_id, n))
        if hasattr(dp_node, 'replace_edit') and dp_node.replace_edit:
            dp_node.replace_edit.textChanged.connect(
                lambda *a, n=dp_node: _sync_rule_widget_processed(
                    container_node, part_id, n))

    def _handle_rule_part_drop(self, container_node, part_id, path, value):
        """
        容器规则拼合文本栏拖入数据 → 自动连接上游源元素框（插入 DataProcessNode）。
        - 已有 DP 节点：更新其数据
        - 有原始上游连线：拆除并插入 DP 节点
        - 无上游：仅显示 DP 在容器左侧
        """
        # 查找已有 DP 输出连线（DP → 该拼合段）
        existing_dp_conn = None
        for conn in self.connections:
            if conn.end_obj == container_node and conn.end_param == part_id:
                if isinstance(conn.start_obj, DataProcessNode):
                    existing_dp_conn = conn
                break

        # ---- 情况1：已有 DP 节点 → 更新其显示数据 ----
        if existing_dp_conn:
            dp_node = existing_dp_conn.start_obj
            dp_node.source_path = path
            dp_node.set_source_data(value)
            dp_node.src_label.setToolTip(f"路径: {path}")
            # 同步路径到容器规则段（文件夹命名时使用）
            for pd in container_node._rule_parts:
                if pd['id'] == part_id:
                    pd['source_path'] = path
                    break
            # 处理结果显示到文本栏（命名预览）
            _sync_rule_widget_processed(container_node, part_id, dp_node)
            self._connect_rule_dp_sync(dp_node, container_node, part_id)
            self._save_undo()
            return

        # ---- 确定上游源：优先手动连线到该文本段，其次容器主输入连线（不拆除，保留数据流）----
        removed_conn = None
        upstream_obj = None
        upstream_fn = None
        upstream_param = None
        # 1) 手动连到该文本段的连线 → 拆线插入 DP
        for conn in self.connections:
            if conn.end_obj == container_node and not isinstance(conn.start_obj, DataProcessNode):
                ep = conn.end_param or ''
                if ep == part_id:
                    removed_conn = conn
                    break
        if removed_conn is not None:
            upstream_obj = removed_conn.start_obj
            upstream_fn = removed_conn._start_fn
            upstream_param = removed_conn.start_param
            self.removeItem(removed_conn)
            self.connections.remove(removed_conn)
        else:
            # 2) 输出栏拖入来源：数据来自哪个 API 节点，DP 上游跟随它（构成完整回路）
            src_node = getattr(self, '_last_output_drag_node', None)
            if src_node is not None and src_node in self.nodes and hasattr(src_node, 'outputPort'):
                upstream_obj = src_node
                upstream_fn = src_node.outputPort
                upstream_param = None
            # 3) 容器主输入连线（源 → 容器）→ 复用其源作为 DP 上游（保留原连线，构成完整回路）
            if upstream_obj is None:
                for conn in self.connections:
                    if conn.end_obj == container_node and not isinstance(conn.start_obj, DataProcessNode):
                        ep = conn.end_param or ''
                        if not ep.startswith('rule_'):
                            upstream_obj = conn.start_obj
                            upstream_fn = conn._start_fn
                            upstream_param = conn.start_param
                            break
            # 4) 其他参数连线
            if upstream_obj is None:
                for conn in self.connections:
                    if (conn.end_obj == container_node and not isinstance(conn.start_obj, DataProcessNode)
                            and conn.end_param and not conn.end_param.startswith('rule_')):
                        upstream_obj = conn.start_obj
                        upstream_fn = conn._start_fn
                        upstream_param = conn.start_param
                        break

        dp_node = DataProcessNode()
        dp_node.set_source_data(value)
        dp_node.source_path = path
        dp_node.src_label.setToolTip(f"路径: {path}")
        # 同步路径到容器规则段（文件夹命名时使用）
        for pd in container_node._rule_parts:
            if pd['id'] == part_id:
                pd['source_path'] = path
                break
        self.addItem(dp_node)
        self.nodes.append(dp_node)

        if upstream_obj:
            # 1) 上游输出 → DP 输入
            if upstream_fn is None:
                if isinstance(upstream_obj, (APINode, DataProcessNode)):
                    upstream_fn = upstream_obj.outputPort
                elif isinstance(upstream_obj, CsvDataNode):
                    hd = upstream_param or ''
                    upstream_fn = lambda n=upstream_obj, h=hd: n.output_port_for_header(h)
                else:
                    upstream_fn = upstream_obj.outputPort if hasattr(upstream_obj, 'outputPort') else None
            if upstream_fn:
                conn1 = ConnectionPath(upstream_fn, dp_node.inputPort, upstream_obj, dp_node,
                                       start_param=upstream_param)
                self.addItem(conn1)
                self.connections.append(conn1)
            # DP 输出 → 规则拼合段
            end_fn = lambda n=container_node, p=part_id: n.rule_part_input_port(p)
            conn2 = ConnectionPath(dp_node.outputPort, end_fn, dp_node, container_node, part_id)
            self.addItem(conn2)
            self.connections.append(conn2)
            # 定位在连线中间
            mid = (upstream_obj.pos() + container_node.pos()) / 2
            dp_node.setPos(mid - QPointF(100, 50))
        else:
            # 无上游源：仅显示在容器左侧
            pos = container_node.pos() - QPointF(240, -80)
            dp_node.setPos(pos)
            end_fn = lambda n=container_node, p=part_id: n.rule_part_input_port(p)
            conn = ConnectionPath(dp_node.outputPort, end_fn, dp_node, container_node, part_id)
            self.addItem(conn)
            self.connections.append(conn)
        # 处理结果显示到文本栏（命名预览）
        _sync_rule_widget_processed(container_node, part_id, dp_node)
        self._connect_rule_dp_sync(dp_node, container_node, part_id)
        self._save_undo()

    def start_connection(self, point_fn, obj, start_param=None):
        self._temp_start_fn = point_fn
        self._temp_start_obj = obj
        self._temp_start_param = start_param  # CsvDataNode 的 header 名
        self._temp_line = QGraphicsPathItem()
        self._temp_line.setPen(QPen(Qt.red, 2, Qt.DashLine))
        self.addItem(self._temp_line)

    def update_temp_line(self, pos):
        if self._temp_line and self._temp_start_fn:
            p1 = self._temp_start_fn()
            path = QPainterPath()
            path.moveTo(p1)
            path.lineTo(pos)
            self._temp_line.setPath(path)

    def finish_connection(self, end_fn, end_obj, end_param=None):
        # 禁止自连：同一元素框的输出不能连回自己的输入
        if end_obj is self._temp_start_obj:
            self.clear_temp()
            return
        # 类框端口只允许类↔类（触发信号）；连到普通元素框则取消
        if isinstance(self._temp_start_obj, ClassNode) != isinstance(end_obj, ClassNode):
            self.clear_temp()
            return
        # ---- 上游 → 容器框（根目录输入点）：用上游数据当根目录文件夹名 ----
        if isinstance(end_obj, ContainerNode) and end_param == 'root':
            _src = self._temp_start_obj
            if isinstance(_src, (ContainerNode, ImageNode, ClassNode)):
                self.clear_temp()
                self._scene_log(
                    "⚠ 容器框的「根目录输入点」只接收会产出内容的元素框"
                    f"（接口 / 数据处理 / 站点解析 / 步进器 / 数据库框）；"
                    f"{type(_src).__name__} 不产出数据，已取消连线。")
                return
            if self._creates_cycle(_src, end_obj):
                self.clear_temp()
                self._scene_log(
                    "⚠ 已拒绝这条连线：它会让数据绕回自己（成环）。")
                return
            if self._temp_line:
                self.removeItem(self._temp_line)
                self._temp_line = None
            conn = ConnectionPath(self._temp_start_fn, end_obj.root_path_input_port,
                                  _src, end_obj, 'root',
                                  start_param=self._temp_start_param)
            self.addItem(conn)
            self.connections.append(conn)
            self._scene_log(
                f"🔗 已把 {type(_src).__name__} 的输出连到容器框的「根目录输入点」："
                "上游的值将作为根目录文件夹名，在当前路径下自动创建"
                "（已有同名文件夹则直接写入，不重复创建）")
            self._save_undo()
            self._temp_start_fn = None
            self._temp_start_obj = None
            self._temp_start_param = None
            return
        # ---- 上游 → 数据库框（输入端）：只收数据类节点，且拒绝成环 ----
        if isinstance(end_obj, CsvDataNode):
            _src = self._temp_start_obj
            if not isinstance(_src, (APINode, DataProcessNode)):
                self.clear_temp()
                self._scene_log(
                    "⚠ 数据库框的输入端只接收「接口」或「数据处理」元素框的数据；"
                    f"{type(_src).__name__} 的输出不是键值数据，已取消连线。")
                return
            if self._creates_cycle(_src, end_obj):
                self.clear_temp()
                self._scene_log(
                    "⚠ 已拒绝这条连线：它会让数据绕回自己（成环）。"
                    "数据库框的输入只用于「写」，请勿把它的输出再接回上游。")
                return
            if self._temp_line:
                self.removeItem(self._temp_line)
                self._temp_line = None
            conn = ConnectionPath(self._temp_start_fn, end_obj.inputPort,
                                  _src, end_obj, 'ingest',
                                  start_param=self._temp_start_param)
            self.addItem(conn)
            self.connections.append(conn)
            end_obj._set_ingest_status(
                f"🔗 已连接上游：{type(_src).__name__} 的数据将按「{end_obj._ingest_mode_label()}」写入本表")
            self._scene_log(
                f"🔗 已把 {type(_src).__name__} 的输出连到数据库框 "
                f"[{os.path.basename(end_obj.file_path or '')}] 的输入端"
                f"（写入方式：{end_obj._ingest_mode_label()}）")
            self._save_undo()
            self._temp_start_fn = None
            self._temp_start_obj = None
            self._temp_start_param = None
            return
        if self._temp_line:
            self.removeItem(self._temp_line)
            self._temp_line = None
        if self._temp_start_fn and end_fn:
            conn = ConnectionPath(self._temp_start_fn, end_fn,
                                  self._temp_start_obj, end_obj, end_param,
                                  start_param=self._temp_start_param)
            self.addItem(conn)
            self.connections.append(conn)
            # ---- CsvDataNode → APINode 自动注入 + 插入 DataProcessNode ----
            if (isinstance(self._temp_start_obj, CsvDataNode) and
                    isinstance(end_obj, APINode) and end_param):
                self._auto_insert_dp_for_csv(
                    self._temp_start_obj, self._temp_start_param,
                    end_obj, end_param, conn
                )
            # ---- APINode 接收连线：拼合信息栏 + DP 内容注入 ----
            if isinstance(end_obj, APINode) and end_param:
                is_dp = isinstance(self._temp_start_obj, DataProcessNode)
                dp_text = self._node_output_text(self._temp_start_obj) if is_dp else ''
                _src_is_csv = isinstance(self._temp_start_obj, CsvDataNode)
                # 统计已指向该 API 节点的非拼合连线数（排除刚添加的当前连线）
                existing_count = sum(
                    1 for c in self.connections
                    if c is not conn
                    and c.end_obj == end_obj
                    and c.end_param
                    and not c.end_param.startswith('concat_')
                )
                # 主输入点是否已有【手动输入内容】（锁定值，不覆盖）
                manual_main = (not end_param.startswith('concat_')
                               and _api_param_is_manual_input(end_obj, end_param))
                if is_dp and end_param.startswith('concat_'):
                    # DP → 已有拼合栏输入点：把处理后内容即时注入该拼合栏
                    self._set_api_concat_value(end_obj, end_param, dp_text)
                elif (manual_main and existing_count == 0 and not _src_is_csv
                        and not end_param.startswith('concat_')):
                    # 【任何源】首根线指向【有手动内容】的主输入点：
                    # 不覆盖手动值，直接把连线连到数据拼合栏（新建拼合栏并重定向）；
                    # DP 源顺带把处理后内容填入拼合栏；其它源（如 API→API）则直连拼合栏，
                    # 之后可再从输出栏拖字段进拼合栏升级成 DP 链路。
                    new_cid = end_obj.add_concat_field(anchor_param=end_param)
                    if is_dp and dp_text:
                        self._set_api_concat_value(end_obj, new_cid, dp_text)
                    conn.end_param = new_cid
                    conn._end_fn = lambda n=end_obj, c=new_cid: n.concat_input_port(c)
                    conn.update_path()
                elif existing_count >= 1 and not _src_is_csv:
                    # 已有其他连线时新建拼合栏，并把刚创建的连线重定向到该拼合栏的
                    # 专属输入点（而不是停留在主输入点），使第二根线连到新拼合栏。
                    new_cid = end_obj.add_concat_field(anchor_param=end_param)
                    if is_dp and dp_text:
                        # DP 带来的新拼合栏：自动填入处理后内容
                        self._set_api_concat_value(end_obj, new_cid, dp_text)
                    conn.end_param = new_cid
                    conn._end_fn = lambda n=end_obj, c=new_cid: n.concat_input_port(c)
                    conn.update_path()
                elif (is_dp and dp_text and not end_param.startswith('concat_')
                        and existing_count == 0):
                    # DP → API 主输入点（普通参数输入，主输入为空）：注入主输入
                    # 参数值（与 CSV→API 注入一致），连线停留在主输入点。
                    self._inject_api_param_value(end_obj, end_param, dp_text)
            # ---- 步进器接收连线 → 注册一个数字输入（多条线 = 多个输入） ----
            if isinstance(end_obj, StepperNode):
                if end_param in (None, 'main'):
                    key = end_obj.register_input()
                    conn.end_param = key
                    conn._end_fn = lambda n=end_obj, k=key: n.input_port_for_key(k)
                    conn.update_path()
            # ---- 数据源 → 站点解析输入：自动插入数据处理框（可正则预处理 URL 文本） ----
            if (isinstance(end_obj, SiteParserNode) and
                    not isinstance(self._temp_start_obj, (DataProcessNode, SiteParserNode))):
                self._auto_insert_dp_before_site_parser(
                    self._temp_start_obj, self._temp_start_param, end_obj, conn)
            # ---- 实时预览推送：数据处理框/数据库内容预先向下游流动 ----
            if isinstance(self._temp_start_obj, (DataProcessNode, CsvDataNode)):
                self.push_preview_downstream(self._temp_start_obj)
            self._save_undo()  # 在所有修改之后保存
        self._temp_start_fn = None
        self._temp_start_obj = None
        self._temp_start_param = None

    def _set_api_concat_value(self, api_node, concat_id, text):
        """把文本写入 API 节点的指定拼合栏（edit.setText 会同步 concat_value 并重绘）。"""
        if not text:
            return False
        for cw in list(getattr(api_node, '_concat_widgets', None) or []):
            if cw.concat_id == concat_id:
                try:
                    cw.edit.setText(str(text))
                except Exception:
                    cw.concat_value = str(text)
                return True
        return False

    def _inject_api_param_value(self, api_node, param_name, text):
        """把文本写入 API 节点的普通参数（主输入），与 CSV→API 注入一致。"""
        if not text or not param_name:
            return False
        for p in list(getattr(api_node, 'params', None) or []):
            if p.get('name') == param_name:
                p['value'] = str(text)
                break
        try:
            pw = getattr(api_node, 'param_widgets', None) or {}
            if param_name in pw and hasattr(pw[param_name], 'edit'):
                pw[param_name].edit.setText(str(text))
        except Exception:
            pass
        return True

    def _auto_insert_dp_for_csv(self, csv_node, csv_header, api_node, api_param, existing_conn):
        """CsvDataNode 连线到 APINode 时，自动注入当前值并插入 DataProcessNode"""
        if not csv_header or csv_header not in csv_node.headers:
            return
        # 1) 注入当前值到 APINode 参数
        val = csv_node.get_current_value(csv_header)
        for p in api_node.params:
            if p['name'] == api_param:
                p['value'] = val
                if api_param in api_node.param_widgets:
                    api_node.param_widgets[api_param].edit.setText(val)
                break
        # 2) 插入 DataProcessNode 到中间，记录其关联的 CSV 数据源
        dp_node = DataProcessNode()
        dp_node._csv_source = (csv_node, csv_header)  # 记录来源
        dp_node.set_source_data(val)  # 显示实际当前值而非key名
        self.addItem(dp_node)
        self.nodes.append(dp_node)
        # 位置在连线中间偏左
        mid = (existing_conn._start_fn() + api_node.param_input_port(api_param)) / 2
        dp_node.setPos(mid - QPointF(100, 50))
        # 移除原连线
        self.removeItem(existing_conn)
        self.connections.remove(existing_conn)
        # 新连线 1: CsvDataNode 输出 → DP 输入（记录 start_param）
        start_fn = lambda n=csv_node, h=csv_header: n.output_port_for_header(h)
        conn1 = ConnectionPath(start_fn, dp_node.inputPort, csv_node, dp_node,
                               start_param=csv_header)
        self.addItem(conn1)
        self.connections.append(conn1)
        dp_node.input_conn = conn1
        # 新连线 2: DP 输出 → APINode 参数输入
        end_fn = lambda n=api_node, p=api_param: n.param_input_port(p)
        conn2 = ConnectionPath(dp_node.outputPort, end_fn, dp_node, api_node, api_param)
        self.addItem(conn2)
        self.connections.append(conn2)
        dp_node.output_conn = conn2

    def _auto_insert_dp_before_site_parser(self, start_obj, start_param, end_obj, existing_conn):
        """数据源 → 站点解析输入连线时，自动插入 DataProcessNode 到中间。

        方便用正则预先处理 URL 文本（例如从大段文本中提取 mega 链接），
        插入后立即把种子数据实时推送，让站点解析的「已识别」预览即时更新。
        """
        val = self._node_output_text(start_obj, start_param)
        dp_node = DataProcessNode()
        self.addItem(dp_node)
        self.nodes.append(dp_node)
        mid = (existing_conn._start_fn() + end_obj.inputPort()) / 2
        dp_node.setPos(mid - QPointF(100, 50))
        # 移除原连线
        self.removeItem(existing_conn)
        self.connections.remove(existing_conn)
        # 新连线 1: 数据源 → DP 输入
        start_fn = existing_conn._start_fn
        conn1 = ConnectionPath(start_fn, dp_node.inputPort, start_obj, dp_node,
                               start_param=start_param)
        self.addItem(conn1)
        self.connections.append(conn1)
        dp_node.input_conn = conn1
        # 新连线 2: DP 输出 → 站点解析输入
        conn2 = ConnectionPath(dp_node.outputPort, end_obj.inputPort, dp_node, end_obj)
        self.addItem(conn2)
        self.connections.append(conn2)
        dp_node.output_conn = conn2
        # 种子数据 + 实时推送（DP → 站点解析识别预览）
        dp_node.set_source_data(val)

    def clear_temp(self):
        if self._temp_line:
            self.removeItem(self._temp_line)
            self._temp_line = None
        self._temp_start_fn = None
        self._temp_start_obj = None
        self._temp_start_param = None

    # ================= 数据库框输入端专用工具 =================
    def _creates_cycle(self, start_obj, end_obj):
        """新增 start→end 连线是否会成环：从 end 出发沿现有连线能否回到 start。
        本项目没有其它环形依赖检测，数据库框会写文件，成环会造成数据自激，必须自己拦。"""
        if start_obj is end_obj:
            return True
        seen = set()
        stack = [end_obj]
        while stack:
            cur = stack.pop()
            if cur is start_obj:
                return True
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            for c in self.connections:
                if c.start_obj is cur and c.end_obj is not None:
                    if c.end_obj is start_obj:
                        return True
                    if id(c.end_obj) not in seen:
                        stack.append(c.end_obj)
        return False

    def _scene_log(self, msg):
        """把说明写进流程编辑器的运行日志面板（找不到就安静跳过）"""
        try:
            for v in self.views():
                box = getattr(v.window(), 'api_log_text', None)
                if box is not None:
                    box.append(msg)
                    return
        except Exception:
            pass

    def csv_ingest_targets(self, api_node):
        """找出一份接口结果应当流进哪些数据库框：
        直接连线（api → csv）与经过数据处理框中转（api → dp → … → csv）。
        返回 [(csv_node, 路径说明), ...]"""
        targets = []
        seen = set()
        queue = [(api_node, '')]
        guard = 0
        while queue and guard < 64:
            guard += 1
            cur, path = queue.pop(0)
            for c in self.connections:
                if c.start_obj is not cur or c.end_obj is None:
                    continue
                nxt = c.end_obj
                if isinstance(nxt, CsvDataNode):
                    if id(nxt) not in seen:
                        seen.add(id(nxt))
                        targets.append((nxt, path or '直连'))
                elif isinstance(nxt, DataProcessNode) and id(nxt) not in seen:
                    seen.add(id(nxt))
                    label = getattr(nxt, '_rule_name', '') or '数据处理'
                    queue.append((nxt, (path + ' → ' if path else '') + label))
        return targets

    def update_connections_for_node(self, node):
        for conn in self.connections:
            if conn.start_obj == node or conn.end_obj == node:
                conn.update_path()
        self._refit_classes_for(node)

    # ---------- 类嵌套方案：类框 ----------
    def class_of(self, node):
        """返回节点所属的最内层类框（无则 None）。"""
        return getattr(node, '_class', None)

    def create_class_from_nodes(self, nodes):
        """把一组元素框打包成类；第一个创建的类框为 main。

        - 若所有节点已属于同一个类 → 不重复建类（返回 None）
        - main：类名固定、无输入触发点、运行时必定触发
        - 其余类：自定义类名、左输入/右输出触发点、按触发链执行
        返回新建的 ClassNode 或 None。
        """
        members = [n for n in nodes if isinstance(
            n, (APINode, DataProcessNode, CsvDataNode, ContainerNode,
                StepperNode, SiteParserNode, ImageNode))]
        if not members:
            return None
        # 全部已属于同一个类 → 仅选择，不重复建类
        owner_set = {getattr(n, '_class', None) for n in members}
        owner_set.discard(None)
        if len(owner_set) == 1:
            only = owner_set.pop()
            if only is not None and all(n in only._members for n in members):
                return None
        is_main = not any(getattr(cc, '_is_main', False) for cc in self.classes)
        n_non_main = sum(1 for cc in self.classes if not getattr(cc, '_is_main', False))
        cls = ClassNode(
            class_name='main' if is_main else f'class{n_non_main + 1}',
            is_main=is_main)
        for m in members:
            cls.add_member(m)
        self.addItem(cls)
        self.nodes.append(cls)
        self.classes.append(cls)
        cls._refit()
        self._save_undo()
        return cls

    def remove_class(self, cls):
        """取消类整合：内部内容保持不变，成员重新跟踪全局大轮询。"""
        if cls in self.classes:
            self.classes.remove(cls)
        for m in list(cls._members):
            m._class = None
            try:
                m.setToolTip('')
            except Exception:
                pass
        cls._members = []
        # 移除类框与它的触发连线（类间连线）
        for conn in list(self.connections):
            if conn.start_obj is cls or conn.end_obj is cls:
                self.removeItem(conn)
                if conn in self.connections:
                    self.connections.remove(conn)
        self.removeItem(cls)
        if cls in self.nodes:
            self.nodes.remove(cls)
        self._save_undo()

    def _refit_classes_for(self, node):
        """节点位置变化时，让包含它的类框重新贴合。"""
        if not self.classes:
            return
        owner = getattr(node, '_class', None)
        if (owner is not None and node in getattr(owner, '_members', [])
                and not getattr(owner, '_suppress_refit', False)):
            owner._refit()

    # ---------- 实时预览推送（数据预先向下一层流动） ----------
    @staticmethod
    def _node_output_text(node, param=None):
        """取节点当前输出文本（用于实时预览推送 / 自动插入数据处理框时取种子值）。"""
        try:
            if isinstance(node, DataProcessNode):
                return str(getattr(node, 'processed_data', '') or '')
            if isinstance(node, CsvDataNode):
                if param and param in node.headers:
                    return str(node.get_current_value(param) or '')
                return ''
            if isinstance(node, SiteParserNode):
                return str(getattr(node, 'url', '') or '')
            if isinstance(node, StepperNode):
                return str(getattr(node, 'output_value', '') or '')
            if isinstance(node, APINode):
                rj = getattr(node, 'result_json', None)
                if isinstance(rj, str):
                    return rj.strip()
                if isinstance(rj, dict) and '_binary' not in rj:
                    for v in rj.values():
                        if isinstance(v, str):
                            return v.strip()
                return ''
        except Exception:
            return ''
        return ''

    def push_preview_downstream(self, node):
        """把节点输出实时推送到下游元素框（预览用）。

        例如 DataProcessNode 处理完后 → SiteParserNode 的「已识别」状态行即时更新，
        无需等到流程执行。后续新增可预览的下游元素框时在此扩展。
        """
        if node is None or node.scene() is not self:
            return
        for conn in list(self.connections):
            if conn.start_obj is not node:
                continue
            end = conn.end_obj
            if isinstance(end, SiteParserNode):
                val = self._node_output_text(node, conn.start_param)
                end.set_input_text(val)

    # ---------- 网格背景 ----------
    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # ⚡ 性能：原来每画一条线就调一次 painter.drawLine()，还要就地新建两个 QPointF。
        # 1500×940 的视口在 20px 间距下就是 ~120 条细线 + ~25 条粗线 —— 每帧约 147 次
        # Python↔C++ 跨界、约 440 次对象构造。实测这一项占**整个重绘成本的 54%**
        # （4.02 ms / 基线 7.50 ms），是单点最大开销。
        #
        # 改法：先把线攒进 list[QLineF]，再各用一次 drawLines() 批量提交
        # （147 次跨界 -> 2 次）。**几何逐点不变** —— 取整方式、步进、以及
        # `x < rect.right()` 这个上界判断都原样保留（right 是 float，不能换成
        # range()，那会在 right 恰为整数时多画一条）。
        def _grid_lines(step):
            xs, ys = [], []
            x = int(rect.left() // step) * step
            right, bottom = rect.right(), rect.bottom()
            while x < right:
                xs.append(x)
                x += step
            y = int(rect.top() // step) * step
            while y < bottom:
                ys.append(y)
                y += step
            top, left = rect.top(), rect.left()
            return ([QLineF(x, top, x, bottom) for x in xs]
                    + [QLineF(left, y, right, y) for y in ys])

        # 小网格线（浅灰）
        minor_pen = QPen(QColor(220, 220, 220, 100))
        minor_pen.setWidthF(0.5)
        painter.setPen(minor_pen)
        painter.drawLines(_grid_lines(20))
        # 大网格线（深灰）
        major_pen = QPen(QColor(180, 180, 180, 150))
        major_pen.setWidthF(1.0)
        painter.setPen(major_pen)
        painter.drawLines(_grid_lines(100))

# ================= 视图 =================
# ================= 元素框悬停 SVG 提示 =================
_NODE_HOVER_TYPES = (
    APINode, DataProcessNode, CsvDataNode, ContainerNode,
    StepperNode, SiteParserNode, ImageNode, ClassNode, ConnectionPath,
)
_NODE_HOVER_SVG = (
    (APINode, 'api_node.svg'),
    (DataProcessNode, 'data_process_node.svg'),
    (CsvDataNode, 'database_node.svg'),
    (ContainerNode, 'container_node.svg'),
    (StepperNode, 'stepper_node.svg'),
    (SiteParserNode, 'site_parser_node.svg'),
    (ImageNode, 'image_node.svg'),
    (ClassNode, 'class_node.svg'),
    (ConnectionPath, 'connection.svg'),
)


def _hover_escape(s):
    """SVG 文本的 XML 转义（防 & < > 破坏 SVG 结构）。"""
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _hover_truncate(s, n=22):
    """截断过长的显示文本，避免超出 SVG 示意框。"""
    s = str(s or '')
    return s if len(s) <= n else s[:max(0, n - 1)] + '…'


def _hover_fit_text(s, max_px, em=10.0):
    """按**粗略像素宽度**截断文本（CJK/全角按 1em，其余按 0.58em）。

    比按字符数截断准得多：同样是 8 个字符，「作者唯一标识符ID」约 82px，
    「creator_」只有约 47px。图源配置里的注释大多是中文，按字符数算会溢出框。
    """
    s = str(s or '')
    if max_px <= 0:
        return ''
    out, w = [], 0.0
    for ch in s:
        cw = em if ord(ch) > 0x2E7F else em * 0.58
        if w + cw > max_px:
            return ''.join(out) + '…'
        out.append(ch)
        w += cw
    return s


def _hover_fmt_size(b):
    """字节数 → 人类可读大小。"""
    try:
        b = int(b or 0)
    except Exception:
        return '0 B'
    if b < 1024:
        return f'{b} B'
    if b < 1024 * 1024:
        return f'{b / 1024:.1f} KB'
    if b < 1024 * 1024 * 1024:
        return f'{b / (1024 * 1024):.1f} MB'
    return f'{b / (1024 * 1024 * 1024):.1f} GB'


# ---- 悬停 SVG 的「端口清单」----
# 端口行的位置、键名、当前值全部在「SVG 框被拉开的那一刻」从运行态现读一次
# （由 NodeView._poll_hover 在 expand 前调用 _render_svg_pixmap 触发），之后不再刷新。
# 颜色与各节点 paint() 里 drawEllipse 的 QColor 一一对应，方便和画布上的圆点对上号。
_HOVER_PORT_COLORS = {
    'input':     '#64c864',   # 主输入 / 参数输入（绿）
    'output':    '#c86464',   # 一般输出（红）
    'concat':    '#e5a53b',   # API 拼合栏输入 / 容器规则段输入（橙）
    'root':      '#50e6ff',   # 容器根目录输入（青，已接上游）
    'root_idle': '#46aac8',   # 容器根目录输入（暗青，未接上游）
    'column':    '#ffc832',   # 数据库列输出（黄）
    'class_in':  '#ffbe5a',   # 类框输入（橙黄）
    'class_out': '#78e678',   # 类框输出（亮绿）
    'note':      '#8ab8ff',   # 说明行（无端口的框）
}

_HOVER_NODE_NAMES = (
    (APINode, 'API 框'),
    (DataProcessNode, '数据处理框'),
    (CsvDataNode, '数据库框'),
    (ContainerNode, '容器框'),
    (StepperNode, '步进器框'),
    (SiteParserNode, '站点解析框'),
    (ClassNode, '类框'),
)

_HOVER_PORT_ROW_H = 22       # 单行端口高度
_HOVER_PORT_HEAD_H = 15      # 「◀ 输入端口 / 输出端口 ▶」小标题占的高度
_HOVER_PORT_MAX_ROWS = 7     # 一屏最多列几行，超出折叠成「另有 N 个端口」

# 悬停多久才把详情框拉开展示（秒）。
# 为什么是 0.25 而不是原来的 4.0：4 s 的唯一目的是「别在扫鼠标时乱弹」——
# 当时浮窗会截获鼠标事件、展开后又锁住位置，只能靠长等待来降低撞上的概率。
# 现在浮窗点击穿透 + 离开即收（见 NodeHoverTip / NodeView._poll_hover），
# 长等待失去意义。改动前的实测（本机量测脚本，私有不入库）：
# 「悬停 → 内容可见」要 4.44 s，其中真·渲染只有 5–31 ms（99.4% 是等待与动效）。
HOVER_REVEAL_DELAY_S = 0.25

# 模板里用 <!-- HOVER:PORTS top=起点y tail=页脚原始y --> 标出端口区位置
_HOVER_PORTS_RE = re.compile(
    r'<!--\s*HOVER:PORTS\s+top=(\d+)\s+tail=(\d+)(?:\s+row=(\d+))?\s*-->')
_SVG_ROOT_TAG_RE = re.compile(r'<svg\b[^>]*>', re.S)
_SVG_BOX_RECT_RE = re.compile(r'<rect\b[^>]*class="box"[^>]*>', re.S)


def _hover_attr_add(tag, attr, delta):
    """把标签里 attr="数字" 的值加上 delta（用于动态撑高画布 / 外框）。"""
    m = re.search(r'\b' + attr + r'="(-?[\d.]+)"', tag)
    if not m:
        return tag
    new = f'{float(m.group(1)) + delta:.0f}'
    return tag[:m.start(1)] + new + tag[m.end(1):]


def _hover_grow_canvas(svg, dy):
    """画布高度 +dy：同步改 <svg height/viewBox> 与外框 <rect class="box"> 的 height。

    端口行数决定端口区高度，页脚靠 {{tail_dy}} 整体平移贴到端口区下方，
    所以整张图的高度也要跟着变，否则 QSvgRenderer 会把内容裁掉。
    """
    if not dy:
        return svg
    m = _SVG_ROOT_TAG_RE.search(svg)
    if m:
        tag = _hover_attr_add(m.group(0), 'height', dy)
        vm = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', tag)
        if vm:
            tag = tag[:vm.start(2)] + f'{float(vm.group(2)) + dy:.0f}' + tag[vm.end(2):]
        svg = svg[:m.start()] + tag + svg[m.end():]
    bm = _SVG_BOX_RECT_RE.search(svg)
    if bm:
        svg = svg[:bm.start()] + _hover_attr_add(bm.group(0), 'height', dy) + svg[bm.end():]
    return svg


class NodeHoverTip(QWidget):
    """元素框悬停详情浮窗。

    时间线：悬停后显示 thinking 文本框（₍^. .^₎⟆thinking....）并平滑跟随鼠标，
    满 HOVER_REVEAL_DELAY_S 秒拉开展示框动效并展示 SVG（端口清单 + 当前值）；
    **展开后仍跟随鼠标**；鼠标离开元素框立即收（Esc 也可退出）。

    两条**改动前必读**的约束：

    - **点击穿透**：本窗口带 `Qt.WindowTransparentForInput` ＋ `WA_TransparentForMouseEvents`，
      落在它上面的点击 / 拖动会穿透到画布。所以这里**不处理鼠标事件** —— 早期版本
      「把挡路的浮窗拖走」的能力是**故意去掉的**（穿透之后这个需求本身就不存在了）。
      要改回可交互，得先想清楚它会重新挡住操作，并配合「按键门控」之类的方案
      （缓冲带也要一起加回来 —— 本机记忆里记过这条坑）。
    - **两个动效不要同时跑**：`_anim` 动的是 geometry（含位置），`_move_anim` 动的是 pos ——
      同时进行会互相覆盖、抖动。所以 `animate_move_to()` 在展开动效未结束时直接返回。
    """

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 点击穿透：鼠标事件落到下层画布，浮窗不再抢点击 / 拖动（见类注释第 1 条）
        # 两层都要设，缺一不可：
        #   · Qt.WindowTransparentForInput → 平台层（Windows 上给窗口加 WS_EX_TRANSPARENT），
        #     真实点击/拖动会穿到下层窗口 —— 这才是「点得到下面的框」的关键；
        #   · WA_TransparentForMouseEvents → Qt 自己的命中测试也算它透明。
        #     ⚠️ 实测：只设前者时 QApplication.topLevelAt() 仍会**返回本浮窗**（Qt 枚举
        #     自己的顶层窗口、只认这个属性），靠 topLevelAt 判「鼠标在哪个窗口上」的代码
        #     会把画布误判成「被浮窗挡住」；设上它两者才一致。
        self.setWindowFlag(Qt.WindowTransparentForInput, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._label)
        self._label.setStyleSheet(
            "QLabel { background-color: rgba(16,24,36,245); color:#9ecbff; "
            "border:1px solid #3a6ea5; border-radius:8px; padding:6px 10px; "
            "font-family: Consolas, 'Courier New', monospace; font-size:13px; }")
        self._pixmap = None
        self._has_svg = False   # 是否已展示 SVG（此时 label 不再用 thinking 文本）
        self._expanding = False  # 展开动效进行中（期间不跟随，见类注释第 2 条）
        self._anim = QPropertyAnimation(self, b"geometry", self)  # 展开动效（尺寸）
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_expand_done)
        self._move_anim = QPropertyAnimation(self, b"pos", self)  # 平滑跟随（位置）
        self._move_anim.setDuration(180)
        self._move_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.hide()

    # ---------- 平滑跟随 ----------
    def animate_move_to(self, x, y):
        """把浮窗平滑移到屏幕 (x, y)（跟随鼠标锚点；已在目标附近则不动）。

        展开动效（_anim 动的是 geometry，含位置）未结束时直接返回 —— 与这里的 pos
        动画同时进行会互相覆盖、抖动。等它结束（`_on_expand_done` 清 `_expanding`）恢复跟随。
        """
        if self._expanding:
            return
        cur = self.pos()
        if abs(cur.x() - x) < 2 and abs(cur.y() - y) < 2:
            return
        self._move_anim.stop()
        self._move_anim.setStartValue(cur)
        self._move_anim.setEndValue(QPoint(int(x), int(y)))
        self._move_anim.start()

    def _on_expand_done(self):
        """展开动效结束：把 SVG 塞进 label，并恢复跟随（清 `_expanding`）。"""
        self._expanding = False
        if getattr(self, '_pixmap', None) is not None and not self._pixmap.isNull():
            self._label.setPixmap(self._pixmap)
            self._label.setAlignment(Qt.AlignCenter)

    def show_thinking(self, x, y):
        """第一阶段：显示 thinking 文本框（之后平滑跟随鼠标）。"""
        self._anim.stop()
        self._move_anim.stop()
        self._expanding = False
        self._has_svg = False
        self._label.setPixmap(QPixmap())   # 先清空 pixmap，再设文本（二者互斥，后设者生效）
        self._label.setText("₍^. .^₎⟆thinking....")
        self._label.setAlignment(Qt.AlignCenter)
        self._pixmap = None
        self.adjustSize()
        w = max(190, self.sizeHint().width() + 30)
        h = self.sizeHint().height() + 12
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()

    def expand_to(self, x, y, w, h, pixmap):
        """第三阶段：拉开展示框动效并渲染 SVG。**不锁定位置** —— 展开结束继续跟随鼠标。"""
        self._move_anim.stop()
        self._pixmap = pixmap
        self._has_svg = True
        self._expanding = True
        start = self.geometry()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(QRect(int(x), int(y), int(w), int(h)))
        self._anim.start()
        self.show()
        self.raise_()

    def hide_tip(self):
        self._anim.stop()
        self._move_anim.stop()
        self._expanding = False
        self._has_svg = False
        self.hide()
        self._label.clear()
        self._label.setPixmap(QPixmap())
        self._pixmap = None


class NodeView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        # 视口更新模式：**不要**改回 FullViewportUpdate。
        # 实测（20 节点场景、同一进程内切换、模拟拖动 40 步的每步墙钟）：
        #   Full 10.02 ms ／ Smart 5.83 ms ／ BoundingRect 4.50 ms ／ **Minimal 1.82 ms**
        # 也就是说 Full 下每次改动都在重画整个视口（含内嵌的 proxy 控件），白扔约 5 倍。
        # 若将来在真机上发现 Minimal 留下描画痕迹，退一步用 BoundingRectViewportUpdate，
        # 但**不要**回到 FullViewportUpdate。
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self._connecting = False
        self._panning = False
        self._pan_start = None
        self._pan_start_scroll = None
        self._rubber_band = None
        self._rubber_band_origin = None
        self._rubber_band_rect = QRect()
        self._drag_ghost = None
        self._highlighted_pw = None
        self._last_image_node = None  # 图纸拖入：记录最后放置的图纸（快速拼合）
        # ---- 悬停详情（满 HOVER_REVEAL_DELAY_S 秒展开；展开后仍跟随；点击穿透；Esc 取消） ----
        self._hover_tip = None        # NodeHoverTip（惰性创建）
        self._hover_item = None       # 当前悬停的目标元素框
        self._hover_enter = 0.0       # 进入时刻（time.monotonic 秒）
        self._hover_stage = 0         # 0无 1thinking 2预渲染 3展示
        self._hover_svg_path = None
        self._hover_pixmap = None
        self._hover_enabled = True    # 总闸（工具栏「悬停详情」；由 FlowEditorDialog 统一设置）
        self.viewport().setMouseTracking(True)
        self._hover_poll = QTimer(self)
        self._hover_poll.setInterval(200)
        self._hover_poll.timeout.connect(self._poll_hover)
        self._hover_poll.start()
        self.setDragMode(QGraphicsView.NoDrag)
        # ---- 连线拖拽时的画板边缘自动滚动（目标节点在画板外时跟随鼠标滚动） ----
        self._edge_last_pos = None    # 最近一次鼠标视口坐标（拖拽期间持续有效）
        self._edge_margin = 48        # 距视口边框多少像素开始滚动
        self._edge_max_step = 26      # 每帧最大滚动像素（约 60fps → 最猛 1560px/s）
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(16)
        self._edge_timer.timeout.connect(self._edge_auto_scroll)

    # ---------- 连线拖拽：边缘自动滚动 ----------
    def _edge_scroll_delta(self, pos):
        """按鼠标视口坐标算出本帧滚动量 (dx, dy)；远离边框时为 (0, 0)。

        越靠近边框滚得越快（线性加速），拖到画板外则按最大速度持续滚动。
        """
        vp = self.viewport().rect()
        margin = max(1, self._edge_margin)
        max_step = self._edge_max_step

        def axis(v, lo, hi):
            if v < lo + margin:
                depth = (lo + margin) - v
                return -int(round(max_step * min(1.0, depth / float(margin))))
            if v > hi - margin:
                depth = v - (hi - margin)
                return int(round(max_step * min(1.0, depth / float(margin))))
            return 0

        return axis(pos.x(), vp.left(), vp.right()), axis(pos.y(), vp.top(), vp.bottom())

    def _update_edge_scroll(self):
        """按当前鼠标位置启停边缘自动滚动定时器。"""
        if not self._connecting or self._edge_last_pos is None:
            self._edge_timer.stop()
            return
        dx, dy = self._edge_scroll_delta(self._edge_last_pos)
        if dx or dy:
            if not self._edge_timer.isActive():
                self._edge_timer.start()
        elif self._edge_timer.isActive():
            self._edge_timer.stop()

    def _stop_edge_scroll(self):
        """连线结束/取消时停止边缘自动滚动并清理状态。"""
        self._edge_last_pos = None
        try:
            self._edge_timer.stop()
        except Exception:
            pass

    def _edge_auto_scroll(self):
        """定时器回调：鼠标停在边框附近时持续推动画板，直到目标节点可见并连上。"""
        if not self._connecting or self._edge_last_pos is None:
            self._edge_timer.stop()
            return
        if not self.isVisible():
            # 视图被隐藏（切选项卡等）→ 停掉，避免后台空转
            self._stop_edge_scroll()
            return
        dx, dy = self._edge_scroll_delta(self._edge_last_pos)
        if not (dx or dy):
            self._edge_timer.stop()
            return
        h_bar = self.horizontalScrollBar()
        v_bar = self.verticalScrollBar()
        old = (h_bar.value(), v_bar.value())
        if dx:
            h_bar.setValue(old[0] + dx)
        if dy:
            v_bar.setValue(old[1] + dy)
        if (h_bar.value(), v_bar.value()) == old:
            return  # 已滚到尽头，不再重画虚线
        # 画板滚动了 → 光标下方的场景点变了，虚线终点必须跟着修正
        try:
            self.scene().update_temp_line(self.mapToScene(self._edge_last_pos))
        except Exception:
            pass

    # ---------- 滚轮缩放 ----------
    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 0.85
            self.scale(factor, factor)
            # 限制缩放范围：防止缩放过小导致嵌入式控件绘制退化（QPainter engine==0）
            s = self.transform().m11()
            if s < 0.02:
                self.scale(0.02 / s, 0.02 / s)
            elif s > 50:
                self.scale(50 / s, 50 / s)
            event.accept()
        else:
            super().wheelEvent(event)

    # ---------- 键盘 ----------
    def _is_editing_text(self):
        """判断当前是否正在编辑文本（代理内或常规窗口内的编辑控件真正持有焦点）。

        注意：点中元素框时 scene.focusItem() 也会是 QGraphicsProxyWidget（代理可聚焦），
        但此时并未编辑文本——因此不能只看 focusItem 类型就拦截按键，必须检查代理内
        实际聚焦的控件是否为 QLineEdit/QTextEdit/QComboBox/QAbstractSpinBox 等文本
        编辑控件；否则 Delete/Backspace/Ctrl+Z 等画布快捷键会被误吞，导致无法删除
        元素框、无法撤销。
        """
        from PySide6.QtWidgets import QAbstractSpinBox
        edit_types = (QLineEdit, QTextEdit, QComboBox, QAbstractSpinBox)
        fi = self.scene().focusItem() if self.scene() else None
        if isinstance(fi, QGraphicsProxyWidget):
            pw = fi.widget()
            if pw is not None:
                fw = pw.focusWidget()
                if fw is not None and isinstance(fw, edit_types):
                    return True
        fw2 = QApplication.focusWidget()
        if fw2 is not None and isinstance(fw2, edit_types):
            return True
        return False

    def keyPressEvent(self, event):
        # 找 FlowEditorDialog
        def _get_editor():
            w = self.window() if self.window() != self else self.parent()
            while w and not isinstance(w, FlowEditorDialog):
                w = w.parent()
            return w

        # 仅当确实在编辑文本时才把按键原样交给嵌入式控件（不拦截 Backspace/Delete、
        # 不吞编辑快捷键）；选中元素框但未编辑文本时，Delete/Backspace/Ctrl+Z 等
        # 画布快捷键照常生效。
        if self._is_editing_text():
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key_Delete or event.key() == Qt.Key_Backspace:
            sel_items = list(self.scene().selectedItems())
            for item in list(sel_items):
                if isinstance(item, ClassNode):
                    self.scene().remove_class(item)
                elif isinstance(item, (APINode, CsvDataNode, DataProcessNode, ContainerNode,
                                       StepperNode, SiteParserNode, ImageNode)):
                    self.scene().remove_node(item)
                elif isinstance(item, ConnectionPath):
                    scene = self.scene()
                    if hasattr(scene, 'remove_connection') and item in scene.connections:
                        scene.remove_connection(item)
                    elif item in scene.connections:
                        scene.connections.remove(item)
                        scene.removeItem(item)
            event.accept()
        elif event.key() == Qt.Key_Z and (event.modifiers() & Qt.ControlModifier
                                          ) and not (event.modifiers() & Qt.ShiftModifier):
            dlg = _get_editor()
            if dlg:
                dlg.undo_mgr.undo()
            event.accept()
        elif event.key() == Qt.Key_Y and (event.modifiers() & Qt.ControlModifier):
            dlg = _get_editor()
            if dlg:
                dlg.undo_mgr.redo()
            event.accept()
        elif event.key() == Qt.Key_S and (event.modifiers() & Qt.ControlModifier):
            dlg = _get_editor()
            if dlg:
                dlg._save_flow()
            event.accept()
        elif event.key() == Qt.Key_A and (event.modifiers() & Qt.ControlModifier):
            for item in self.scene().items():
                if isinstance(item, (APINode, CsvDataNode, DataProcessNode, ContainerNode,
                                     StepperNode, SiteParserNode, ClassNode, ImageNode, ConnectionPath)):
                    item.setSelected(True)
            event.accept()
        elif event.key() == Qt.Key_C and (event.modifiers() & Qt.ControlModifier):
            # Ctrl+C: 复制选中节点
            dlg = _get_editor()
            if dlg:
                dlg._copy_selected_nodes()
            event.accept()
        elif event.key() == Qt.Key_V and (event.modifiers() & Qt.ControlModifier):
            # Ctrl+V: 粘贴节点
            dlg = _get_editor()
            if dlg:
                dlg._paste_nodes()
            event.accept()
        elif event.key() == Qt.Key_Escape:
            # Escape 取消选中 + 取消框选 + 关闭悬停提示
            self._hide_hover_tip()
            self.scene().clearSelection()
            if hasattr(self, '_rubber_band') and self._rubber_band:
                self._rubber_band.hide()
                self._rubber_band.deleteLater()
                self._rubber_band = None
                self._rubber_band_origin = None
                self._rubber_band_rect = QRect()
            event.accept()
        else:
            super().keyPressEvent(event)

    # ---------- 端口检测 ----------
    def _find_port_at(self, scene_pos):
        """查找场景位置对应的端口，返回 (port_fn, obj, param_name_or_None)"""
        for item in self.scene().items():
            if isinstance(item, APINode):
                out = item.outputPort()
                if (out - scene_pos).manhattanLength() < 15:
                    return (item.outputPort, item, None)
                # 参数输入点 + 拼合栏输入点取最近者（避免第二根线误连到主输入点）
                best = None
                best_dist = 15
                for name in item.param_widgets.keys():
                    port = item.param_input_port(name)
                    d = (port - scene_pos).manhattanLength()
                    if d < best_dist:
                        best_dist = d
                        best = (lambda n=item, nm=name: n.param_input_port(nm), item, name)
                for cw in item._concat_widgets:
                    port = item.concat_input_port(cw.concat_id)
                    d = (port - scene_pos).manhattanLength()
                    if d < best_dist:
                        best_dist = d
                        best = (lambda n=item, c=cw.concat_id: n.concat_input_port(c), item, cw.concat_id)
                if best:
                    return best
            elif isinstance(item, DataProcessNode):
                out = item.outputPort()
                if (out - scene_pos).manhattanLength() < 15:
                    return (item.outputPort, item, None)
                inp = item.inputPort()
                if (inp - scene_pos).manhattanLength() < 15:
                    return (item.inputPort, item, None)
            elif isinstance(item, StepperNode):
                out = item.outputPort()
                if (out - scene_pos).manhattanLength() < 15:
                    return (item.outputPort, item, None)
                if (item.inputPort() - scene_pos).manhattanLength() < 15:
                    return (item.inputPort, item, 'main')
                for i, x in enumerate(item._inputs):
                    port = item.input_port(i)
                    if (port - scene_pos).manhattanLength() < 15:
                        return (lambda n=item, k=x['key']: n.input_port_for_key(k), item, x['key'])
            elif isinstance(item, SiteParserNode):
                out = item.outputPort()
                if (out - scene_pos).manhattanLength() < 15:
                    return (item.outputPort, item, None)
                inp = item.inputPort()
                if (inp - scene_pos).manhattanLength() < 15:
                    return (item.inputPort, item, None)
            elif isinstance(item, CsvDataNode):
                # 每列表头一个输出端口
                for h in item.headers:
                    port = item.output_port_for_header(h)
                    if (port - scene_pos).manhattanLength() < 15:
                        return (lambda n=item, hd=h: n.output_port_for_header(hd), item, h)
                # 左侧输入端口（接收上游数据流入本表），param 固定为 'ingest'
                inp = item.inputPort()
                if (inp - scene_pos).manhattanLength() < 15:
                    return (item.inputPort, item, 'ingest')
            elif isinstance(item, ContainerNode):
                inp = item.inputPort()
                if (inp - scene_pos).manhattanLength() < 15:
                    return (item.inputPort, item, None)
                # 根目录输入点（与「选择存储路径」同一行，青色）
                rp = item.root_path_input_port()
                if (rp - scene_pos).manhattanLength() < 15:
                    return (item.root_path_input_port, item, 'root')
                # 规则模式：拼合文本段左侧输入端口
                for pid in list(getattr(item, '_rule_widgets', {}).keys()):
                    port = item.rule_part_input_port(pid)
                    if (port - scene_pos).manhattanLength() < 15:
                        return (lambda n=item, p=pid: n.rule_part_input_port(p), item, pid)
            elif isinstance(item, ClassNode):
                out = item.outputPort()
                if (out - scene_pos).manhattanLength() < 15:
                    return (item.outputPort, item, None)
                if not getattr(item, '_is_main', False):
                    inp = item.inputPort()
                    if (inp - scene_pos).manhattanLength() < 15:
                        return (item.inputPort, item, None)
        return (None, None, None)

    # ---------- 鼠标事件 ----------
    def _hit_test_item(self, scene_pos):
        for item in self.scene().items(scene_pos):
            if isinstance(item, (APINode, DataProcessNode, CsvDataNode, ContainerNode,
                                 StepperNode, SiteParserNode, ClassNode, ImageNode, ConnectionPath)):
                return True
        return False

    def mousePressEvent(self, event):
        self._stop_edge_scroll()   # 任何一次新按下都终止上轮的边缘滚动
        if event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            # 1) 先检查是否点在端口上 → 开始连线
            port_fn, obj, param = self._find_port_at(scene_pos)
            if port_fn and not (isinstance(obj, CsvDataNode) and param == 'ingest'):
                # 数据库框的输入端口只作为连线终点（不能从这里反向拉线）
                self.scene().start_connection(port_fn, obj, param)
                self._connecting = True
                self._edge_last_pos = event.pos()
                return
            # 2) Ctrl+点击 → 切换选中（交给场景处理）
            if event.modifiers() & Qt.ControlModifier:
                super().mousePressEvent(event)
                return
            # 3) 点击在空白处 → 开始框选
            if not self._hit_test_item(scene_pos):
                if not (event.modifiers() & Qt.ControlModifier):
                    self.scene().clearSelection()
                # 点击空白处脱离文本编辑：清除嵌入式文本框的场景焦点，
                # 之后 Backspace/Delete 才可删改元素框
                self.scene().clearFocus()
                self._rubber_band_origin = event.pos()
                self._rubber_band_rect = QRect()
                self._rubber_band = QRubberBand(QRubberBand.Rectangle, self)
                self._rubber_band.setGeometry(QRect(event.pos(), QSize()))
                self._rubber_band.show()
                event.accept()
                return
        elif event.button() == Qt.MiddleButton:
            # 中键 → 开始平移
            self._panning = True
            self._pan_start = event.pos()
            self._pan_start_scroll = (self.horizontalScrollBar().value(),
                                      self.verticalScrollBar().value())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    # ---------- 悬停 SVG 提示 ----------
    def _hover_target_at(self, pos):
        """返回 viewport 坐标 pos 命中的元素框（子项/代理向上找父）。"""
        if not self.scene():
            return None
        sp = self.mapToScene(pos)
        for it in self.scene().items(sp):
            node = it
            while node is not None and not isinstance(node, _NODE_HOVER_TYPES):
                node = node.parentItem()
            if node is not None:
                return node
        return None

    def _svg_file_for(self, item):
        for cls, fname in _NODE_HOVER_SVG:
            if isinstance(item, cls):
                return fname
        return None

    # ---------- 悬停提示的端口清单 ----------
    @staticmethod
    def _hover_port_point(fn):
        """调端口函数取场景坐标；异常 / 空点（端口不存在）统一返回 None。"""
        try:
            p = fn()
        except Exception:
            return None
        if isinstance(p, QPointF) and not p.isNull():
            return p
        return None

    def _hover_upstream_text(self, item, param):
        """输入端口的上游来源描述；没连线时明确写「未连线」。"""
        scene = self.scene()
        for c in (getattr(scene, 'connections', None) or []):
            if c.end_obj is not item or c.end_param != param:
                continue
            src = getattr(c, 'start_obj', None)
            if src is None:
                continue
            name = type(src).__name__
            for cls, label in _HOVER_NODE_NAMES:
                if isinstance(src, cls):
                    name = label
                    break
            sp = getattr(c, 'start_param', None)
            return f'← {name}·{_hover_truncate(sp, 10)}' if sp else f'← {name}'
        return '（未连线）'

    def _hover_rule_part_value(self, container, part_id, cw):
        """容器规则段输入口实际喂进去的值：优先取上游数据处理框的处理结果。"""
        scene = self.scene()
        for c in (getattr(scene, 'connections', None) or []):
            if c.end_obj is not container or c.end_param != part_id:
                continue
            src = getattr(c, 'start_obj', None)
            if isinstance(src, DataProcessNode):
                return (getattr(src, 'processed_data', '') or
                        getattr(src, 'source_data', '') or '（空）')
        try:
            txt = cw.edit.text()
        except Exception:
            txt = ''
        return txt or '（未设置）'

    def _hover_port_rows(self, item):
        """按 _find_port_at 的端口目录，现读一遍每个端口的键名与当前值。"""
        C = _HOVER_PORT_COLORS
        rows = []

        def add(side, color, key, value, fn, note=''):
            p = self._hover_port_point(fn)
            rows.append({'side': side, 'color': color, 'key': str(key),
                         'value': str(value), 'note': str(note or ''),
                         'y': (p.y() if p is not None else None)})

        if isinstance(item, APINode):
            # 图源配置里给每个参数写的「注释」→ 悬停框里跟在参数行后面的提示
            _notes = {}
            for _p in (getattr(item, 'params', None) or []):
                if isinstance(_p, dict):
                    _nm = str(_p.get('name') or '')
                    _nt = str(_p.get('note') or '').strip()
                    if _nm and _nt:
                        _notes[_nm] = _nt
            for name in list(getattr(item, 'param_widgets', None) or {}):
                w = item.param_widgets[name]
                try:
                    val = w.edit.text()
                except Exception:
                    val = ''
                add('in', C['input'], name, val or '（空）',
                    lambda n=name: item.param_input_port(n),
                    note=_notes.get(str(name), ''))
            for cw in list(getattr(item, '_concat_widgets', None) or []):
                if isinstance(cw, dict):
                    cid = cw.get('concat_id') or cw.get('id') or '拼合'
                    val = cw.get('concat_value') or cw.get('value') or \
                        cw.get('source_path') or ''
                else:
                    cid = getattr(cw, 'concat_id', None) or '拼合'
                    val = getattr(cw, 'concat_value', '') or \
                        getattr(cw, 'source_path', '') or ''
                add('in', C['concat'], f'拼合栏{cid}',
                    f'+{val}' if val else '（等上游拼接）',
                    lambda c=cid: item.concat_input_port(c))
            add('out', C['output'], '响应', 'JSON / 二进制（按内容类型判定）',
                item.outputPort)

        elif isinstance(item, DataProcessNode):
            add('in', C['input'], '输入',
                getattr(item, 'source_data', '') or
                self._hover_upstream_text(item, None), item.inputPort)
            add('out', C['output'], '处理后',
                getattr(item, 'processed_data', '') or '（未处理）', item.outputPort)

        elif isinstance(item, StepperNode):
            add('in', C['input'], 'main', self._hover_upstream_text(item, 'main'),
                item.inputPort)
            for i, x in enumerate(list(getattr(item, '_inputs', None) or [])):
                add('in', C['input'], x.get('key', f'#{i}'), x.get('value', ''),
                    lambda k=i: item.input_port(k))
            add('out', C['output'], '数值输出', getattr(item, 'output_value', 0.0),
                item.outputPort)

        elif isinstance(item, SiteParserNode):
            url = ''
            try:
                url = item.get_url()
            except Exception:
                url = ''
            url = url or getattr(item, 'url', '')
            add('in', C['input'], 'URL',
                url or self._hover_upstream_text(item, None), item.inputPort)
            add('out', C['output'], '文件', '下载流 → 下游容器', item.outputPort)

        elif isinstance(item, CsvDataNode):
            add('in', C['input'], 'ingest',
                f'上游写入 · 共 {getattr(item, "_row_count", 0)} 行', item.inputPort)
            for h in list(getattr(item, 'headers', None) or []):
                try:
                    v = item.get_current_value(h)
                except Exception:
                    v = ''
                add('out', C['column'], h, v if v != '' else '（空）',
                    lambda hh=h: item.output_port_for_header(hh))

        elif isinstance(item, ContainerNode):
            add('in', C['input'], '(主输入)', self._hover_upstream_text(item, None),
                item.inputPort)
            rootv = getattr(item, 'dyn_root', '') or ''
            add('in', C['root'] if rootv else C['root_idle'], 'root',
                f'{rootv}（覆盖根目录名）' if rootv else '（未接）用配置的存储路径',
                item.root_path_input_port)
            for pid in list(getattr(item, '_rule_widgets', None) or {}):
                cw = item._rule_widgets[pid]
                add('in', C['concat'], pid,
                    self._hover_rule_part_value(item, pid, cw),
                    lambda p=pid: item.rule_part_input_port(p))

        elif isinstance(item, ClassNode):
            if not getattr(item, '_is_main', False):
                add('in', C['class_in'], '触发输入',
                    self._hover_upstream_text(item, None), item.inputPort)
            add('out', C['class_out'], '触发输出',
                f"{len(getattr(item, '_members', None) or [])} 个成员", item.outputPort)

        elif isinstance(item, ImageNode):
            rows.append({'side': 'note', 'color': C['note'], 'key': '无端口',
                         'value': '图片节点是参考元素，不参与连线', 'y': None})

        # 每个方向内部按端口在框上的真实纵向位置排序（拿不到坐标就保持登记顺序）
        ordered = []
        for side in ('in', 'out'):
            grp = [r for r in rows if r['side'] == side]
            if grp and all(r['y'] is not None for r in grp):
                grp.sort(key=lambda r: r['y'])
            ordered.extend(grp)
        ordered.extend(r for r in rows if r['side'] == 'note')
        return ordered

    @staticmethod
    def _hover_api_desc(item):
        """按 url_path 从图源配置（api_ref.endpoints）里取出该子端口的说明。

        说明与参数注释都写在**图源配置**里（子端口编辑器 → 描述 / 参数表格 → 注释），
        节点上已经带着 `api_ref`（含 endpoints）与 `params`（含 note），所以这里不需要
        额外序列化任何东西，老图纸也能直接显示。
        """
        try:
            ref = getattr(item, 'api_ref', None)
            if not isinstance(ref, dict):
                return ''
            path = str(getattr(item, 'url_path', '') or '')
            for ep in (ref.get('endpoints') or []):
                if isinstance(ep, dict) and str(ep.get('path') or '') == path:
                    return str(ep.get('description') or '').strip()
        except Exception:
            pass
        return ''

    def _hover_ports_svg(self, rows, width, top, row_h, desc=''):
        """生成端口区 SVG，返回 (svg文本, 端口区高度)。

        desc 非空时在端口清单**上方**多渲染一行「子端口说明」（来自图源配置的描述），
        端口区随之加高一行 —— 画布加高走的是已有的 {{tail_dy}} 机制，不用改任何 SVG 模板。
        """
        ins = [r for r in rows if r['side'] == 'in']
        outs = [r for r in rows if r['side'] == 'out']
        notes = [r for r in rows if r['side'] == 'note']
        n = max(len(ins), len(outs), 1 if notes else 0)
        hidden = max(0, n - _HOVER_PORT_MAX_ROWS)
        if hidden:
            n = _HOVER_PORT_MAX_ROWS
        head = _HOVER_PORT_HEAD_H if (ins and outs) else 0
        shown = n + (1 if hidden else 0)
        desc = (desc or '').strip()
        desc_h = row_h if desc else 0

        def _note_of(r):
            return str(r.get('note') or '').strip() if r is not None else ''

        # 带注释的端口行下面再挂一行小字（图源配置「注释」列）。
        # 为什么不让注释跟在值后面：一行里「键 + 值 + 注释」在 440px 宽度下根本放不下
        # （例：creator_id = 102465161 ——作者唯一标识符ID ≈ 242px，可用只有 178px），
        # 硬塞只会把注释截成两三个字。所以注释独占一行，只有带注释的行才加高。
        note_h = max(11, int(row_h * 0.78))
        row_heights = []
        for i in range(shown):
            h = row_h
            if i < n:
                if i < len(ins) and _note_of(ins[i]):
                    h = max(h, row_h + note_h)
                if i < len(outs) and _note_of(outs[i]):
                    h = max(h, row_h + note_h)
            row_heights.append(h)
        band_h = desc_h + head + sum(row_heights)

        parts = []
        if desc:
            # 子端口说明：整行居中，样式沿用 portnote（灰、小号）
            parts.append(
                f'<text x="{width / 2:.0f}" y="{top + row_h * 0.5 + 4:.0f}" '
                f'class="portnote" text-anchor="middle">📖 '
                f'{_hover_escape(_hover_fit_text(desc, width - 48))}</text>')
        top += desc_h
        if head:
            parts.append(f'<text x="20" y="{top + 11}" class="portnote">'
                         f'◀ 输入端口</text>')
            parts.append(f'<text x="{width - 20}" y="{top + 11}" class="portnote" '
                         f'text-anchor="end">输出端口 ▶</text>')
        cap = max(10, int((width / 2 - 42) / 6.3))

        def row_svg(r, side, cy, h):
            key = _hover_truncate(r['key'], 16)
            val = _hover_truncate(r['value'] or '—', max(5, cap - len(key) - 3))
            line = (f'<tspan class="portkey">{_hover_escape(key)}</tspan>'
                    f'<tspan class="portval"> = {_hover_escape(val)}</tspan>')
            note = _note_of(r)
            if side == 'in':
                out = (f'<circle cx="20" cy="{cy:.0f}" r="6" fill="{r["color"]}" '
                       f'stroke="#e8e8e8" stroke-width="1"/>'
                       f'<text x="34" y="{cy + 4:.0f}" class="portkey">{line}</text>')
                if note:
                    out += (f'<text x="34" y="{cy + 4 + note_h:.0f}" class="portnote">'
                            f'——{_hover_escape(_hover_fit_text(note, width - 70))}'
                            f'</text>')
                return out
            out = (f'<circle cx="{width - 20}" cy="{cy:.0f}" r="6" '
                   f'fill="{r["color"]}" stroke="#e8e8e8" stroke-width="1"/>'
                   f'<text x="{width - 34}" y="{cy + 4:.0f}" class="portkey" '
                   f'text-anchor="end">{line}</text>')
            if note:
                out += (f'<text x="{width - 34}" y="{cy + 4 + note_h:.0f}" '
                        f'class="portnote" text-anchor="end">'
                        f'——{_hover_escape(_hover_fit_text(note, width - 70))}</text>')
            return out

        cy_top = top + head
        for i in range(shown):
            h = row_heights[i]
            cy = cy_top + h * 0.5
            if i < n:
                if i < len(ins):
                    parts.append(row_svg(ins[i], 'in', cy, h))
                if i < len(outs):
                    parts.append(row_svg(outs[i], 'out', cy, h))
                if not ins and not outs and notes and i == 0:
                    r = notes[0]
                    parts.append(
                        f'<circle cx="20" cy="{cy:.0f}" r="6" fill="none" '
                        f'stroke="{r["color"]}" stroke-width="1" '
                        f'stroke-dasharray="3,2"/>'
                        f'<text x="34" y="{cy + 4:.0f}" class="portkey">'
                        f'<tspan class="portkey">{_hover_escape(r["key"])}</tspan>'
                        f'<tspan class="portval"> — {_hover_escape(r["value"])}'
                        f'</tspan></text>')
            else:
                parts.append(f'<text x="{width / 2:.0f}" y="{cy + 4:.0f}" '
                             f'class="portnote" text-anchor="middle">'
                             f'… 另有 {hidden} 个端口未列出（见框上的实际端口）</text>')
            cy_top += h
        return ''.join(parts), band_h

    def _apply_hover_ports(self, svg, item):
        """把模板里的 <!-- HOVER:PORTS --> 标记展开成真实端口清单，并撑高整张图。"""
        m = _HOVER_PORTS_RE.search(svg)
        if not m:
            return svg
        top = int(m.group(1))
        tail = int(m.group(2))
        row_h = int(m.group(3) or _HOVER_PORT_ROW_H)
        wm = re.search(r'<svg\b[^>]*?\bwidth="([\d.]+)"', svg, re.S)
        width = float(wm.group(1)) if wm else 400.0
        band, band_h = self._hover_ports_svg(
            self._hover_port_rows(item), width, top, row_h,
            desc=self._hover_api_desc(item))
        dy = band_h - (tail - top)
        svg = svg[:m.start()] + band + svg[m.end():]
        svg = svg.replace('{{tail_dy}}', f'{dy:.0f}')
        return _hover_grow_canvas(svg, dy)

    def _fill_svg(self, svg, item):
        """把 SVG 中的 {{token}} 替换为元素框的真实信息（类框除外，保持静态）。"""
        tok = {}
        if isinstance(item, APINode):
            method = getattr(item, 'method', None) or 'GET'
            url = getattr(item, 'url_path', None) or '/'
            # 请求入口来源：api_ref.base_url（图源入口地址），旧节点无则回退 (未设置)
            ref = getattr(item, 'api_ref', None) or {}
            base = ''
            if isinstance(ref, dict):
                base = (ref.get('base_url', '') or '').strip()
            if not base:
                base = (getattr(item, 'api_base', '') or '').strip()
            tok['method'] = _hover_escape(method)
            tok['url'] = _hover_escape(_hover_truncate(url, 34))
            tok['entry'] = _hover_escape(_hover_truncate(base or '(未设置)', 46))
            pname, pval = '', ''
            params = getattr(item, 'params', None) or []
            if params:
                pname = params[0].get('name', '')
                pval = params[0].get('value', '')
            elif getattr(item, 'param_widgets', None):
                name = next(iter(item.param_widgets))
                pname = name
                try:
                    pval = item.param_widgets[name].edit.text()
                except Exception:
                    pval = ''
            tok['param'] = _hover_escape(
                _hover_truncate(f'{pname} = {pval}' if pname else '无参数', 34))
            cws = getattr(item, '_concat_widgets', None) or []
            if cws:
                cw = cws[0]
                # ConcatEditWidget 对象（运行时的 _concat_widgets）与 dict（旧数据）兼容
                if isinstance(cw, dict):
                    txt = cw.get('value', '') or cw.get('source_path', '') or ''
                else:
                    try:
                        txt = (getattr(cw, 'concat_value', '')
                               or getattr(cw, 'source_path', '') or '')
                    except Exception:
                        txt = ''
                tok['concat'] = _hover_escape(
                    _hover_truncate(f'+ 拼合: {txt}' if txt else '+ 拼合: ...', 34))
            else:
                tok['concat'] = _hover_escape('无拼合栏')
        elif isinstance(item, DataProcessNode):
            tok['source'] = _hover_escape(
                _hover_truncate(getattr(item, 'source_data', '') or '', 22))
            tok['regex'] = _hover_escape(
                _hover_truncate(getattr(item, 'regex', '') or '', 22)) or '(无正则)'
            repl = ''
            if hasattr(item, 'replace_edit') and item.replace_edit:
                try:
                    repl = item.replace_edit.text()
                except Exception:
                    repl = ''
            tok['replace'] = _hover_escape(
                _hover_truncate(repl, 22)) or '(无替换)'
            tok['processed'] = _hover_escape(
                _hover_truncate(getattr(item, 'processed_data', '') or '', 22))
        elif isinstance(item, CsvDataNode):
            tok['file'] = _hover_escape(_hover_truncate(
                os.path.basename(getattr(item, 'file_path', '') or '') or '未选择文件', 24))
            tok['rows'] = str(getattr(item, '_row_count', 0))
            tok['row'] = str((getattr(item, 'current_row', 0) or 0) + 1)
            hs = getattr(item, 'headers', None) or []
            f0 = hs[0] if len(hs) > 0 else ''
            f1 = hs[1] if len(hs) > 1 else ''
            v0 = v1 = ''
            if f0:
                try:
                    v0 = item.get_current_value(f0)
                except Exception:
                    v0 = ''
            if f1:
                try:
                    v1 = item.get_current_value(f1)
                except Exception:
                    v1 = ''
            tok['field0_port'] = _hover_escape(f0 or '-')
            tok['field1_port'] = _hover_escape(f1 or '-')
            tok['field0'] = _hover_escape(
                _hover_truncate(f'{f0} = {v0}' if f0 else '无字段', 30))
            tok['field1'] = _hover_escape(
                _hover_truncate(f'{f1} = {v1}' if f1 else '—', 30))
        elif isinstance(item, ContainerNode):
            tok['path'] = _hover_escape(_hover_truncate(
                getattr(item, 'storage_path', '') or '(未设置)', 26))
            tok['files'] = str(getattr(item, 'total_files', 0))
            tok['size'] = _hover_fmt_size(getattr(item, 'stored_bytes', 0))
            if getattr(item, '_rule_enabled', False):
                cp = getattr(item, '_rule_creation_path', '') or '$.id'
                tok['rule'] = _hover_escape(f'规则模式: 创建时机 = {cp}')
            else:
                tok['rule'] = _hover_escape('一般模式: 直接存入根目录')
        elif isinstance(item, StepperNode):
            formula = getattr(item, 'formula', '') or \
                f'{{步数}} * {getattr(item, "step_len", 1.0)}'
            tok['formula'] = _hover_escape(_hover_truncate(formula, 26))
            ins = getattr(item, '_inputs', None) or []
            if ins:
                i0 = ins[0]
                i1 = ins[1] if len(ins) > 1 else None
                tok['input0'] = _hover_escape(
                    f"{i0.get('key', '')} = {i0.get('value', '')}" if i0.get('key') else '无输入')
                tok['input1'] = _hover_escape(
                    f"{i1.get('key', '')} = {i1.get('value', '')}" if i1 and i1.get('key') else '—')
            else:
                tok['input0'] = _hover_escape('无输入')
                tok['input1'] = _hover_escape('—')
            tok['output'] = _hover_escape(str(getattr(item, 'output_value', 0.0)))
            tok['step'] = str(getattr(item, '_step_count', 1))
        elif isinstance(item, SiteParserNode):
            tok['parser'] = _hover_escape(getattr(item, 'parser_name', '') or '未识别')
            url = ''
            if hasattr(item, 'get_url'):
                try:
                    url = item.get_url()
                except Exception:
                    url = ''
            tok['url'] = _hover_escape(_hover_truncate(url or getattr(item, 'url', ''), 40))
        elif isinstance(item, ImageNode):
            tok['filename'] = _hover_escape(_hover_truncate(
                os.path.basename(getattr(item, 'image_path', '') or '') or '图片', 26))
        elif isinstance(item, ClassNode):
            name = getattr(item, 'class_name', '') or ('main' if getattr(item, '_is_main', False) else 'class')
            tok['class_name'] = _hover_escape(_hover_truncate(name, 24))
            tok['poll_count'] = str(getattr(item, 'poll_count', 1))
            tok['member_count'] = str(len(getattr(item, '_members', None) or []))
        # ConnectionPath：保持静态示意，不填充
        for k, v in tok.items():
            svg = svg.replace('{{' + k + '}}', str(v))
        # 端口清单独立成段：位置/键名/当前值都取自「框被拉开那一刻」的真实运行态。
        # 这里失败只丢端口清单，不影响整张提示图（_render_svg_pixmap 会兜底）。
        try:
            svg = self._apply_hover_ports(svg, item)
        except Exception:
            pass
        return svg

    def _render_svg_pixmap(self, item):
        """读取元素框对应 SVG → 动态填充真实信息 → 渲染为 QPixmap。"""
        fname = self._svg_file_for(item)
        if not fname:
            return None
        path = os.path.join(_project_root(),
                            'data', 'node_svg', fname)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                svg = f.read()
            svg = self._fill_svg(svg, item)
            return self._load_svg_pixmap(fname, svg_text=svg)
        except Exception:
            return None

    def _load_svg_pixmap(self, fname, svg_text=None):
        """渲染 SVG（svg_text 给出则渲染内容，否则读 data/node_svg/<fname>）。"""
        path = os.path.join(_project_root(),
                            'data', 'node_svg', fname)
        if not os.path.exists(path) and svg_text is None:
            return None
        try:
            from PySide6.QtSvg import QSvgRenderer
            if svg_text is not None:
                renderer = QSvgRenderer(svg_text.encode('utf-8'))
            else:
                renderer = QSvgRenderer(path)
            sz = renderer.defaultSize()
            w, h = sz.width(), sz.height()
            if w <= 0 or h <= 0:
                w, h = 400, 300
            max_w, max_h = 480, 360
            if w > max_w:
                h = int(h * max_w / w)
                w = max_w
            if h > max_h:
                w = int(w * max_h / h)
                h = max_h
            pm = QPixmap(w, h)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            renderer.render(p, QRectF(0, 0, w, h))
            p.end()
            return pm
        except Exception:
            return None

    def _hover_screen_pos(self):
        gp = QCursor.pos()
        return gp.x() + 18, gp.y() + 18

    def _start_hover(self, item):
        """进入悬停：显示 thinking 文本框（0~4s 阶段）并平滑跟随鼠标。

        元素框间快速切换时若 thinking 框仍在显示，则平滑滑到新锚点并重新计时。
        """
        self._hover_item = item
        self._hover_enter = time.monotonic()
        self._hover_stage = 1
        self._hover_svg_path = self._svg_file_for(item)
        self._hover_pixmap = None
        if self._hover_tip is None:
            self._hover_tip = NodeHoverTip()
        tip = self._hover_tip
        x, y = self._hover_screen_pos()
        if tip.isVisible() and not tip._has_svg:
            # 不同元素框间快速切换：thinking 内容不变，平滑滑到新锚点
            tip.animate_move_to(x, y)
        else:
            tip.show_thinking(x, y)

    def _hide_hover_tip(self):
        self._hover_item = None
        self._hover_stage = 0
        self._hover_pixmap = None
        if self._hover_tip is not None:
            self._hover_tip.hide_tip()

    # ---------- 悬停详情总闸 ----------
    def set_hover_enabled(self, on):
        """悬停详情总闸：关掉时**停掉 200 ms 轮询**并收掉已展开的提示。

        只加 guard 不停定时器是不够的：轮询里每轮还会调 `QApplication.topLevelAt()`
        （窗口系统查询），功能关掉就该一分钱不花。
        """
        self._hover_enabled = bool(on)
        if self._hover_enabled:
            self._hover_poll.start()
        else:
            self._hover_poll.stop()
            self._hide_hover_tip()

    def is_hover_enabled(self):
        return bool(getattr(self, '_hover_enabled', True))

    def _hover_window_active(self):
        """悬停提示仅在本窗口可见且为活动窗口、鼠标确在本窗口上时触发，
        防止隔着其他应用窗口仍弹出提示。

        浮窗现在是**点击穿透**的（`Qt.WindowTransparentForInput`）：鼠标事件直接落到下层
        窗口，所以这里只需要看「鼠标最顶层窗口是不是本窗口」。下面仍保留一段 tip 兜底
        判断 —— 万一某个平台的 `topLevelAt` 仍返回本浮窗，也不会误判成「鼠标在别的窗口上」
        而把提示关掉。
        """
        win = self.window()
        if win is None or not win.isVisible() or not win.isActiveWindow():
            return False
        gp = QCursor.pos()
        try:
            top = QApplication.topLevelAt(gp)
        except Exception:
            return True      # 平台查询失败时不阻断（宁可显示，也别让整块功能失效）
        if top is None or top is win:
            return True
        tip = self._hover_tip
        if tip is not None:
            try:
                if top is tip or tip.isAncestorOf(top):
                    return True   # 兜底：鼠标确实落在展示浮窗（或其子控件）上
            except Exception:
                pass
        return False

    def _poll_hover(self, pos=None):
        """轮询/鼠标移动时推进悬停提示阶段。pos=None 时取全局鼠标位置。

        阶段：stage1/2 thinking（满 HOVER_REVEAL_DELAY_S 秒）→ stage3 拉开展示 SVG；
        **展开后仍跟随鼠标**，鼠标不在本框上（或跑到别的框上）就立即收 / 换目标。
        浮窗是点击穿透的，所以「鼠标落在浮窗上」不需要任何特殊处理（见 NodeHoverTip）。
        """
        if not getattr(self, '_hover_enabled', True):
            return
        if not self.scene() or not self.isVisible():
            return
        if not self._hover_window_active():
            # 窗口被遮挡/非活动/鼠标在其他窗口上：立即隐藏提示
            if self._hover_item is not None:
                self._hide_hover_tip()
            return
        tip = self._hover_tip
        if pos is None:
            pos = self.viewport().mapFromGlobal(QCursor.pos())
        in_viewport = self.viewport().rect().contains(pos)

        # ---- stage3：SVG 已展示 → 继续跟随鼠标；离开元素框立即收 ----
        if self._hover_stage >= 3 and self._hover_item is not None:
            on_item = self._hover_target_at(pos) if in_viewport else None
            if on_item is self._hover_item:
                # 仍停留在原触发节点上 → 保持展示，并跟着鼠标走（点击穿透，挡不住操作）
                if tip is not None and tip.isVisible():
                    x, y = self._hover_screen_pos()
                    tip.animate_move_to(x, y)
                return
            if on_item is not None:
                # 鼠标移到另一元素框 → 结束当前展示，开始该元素的新一轮悬停
                self._hide_hover_tip()
                self._start_hover(on_item)
                return
            self._hide_hover_tip()   # 鼠标离开所有元素框 → 立即收
            return

        # ---- stage1/2：thinking 阶段（需鼠标在画布内）----
        if not in_viewport:
            if self._hover_item is not None:
                self._hide_hover_tip()
            return
        item = self._hover_target_at(pos)
        if item is not self._hover_item:
            if item is not None:
                self._start_hover(item)
            else:
                self._hide_hover_tip()
            return
        if self._hover_item is None:
            return
        elapsed = time.monotonic() - self._hover_enter
        if elapsed >= HOVER_REVEAL_DELAY_S:
            # 到点：预渲染 SVG（若尚未）→ 拉开展示框（展开结束仍跟随鼠标）
            if self._hover_stage < 2:
                self._hover_stage = 2
                self._hover_pixmap = self._render_svg_pixmap(self._hover_item)
            if self._hover_stage == 2 and self._hover_pixmap is not None:
                self._hover_stage = 3
                if tip is not None:
                    pm = self._hover_pixmap
                    x, y = self._hover_screen_pos()
                    tip.expand_to(x, y, pm.width() + 20, pm.height() + 20, pm)
            return
        # 到点前：thinking 框平滑跟随鼠标锚点
        if tip is not None and tip.isVisible():
            x, y = self._hover_screen_pos()
            tip.animate_move_to(x, y)

    def mouseMoveEvent(self, event):
        # 悬停详情即时推进（连线/框选/平移时不干扰，且把已展开的提示收掉 ——
        # 它会跟着鼠标跑到正要连的目标上，虽然点击穿透，但视觉上挡路）
        _busy = (self._connecting or
                 (hasattr(self, '_rubber_band') and self._rubber_band) or
                 self._panning)
        if _busy:
            if self._hover_item is not None:
                self._hide_hover_tip()
        else:
            self._poll_hover(event.pos())
        if self._connecting:
            self._edge_last_pos = event.pos()
            self.scene().update_temp_line(self.mapToScene(event.pos()))
            # 靠近/超出画板边框 → 启动边缘自动滚动，直到目标节点进入视野
            self._update_edge_scroll()
            event.accept()
            return
        # 框选进行中
        if hasattr(self, '_rubber_band') and self._rubber_band and self._rubber_band_origin is not None:
            self._rubber_band_rect = QRect(self._rubber_band_origin, event.pos()).normalized()
            self._rubber_band.setGeometry(self._rubber_band_rect)
            event.accept()
            return
        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            h_bar = self.horizontalScrollBar()
            v_bar = self.verticalScrollBar()
            h_bar.setValue(self._pan_start_scroll[0] - delta.x())
            v_bar.setValue(self._pan_start_scroll[1] - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._connecting:
            self._stop_edge_scroll()
            scene_pos = self.mapToScene(event.pos())
            end_fn, end_obj, end_param = self._find_port_at(scene_pos)
            if end_fn:
                self.scene().finish_connection(end_fn, end_obj, end_param)
            else:
                self.scene().clear_temp()
            self._connecting = False
            event.accept()
            return
        # 框选结束 → 筛选完全包含的元素
        if hasattr(self, '_rubber_band') and self._rubber_band:
            self._rubber_band.hide()
            self._rubber_band.deleteLater()
            self._rubber_band = None
            if self._rubber_band_rect is not None and self._rubber_band_rect.isValid():
                # 转换矩形到场景坐标
                tl = self.mapToScene(self._rubber_band_rect.topLeft())
                br = self.mapToScene(self._rubber_band_rect.bottomRight())
                rubber_scene_rect = QRectF(tl, br)
                # 遍历所有可选元素，仅保留完全被框住的对象
                for item in self.scene().items():
                    if isinstance(item, (APINode, DataProcessNode, CsvDataNode,
                                         ContainerNode, StepperNode, SiteParserNode,
                                         ClassNode, ImageNode)):
                        if rubber_scene_rect.contains(item.sceneBoundingRect()):
                            item.setSelected(True)
                    elif isinstance(item, ConnectionPath):
                        # 连线：检查路径是否完全在框内
                        path_rect = item.shape().boundingRect().translated(item.pos())
                        if rubber_scene_rect.contains(path_rect):
                            item.setSelected(True)
                # ---- 类嵌套方案：空白处框选 → 打包成类 ----
                # 按住 Shift 框选 = 仅选择不建类（用于多选/删除/移动）
                if not (event.modifiers() & Qt.ShiftModifier):
                    sel_nodes = [it for it in self.scene().selectedItems()
                                 if isinstance(it, (APINode, DataProcessNode, CsvDataNode,
                                                    ContainerNode, StepperNode,
                                                    SiteParserNode, ImageNode))]
                    if sel_nodes:
                        self.scene().create_class_from_nodes(sel_nodes)
            self._rubber_band_origin = None
            self._rubber_band_rect = QRect()
            event.accept()
            return
        if self._panning:
            self._panning = False
            self._pan_start = None
            self._pan_start_scroll = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---------- 拖拽（JSON路径 + API端点 + 输出树） ----------
    def _show_drag_ghost(self, scene_pos, width=200, height=100):
        """在拖拽位置显示半透明虚影"""
        self._hide_drag_ghost()
        ghost = QGraphicsRectItem(0, 0, width, height)
        ghost.setPen(QPen(QColor(0, 200, 255, 180), 2, Qt.DashLine))
        ghost.setBrush(QColor(0, 200, 255, 40))
        ghost.setZValue(999)
        ghost.setPos(scene_pos.x() - width/2, scene_pos.y() - height/2)
        self.scene().addItem(ghost)
        self._drag_ghost = ghost

    def _show_image_ghost(self, scene_pos, pixmap, max_w=200):
        """图纸拖入：预渲染原图虚影（保持比例）判断绘制大小。"""
        self._hide_drag_ghost()
        if pixmap is None or pixmap.isNull():
            return
        h = max(30, int(max_w * pixmap.height() / max(1, pixmap.width())))
        pm = pixmap.scaled(max_w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ghost = QGraphicsPixmapItem(pm)
        ghost.setOpacity(0.55)
        ghost.setZValue(999)
        ghost.setPos(scene_pos.x() - pm.width() / 2, scene_pos.y() - pm.height() / 2)
        self.scene().addItem(ghost)
        self._drag_ghost = ghost

    def _show_flow_ghost(self, scene_pos, w, h, name=''):
        """流程图(.wbt 图纸)拖入：按内容范围渲染虚线虚影 + 流程名标签。"""
        self._hide_drag_ghost()
        w, h = max(40, w), max(30, h)
        ghost = QGraphicsRectItem(0, 0, w, h)
        ghost.setPen(QPen(QColor(0, 220, 120, 220), 2, Qt.DashLine))
        ghost.setBrush(QColor(0, 220, 120, 30))
        ghost.setZValue(999)
        ghost.setPos(scene_pos.x() - w / 2, scene_pos.y() - h / 2)
        self.scene().addItem(ghost)
        self._drag_ghost = ghost
        if name:
            lbl = QGraphicsTextItem(name)
            lbl.setDefaultTextColor(QColor(0, 220, 120))
            lbl.setZValue(1000)
            lbl.setPos(ghost.pos().x() + 6, ghost.pos().y() + 6)
            self.scene().addItem(lbl)
            self._drag_ghost_label = lbl

    def _hide_drag_ghost(self):
        if hasattr(self, '_drag_ghost') and self._drag_ghost:
            if self._drag_ghost.scene():
                self._drag_ghost.scene().removeItem(self._drag_ghost)
            self._drag_ghost = None
        if hasattr(self, '_drag_ghost_label') and self._drag_ghost_label:
            if self._drag_ghost_label.scene():
                self._drag_ghost_label.scene().removeItem(self._drag_ghost_label)
            self._drag_ghost_label = None

    def _clear_target_highlight(self):
        """清除所有目标高亮"""
        edit = getattr(self, '_highlighted_edit', None)
        if edit is not None:
            try:
                edit.setStyleSheet("")
            except RuntimeError:
                pass
            self._highlighted_edit = None

    def _find_drop_target(self, scene_pos):
        """查找场景位置下的拖放目标。

        返回 (type, node, widget)：
        - ('param', APINode, ParamEditWidget 的 QLineEdit)
        - ('concat', APINode, ConcatEditWidget 的 QLineEdit)
        - ('rule_part', ContainerNode, RulePartWidget 的 QLineEdit)
        - ('rule_creation', ContainerNode, 创建时机 QLineEdit)
        - (None, None, None)
        """
        for item in self.scene().items():
            if isinstance(item, QGraphicsProxyWidget):
                proxy = item
                local = proxy.mapFromScene(scene_pos)
                widget = proxy.widget()
                if widget and widget.rect().contains(local.toPoint()):
                    child = widget.childAt(local.toPoint())
                    if isinstance(child, QLineEdit):
                        pw = child.parent()
                        while pw:
                            if isinstance(pw, ParamEditWidget):
                                return ('param', proxy.parentItem(), pw.edit)
                            if isinstance(pw, ConcatEditWidget):
                                return ('concat', proxy.parentItem(), pw.edit)
                            if isinstance(pw, RulePartWidget):
                                return ('rule_part', proxy.parentItem(), pw.edit)
                            pw = pw.parent()
                        # 创建时机行：child 是容器节点的 _rule_creation_edit
                        parent_item = proxy.parentItem()
                        if isinstance(parent_item, ContainerNode):
                            rc = getattr(parent_item, '_rule_creation_edit', None)
                            if rc is child:
                                return ('rule_creation', parent_item, rc)
        return (None, None, None)

    def _find_param_widget_at(self, scene_pos):
        """查找场景位置下的 ParamEditWidget"""
        for item in self.scene().items(scene_pos):
            if isinstance(item, QGraphicsProxyWidget):
                proxy = item
                local = proxy.mapFromScene(scene_pos)
                widget = proxy.widget()
                if widget and widget.rect().contains(local.toPoint()):
                    child = widget.childAt(local.toPoint())
                    pw = child.parent() if child else None
                    while pw:
                        if isinstance(pw, ParamEditWidget):
                            return pw
                        pw = pw.parent()
        return None

    def _flow_editor(self):
        """向上查找流程编辑器对话框（合并 .wbt 用）。"""
        w = self.window() if self.window() != self else self.parent()
        while w and not isinstance(w, FlowEditorDialog):
            w = w.parent()
        return w

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-jsonpath"):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat("application/x-api-endpoint"):
            event.acceptProposedAction()
        elif event.mimeData().hasFormat("application/x-wbt-path"):
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            # 图纸拖入：图片 或 .wbt 流程图
            if (any(_is_image_file(u.toLocalFile()) for u in event.mimeData().urls())
                    or any(_is_wbt_file(u.toLocalFile()) for u in event.mimeData().urls())):
                event.acceptProposedAction()
            else:
                super().dragEnterEvent(event)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-jsonpath"):
            sp = self.mapToScene(event.pos())
            self._show_drag_ghost(sp, 120, 40)
            # 目标高亮（参数栏 / 拼合栏 / 规则文本栏 / 创建时机行）
            ttype, _tgt_node, tgt_widget = self._find_drop_target(sp)
            if ttype and tgt_widget is not None and tgt_widget != getattr(self, '_highlighted_edit', None):
                self._clear_target_highlight()
                tgt_widget.setStyleSheet(
                    "QLineEdit { background-color: #4a9a4a; border: 2px solid #00ff00; "
                    "border-radius: 3px; color: white; }"
                )
                self._highlighted_edit = tgt_widget
            elif not ttype:
                self._clear_target_highlight()
            event.acceptProposedAction()
        elif event.mimeData().hasFormat("application/x-api-endpoint"):
            sp = self.mapToScene(event.pos())
            self._show_drag_ghost(sp, 220, 100)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-wbt-path"):
            # 图纸拖入：图片 → 原图虚影；.wbt 流程图 → 内容范围虚影
            sp = self.mapToScene(event.pos())
            img = next((u.toLocalFile() for u in event.mimeData().urls()
                        if _is_image_file(u.toLocalFile())), None)
            if img:
                pm = QPixmap(img)
                if not pm.isNull():
                    self._show_image_ghost(sp, pm)
                    event.acceptProposedAction()
                    return
            wbt = None
            if event.mimeData().hasFormat("application/x-wbt-path"):
                import pickle
                try:
                    _ps = pickle.loads(event.mimeData().data("application/x-wbt-path"))
                    wbt = _ps[0] if _ps else None
                except Exception:
                    wbt = None
            if wbt is None:
                wbt = next((u.toLocalFile() for u in event.mimeData().urls()
                            if _is_wbt_file(u.toLocalFile())), None)
            if wbt and os.path.isfile(wbt):
                editor = self._flow_editor()
                if editor is not None:
                    try:
                        with open(wbt, 'r', encoding='utf-8') as _f:
                            _data = json.load(_f)
                        bbox = editor._flow_content_bbox(_data)
                        self._show_flow_ghost(sp, bbox.width(), bbox.height(),
                                              os.path.basename(wbt))
                        event.acceptProposedAction()
                        return
                    except Exception:
                        pass
            super().dragMoveEvent(event)
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._hide_drag_ghost()
        self._clear_target_highlight()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._hide_drag_ghost()
        self._clear_target_highlight()
        scene_pos = self.mapToScene(event.pos())

        # 处理 API 端点拖入
        if event.mimeData().hasFormat("application/x-api-endpoint"):
            import pickle
            data = pickle.loads(event.mimeData().data("application/x-api-endpoint"))
            if isinstance(data, tuple) and len(data) == 2:
                src, ep = data
                node = APINode()
                node.api_ref = src
                node.set_api(ep['method'], ep['path'], ep.get('parameters', []),
                             ep.get('headers', {}))
                self.scene().add_node(node)
                node.setPos(scene_pos.x() - node.rect().width()/2,
                            scene_pos.y() - node.rect().height()/2)
            event.acceptProposedAction()
            return

        # 处理流程图(.wbt 图纸)拖入：应用内流程图列表 或 文件管理器 → 合并插入
        if (event.mimeData().hasFormat("application/x-wbt-path")
                or (event.mimeData().hasUrls()
                    and any(_is_wbt_file(u.toLocalFile()) for u in event.mimeData().urls()))):
            sp = self.mapToScene(event.pos())
            paths = []
            if event.mimeData().hasFormat("application/x-wbt-path"):
                import pickle
                try:
                    paths = pickle.loads(event.mimeData().data("application/x-wbt-path"))
                except Exception:
                    paths = []
            else:
                paths = [os.path.normpath(u.toLocalFile()) for u in event.mimeData().urls()
                         if _is_wbt_file(u.toLocalFile())]
            editor = self._flow_editor()
            if editor is not None:
                for p in paths:
                    if p and os.path.isfile(p):
                        editor._merge_flow_file_at(p, sp)
            event.acceptProposedAction()
            return

        # 处理图纸（图片文件）拖入：松开携带信息绘制，快速拼合
        if event.mimeData().hasUrls():
            sp = self.mapToScene(event.pos())
            placed = None
            for u in event.mimeData().urls():
                path = os.path.normpath(u.toLocalFile())
                if path and _is_image_file(path):
                    pm = QPixmap(path)
                    if pm.isNull():
                        continue
                    node = ImageNode(image_path=path, pixmap=pm)
                    # 快速拼合：相对上一张图纸自动向右排布（左缘对齐上一张右缘）
                    anchor = None
                    if (self._last_image_node is not None
                            and self._last_image_node.scene() is self.scene()):
                        anchor = self._last_image_node
                    elif placed is not None:
                        anchor = placed
                    if anchor is not None:
                        node.setPos(anchor.pos().x() + anchor.rect().width() + 30,
                                     anchor.pos().y())
                    else:
                        node.setPos(sp.x() - node.rect().width() / 2,
                                    sp.y() - node.rect().height() / 2)
                    self.scene().add_node(node)
                    placed = node
                    self._last_image_node = node
            event.acceptProposedAction()
            return

        # 处理 JSON 路径拖入（已有逻辑）
        if event.mimeData().hasFormat("application/x-jsonpath"):
            raw = str(event.mimeData().data("application/x-jsonpath"), 'utf-8')
            try:
                payload = json.loads(raw)
                path = payload.get('path', '')
                drag_value = payload.get('value', '')
            except (json.JSONDecodeError, TypeError):
                path = raw
                drag_value = raw

            target_type, target_node, target_widget = self._find_drop_target(scene_pos)

            # ---- 创建时机行：拖入 JSON 层级设置创建时机 ----
            if target_type == 'rule_creation' and isinstance(target_node, ContainerNode):
                target_widget.setText(path)
                target_widget.setToolTip(f"路径: {path}")
                target_node._rule_creation_path = path
                event.acceptProposedAction()
                return

            # ---- 规则拼合文本栏：设置数据并自动连线（插入 DP）----
            if target_type == 'rule_part' and isinstance(target_node, ContainerNode):
                cw = target_widget.parent()
                cw.set_drop_data(path, drag_value)
                self.scene()._handle_rule_part_drop(target_node, cw.part_id, path, drag_value)
                event.acceptProposedAction()
                return

            # ---- 拼合栏：设置数据并自动连线 ----
            if target_type == 'concat' and isinstance(target_node, APINode):
                cw = target_widget.parent()
                cw.set_drop_data(path, drag_value)
                self.scene()._handle_concat_drop(target_node, cw.concat_id, path, drag_value)
                event.acceptProposedAction()
                return

            # ---- 参数栏 ----
            if target_type == 'param' and isinstance(target_node, APINode):
                target_pw = target_widget.parent()
                target_pw.set_json_path(path, display_value=drag_value)
                param_name = target_pw.param_name
                # 同步 source_path 到 API 节点 params（持久化保存）
                for p in target_node.params:
                    if p.get('name') == param_name:
                        p['source_path'] = path
                        break
                existing_conn = None
                for conn in self.scene().connections:
                    if conn.end_obj == target_node and conn.end_param == param_name:
                        existing_conn = conn
                        break
                if existing_conn:
                    # ---- 修复：检查是否已有 DP 节点，有则复用 ----
                    src_obj = existing_conn.start_obj
                    if isinstance(src_obj, DataProcessNode):
                        # 已有 DP 节点，直接更新其显示数据和路径绑定
                        src_obj.source_path = path
                        src_obj.set_source_data(drag_value)
                        src_obj.src_label.setToolTip(f"📎 路径: {path}")
                    else:
                        # 没有 DP 节点，新建一个插入到连线中间
                        dp_node = DataProcessNode()
                        dp_node.set_source_data(drag_value)
                        dp_node.source_path = path
                        dp_node.src_label.setToolTip(f"📎 路径: {path}")
                        self.scene().addItem(dp_node)
                        self.scene().nodes.append(dp_node)
                        mid = (existing_conn._start_fn() + target_node.param_input_port(param_name)) / 2
                        dp_node.setPos(mid - QPointF(100, 50))
                        self.scene().removeItem(existing_conn)
                        self.scene().connections.remove(existing_conn)
                        start_fn = existing_conn._start_fn
                        start_obj = existing_conn.start_obj
                        conn1 = ConnectionPath(start_fn, dp_node.inputPort, start_obj, dp_node)
                        self.scene().addItem(conn1)
                        self.scene().connections.append(conn1)
                        dp_node.input_conn = conn1
                        end_fn = lambda n=target_node, p=param_name: n.param_input_port(p)
                        conn2 = ConnectionPath(dp_node.outputPort, end_fn, dp_node, target_node, param_name)
                        self.scene().addItem(conn2)
                        self.scene().connections.append(conn2)
                        dp_node.output_conn = conn2
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

# ================= 撤销/还原管理器（稳定版） =================
# 使用 JSON 序列化存储快照，复用 _serialize_flow / _deserialize_node 等成熟逻辑
class UndoManager:
    def __init__(self, editor, max_steps=20):
        self.editor = editor
        self.max_steps = max_steps
        self._undo_stack = []   # list of serialized dicts
        self._redo_stack = []
        self._saving = False

    def save(self):
        if self._saving:
            return
        try:
            state = self.editor._serialize_flow()
        except Exception as e:
            return
        self._undo_stack.append(state)
        self._redo_stack.clear()
        if len(self._undo_stack) > self.max_steps:
            self._undo_stack.pop(0)

    def undo(self):
        if len(self._undo_stack) < 2:
            return False
        cur = self._undo_stack.pop()
        self._redo_stack.append(cur)
        self._apply(self._undo_stack[-1])
        return True

    def redo(self):
        if not self._redo_stack:
            return False
        nxt = self._redo_stack.pop()
        self._undo_stack.append(nxt)
        self._apply(nxt)
        return True

    def _apply(self, data):
        self._saving = True
        view = None
        if self.editor.scene.views():
            view = self.editor.scene.views()[0]
        if view:
            view.setUpdatesEnabled(False)
        try:
            self.editor._silent_load(data)
        finally:
            if view:
                view.setUpdatesEnabled(True)
                view.update()
            self._saving = False

# ================= 区块收起 / 放出小三角 =================
COLLAPSE_BAR_W = 18          # 侧栏内边缘竖条的宽度（收起后剩下的就是它）


def _make_collapse_bar(width=COLLAPSE_BAR_W):
    """侧栏**内边缘**的竖向小条：收起三角垂直居中放在这里。

    为什么放侧边而不是顶部：顶部那一行要占掉内容的竖向空间；放在侧边既不多占高度，
    收起之后三角也**停在原地**，不用去一条细边里上下找。
    返回 (竖条控件, 它的竖向布局)；三角用 `lay.insertWidget(1, tri)` 插在两个
    stretch 中间即为垂直居中。
    """
    bar = QWidget()
    bar.setFixedWidth(int(width))
    bar.setStyleSheet(
        "background:#16202c; border-left:1px solid #2a3a4a; "
        "border-right:1px solid #2a3a4a;")
    lay = QVBoxLayout(bar)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addStretch(1)
    lay.addStretch(1)
    return bar, lay


class CollapseTriangle(QToolButton):
    """小三角：**即时**收起 / 放出某个区块（**无动画** —— 用户明确要求这几处不要动画）。

    - 竖向区块（左侧「API 入口」/「已保存流程图」）：收起时把面板的 `maximumHeight`
      钳到标题行高度，`QSplitter` 会把空间让给同栏的另一块。
    - 横向区块（右侧「数据处理 / 输出」侧边栏）：收起时把面板的 `maximumWidth`
      钳成一条细边，空间让给画板；标题隐藏只留三角，方便再点回来。
    """

    def __init__(self, pane, content, header=None, parent=None, collapsed=False,
                 collapse_to_width=None, also_hide=None, tip='收起 / 放出面板',
                 sym_expanded='▼', sym_collapsed='▶'):
        super().__init__(parent)
        self._pane = pane
        self._content = content
        self._header = header if header is not None else content
        self._to_width = collapse_to_width
        self._also_hide = list(also_hide or [])
        self._saved_sizes = None
        self._collapsed = False
        # 方向感箭头：竖向区块 ▼/▶；左侧栏 ◀/▶（往左收）；右侧栏 ▶/◀（往右收）
        self._sym_exp = sym_expanded
        self._sym_col = sym_collapsed
        self.setText(sym_expanded)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(16, 16)
        self.setToolTip(tip)
        self.setStyleSheet(
            "QToolButton { background:transparent; color:#8ab8ff; border:none; "
            "font-size:10px; padding:0px; }"
            "QToolButton:hover { color:#ffffff; background:#1e3a5a; border-radius:2px; }")
        self.clicked.connect(self.toggle)
        self.set_collapsed(collapsed)

    # ---------- 内部 ----------
    def _splitter(self):
        w = self._pane.parentWidget() if self._pane is not None else None
        while w is not None:
            if isinstance(w, QSplitter):
                return w
            w = w.parentWidget()
        return None

    def _header_extent(self):
        try:
            return max(18, int(self._header.sizeHint().height()))
        except Exception:
            return 20

    # ---------- 对外 ----------
    def set_collapsed(self, on):
        on = bool(on)
        self._collapsed = on
        sp = self._splitter()
        if on and sp is not None:
            self._saved_sizes = sp.sizes()
        try:
            self._content.setVisible(not on)
            for w in self._also_hide:
                w.setVisible(not on)
        except Exception:
            pass
        if self._pane is not None:
            if self._to_width is not None:
                self._pane.setMaximumWidth(int(self._to_width) if on else 16777215)
            else:
                self._pane.setMaximumHeight(self._header_extent() if on else 16777215)
        self.setText(self._sym_col if on else self._sym_exp)
        self.setToolTip('放回面板' if on else '收起面板')
        if sp is not None:
            if on:
                # 收起：**显式**把让出来的空间补给另一边。
                # 不能指望 QSplitter 自己算 —— 它按各 child 的 sizeHint 与拉伸因子分配，
                # 一旦给画板设了拉伸因子（为了让它吃满窗口），被收起那侧让出来的宽度
                # 就**不会**自动补过去（实测：收起左侧栏后画板宽度纹丝不动）。
                QTimer.singleShot(0, lambda s=sp: self._rebalance(s))
            elif self._saved_sizes:
                _sz = self._saved_sizes
                QTimer.singleShot(0, lambda s=sp, z=_sz: self._restore_sizes(s, z))

    @staticmethod
    def _rebalance(sp):
        """把"被收起那一侧"压到细边，其余空间全给另一侧。

        只处理两个 child 的分割器（本处全部如此）；横向按宽度、竖向按高度。
        """
        try:
            if sp.count() != 2:
                return
            horiz = (sp.orientation() == Qt.Horizontal)
            cur = sp.sizes()
            total = sum(cur) or 1
            out = list(cur)
            collapsed = []
            for i in (0, 1):
                w = sp.widget(i)
                if w is None:
                    continue
                cap = w.maximumWidth() if horiz else w.maximumHeight()
                if cap < 100000:                 # 这一侧被收起了
                    out[i] = max(1, int(cap))
                    collapsed.append(i)
            rest = [i for i in (0, 1) if i not in collapsed]
            if rest:
                out[rest[0]] = max(1, total - sum(out[i] for i in collapsed))
            sp.setSizes(out)
        except Exception:
            pass

    @staticmethod
    def _restore_sizes(sp, sizes):
        try:
            sp.setSizes(sizes)
        except Exception:
            pass

    def toggle(self):
        self.set_collapsed(not self._collapsed)

    def is_collapsed(self):
        return self._collapsed


# ================= 数据传递参数仪表盘 =================
class _ClickRow(QWidget):
    """整行可点的容器（用于仪表盘标题行展开/收起）。"""
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                self.clicked.emit()
        except Exception:
            pass
        super().mouseReleaseEvent(event)


class TransferDashboard(QWidget):
    """数据传递（速率 / 频次）仪表盘 —— 贴在可视化画板上边界、可下拉展开。

    位置：`_build_tab_content()` 里加在 `tab['view']` **之前**，
    因此正好夹在「图纸选项卡」与「可视化画板」之间，且**每个选项卡一份** ——
    切换图纸时天然就是该图纸自己的参数，无需额外刷新。

    值域：由 `TRANSFER_SPECS` 单一权威定义；默认值一律等于引入本功能之前的
    模块常量，所以「没动过 = 行为与改动前逐字节一致」。

    展开动画：Qt 的 QSS 不能做过渡动画，这里用 `QPropertyAnimation` 拉
    `maximumHeight` 来还原「CSS 拉开/收回」的观感（OutCubic，220ms）。
    """

    changed = Signal()          # 任一参数被改动（用于 mark_dirty / 待落盘标记）
    _ANIM_MS = 220

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._cfg = TransferConfig.defaults()
        self._editors = {}      # key -> (spec, 编辑控件)
        self._mirrors = {}      # key -> 工具栏上的镜像控件
        self._syncing = False
        self._expanded = False
        self._badge_state = None   # None / 'dflt' / 'mod'（避免每帧重刷样式表）
        self._build()
        self._bind_mirrors()
        self.set_config(self._cfg, notify=False)

    # ---------------- 构建 ----------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 标题行（始终可见）----
        head = _ClickRow()
        head.setObjectName('tdHead')
        head.setStyleSheet(
            "QWidget#tdHead { background:#16202c; border-bottom:1px solid #2a3a4a; }"
            "QWidget#tdHead:hover { background:#1b2735; }")
        head.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 2, 8, 2)
        hl.setSpacing(8)

        self._arrow = QLabel('▼')
        self._arrow.setFixedWidth(12)
        self._arrow.setStyleSheet("color:#8fc7ff; font-size:11px; background:transparent;")
        hl.addWidget(self._arrow)

        _title = QLabel('数据传递参数')
        _title.setStyleSheet(
            "color:#cfe4ff; font-size:11px; font-weight:bold; background:transparent;")
        hl.addWidget(_title)

        self._badge = QLabel('默认')
        self._badge.setStyleSheet(
            "color:#7ee0a0; font-size:10px; background:#14301f; "
            "border:1px solid #2c6a45; border-radius:8px; padding:1px 7px;")
        hl.addWidget(self._badge)

        self._summary = QLabel('')
        self._summary.setStyleSheet("color:#8aa0b8; font-size:10px; background:transparent;")
        self._summary.setToolTip(
            '展开「数据传递参数」可逐项调整；所有默认值＝引入本功能之前的原样。')
        hl.addWidget(self._summary)
        hl.addStretch(1)

        self._btn_reset = QToolButton()
        self._btn_reset.setText('恢复默认')
        self._btn_reset.setToolTip('把本图纸的全部数据传递参数还原为默认值（＝改动前的原样）')
        self._btn_reset.setStyleSheet(
            "QToolButton { color:#a9bdd4; background:#1e2a38; border:1px solid #2f4256; "
            "border-radius:3px; padding:1px 8px; font-size:10px; }"
            "QToolButton:hover { background:#26374a; color:#dceaf8; }")
        self._btn_reset.clicked.connect(self.reset_defaults)
        hl.addWidget(self._btn_reset)

        outer.addWidget(head)
        self._head = head
        head.clicked.connect(self.toggle)

        # ---- 折叠体 ----
        self._body = QWidget()
        self._body.setStyleSheet("background:#131c26;")
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(10, 4, 10, 8)
        bl.setSpacing(2)

        for grp in TRANSFER_GROUPS:
            _gh = QLabel(grp)
            _gh.setStyleSheet(
                "color:#7fa8d8; font-size:10px; font-weight:bold; "
                "background:transparent; padding-top:3px;")
            bl.addWidget(_gh)
            grid = QGridLayout()
            grid.setContentsMargins(6, 0, 0, 2)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(3)
            for i, spec in enumerate([s for s in TRANSFER_SPECS if s['group'] == grp]):
                r, c = divmod(i, 3)          # 每行 3 项
                grid.addWidget(self._make_cell(spec), r, c)
            grid.setColumnStretch(3, 1)
            bl.addLayout(grid)

        outer.addWidget(self._body)
        self._body.setMaximumHeight(0)
        self._body.setVisible(False)

        self._anim = QPropertyAnimation(self._body, b"maximumHeight", self)
        self._anim.setDuration(self._ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

    def _make_cell(self, spec):
        """一个参数 = 标签 + 编辑控件 + 单位。"""
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        lab = QLabel(spec['label'])
        lab.setStyleSheet("color:#a9bdd4; font-size:10px; background:transparent;")
        lab.setFixedWidth(88)
        lab.setToolTip(spec['tip'])
        h.addWidget(lab)

        kind = spec['kind']
        if kind == 'bool':
            ed = QCheckBox()
            ed.setStyleSheet("QCheckBox { background:transparent; }")
            ed.toggled.connect(lambda v, k=spec['key']: self._on_edit(k, v))
        elif kind == 'int':
            ed = QSpinBox()
            ed.setRange(int(spec['lo']), int(spec['hi']))
            ed.setSingleStep(int(spec['step']))
            ed.setFixedWidth(64)
            ed.valueChanged.connect(lambda v, k=spec['key']: self._on_edit(k, v))
        else:
            ed = QDoubleSpinBox()
            ed.setRange(float(spec['lo']), float(spec['hi']))
            ed.setSingleStep(float(spec['step']))
            ed.setDecimals(int(spec['dec']))
            ed.setFixedWidth(72)
            ed.valueChanged.connect(lambda v, k=spec['key']: self._on_edit(k, v))
        ed.setToolTip(spec['tip'])
        ed.setStyleSheet("background:#1b2735; color:#dceaf8; border:1px solid #2f4256; "
                         "border-radius:3px; font-size:10px;")
        h.addWidget(ed)

        if spec.get('unit'):
            u = QLabel(spec['unit'])
            u.setStyleSheet("color:#6f88a3; font-size:10px; background:transparent;")
            h.addWidget(u)
        h.addStretch(1)

        self._editors[spec['key']] = (spec, ed)
        return w

    def _bind_mirrors(self):
        """与工具栏上已有的「轮询次数 / 自增 / 元素间等待」建立双向同步。"""
        for spec in TRANSFER_SPECS:
            name = spec.get('mirror')
            if not name:
                continue
            w = getattr(self._host, name, None)
            if w is None:
                continue
            self._mirrors[spec['key']] = w
            try:
                if spec['kind'] == 'bool':
                    w.toggled.connect(
                        lambda v, k=spec['key']: self._on_mirror_changed(k, v))
                else:
                    w.valueChanged.connect(
                        lambda v, k=spec['key']: self._on_mirror_changed(k, v))
            except Exception:
                pass

    # ---------------- 值同步 ----------------
    def config(self):
        """本图纸当前参数（不可变快照）。"""
        return self._cfg

    def set_config(self, cfg, notify=True):
        """整体套用一份参数（加载图纸 / 恢复默认 / 外部同步都用它）。"""
        self._syncing = True
        try:
            self._cfg = cfg if isinstance(cfg, TransferConfig) else TransferConfig.defaults()
            for key, (spec, ed) in self._editors.items():
                v = getattr(self._cfg, key)
                if spec['kind'] == 'bool':
                    ed.setChecked(bool(v))
                else:
                    ed.setValue(v)
            self._push_all_mirrors()
        finally:
            self._syncing = False
        self._refresh_header()
        if notify:
            self.changed.emit()

    def reset_defaults(self):
        self.set_config(TransferConfig.defaults())

    def _on_edit(self, key, value):
        if self._syncing:
            return
        self._cfg = self._cfg.copy(**{key: value})
        self._push_mirror(key, getattr(self._cfg, key))
        self._refresh_header()
        self.changed.emit()

    def _on_mirror_changed(self, key, value):
        """工具栏镜像控件被改动：只回写**这一个**编辑控件。

        不要再走 set_config() —— 那会把 14 个控件全部 setValue 一遍，
        拖动工具栏拨盘时每一次 valueChanged 都触发 14 次控件写 + 信号分发，
        是最容易被误认成"动画卡顿"的开销来源。
        """
        if self._syncing:
            return
        spec = TRANSFER_SPEC_BY_KEY.get(key)
        if spec is None:
            return
        self._cfg = self._cfg.copy(**{key: value})
        self._syncing = True
        try:
            ed = self._editors.get(key, (None, None))[1]
            v = getattr(self._cfg, key)
            if ed is not None:
                if spec['kind'] == 'bool':
                    ed.setChecked(bool(v))
                else:
                    ed.setValue(v)
        except Exception:
            pass
        finally:
            self._syncing = False
        self._refresh_header()
        self.changed.emit()

    def _push_mirror(self, key, value):
        w = self._mirrors.get(key)
        if w is None:
            return
        self._syncing = True
        try:
            if isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            else:
                w.setValue(value)
        except Exception:
            pass
        finally:
            self._syncing = False

    def _push_all_mirrors(self):
        for key in list(self._mirrors):
            self._push_mirror(key, getattr(self._cfg, key))

    # ---------------- 标题行 ----------------
    _BADGE_QSS = {
        'dflt': ("color:#7ee0a0; font-size:10px; background:#14301f; "
                 "border:1px solid #2c6a45; border-radius:8px; padding:1px 7px;"),
        'mod': ("color:#ffd479; font-size:10px; background:#3a2f14; "
                "border:1px solid #7a6023; border-radius:8px; padding:1px 7px;"),
    }

    def _refresh_header(self):
        d = self._cfg.diff()
        state = 'mod' if d else 'dflt'
        # setStyleSheet 会触发整个控件的样式重算（Qt 里出了名的贵），
        # 只在「默认 ↔ 已改」这个状态**真的翻转**时才重刷样式。
        if state != self._badge_state:
            self._badge_state = state
            self._badge.setStyleSheet(self._BADGE_QSS[state])
        if d:
            self._badge.setText(f'已改 {len(d)} 项')
            _names = '、'.join(TRANSFER_SPEC_BY_KEY[k]['label'] for k in list(d)[:4])
            self._summary.setText(self._cfg.summary() + f'　✎ {_names}'
                                  + ('…' if len(d) > 4 else ''))
        else:
            self._badge.setText('默认')
            self._summary.setText(self._cfg.summary())

    def toggle(self):
        self.set_expanded(not self._expanded)

    def _measure_body(self):
        """量一次折叠体的自然高度（临时解除高度上限，量完还原）。

        必须在**冻结子布局之前**量，否则布局停摆后 sizeHint 不再可靠。
        """
        body = self._body
        old = body.maximumHeight()
        try:
            body.setMaximumHeight(16777215)
            lay = body.layout()
            if lay is not None:
                lay.activate()
            h = int(body.sizeHint().height())
        except Exception:
            h = 0
        finally:
            body.setMaximumHeight(old)
        return max(1, h)

    def _freeze_body_layout(self, freeze):
        """动画期间冻结/解冻折叠体的子布局。

        这是消除卡顿的关键：不冻结的话，`maximumHeight` 每变一帧都会让
        14 个控件的 QGridLayout 整棵重排 + 逐个重绘；冻结后子控件几何不变，
        只有父层做裁剪，每帧代价从「重排整棵树」降到「裁剪一次」。
        """
        try:
            lay = self._body.layout()
            if lay is not None:
                lay.setEnabled(not freeze)
        except Exception:
            pass

    def set_expanded(self, on, animate=True):
        on = bool(on)
        self._expanded = on
        self._arrow.setText('▲' if on else '▼')

        if on:
            self._body.setVisible(True)
            start, end = 0, self._measure_body()
        else:
            # 关键修复：收回的起点必须是**当前真实高度**，不能是 maximumHeight()。
            # 展开结束后 maximumHeight 被解除成 16777215，若从它开始动画，
            # 前面 99.9% 的时间都花在内容高度之上（布局把它钳到内容高度，看起来
            # 一动不动），真正可见的收缩只剩最后一两帧 —— 表现为"收回没动画"。
            start, end = self._body.height(), 0

        if not animate:
            self._anim.stop()
            self._body.setMaximumHeight(end)
            self._body.setVisible(on)
            if on:
                self._freeze_body_layout(False)
            return

        self._freeze_body_layout(True)
        self._anim.stop()
        self._anim.setStartValue(max(0, int(start)))
        self._anim.setEndValue(max(0, int(end)))
        self._anim.start()

    def _on_anim_done(self):
        self._freeze_body_layout(False)
        if self._expanded:
            # 展开完毕：解除高度上限，让内容可随字体/缩放自适应
            self._body.setMaximumHeight(16777215)
            try:
                lay = self._body.layout()
                if lay is not None:
                    lay.activate()
            except Exception:
                pass
        else:
            self._body.setVisible(False)

    def is_expanded(self):
        return self._expanded


# ================= 主编辑器 =================
class FlowEditorDialog(QDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        # ---- 黑夜模式：**永远**深色，不跟随系统明暗 ----
        # 嵌在主程序里时本来就继承主窗口的深色调色板；但端口画板要被单独打包成 exe
        # 发给别的用户，那时它没有父窗口，会跟随系统主题（浅色 Windows 上就是一片白）。
        # 这里显式套一份与主程序**逐字相同**的调色板 + 样式表，两种情况外观一致。
        try:
            from portpanel.integration.theme import apply_dark_theme
            apply_dark_theme(self)
        except Exception:
            pass
        self.setWindowTitle("端口画板")
        self.setMinimumSize(1200, 800)
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint |
                            Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        if config is None:
            config = self._load_config()
        self.config = config
        self._config_hash = self._compute_config_hash(config)
        self.scene = NodeScene()
        self.view = NodeView(self.scene)
        self.undo_mgr = UndoManager(self, max_steps=20)
        self.scene._undo_save_cb = self._on_undo_saved
        self._current_file = None  # 当前打开的 .wbt 文件路径
        self._current_worker = None   # 当前下载线程
        self._inner_cancel = False    # 内部取消标志
        self._cancel_pressed = False  # 取消按钮点击标志
        self._store_workers = set()   # 后台存盘线程集合（保证引用存活）
        self._store_counter = 0       # 存盘任务自增 ID（避免 id() 超出 32 位 int）
        # ---- 输出面板状态（保留历史 + 多选 + 钉子 + 上限） ----
        self._output_items = []       # 按输出顺序排列（append 顺序）
        self._output_select_mode = False
        self._output_max_items = 200  # 存储上限；固定条目不计数、不释放
        # ---- API 日志富文本模式状态（数据处理选项卡切换） ----
        self._log_rich_mode = False      # False=纯文本 True=富文本
        self._log_lines = []             # 权威日志行列表（完整，不受窗口裁剪影响）
        self._rich_log_text = None       # 富文本 QTextEdit（_init_ui 创建）
        # 日志窗口 [start, end)：纯文本与富文本共用，保证两种状态页面同步
        self._rich_window_start = 0      # 窗口起始日志行索引
        self._rich_window_end = 0        # 窗口结束日志行索引（不含）
        self._log_follow_tail = True     # True=窗口跟随最新输出（自动折叠旧行）
        self._rich_refresh_timer = None  # 富文本追加防抖定时器（_build_tab_content 创建）
        self._plain_buf = []             # 纯文本日志缓冲（去抖后批量插入，见 _flush_plain_append）
        self._plain_flush_timer = None   # 纯文本追加去抖定时器（_build_tab_content 创建）
        self._rich_block_cache = {}      # {日志块起点行: 渲染HTML片段}（着色缓存）
        # 富文本增量追加账本：[[起始行, 结束行, 该段产生的文档块数], ...]，
        # 按行号连续排列，与文档现有块一一对应（头部裁剪时同步出队）。
        # 详见 _rich_append_new()：整页 setHtml 要 52ms，增量 insertHtml 只要 0.16ms。
        self._rich_doc_segs = []
        self._suppress_scroll_load = False  # setValue 期间抑制滚动加载（防递归）
        self._log_clearing = False       # 清空日志防重入守卫
        # ---- 悬停详情总闸（工具栏「悬停详情」；默认开、可记住）----
        # 用户反馈「悬停详情会影响画布操作」→ 给一个「我现在要专心」的总闸；
        # 关掉时连该视图的 200 ms 轮询一起停（见 NodeView.set_hover_enabled）。
        self._hover_enabled = True
        try:
            import ui_prefs
            self._hover_enabled = bool(ui_prefs.get('hover_detail', True))
        except Exception:
            pass
        # ---- 多流程图选项卡：每个选项卡独立场景/视图/日志/输出，常加载切换 ----
        self._tabs = []        # 选项卡状态列表
        self._active_tab = -1  # 当前激活选项卡索引
        self._running = False  # 流程执行中（禁止切换/关闭）
        self._dirty = False    # 当前选项卡是否有未保存更改
        self._init_ui()
        self._register_initial_tab()
        # 初始选项卡的 view 到这里才存在 → 补套一次总闸（新图纸走 _new_tab 里的同步）
        self._apply_hover_enabled()
        self._populate_api_list()
        self._refresh_flow_list()
        self.undo_mgr.save()
        # 首次运行自动弹一次新手引导（看过就不再打扰；延后一点等窗口真的显示出来）
        self._tour = None
        QTimer.singleShot(900, self._maybe_auto_tour)

    @staticmethod
    def _load_config():
        try:
            dlg = APIConfigDialog()
            return dlg.config
        except:
            return {}

    def _global_poll_auto(self):
        """全局「自增轮询」是否勾选（控件缺失时按关闭处理）。"""
        try:
            return bool(self.poll_auto_chk.isChecked())
        except Exception:
            return bool(getattr(self, 'poll_auto_chk', None) is not None
                        and getattr(self.poll_auto_chk, '_checked', False))

    def _on_global_poll_auto_toggled(self, checked):
        """全局自增开关：勾选后拨盘只作显示，实际轮数由运行时递增决定。"""
        try:
            self.poll_count_spin.setEnabled(not bool(checked))
        except Exception:
            pass

    def _on_poll_fold_toggled(self, on):
        """横向折叠「轮询次数 / 自增 / 等待时间(秒)」。

        收起时整块容器从横向布局里消失，后面的功能键自动左移（腾出位置）；
        控件本身仍然存在，所以仪表盘的镜像同步照常工作。
        """
        try:
            self._poll_group.setVisible(bool(on))
            self.btn_poll_fold.setText(('▾ ' if on else '▸ ') + '轮询参数')
        except Exception:
            pass

    # ---------------- 新手引导（遮罩 + 聚光灯 + 交互式推进） ----------------
    def _maybe_auto_tour(self, _tries=0):
        """首次运行自动弹一次新手引导。

        · 只在"确实没看过"时弹（标记落在 `data/ui_state.json`）。
        · 窗口还没显示出来就等一会儿再试 —— 引导要高亮真实控件，窗口没显示时
          量不到几何。
        · `PORT_PANEL_NO_TOUR=1` 可直接关掉**自动弹**：**自动化/回归脚本必须设它** ——
          引导会盖一层遮罩、还会在全局吞掉 Esc（它自己要用 Esc 退出），
          跑测试时弹出来只会干扰被测代码。
          从菜单手动开引导不受这个开关影响。
        """
        if os.environ.get('PORT_PANEL_NO_TOUR') == '1':
            return
        try:
            from portpanel.ui.tour_layer import tour_seen
            from portpanel.ui.tour_script_panel import TOUR_KEY
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
        """开一次新手引导。首次运行自动调，之后从「🛠️ 工具栏 → 🎓 新手引导」重看。"""
        cur = getattr(self, '_tour', None)
        if cur is not None:
            try:
                if cur.active:
                    return                     # 已经在跑了，别叠两层遮罩
            except RuntimeError:
                self._tour = None              # C++ 对象已被销毁
        if getattr(self, '_running', False):
            QMessageBox.information(self, '新手引导', '流程正在执行中，先停下再开引导吧。')
            return
        try:
            from portpanel.ui.tour_layer import GuidedTour
            from portpanel.ui.tour_script_panel import TOUR_KEY, panel_tour_steps
        except Exception as e:                                  # noqa: BLE001
            QMessageBox.warning(self, '新手引导', f'引导模块不可用：{e}')
            return
        try:
            self._tour = GuidedTour(panel_tour_steps(self), self)
            self._tour.finished.connect(
                lambda done: self._on_tour_finished(TOUR_KEY, done))
            self._tour.start()
        except Exception as e:                                  # noqa: BLE001
            self._tour = None
            QMessageBox.critical(self, '新手引导', f'引导启动失败：{e}')

    def _on_tour_finished(self, key, completed):
        """引导收工：不管走完还是中途跳过，都记成"看过"，下次不再自动弹。

        （这里**不**把 self._tour 置空 —— finished 是在 GuidedTour.close() 内部发出的，
        那时它自己还在跑收尾代码；提前丢掉最后一个 Python 引用会让它在半路被回收。）
        """
        try:
            from portpanel.ui.tour_layer import mark_tour_seen
            mark_tour_seen(key, True)
        except Exception:
            pass

    def _on_open_image_sources(self):
        """以**独立窗口**打开「图源配置」（与「API 和云服务配置」里的那一页同一套代码）。

        保存后本窗口的「📡 API 入口」列表会立刻刷新，并按既有的「过时节点」逻辑
        静默同步画布上受影响的 API 节点。
        """
        if getattr(self, '_running', False):
            QMessageBox.information(self, "提示", "流程执行中，请先停止再修改图源配置。")
            return
        try:
            dlg = ImageSourceConfigDialog(self)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法打开「图源配置」:\n{e}")
            return
        _saved = {'done': False}
        try:
            dlg.config_saved.connect(lambda: _saved.__setitem__('done', True))
        except Exception:
            pass
        dlg.exec()
        # 关闭后一律按「可能改过」处理：
        #   · 点过「保存图源配置」→ 直接用对话框里那份（含刚导入的入口）
        #   · 没保存 → 以**磁盘**为准重新读一遍，避免把未保存的改动带进端口画板
        try:
            if _saved['done'] and isinstance(getattr(dlg, 'config', None), dict):
                self.config = dlg.config
                self.api_log_text.append("📷 图源配置已保存，API 入口列表已刷新")
            else:
                _fresh = dlg.load_config()
                if isinstance(_fresh, dict):
                    self.config = _fresh
        except Exception:
            pass
        try:
            self._populate_api_list()
        except Exception:
            pass
        try:
            self.on_config_externally_changed(confirm=False)
        except Exception:
            pass

    # ---------- 悬停详情总闸 ----------
    def _refresh_hover_tip_btn(self):
        """按总闸状态刷新工具栏按钮文字（checked = 功能开）。"""
        btn = getattr(self, 'btn_hover_tip', None)
        if btn is not None:
            btn.setText('悬停详情' if btn.isChecked() else '悬停详情（关）')

    def _apply_hover_enabled(self):
        """把总闸状态套到**所有**选项卡的视图上。

        每个图纸选项卡各有自己的 `NodeView`（各有各的 200 ms 轮询定时器），
        只改当前视图会漏掉别的图纸 —— 切过去就发现「开关没生效」。
        """
        for tab in getattr(self, '_tabs', None) or []:
            view = tab.get('view') if isinstance(tab, dict) else None
            if view is not None and hasattr(view, 'set_hover_enabled'):
                try:
                    view.set_hover_enabled(self._hover_enabled)
                except Exception:
                    pass

    def _on_hover_tip_toggled(self, on):
        self._hover_enabled = bool(on)
        self._apply_hover_enabled()
        self._refresh_hover_tip_btn()
        try:
            import ui_prefs
            ui_prefs.set('hover_detail', bool(on))
        except Exception:
            pass

    def _init_ui(self):
        main = QVBoxLayout(self)
        tb = QHBoxLayout()

        self.btn_run = QPushButton("▶ 执行")
        self.btn_run.clicked.connect(self.run_flow)
        tb.addWidget(self.btn_run)

        # ---- 轮询参数：**横向折叠**（默认收起）----
        # 这三项已经并进画板顶部的「数据传递参数」仪表盘、并随图纸保存，
        # 工具栏上再占一整排就太挤了；折叠起来给功能键腾位置（保险起见不删）。
        self.btn_poll_fold = QToolButton()
        self.btn_poll_fold.setText("▸ 轮询参数")
        self.btn_poll_fold.setCheckable(True)
        self.btn_poll_fold.setChecked(False)
        self.btn_poll_fold.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_poll_fold.setStyleSheet(
            "QToolButton { background:transparent; color:#8ab8ff; "
            "border:1px solid #3a4a5a; border-radius:3px; padding:4px 8px; "
            "font-size:11px; }"
            "QToolButton:hover { background:#1e3a5a; }"
            "QToolButton:checked { background:#1e3a5a; color:#dceaf8; }")
        self.btn_poll_fold.setToolTip(
            "轮询次数 / 自增 / 元素间等待(秒)\n"
            "这三项已经并进画板顶部的「数据传递参数」仪表盘（随图纸保存），\n"
            "工具栏上默认折叠起来给功能键腾位置；需要时点一下展开。")
        self.btn_poll_fold.toggled.connect(self._on_poll_fold_toggled)
        tb.addWidget(self.btn_poll_fold)

        # 折叠体：三个控件装在一个容器里，收起时整块从横向布局里消失
        self._poll_group = QWidget()
        _pg = QHBoxLayout(self._poll_group)
        _pg.setContentsMargins(0, 0, 0, 0)
        _pg.setSpacing(6)

        _pg.addWidget(QLabel("轮询次数:"))
        self.poll_count_spin = QSpinBox()
        self.poll_count_spin.setRange(1, 9999)
        self.poll_count_spin.setValue(1)
        self.poll_count_spin.setFixedWidth(60)
        self.poll_count_spin.setToolTip("所有已连线的元素完整执行一遍为一次轮询")
        _pg.addWidget(self.poll_count_spin)

        self.poll_auto_chk = QCheckBox("自增")
        self.poll_auto_chk.setChecked(False)
        self.poll_auto_chk.setToolTip(
            f"自增轮询（仅作用于「不在任何类框内」的全局元素）：\n"
            f"不按左侧拨盘的固定轮数跑，而是从第 1 轮开始逐轮 +1（同步喂给步进器），\n"
            f"直到全局下载层报错（HTTP 404 等）或全局数据库框数据走完一整圈；\n"
            f"最多递增到 {AUTO_POLL_MAX} 轮（再多请取消勾选、用拨盘手动指定）。\n"
            f"类框内元素各自用自己的「自增」勾选框。")
        self.poll_auto_chk.toggled.connect(self._on_global_poll_auto_toggled)
        _pg.addWidget(self.poll_auto_chk)

        _pg.addWidget(QLabel("等待时间(秒):"))
        self.wait_time_spin = QDoubleSpinBox()
        self.wait_time_spin.setRange(0.0, 60.0)
        self.wait_time_spin.setSingleStep(0.1)
        self.wait_time_spin.setValue(0.5)
        self.wait_time_spin.setDecimals(1)
        self.wait_time_spin.setFixedWidth(70)
        self.wait_time_spin.setToolTip("元素收发完成后，走向下一连接元素前的等待间隔（防止被服务器拉黑）")
        _pg.addWidget(self.wait_time_spin)

        self._poll_group.setVisible(False)      # 默认收起
        tb.addWidget(self._poll_group)

        # ---- 悬停详情总闸：画布上悬停元素框弹出的详情框（默认开，状态会记住）----
        # 由来：悬停详情会在连线/精修时挡视线，需要一个「我现在要专心」的开关。
        # checked = 功能开；关掉时 NodeView 连 200 ms 轮询一起停（见 set_hover_enabled）。
        self.btn_hover_tip = QToolButton()
        self.btn_hover_tip.setCheckable(True)
        self.btn_hover_tip.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_hover_tip.setStyleSheet(
            "QToolButton { background:transparent; color:#8ab8ff; "
            "border:1px solid #3a4a5a; border-radius:3px; padding:4px 8px; "
            "font-size:11px; }"
            "QToolButton:hover { background:#1e3a5a; }"
            "QToolButton:checked { background:#1e3a5a; color:#dceaf8; }")
        self.btn_hover_tip.setToolTip(
            "画布悬停详情（默认开）：鼠标停在元素框上约 0.25 秒，弹出它的端口清单与当前值。\n"
            "· 浮窗点击穿透：不挡下面的框，也不吃点击\n"
            "· 鼠标离开元素框立即收起；展开后仍跟随鼠标\n"
            "关掉它 = 画布完全不受打扰（专心连线 / 拖框时用），状态会被记住。")
        self.btn_hover_tip.setChecked(bool(getattr(self, '_hover_enabled', True)))
        self._refresh_hover_tip_btn()
        # 信号最后接：setChecked 不触发（否则启动时会白写一次偏好）
        self.btn_hover_tip.toggled.connect(self._on_hover_tip_toggled)
        tb.addWidget(self.btn_hover_tip)

        tb.addWidget(QLabel(""))

        # ---- 图源配置：独立窗口打开（与「API 和云服务配置」里的那一页同一套代码）----
        self.btn_image_sources = QPushButton("📷 图源配置")
        self.btn_image_sources.setStyleSheet("""
            QPushButton { background-color: #d63384; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #b02a6d; }
        """)
        self.btn_image_sources.setToolTip(
            "单独打开「图源配置」（不必先开「API 和云服务配置」）：\n"
            "管理 API 入口与其子端口、参数、Cookie、调试，支持导入/分享入口。\n"
            "保存后本窗口的「API 入口」列表会自动刷新。")
        self.btn_image_sources.clicked.connect(self._on_open_image_sources)
        tb.addWidget(self.btn_image_sources)

        self.btn_add_db = QPushButton("🗄️ 数据库框")
        self.btn_add_db.setStyleSheet("""
            QPushButton { background-color: #6f42c1; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #5a32a3; }
        """)
        self.btn_add_db.clicked.connect(self._on_add_db_clicked)
        tb.addWidget(self.btn_add_db)

        self.btn_add_container = QPushButton("📦 容器框")
        self.btn_add_container.setStyleSheet("""
            QPushButton { background-color: #2a6a4a; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #1e5a3a; }
        """)
        self.btn_add_container.clicked.connect(self._on_add_container_clicked)
        tb.addWidget(self.btn_add_container)

        # 保存/加载按钮
        self.btn_save = QPushButton("💾 保存流程")
        self.btn_save.setStyleSheet("""
            QPushButton { background-color: #28a745; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #218838; }
        """)
        self.btn_save.clicked.connect(self._save_flow)
        tb.addWidget(self.btn_save)

        self.btn_load = QPushButton("📂 加载流程")
        self.btn_load.setStyleSheet("""
            QPushButton { background-color: #17a2b8; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #138496; }
        """)
        self.btn_load.clicked.connect(self._load_flow)
        tb.addWidget(self.btn_load)

        # 工具栏（下拉栏）：后续新增工具统一加到此菜单
        self.btn_tools = QToolButton()
        self.btn_tools.setText("🛠️ 工具栏")
        self.btn_tools.setPopupMode(QToolButton.InstantPopup)
        self.btn_tools.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_tools.setStyleSheet("""
            QToolButton { background-color: #6f42c1; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QToolButton:hover { background-color: #5a32a3; }
            QToolButton::menu-indicator { image: none; }
        """)
        self._tools_menu = QMenu(self)
        act_step = self._tools_menu.addAction("🔢 添加步进器")
        act_step.triggered.connect(self._on_add_stepper_clicked)
        act_site = self._tools_menu.addAction("🌐 添加站点解析")
        act_site.triggered.connect(self._on_add_site_parser_clicked)
        self._tools_menu.addSeparator()
        act_tour = self._tools_menu.addAction("🎓 新手引导")
        act_tour.setToolTip("带高亮遮罩的交互式引导：该点的地方直接点，做完自动继续")
        act_tour.triggered.connect(self._start_tour)
        self.btn_tools.setMenu(self._tools_menu)
        tb.addWidget(self.btn_tools)

        tb.addStretch()

        # ---- 跨进程交接：下载完成 → 图库检索管理器（image-search）----
        self.btn_handoff = QPushButton("🔁 下载完成→图库检索")
        self.btn_handoff.setToolTip(
            "把各容器框下载/校验完成的内容交给「图库检索管理器」自动增量建库：\n"
            "写交接文件 → 分离启动过渡进程 → 退出本程序（释放内存）\n"
            "随后由 image-search 打开 GUI 自动建库并回写 result")
        self.btn_handoff.setStyleSheet("""
            QPushButton { background-color: #e67e22; color: white; border-radius: 4px;
                          padding: 6px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #ca6f1e; }
        """)
        self.btn_handoff.clicked.connect(self._on_handoff_clicked)
        tb.addWidget(self.btn_handoff)

        main.addLayout(tb)

        # ---- 多流程图选项卡：可视化区域上方紧贴功能栏 ----
        self.tab_bar = FlowTabBar()
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setUsesScrollButtons(True)
        # 注意：不能在此设置 color —— FlowTabBar 完全自绘文本（含斜体橙色），
        # QSS 的 color 会覆盖自绘文本色导致"两种文字重叠"。文本色由 paintEvent 控制。
        self.tab_bar.setStyleSheet(
            "QTabBar::tab { background:#1e2a3a; border:1px solid #3a4a5a; "
            "border-bottom:none; padding:4px 10px; margin-right:2px; "
            "border-top-left-radius:4px; border-top-right-radius:4px; min-width:80px; }"
            "QTabBar::tab:selected { background:#2a4a7a; }"
            "QTabBar::tab:hover { background:#2a3a4a; }"
        )
        self.tab_bar.tabCloseRequested.connect(self._close_tab)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        main.addWidget(self.tab_bar)

        splitter = QSplitter(Qt.Horizontal)
        # 左栏 = [内容][内边缘竖条]：竖条贴在侧边，不占竖向空间
        left = QWidget()
        _left_outer = QHBoxLayout(left)
        _left_outer.setContentsMargins(0, 0, 0, 0)
        _left_outer.setSpacing(0)
        _left_body = QWidget()
        lv = QVBoxLayout(_left_body)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        # 上半：API 入口（可折叠树状图）
        class ApiTreeWidget(QTreeWidget):
            """支持拖出 API 端点数据的树控件"""
            def mimeTypes(self):
                return ["application/x-api-endpoint"]
            def mimeData(self, items):
                if not items:
                    return super().mimeData(items)
                item = items[0]
                if item.data(0, Qt.UserRole + 1) != 'endpoint':
                    return super().mimeData(items)
                data = item.data(0, Qt.UserRole)
                if data:
                    import pickle
                    md = QMimeData()
                    md.setData("application/x-api-endpoint", pickle.dumps(data))
                    return md
                return super().mimeData(items)
        self.api_tree = ApiTreeWidget()
        self.api_tree.setHeaderHidden(True)
        self.api_tree.setDragEnabled(True)
        self.api_tree.setAcceptDrops(False)
        self.api_tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.api_tree.itemDoubleClicked.connect(self._on_api_tree_dblclick)
        self.api_tree.setAnimated(True)
        self.api_tree.setIndentation(16)
        self.api_tree.setExpandsOnDoubleClick(False)
        self.api_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.api_tree.setMinimumHeight(240)  # 键值/端点信息默认更大
        # 悬浮信息栏深色样式（防止白底白字不可读）
        self.api_tree.setStyleSheet(
            "QTreeWidget::item { padding: 2px; }"
            "QToolTip { color: #ffffff; background-color: #2d2d30; "
            "border: 1px solid #555555; padding: 4px; }"
        )
        top_widget = QWidget()
        tv = QVBoxLayout(top_widget)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(QLabel("📡 API 入口"))
        tv.addWidget(self.api_tree)

        # 下半：已保存流程图列表（支持拖出 .wbt 到画布 → 插入图纸）
        class FlowListWidget(QListWidget):
            """支持把流程图(.wbt)拖到画布插入的列表。"""
            def mimeTypes(self):
                return ["application/x-wbt-path", "text/plain"]

            def mimeData(self, items):
                import pickle
                md = QMimeData()
                paths = []
                for it in items:
                    p = it.data(Qt.UserRole)
                    if p:
                        paths.append(p)
                if paths:
                    try:
                        md.setData("application/x-wbt-path", pickle.dumps(paths))
                    except Exception:
                        pass
                    md.setText(os.path.basename(paths[0]))
                return md

        self.flow_list = FlowListWidget()
        self.flow_list.setDragEnabled(True)
        self.flow_list.setDefaultDropAction(Qt.CopyAction)
        self.flow_list.itemDoubleClicked.connect(self._on_flow_list_dblclick)
        self.flow_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.flow_list.customContextMenuRequested.connect(self._on_flow_list_context_menu)
        self.flow_list.setStyleSheet(
            "QListWidget::item:selected { background-color: #0078d4; color: white; }"
        )
        # 键盘删除
        self.flow_list.keyPressEvent = lambda ev: (
            self._delete_selected_flow() if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.flow_list.currentItem()
            else QListWidget.keyPressEvent(self.flow_list, ev)
        )
        bottom_widget = QWidget()
        bv = QVBoxLayout(bottom_widget)
        bv.setContentsMargins(0, 0, 0, 0)
        # 标题行：标题 + 新建图纸 + 导入图纸（简约贴合背景）
        _flow_title = QWidget()
        _flow_title_row = QHBoxLayout(_flow_title)
        _flow_title_row.setContentsMargins(0, 0, 0, 0)
        _flow_title_row.setSpacing(4)
        _flow_title_lbl = QLabel("📁 已保存流程图")
        _flow_title_lbl.setStyleSheet("color:#bbb; font-weight:bold;")
        _flow_title_row.addWidget(_flow_title_lbl)
        _flow_title_row.addStretch()
        self.btn_new_drawing = QPushButton("＋ 新建图纸")
        self.btn_new_drawing.setStyleSheet(
            "QPushButton { background:transparent; color:#8ab8ff; "
            "border:1px solid #3a4a5a; border-radius:3px; padding:1px 6px; "
            "font-size:11px; }"
            "QPushButton:hover { background:#1e3a5a; }"
        )
        self.btn_new_drawing.setToolTip("新建图纸：创建新选项卡并保存占位到流程图栏")
        self.btn_new_drawing.clicked.connect(self._on_new_drawing_clicked)
        _flow_title_row.addWidget(self.btn_new_drawing)
        self.btn_import_drawing = QPushButton("⇪ 导入图纸")
        self.btn_import_drawing.setStyleSheet(
            "QPushButton { background:transparent; color:#8ab8ff; "
            "border:1px solid #3a4a5a; border-radius:3px; padding:1px 6px; "
            "font-size:11px; }"
            "QPushButton:hover { background:#1e3a5a; }"
        )
        self.btn_import_drawing.setToolTip("导入图纸：把非默认文件夹的 .wbt 导入到流程图文件夹")
        self.btn_import_drawing.clicked.connect(self._on_import_drawing_clicked)
        _flow_title_row.addWidget(self.btn_import_drawing)
        bv.addWidget(_flow_title)
        bv.addWidget(self.flow_list)

        # 上下两栏用垂直分割器，可拖动调整大小（默认 API 入口占大头）
        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.addWidget(top_widget)
        v_splitter.addWidget(bottom_widget)
        v_splitter.setSizes([420, 220])
        v_splitter.setChildrenCollapsible(False)
        lv.addWidget(v_splitter, 1)

        # ---- 竖条 + 收起三角：贴在左栏的**右边缘**（靠画板那侧），垂直居中 ----
        # 收起时同时隐藏 _left_body（两块内容一起走），这样 left 的最小宽度
        # 只剩竖条本身，QSplitter 就能把它压到细边宽。
        _left_bar, _left_bar_lay = _make_collapse_bar()
        self._left_tri = CollapseTriangle(
            left, _left_body, _left_bar, collapse_to_width=COLLAPSE_BAR_W,
            sym_expanded='◀', sym_collapsed='▶',
            tip='收起 / 放出整个左侧栏（给画板腾出宽度）')
        _left_bar_lay.insertWidget(1, self._left_tri)
        _left_outer.addWidget(_left_body, 1)
        _left_outer.addWidget(_left_bar)

        splitter.addWidget(left)

        # ---- 多流程图选项卡：内容区堆叠（每个选项卡独立场景/视图/日志/输出） ----
        self._content_stack = QStackedWidget()
        splitter.addWidget(self._content_stack)

        splitter.setSizes([300, 1200])
        # ---- 默认给画板让出更多宽度 ----
        # setSizes 只管初始宽度，**拉伸因子**才是关键：
        #   · 侧栏设 0 → 窗口拉大时保持自己的宽度不变；
        #   · 画板设 1 → 多出来的宽度**全部**归它。
        # 不设的话 Qt 按比例把新增宽度摊给两边，侧栏跟着一起变胖，
        # 画板反而占不到便宜（实测：1500 窗口下侧栏被摊到 423px，画板只剩 683px）。
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        main.addWidget(splitter)

        # ---- 底部状态栏（左下角：状态 + 进度条 + 取消按钮）----
        # 空闲时整行隐藏（不占任何空间），仅流程执行时显示
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(2, 2, 2, 2)
        self._status_label = QLabel("✅ 就绪")
        self._status_label.setStyleSheet("color:#aaa; font-size:11px; padding:2px;")
        self._status_label.setMinimumWidth(120)  # 固定最小宽度，避免文本变化导致布局抖动
        self._status_label.setVisible(False)
        status_layout.addWidget(self._status_label)

        self._status_bar = QProgressBar()
        self._status_bar.setRange(0, 100)
        self._status_bar.setFixedSize(240, 18)
        self._status_bar.setVisible(False)
        self._status_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #3a5a3a; border-radius: 3px; "
            "background: #1e1e1e; text-align: center; font-size:9px; color:#ccc; }"
            "QProgressBar::chunk { background-color: #4a9a4a; border-radius: 2px; }"
        )
        status_layout.addWidget(self._status_bar)

        self._status_cancel_btn = QPushButton("✕ 取消下载")
        self._status_cancel_btn.setVisible(False)
        self._status_cancel_btn.setFixedHeight(22)
        self._status_cancel_btn.setStyleSheet(
            "QPushButton { background:#5a2a2a; color:#ff9999; border-radius:3px; "
            "padding:2px 10px; font-size:11px; }"
            "QPushButton:hover { background:#7a3a3a; }"
        )
        self._status_cancel_btn.clicked.connect(self._cancel_download)
        status_layout.addWidget(self._status_cancel_btn)
        status_layout.addStretch()
        main.addLayout(status_layout)

    def _show_status(self):
        """显示底部状态栏（整个流程期间保持显示，不闪烁不跳行）"""
        self._status_label.setVisible(True)
        self._status_bar.setVisible(True)
        self._status_cancel_btn.setVisible(True)

    def _cancel_download(self):
        """取消当前下载（标志位跨整个流程生效，事件循环会拾取）"""
        self._inner_cancel = True      # 阻止后续所有请求
        self._cancel_pressed = True    # 让当前 _threaded_request 循环退出
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.stop()
        self._status_label.setText("⛔ 正在取消...")

    def _update_status_progress(self, received, total):
        """更新底部进度条"""
        if total > 0:
            pct = min(100, int(received * 100 / total))
            self._status_bar.setRange(0, 100)
            self._status_bar.setValue(pct)
            self._status_bar.setFormat(f"{pct}%  ({received/1024:.0f}/{total/1024:.0f} KB)")
        else:
            self._status_bar.setRange(0, 0)
            self._status_bar.setFormat(f"下载中 {received/1024:.0f} KB")
        self._status_label.setText("⬇️ 下载中...")

    def _reset_status(self):
        """重置底部状态栏为就绪（整行隐藏，不占空间）"""
        self._status_label.setText("✅ 就绪")
        self._status_label.setVisible(False)
        self._status_bar.setVisible(False)
        self._status_cancel_btn.setVisible(False)
        self._cancel_pressed = False
        self._running = False        # 运行结束，解锁选项卡切换/关闭

    def _wait_interruptible(self, secs):
        """可中断的等待：保持 UI 响应，等待期间可被取消按钮打断。
        返回 False 表示已被取消。"""
        deadline = time.time() + secs
        while time.time() < deadline and not self._inner_cancel:
            QApplication.processEvents()
            time.sleep(0.05)
        return not self._inner_cancel

    def _populate_api_list(self):
        self.api_tree.clear()
        sources = self.config.get('image_sources', [])
        for src in sources:
            # 文件夹名称格式：[基础URL] + 原ID
            base_url = src.get('base_url', '').strip()
            src_name = src.get('name', '')
            if base_url:
                src_label_text = f"📂 [{base_url}] {src_name}"
            else:
                src_label_text = f"📂 {src_name}"
            src_item = QTreeWidgetItem([src_label_text])
            src_item.setData(0, Qt.UserRole, src)
            src_item.setData(0, Qt.UserRole + 1, 'source')
            src_item.setToolTip(0, f"基础URL: {base_url or '(未设置)'}")
            src_item.setFlags(src_item.flags() | Qt.ItemIsDragEnabled)
            self.api_tree.addTopLevelItem(src_item)
            endpoints = src.get('endpoints', [])
            for ep in endpoints:
                method = ep.get('method', 'GET')
                path = ep.get('path', '/')
                desc = ep.get('description', '')
                label = f"  [{method}] {path}"
                if desc:
                    label += f"  — {desc[:25]}"
                ep_item = QTreeWidgetItem([label])
                ep_item.setData(0, Qt.UserRole, (src, ep))
                ep_item.setData(0, Qt.UserRole + 1, 'endpoint')
                # 增强悬浮信息：多行显示 完整URL + 描述
                tooltip_lines = [f"{method} {base_url}{path}"]
                if desc:
                    tooltip_lines.append(f"📝 {desc}")
                ep_item.setToolTip(0, "\n".join(tooltip_lines))
                ep_item.setFlags(ep_item.flags() | Qt.ItemIsDragEnabled)
                src_item.addChild(ep_item)
            src_item.setExpanded(True)

    def _add_api_node(self, src, ep):
        """从 API 入口创建 APINode 加入场景"""
        node = APINode()
        node.api_ref = src
        node.set_api(ep['method'], ep['path'], ep.get('parameters', []),
                     ep.get('headers', {}))
        self.scene.add_node(node)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(center.x() - node.rect().width()/2, center.y() - node.rect().height()/2)

    def _on_api_tree_dblclick(self, item, column):
        """双击树节点 → 生成 API 节点"""
        item_type = item.data(0, Qt.UserRole + 1)
        if item_type == 'source':
            # 双击源：展开/折叠
            item.setExpanded(not item.isExpanded())
            return
        elif item_type == 'endpoint':
            data = item.data(0, Qt.UserRole)
            if data:
                src, ep = data
                self._add_api_node(src, ep)

    def _on_add_db_clicked(self):
        """添加数据库节点（CSV/XLSX）"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择数据库文件", "",
            "数据文件 (*.csv *.xlsx *.xls);;CSV 文件 (*.csv);;Excel 文件 (*.xlsx *.xls);;所有文件 (*.*)"
        )
        if not file_path:
            return
        node = CsvDataNode(file_path)
        self.scene.add_node(node)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(center.x() - node.rect().width()/2, center.y() - node.rect().height()/2)

    def _on_add_container_clicked(self):
        """添加容器框节点"""
        node = ContainerNode()
        self.scene.add_node(node)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(center.x() - node.rect().width()/2, center.y() - node.rect().height()/2)

    def _on_add_stepper_clicked(self):
        """工具栏 → 添加步进器特殊元素框"""
        node = StepperNode()
        self.scene.add_node(node)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(center.x() - node.rect().width()/2, center.y() - node.rect().height()/2)

    def _on_add_site_parser_clicked(self):
        """工具栏 → 添加站点解析元素框（输入 URL → 识别站点 → 解析器下载 → 存容器根文件夹）"""
        node = SiteParserNode()
        self.scene.add_node(node)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(center.x() - node.rect().width() / 2,
                    center.y() - node.rect().height() / 2)

    # ---------- 图源配置变更检测 & 刷新 ----------
    @staticmethod
    def _compute_config_hash(config):
        """计算图源配置部分的哈希值用于检测变更"""
        sources = config.get('image_sources', [])
        return hashlib.md5(
            json.dumps(sources, sort_keys=True, ensure_ascii=False).encode('utf-8')
        ).hexdigest()

    def _find_outdated_api_nodes(self):
        """
        找出当前画布中已过时的 API 节点。
        返回: [(node, source_name, new_ep, orphaned_params, headers_changed), ...]
        """
        outdated = []
        sources = {src.get('name', ''): src for src in self.config.get('image_sources', [])}
        for node in self.scene.nodes:
            if not isinstance(node, APINode):
                continue
            old_src = node.api_ref or {}
            old_name = old_src.get('name', '')
            if old_name not in sources:
                continue  # 源不存在，跳过
            new_src = sources[old_name]
            # 在旧源中查找匹配的 endpoint
            old_method = node.method
            old_path = node.url_path
            found = None
            for ep in new_src.get('endpoints', []):
                if ep.get('method', 'GET') == old_method and ep.get('path', '/') == old_path:
                    found = ep
                    break
            old_params = {p['name']: p.get('value', '') for p in node.params}
            if found:
                new_params = {p['name']: p.get('value', '') for p in found.get('parameters', [])}
                # 检查是否有旧参数在新配置中已不存在
                orphaned = [n for n in old_params if n not in new_params]
                # 请求头差异也算过时：否则「在图源配置里改了请求头，画布节点纹丝不动」，
                # 用户会误判成功能没生效。老配置没有 headers 键时不参与比较，
                # 避免把用户手工加的节点级请求头误报成过时。
                ep_headers = found.get('headers', None)
                headers_changed = (ep_headers is not None
                                   and dict(node.headers or {}) != dict(ep_headers or {}))
                if orphaned or headers_changed:
                    outdated.append((node, old_name, found, orphaned, headers_changed))
            else:
                outdated.append((node, old_name, None, list(old_params.keys()), False))
        return outdated

    def _refresh_outdated_api_nodes(self, nodes_to_update):
        """刷新指定 API 节点的 UI 展示（保留位置）"""
        for node, src_name, new_ep, _orphaned, _hdr_changed in nodes_to_update:
            if new_ep is None:
                continue  # endpoint 已删除，跳过
            # 查找最新 source 配置
            for src in self.config.get('image_sources', []):
                if src.get('name', '') == src_name:
                    new_src = src
                    break
            else:
                continue
            # 保留原有参数值
            old_values = {p['name']: p.get('value', '') for p in node.params}
            pos = node.pos()
            node.api_ref = dict(new_src)
            # new_ep 没有 headers 键时传 None → set_api 保持节点现有请求头不动
            new_headers = new_ep.get('headers', None)
            node.set_api(new_ep['method'], new_ep['path'], new_ep.get('parameters', []),
                         new_headers)
            # 恢复已有参数的值
            for p in node.params:
                if p['name'] in old_values:
                    p['value'] = old_values[p['name']]
                    if p['name'] in node.param_widgets:
                        node.param_widgets[p['name']].edit.setText(old_values[p['name']])
            node.setPos(pos)
            # set_api 内部已调用 _build_ui → update_node_size，无需再手动调用

    def on_config_externally_changed(self, confirm=True):
        """
        当外部（main.py 的 config_saved 信号）通知配置已修改时调用。
        检测过时节点；confirm=True 时弹出询问对话框，confirm=False 直接刷新。

        返回是否执行了刷新。
        """
        new_hash = self._compute_config_hash(self.config)
        if new_hash == self._config_hash:
            return False  # 未变更
        self._config_hash = new_hash
        outdated = self._find_outdated_api_nodes()
        if not outdated:
            return False
        # 构建提示信息
        msg_lines = ["🔄 图源配置已更改，是否更新可视化信息？\n（可能会导致部分原信息被孤立，注意甄别）\n"]
        for _node, src_name, new_ep, orphaned, headers_changed in outdated:
            if new_ep:
                info = f"  • [{src_name}] {_node.method} {_node.url_path}"
                if orphaned:
                    info += f"  ⚠ 参数将丢失: {', '.join(orphaned)}"
                if headers_changed:
                    info += "  🔧 请求头将同步为图源配置"
                msg_lines.append(info)
            else:
                msg_lines.append(f"  • [{src_name}] {_node.method} {_node.url_path}  → 已删除")
        msg = '\n'.join(msg_lines)
        if confirm:
            reply = QMessageBox.question(
                self, "图源配置已更改", msg,
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return False
        else:
            # 已在外部确认（main.py 的"是否同步可视化界面"弹窗），直接刷新并记录日志
            self.api_log_text.append("🔄 已根据最新图源配置同步可视化节点:")
            for _node, src_name, new_ep, _orphaned, _hdr_changed in outdated:
                if new_ep:
                    _extra = "（含请求头）" if _hdr_changed else ""
                    self.api_log_text.append(f"  • 已更新: [{src_name}] {_node.method} {_node.url_path}{_extra}")
                else:
                    self.api_log_text.append(f"  • 已移除: [{src_name}] {_node.method} {_node.url_path}")
        self._refresh_outdated_api_nodes(outdated)
        self.scene._save_undo()
        return True

    # ================= 保存 / 加载流程图 (.wbt) =================
    @staticmethod
    def _get_webtree_dir():
        d = os.path.join(_project_root(), "data", "webtree")
        os.makedirs(d, exist_ok=True)
        return d

    def _refresh_flow_list(self):
        """刷新已保存流程图列表"""
        self.flow_list.clear()
        wd = self._get_webtree_dir()
        if not os.path.isdir(wd):
            return
        for fn in sorted(os.listdir(wd)):
            if fn.lower().endswith('.wbt'):
                item = QListWidgetItem(fn)
                item.setData(Qt.UserRole, os.path.join(wd, fn))
                self.flow_list.addItem(item)

    def _on_flow_list_dblclick(self, item):
        """双击已保存流程图 → 在选项卡中打开（已打开则切换过去）"""
        path = item.data(Qt.UserRole)
        if path and os.path.exists(path):
            self._open_flow_in_tab(path)

    def _on_flow_list_context_menu(self, pos):
        """已保存流程图右键菜单"""
        item = self.flow_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        menu = QMenu(self)
        rename_action = menu.addAction("✏️ 编辑名称")
        reveal_action = menu.addAction("📂 在资源管理器中打开")
        delete_action = menu.addAction("🗑️ 删除")
        menu.addSeparator()
        info_action = menu.addAction(f"📄 {os.path.basename(path)}")
        info_action.setEnabled(False)
        action = menu.exec(self.flow_list.mapToGlobal(pos))
        if action == rename_action:
            self._rename_flow_file(item, path)
        elif action == reveal_action:
            self._reveal_flow_file_in_explorer(path)
        elif action == delete_action:
            self._delete_flow_file(item, path)

    def _reveal_flow_file_in_explorer(self, path):
        """在资源管理器中打开该图纸所在的文件夹（Windows 下顺便选中它）。"""
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "打开失败", f"文件不存在：{path}")
            return
        try:
            ok, info = _reveal_in_explorer(path)
        except Exception as e:
            ok, info = False, str(e)
        if not ok:
            QMessageBox.warning(self, "打开失败",
                                f"无法打开文件夹：{os.path.dirname(path)}\n{info or ''}")

    def _rename_flow_file(self, item, path):
        """重命名流程图文件"""
        old_name = os.path.splitext(os.path.basename(path))[0]
        new_name, ok = QInputDialog.getText(
            self, "重命名流程图", "新名称:", text=old_name
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip().replace('.', '_').replace('/', '_')
        new_path = os.path.join(os.path.dirname(path), f"{new_name}.wbt")
        if os.path.exists(new_path):
            QMessageBox.warning(self, "重命名失败", f"文件 {new_name}.wbt 已存在")
            return
        try:
            os.rename(path, new_path)
            self._refresh_flow_list()
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", str(e))

    def _delete_flow_file(self, item, path):
        """删除流程图文件"""
        name = os.path.basename(path)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除流程图 \"{name}\" 吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        try:
            os.remove(path)
            self._refresh_flow_list()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def _delete_selected_flow(self):
        """键盘删除当前选中的流程图"""
        item = self.flow_list.currentItem()
        if item:
            path = item.data(Qt.UserRole)
            if path:
                self._delete_flow_file(item, path)

    def _save_flow_to(self, filepath, show_success=True, include_transfer=True):
        """将当前场景保存到指定路径；返回是否成功。

        include_transfer=False ⇒ 本次不把数据传递参数写进图纸。
        """
        data = self._serialize_flow(include_transfer=include_transfer)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._current_file = filepath
            if include_transfer:
                # 参数已经落盘 → 解除「待更新」标记（下次保存不再追问）
                self._clear_transfer_pending()
            # 同步当前选项卡标题（保存成功 → 清除更改标记，去掉 * 前缀）
            if 0 <= self._active_tab < len(self._tabs):
                _n = os.path.splitext(os.path.basename(filepath))[0]
                self._tabs[self._active_tab]['name'] = _n
                self._tabs[self._active_tab]['file'] = filepath
                self._tabs[self._active_tab]['_dirty'] = False
                self._dirty = False
                self._update_tab_title(self._active_tab)
            self._refresh_flow_list()
            if show_success:
                QMessageBox.information(self, "保存成功",
                    f"流程图已保存到 {os.path.basename(filepath)}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存流程图时出错:\n{str(e)}")
            return False

    def _save_flow(self):
        """保存（按钮版）：有当前文件则询问覆盖，否则另存为；返回是否保存成功。"""
        if not self.scene.nodes:
            QMessageBox.information(self, "提示", "画布上没有节点，无需保存")
            return False
        if self._current_file and os.path.exists(self._current_file):
            reply = QMessageBox.question(
                self, "保存",
                f"当前文件：{os.path.basename(self._current_file)}\n是否覆盖源文件？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.Yes:
                # 覆盖确认之后的**第二次**询问：是否一并更新下载参数
                _inc = self._ask_update_transfer()
                return self._save_flow_to(self._current_file,
                                          include_transfer=_inc)
            elif reply == QMessageBox.No:
                pass  # 跳到另存为
            else:
                return False
        return self._save_flow_as()

    def _save_flow_as(self):
        """另存为：弹出命名框；返回是否保存成功。"""
        if not self.scene.nodes:
            return False
        name, ok = QInputDialog.getText(
            self, "另存为", "请输入流程名称:",
            text=f"flow_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        if not ok or not name.strip():
            return False
        name = name.strip().replace('.', '_').replace('/', '_')
        filepath = os.path.join(self._get_webtree_dir(), f"{name}.wbt")
        if os.path.exists(filepath):
            reply = QMessageBox.question(self, "覆盖确认", f"文件 {name}.wbt 已存在，是否覆盖？",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return False
        # 覆盖确认之后的**第二次**询问：是否一并更新下载参数
        _inc = self._ask_update_transfer()
        return self._save_flow_to(filepath, include_transfer=_inc)

    def _load_flow(self):
        """打开文件对话框选择 .wbt 文件并加载"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载流程图", self._get_webtree_dir(),
            "流程图文件 (*.wbt);;所有文件 (*.*)"
        )
        if filepath:
            self._load_flow_from(filepath)

    def _load_flow_from(self, filepath):
        """从指定 .wbt 文件加载流程图"""
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "加载失败", "文件不存在")
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "读取失败", f"无法读取文件:\n{str(e)}")
            return

        # 清空当前场景 + 重置撤销栈
        self._clear_scene()
        self.undo_mgr = UndoManager(self, max_steps=20)
        self.scene._undo_save_cb = self._on_undo_saved
        self._current_file = filepath  # 记住当前文件
        # 同步当前选项卡标题
        if 0 <= self._active_tab < len(self._tabs):
            _n = os.path.splitext(os.path.basename(filepath))[0]
            self._tabs[self._active_tab]['name'] = _n
            self._tabs[self._active_tab]['file'] = filepath
        self._update_tab_title(self._active_tab)
        # 反序列化
        warnings = []
        node_map = {}
        for idx, ndata in enumerate(data.get('nodes', [])):
            node = self._deserialize_node(ndata, warnings)
            if node is None:
                continue
            node_map[idx] = node
            self.scene.add_node(node)
            node.setPos(ndata.get('pos_x', 0), ndata.get('pos_y', 0))

        # 恢复 DataProcessNode 的 _csv_source 引用
        for idx, ndata in enumerate(data.get('nodes', [])):
            if ndata.get('type') == 'DataProcessNode' and idx in node_map:
                dp = node_map[idx]
                csv_file = ndata.get('csv_source_file')
                csv_header = ndata.get('csv_source_header')
                if csv_file and csv_header:
                    for n in node_map.values():
                        if isinstance(n, CsvDataNode) and n.file_path == csv_file:
                            dp._csv_source = (n, csv_header)
                            # 刷新显示为实际值
                            val = n.get_current_value(csv_header)
                            dp.set_source_data(val)
                            break

        # 恢复拼合信息栏（_load_flow_from）
        for idx, ndata in enumerate(data.get('nodes', [])):
            if ndata.get('type') == 'APINode' and idx in node_map:
                api = node_map[idx]
                for cw_data in ndata.get('concat_widgets', []):
                    cid = api.add_concat_field(anchor_param=cw_data.get('anchor_param', ''))
                    for cw in api._concat_widgets:
                        if cw.concat_id == cid:
                            if cw_data.get('source_path'):
                                cw.source_path = cw_data['source_path']
                            if cw_data.get('value'):
                                cw.edit.setText(cw_data['value'])
                                cw.concat_value = cw_data['value']
                                cw.clear_btn.show()
                            break

        # 类嵌套方案：先恢复类框（类间连线需要类对象）
        self._restore_classes(data, node_map)
        # 重建连线
        for cdata in data.get('connections', []):
            si, ei = cdata.get('start_node'), cdata.get('end_node')
            ep = cdata.get('end_param')
            sh = cdata.get('start_header')  # CsvDataNode 的 header
            if si in node_map and ei in node_map:
                start_node = node_map[si]
                end_node = node_map[ei]
                # 起始端口函数
                if isinstance(start_node, CsvDataNode):
                    hd = sh or ''
                    sf = lambda n=start_node, h=hd: n.output_port_for_header(h)
                elif isinstance(start_node, DataProcessNode):
                    sf = start_node.outputPort
                else:
                    sf = start_node.outputPort if hasattr(start_node, 'outputPort') else (lambda: QPointF())
                # 终点端口函数
                if isinstance(end_node, APINode):
                    if ep and ep.startswith('concat_'):
                        ef = lambda n=end_node, c=ep: n.concat_input_port(c)
                    else:
                        ef = lambda n=end_node, p=ep: n.param_input_port(p)
                elif isinstance(end_node, DataProcessNode):
                    ef = end_node.inputPort
                elif isinstance(end_node, ContainerNode):
                    # 规则模式：拼合文本段连线应指向其专属输入点，而非主输入点
                    if ep and ep.startswith('rule_'):
                        ef = lambda n=end_node, p=ep: n.rule_part_input_port(p)
                    elif ep == 'root':
                        ef = end_node.root_path_input_port
                    else:
                        ef = end_node.inputPort
                elif isinstance(end_node, StepperNode):
                    if ep and ep != 'main':
                        ef = lambda n=end_node, k=ep: n.input_port_for_key(k)
                    else:
                        ef = end_node.inputPort
                elif isinstance(end_node, SiteParserNode):
                    ef = end_node.inputPort
                elif isinstance(end_node, CsvDataNode):
                    # 数据库框的输入端（上游数据流入本表）
                    ef = end_node.inputPort
                elif isinstance(end_node, ClassNode):
                    ef = end_node.inputPort if not getattr(end_node, '_is_main', False) else end_node.outputPort
                else:
                    ef = lambda: QPointF()
                conn = ConnectionPath(sf, ef, start_node, end_node, ep, start_param=sh)
                self.scene.addItem(conn)
                self.scene.connections.append(conn)

        # 实时预览推送：加载后让下游元素框（如站点解析识别预览）立即更新
        for nd in self.scene.nodes:
            if isinstance(nd, (DataProcessNode, CsvDataNode)):
                self.scene.push_preview_downstream(nd)

        # 数据传递参数：图纸里有 transfer 就用它；老图纸没有 → 用默认值在内存里
        # 「缓冲更新」一份并标记待落盘，保存时会再问一次是否写进图纸。
        self._apply_transfer_from_data(data)

        # 恢复右侧"数据处理/输出"标签页的上次保存历史记录
        self._restore_saved_outputs()
        # 刷新列表
        self._refresh_flow_list()
        # 显示警告
        if warnings:
            QMessageBox.warning(self, "加载完成（含警告）",
                                "\n".join(warnings) + "\n\n已自动移除失效元素及其连线。")
        else:
            QMessageBox.information(self, "加载成功", f"已加载 {len(node_map)} 个节点")
        # 记录加载后的初始状态
        self.undo_mgr.save()
        self._clear_dirty()   # 加载后视为未更改（标题去掉 *）

    # ================= API 日志：纯文本 / 富文本切换（数据处理选项卡） =================
    def _on_log_tab_clicked(self, index):
        """点击"数据处理"选项卡按钮：已是当前页时再点 → 立即切换显示模式。"""
        if index != 0:
            return
        if self.log_tabs.currentIndex() != 0:
            return  # 从其他页切回本页：只切页，不切换显示模式
        if self._log_rich_mode:
            self._exit_rich_mode()
        else:
            self._enter_rich_mode()

    def _update_log_tab_style(self):
        """富文本模式时"数据处理"选项卡按钮边框变绿（仅 index 0）。"""
        try:
            bar = self.log_tabs.tabBar()
            if hasattr(bar, 'set_log_rich'):
                bar.set_log_rich(self._log_rich_mode)
        except Exception:
            pass

    # ---------- 日志窗口（纯文本 / 富文本共用同一个窗口，保证两种状态页面同步） ----------
    def _log_win(self):
        """当前日志窗口 [start, end)（行号，基于 self._log_lines）。"""
        start = max(0, int(self._rich_window_start))
        end = min(len(self._log_lines), int(self._rich_window_end))
        if end < start:
            end = start
        return start, end

    def _log_set_window(self, start, end):
        """设置窗口并更新"是否跟随尾部"标志（两种显示状态都读它）。"""
        total = len(self._log_lines)
        start = max(0, min(int(start), total))
        end = max(start, min(int(end), total))
        # 页面保持对齐到着色批次边界，避免 JSON/HTML 块被切成两半
        start = min(start, max(0, end - 1))
        self._rich_window_start = start
        self._rich_window_end = end
        self._log_follow_tail = (end >= total)

    def _log_goto_tail(self):
        """窗口跳到最新一页（跟随实时输出）。"""
        total = len(self._log_lines)
        self._log_set_window(max(0, total - LOG_PAGE_SIZE), total)

    def _enter_rich_mode(self):
        """纯文本 → 富文本：保持同一窗口与同一可视首行（页面同步）。"""
        # 切富文本前先把待插入的纯文本行**冲刷掉**（更新视图与日志窗口），再切模式。
        # 注意：不能简单丢弃缓冲 —— 那会让"窗口"停在旧位置，切回纯文本时这些行就看不见了；
        # 也不能留着缓冲 —— 切回纯文本后定时器会再插一遍 → 重复行。所以必须"先冲再切"。
        try:
            self._flush_plain_append()      # 此刻 _log_rich_mode 仍为 False，走正常插入路径
        except Exception:
            pass
        if self._plain_flush_timer is not None:
            try:
                self._plain_flush_timer.stop()
            except Exception:
                pass
        if self._plain_buf:
            del self._plain_buf[:]
        self._log_rich_mode = True
        self._rich_block_cache.clear()  # 新会话清空着色缓存
        self.api_log_text.hide()
        self._rich_log_text.show()
        if self._log_follow_tail:
            # 跟随尾部时直接贴底：锚定首行会让富文本停在页面中部，
            # 之后新日志就不再自动跟随了（两种状态的"跟随"必须一致）
            self._rich_rerender(direction='down')
        else:
            # 纯文本块号是"窗口内相对行号"，加上窗口起点才是日志行号
            anchor = self._rich_window_start + self._plain_first_visible_line()
            self._rich_rerender(anchor_log_line=anchor)
        self._update_log_tab_style()

    def _exit_rich_mode(self):
        """富文本 → 纯文本：保持同一窗口与同一可视首行（页面同步）。"""
        self._log_rich_mode = False
        anchor = self._rich_window_start + self._rich_first_visible_line()
        self._rich_log_text.hide()
        self.api_log_text.show()
        self._render_plain_window(anchor_line=anchor)
        self._update_log_tab_style()

    def _render_plain_window(self, anchor_line=None):
        """按当前窗口渲染纯文本视图（只在翻页/切换显示状态时整页重绘）。

        anchor_line：渲染后保持该日志行可见；为 None 时跟随尾部或维持滚动比例。
        """
        start, end = self._log_win()
        text = '\n'.join(self._log_lines[start:end])
        vsb = self.api_log_text.verticalScrollBar()
        ratio = vsb.value() / max(1, vsb.maximum()) if vsb.maximum() > 0 else 0.0
        self._suppress_scroll_load = True
        try:
            self.api_log_text.setPlainText(text)
            vsb2 = self.api_log_text.verticalScrollBar()
            if anchor_line is not None:
                self._scroll_plain_to_line(int(anchor_line) - start)
            elif self._log_follow_tail:
                vsb2.setValue(vsb2.maximum())
            else:
                vsb2.setValue(int(ratio * vsb2.maximum()))
        finally:
            self._suppress_scroll_load = False

    def _flush_plain_append(self, tab=None):
        """把缓冲的日志行**一次性批量**插入纯文本视图（去抖，见 PLAIN_FLUSH_MS）。

        - `tab` 由定时器闭包绑定：**只冲刷"当前生效"的选项卡** —— 避免切页后旧定时器
          把 A 的缓冲塞进 B 的编辑器；缓冲不会丢（切回该选项卡时会再冲一次）。
        - 清缓冲用 `del buf[:]`（保留同一个 list 对象，`_TAB_STATE_KEYS` 的绑定才不会断）。
        """
        buf = self._plain_buf
        timer = self._plain_flush_timer
        if tab is not None:
            active = (self._tabs[self._active_tab]
                      if 0 <= self._active_tab < len(self._tabs) else None)
            if tab is not active:
                return
            buf = tab.get('_plain_buf', buf)
            timer = tab.get('_plain_flush_timer', timer)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        if not buf:
            return
        lines = list(buf)
        del buf[:]
        if self._log_rich_mode:
            return            # 已切富文本：这些行已在 _log_lines 里，交给富文本刷新
        self._plain_append_lines(lines)

    def _plain_append_lines(self, lines):
        """跟随尾部时的增量追加（快路径）：超过一页由文档自动裁掉最旧的行。

        性能红线（第十轮修复，实测数据见 HANDOFF-DSH\\12_LOG_STUTTER.md）：
        整个追加过程都必须屏蔽滚动回调 —— `QTextEdit.append()` 会把光标带到末尾并
        自动滚动，从而同步触发 `valueChanged` → `_on_plain_scrolled` →
        `_plain_load_more` → `_render_plain_window`，结果变成「每追加一行就整页
        setPlainText 一次」。实测 3000 次追加耗时 40.1s（13.4 ms/行、p95 17.6 ms），
        期间整页重绘被触发 2962 次 —— 这就是跑码时肉眼可见的卡顿。
        另外用一次 `insertText` 批量落字，避免 N 行产生 N 次独立排版。
        """
        ed = self.api_log_text
        lines = [ln for ln in lines]
        if not lines:
            return
        self._suppress_scroll_load = True
        try:
            doc = ed.document()
            try:
                if doc.maximumBlockCount() != LOG_PAGE_SIZE:
                    doc.setMaximumBlockCount(LOG_PAGE_SIZE)
            except Exception:
                pass
            # 批量插入：块号语义与逐行 append 一致，但只触发一次文档排版
            try:
                cur = QTextCursor(doc)
                cur.movePosition(QTextCursor.MoveOperation.End)
                body = '\n'.join(lines)
                cur.insertText(body if doc.isEmpty() else '\n' + body)
            except Exception:
                for ln in lines:            # 兜底：退回逐行（仍被上面的抑制保护）
                    try:
                        QTextEdit.append(ed, ln)
                    except Exception:
                        break
            # 文档最多保留 LOG_PAGE_SIZE 个块 ⇒ 窗口起点即 total - 块数
            try:
                kept = doc.blockCount()
            except Exception:
                kept = LOG_PAGE_SIZE
            total = len(self._log_lines)
            self._log_set_window(max(0, total - kept), total)
            if self._log_follow_tail:
                # 跟随尾部时把最新一行顶进视口（等价于原 append() 的自动滚动）
                try:
                    _vsb = ed.verticalScrollBar()
                    if _vsb.value() != _vsb.maximum():
                        _vsb.setValue(_vsb.maximum())
                except Exception:
                    pass
        finally:
            self._suppress_scroll_load = False

    def _first_visible_block(self, edit):
        """指定 QTextEdit 当前可视首行（0-based block 号，基于布局高度）。"""
        try:
            doc = edit.document()
            layout = doc.documentLayout()
            y = max(0, edit.verticalScrollBar().value())
            for i in range(doc.blockCount()):
                if layout.blockBoundingRect(doc.findBlockByNumber(i)).bottom() >= y:
                    return i
            return 0
        except Exception:
            return 0

    def _rich_first_visible_line(self):
        return self._first_visible_block(self._rich_log_text)

    def _plain_first_visible_line(self):
        """纯文本当前可视首行（0-based block 号）。"""
        return self._first_visible_block(self.api_log_text)

    def _scroll_plain_to_line(self, line):
        try:
            doc = self.api_log_text.document()
            line = max(0, min(int(line), doc.blockCount() - 1))
            block = doc.findBlockByNumber(line)
            if not block.isValid():
                return
            cur = QTextCursor(block)
            self.api_log_text.setTextCursor(cur)
            self.api_log_text.ensureCursorVisible()
        except Exception:
            pass

    def _scroll_rich_to_block(self, block_idx):
        """滚动富文本到指定块（块号 → 内容 y 坐标），用于翻页后平缓定位。"""
        try:
            doc = self._rich_log_text.document()
            block = doc.findBlockByNumber(min(int(block_idx), doc.blockCount() - 1))
            if not block.isValid():
                return
            layout = doc.documentLayout()
            y = int(layout.blockBoundingRect(block).top())
            vsb = self._rich_log_text.verticalScrollBar()
            vsb.setValue(max(0, y - 40))  # 上方留 40px 露出新加载内容
        except Exception:
            pass

    def _resolve_anchor_block(self, line_map, anchor_log_line):
        """在 line_map 中找 <= anchor_log_line 的最近块起点对应的块号。"""
        if anchor_log_line in line_map:
            return line_map[anchor_log_line]
        best = None
        for k, v in line_map.items():
            if k <= anchor_log_line and (best is None or k > best[0]):
                best = (k, v)
        return best[1] if best else None

    def _evict_rich_cache(self, ws, we):
        """淘汰完全在缓存区（窗口 ± 1 批缓冲）之外的块，限制缓存大小。

        着色块（JSON/HTML）滑出可见区仍保留在缓冲区内，翻回时直接复用不重绘。
        """
        buf = RICH_BATCH_SIZE
        low = ws - buf
        high = we + buf
        for bs in list(self._rich_block_cache.keys()):
            if bs < low or bs >= high:
                del self._rich_block_cache[bs]
        # 块数上限兜底（防止超大日志无限膨胀）：优先淘汰窗口之前的块
        # 窗口现在是 LOG_PAGE_SIZE 行，缓存上限随之放大（至少一整页，留 2 批余量）
        cap = LOG_PAGE_SIZE + 2 * RICH_BATCH_SIZE
        if len(self._rich_block_cache) > cap:
            over = len(self._rich_block_cache) - cap
            for bs in sorted(self._rich_block_cache.keys()):
                if bs >= ws:
                    continue
                del self._rich_block_cache[bs]
                over -= 1
                if over <= 0:
                    break

    def _rich_rerender(self, direction=None, anchor_log_line=None):
        """按当前窗口 [window_start, window_end) 重新渲染富文本。

        anchor_log_line：加载后保持该日志行可见（平缓过渡，不跳顶/底）。
        direction: 'down'→滚到底部（运行中追加场景）；None→保持滚动比例。
        渲染优先复用 _rich_block_cache 中的块（着色不丢、不重复解析）。
        """
        start = self._rich_window_start
        end = min(len(self._log_lines), self._rich_window_end)
        if end <= start:
            self._rich_log_text.clear()
            self._rich_doc_segs = []
            return
        vsb = self._rich_log_text.verticalScrollBar()
        ratio = vsb.value() / max(1, vsb.maximum()) if vsb.maximum() > 0 else 0.0
        html, line_map = _render_log_batch_html(
            self._log_lines, start, end, cache=self._rich_block_cache)
        self._evict_rich_cache(start, end)
        # setValue 会触发 valueChanged → 抑制期间忽略，防“往下滑又触发上翻”
        self._suppress_scroll_load = True
        try:
            self._rich_log_text.setHtml(
                f'<html><body style="font-family:Consolas, \'Courier New\', monospace; '
                f'font-size:12px; background:{_BG_COLOR}; color:{_COLOR_DEFAULT}; '
                f'margin:4px;">{html}</body></html>')
            # 整页渲染后重建增量账本：整篇内容 = 一段
            try:
                self._rich_doc_segs = [[int(start), int(end),
                                        self._rich_log_text.document().blockCount()]]
            except Exception:
                self._rich_doc_segs = []
            vsb2 = self._rich_log_text.verticalScrollBar()
            if anchor_log_line is not None:
                target = self._resolve_anchor_block(line_map, anchor_log_line)
                if target is not None:
                    self._scroll_rich_to_block(target)
                    return
            if direction == 'down':
                vsb2.setValue(vsb2.maximum())
            else:
                vsb2.setValue(int(ratio * vsb2.maximum()))
        finally:
            self._suppress_scroll_load = False

    def _rich_append_new(self, total):
        """富文本跟随尾部时的**增量追加**（快路径）。

        为什么值得单独写一条路径（第十轮实测，见 HANDOFF-DSH\\12_LOG_STUTTER.md）：
        整页 setHtml 渲染 1000 行窗口要 **52 ms**，其中 HTML 生成本身只占 3.7 ms，
        其余全是 Qt 的 HTML 解析 + 排版；而把新增的几行 insertHtml 进去只要
        **0.16 ms**。跑码期间防抖每 200ms 触发一次整页重渲 ⇒ 约三分之一到一半的
        墙钟时间耗在排版上，这就是富文本状态下的卡顿。

        做法：只渲染 [上次渲染末尾, total) 这一段新行，insertHtml 到文档末尾，
        再从**文档头部**删掉超出 LOG_PAGE_SIZE 行的旧段（按 _rich_doc_segs 账本
        记账，每段记录它产生了几个文档块）。

        返回 True=已增量刷新完毕；False=前提不成立，调用方应退回整页 _rich_rerender。
        """
        segs = self._rich_doc_segs
        if not segs:
            return False
        doc_start, doc_end, _n0 = segs[-1]
        if doc_end > total or doc_end != segs[-1][1]:
            return False
        if doc_end == total:
            return True                       # 没有新行，无需重画
        if not _is_log_block_boundary(self._log_lines, doc_end):
            return False                      # 追加点落在 JSON/HTML 块内部
        # 安全阀：账本异常膨胀（例如单次塞进超长日志）时交回整页重渲自愈
        if total - segs[0][0] > LOG_PAGE_SIZE * 3:
            return False
        rt = self._rich_log_text
        doc = rt.document()
        frag, _lmap = _render_log_batch_html(
            self._log_lines, doc_end, total, cache=self._rich_block_cache)
        if not frag:
            return True
        self._suppress_scroll_load = True
        try:
            before = doc.blockCount()
            if not doc.isEmpty():
                cur = QTextCursor(doc)
                cur.movePosition(QTextCursor.MoveOperation.End)
                # 先另起一段：否则 insertHtml 的**第一个** <div> 会被并进当前段落
                cur.insertBlock()
                cur.insertHtml(frag)
            else:
                rt.setHtml(f'<html><body style="font-family:Consolas, \'Courier New\', '
                           f'monospace; font-size:12px; background:{_BG_COLOR}; '
                           f'color:{_COLOR_DEFAULT}; margin:4px;">{frag}</body></html>')
            added = doc.blockCount() - before
            if added <= 0:
                return False
            segs.append([int(doc_end), int(total), int(added)])
            # 头部裁剪：**丢掉最旧一段后窗口仍不少于一页**才丢。
            # 为什么不是"一超页就丢最旧一段"：整页重渲留下的那一段本来就有一整页，
            # 一超页就丢它会让窗口瞬间只剩几行（用户往回滚就没内容了），而且下一次
            # 刷新会立刻又触发整页重渲，退化成"每批都重渲"。按本条规则，窗口在
            # 一页到两页之间浮动，稳态约每积累一页才裁剪一次。
            retained = sum(e - s for s, e, _c in segs)
            while len(segs) > 1 and retained - (segs[0][1] - segs[0][0]) >= LOG_PAGE_SIZE:
                s0, e0, c0 = segs[0]
                blk = doc.findBlockByNumber(c0)
                if not blk.isValid():
                    return False
                cur2 = QTextCursor(doc)
                cur2.setPosition(0)
                cur2.setPosition(blk.position(), QTextCursor.MoveMode.KeepAnchor)
                cur2.removeSelectedText()
                segs.pop(0)
                retained -= (e0 - s0)
            new_start = segs[0][0]
            self._rich_window_start = new_start
            self._rich_window_end = total
            self._log_follow_tail = True
            self._evict_rich_cache(new_start, total)
            vsb = rt.verticalScrollBar()
            if vsb.value() != vsb.maximum():
                vsb.setValue(vsb.maximum())
        finally:
            self._suppress_scroll_load = False
        return True

    def _rich_load_more(self, force_bottom=False):
        """向下加载：滚动接近底部时加载下一段；窗口满 LOG_PAGE_SIZE 行则前移删最旧一段。

        force_bottom=True（运行中追加）：加载后滚到底部看新内容；
        否则锚定到原窗口末尾行，平缓过渡（新内容在其下方）。
        """
        if self._suppress_scroll_load or not self._log_rich_mode:
            return
        total = len(self._log_lines)
        start, end = self._log_win()
        if end >= total:
            self._log_follow_tail = True
            return  # 已全部加载（窗口已到末尾）
        anchor = max(start, end - 1)
        if total - end > LOG_PAGE_SIZE * 2:
            # 落后超过两页（用户在翻历史时又产生了大量新日志）：直接跳到最新一页
            self._log_goto_tail()
            force_bottom = True
        elif end - start >= LOG_PAGE_SIZE:
            # 窗口已满：前移一段（并回溯到完整 JSON/HTML 块边界，防着色丢失）
            new_start = _snap_window_start(self._log_lines, start + RICH_BATCH_SIZE)
            self._log_set_window(new_start, min(total, new_start + LOG_PAGE_SIZE))
        else:
            self._log_set_window(start, min(total, end + RICH_BATCH_SIZE))
        if force_bottom:
            self._rich_rerender(direction='down')
        else:
            self._rich_rerender(anchor_log_line=anchor)

    def _rich_load_prev(self):
        """向上加载：滚动接近顶部时加载上一段，并保持原窗口首行可见（平缓过渡）。"""
        if self._suppress_scroll_load or not self._log_rich_mode:
            return
        start, end = self._log_win()
        if start <= 0:
            return  # 已到日志开头
        total = len(self._log_lines)
        anchor = start  # 原窗口首行：加载后保持其位置，新内容出现在上方
        new_start = _snap_window_start(
            self._log_lines, max(0, start - RICH_BATCH_SIZE))
        if new_start >= start:
            return  # 无新内容
        self._log_set_window(new_start, min(total, new_start + LOG_PAGE_SIZE))
        self._rich_rerender(anchor_log_line=anchor)

    # ---------- 纯文本视图的翻页（与富文本共用同一窗口） ----------
    def _plain_load_more(self):
        """纯文本向下翻一页（落后太多时直接跳最新一页）。"""
        if self._suppress_scroll_load or self._log_rich_mode:
            return
        total = len(self._log_lines)
        start, end = self._log_win()
        if end >= total:
            self._log_follow_tail = True
            return
        if total - end > LOG_PAGE_SIZE * 2:
            self._log_goto_tail()
            self._render_plain_window()
            return
        anchor = max(start, end - 1)
        if end - start >= LOG_PAGE_SIZE:
            new_start = _snap_window_start(self._log_lines, start + RICH_BATCH_SIZE)
            self._log_set_window(new_start, min(total, new_start + LOG_PAGE_SIZE))
        else:
            self._log_set_window(start, min(total, end + RICH_BATCH_SIZE))
        self._render_plain_window(anchor_line=anchor)

    def _plain_load_prev(self):
        """纯文本向上翻一页，保持原窗口首行可见。"""
        if self._suppress_scroll_load or self._log_rich_mode:
            return
        start, end = self._log_win()
        if start <= 0:
            return
        total = len(self._log_lines)
        anchor = start
        new_start = _snap_window_start(
            self._log_lines, max(0, start - RICH_BATCH_SIZE))
        if new_start >= start:
            return
        self._log_set_window(new_start, min(total, new_start + LOG_PAGE_SIZE))
        self._render_plain_window(anchor_line=anchor)

    def _on_plain_scrolled(self, value):
        """纯文本滚动条：滑到上/下边缘时加载相邻页（与富文本对称）。"""
        if self._suppress_scroll_load or self._log_rich_mode:
            return
        vsb = self.api_log_text.verticalScrollBar()
        if vsb.maximum() <= 0:
            return
        if value >= vsb.maximum() - 60:
            self._plain_load_more()
        elif value <= 60:
            self._plain_load_prev()

    def _on_rich_scrolled(self, value):
        if self._suppress_scroll_load:
            return  # setValue 重绘期间抑制，防“往下滑又触发上翻”
        vsb = self._rich_log_text.verticalScrollBar()
        if vsb.maximum() <= 0:
            return
        if value >= vsb.maximum() - 60:
            self._rich_load_more()
        elif value <= 60:
            self._rich_load_prev()

    # ---------- 实时追加 ----------
    def _schedule_rich_refresh(self):
        """富文本追加防抖：200ms 内的连续追加只重绘一次，避免每行都重渲整页。"""
        try:
            if self._rich_refresh_timer is not None:
                self._rich_refresh_timer.start()
                return
        except Exception:
            pass
        self._flush_rich_refresh()

    def _flush_rich_refresh(self):
        """把最新一页刷进富文本视图（仅在用户仍停留在底部时）。"""
        if not self._log_rich_mode or not self._log_follow_tail:
            return
        vsb = self._rich_log_text.verticalScrollBar()
        at_bottom = (vsb.maximum() <= 0) or (vsb.maximum() - vsb.value() < 80)
        if not at_bottom:
            return  # 用户正在页内回看，不打断
        total = len(self._log_lines)
        # 快路径：追加模式下只把新增行 insertHtml 进去（0.16ms），避免整页重渲（52ms）
        try:
            if self._rich_append_new(total):
                return
        except Exception:
            pass
        self._log_set_window(max(0, total - LOG_PAGE_SIZE), total)
        self._rich_rerender(direction='down')

    def _on_log_append(self, text):
        """LogTextEdit.append 拦截：实时落盘 + 维护权威行列表 + 跟随刷新两种视图。

        折叠规则：窗口固定为 LOG_PAGE_SIZE 行，超出后由窗口滑动加载相邻页；
        用户翻看历史（未跟随尾部）时不打断，新行只进 _log_lines，滑回底部再补齐。
        """
        txt = str(text)
        _session_log_write(txt)              # ← 实时写入 logs/run_*/session.log
        new_lines = txt.split('\n')
        self._log_lines.extend(new_lines)
        total = len(self._log_lines)
        if not self._log_follow_tail:
            return                            # 用户在看历史：不打断
        if self._log_rich_mode:
            self._rich_window_end = total
            self._rich_window_start = max(0, total - LOG_PAGE_SIZE)
            self._schedule_rich_refresh()
        else:
            # 纯文本快路径：**先进缓冲**，由 _flush_plain_append 批量插入。
            # 逐行调用会让批量函数退化成"每行一次 insertText + 一次滚动条同步"（实测 ≈1.2 ms/行）。
            self._plain_buf.extend(new_lines)
            if len(self._plain_buf) >= PLAIN_FLUSH_MAX:
                self._flush_plain_append()
            elif self._plain_flush_timer is not None:
                self._plain_flush_timer.start()

    def _on_log_clear(self):
        """LogTextEdit.clear 拦截：清空权威行与两种视图（日志文件保留历史）。"""
        if self._log_clearing:
            return  # 防重入：下面用基类直调，避免 LogTextEdit.clear 回调自身
        self._log_clearing = True
        try:
            self._log_lines.clear()
            # 缓冲里可能还有没插进视图的行 —— 一并丢掉，否则"清空"后它们又冒出来
            if self._plain_flush_timer is not None:
                try:
                    self._plain_flush_timer.stop()
                except Exception:
                    pass
            if self._plain_buf:
                del self._plain_buf[:]
            _session_log_write("---- 界面日志已清空（文件保留历史）----")
            self._suppress_scroll_load = True
            for ed in (self.api_log_text, self._rich_log_text):
                if ed is not None:
                    try:
                        QTextEdit.clear(ed)   # 直调基类，绕开 LogTextEdit 的钩子
                    except Exception:
                        pass
            self._suppress_scroll_load = False
            self._rich_window_start = 0
            self._rich_window_end = 0
            self._rich_block_cache.clear()
            self._rich_doc_segs = []
            self._log_follow_tail = True
        finally:
            self._log_clearing = False

    def _describe_result(self, data):
        """描述历史响应数据的内容概要"""
        if isinstance(data, dict):
            if '_binary' in data:
                return (f"二进制文件 {data.get('filename', '')} "
                        f"({data.get('size', 0)}B)")
            return f"{len(data)} 个键"
        if isinstance(data, list):
            return f"{len(data)} 项"
        if data is None:
            return "空"
        s = str(data)
        return s if len(s) <= 60 else s[:60] + '...'

    def _restore_saved_outputs(self):
        """加载 .wbt 后，在右侧"数据处理/输出"标签页恢复上次保存的历史记录。

        输出标签页：为每个带 result_json 的 API 节点重建 JSON 树输出块；
        数据处理标签页：日志区追加历史条目（含 API 响应与数据处理结果）。
        """
        self.output_layout_clear()
        self.api_log_text.clear()
        restored = 0
        for node in self.scene.nodes:
            if isinstance(node, APINode):
                rj = getattr(node, 'result_json', None)
                if rj is None:
                    continue
                method = (getattr(node, 'method', '') or 'GET').upper()
                path = getattr(node, 'url_path', '/') or '/'
                # ---- 输出标签页：重建输出条目（JSON 树，支持拖拽） ----
                method_color = {
                    'GET': '#61af55', 'POST': '#55aaff', 'PUT': '#e5a53b',
                    'DELETE': '#e06c75', 'PATCH': '#c678dd'
                }.get(method, '#aaaaaa')
                title = (f"<b>[{method}] {path}</b> "
                         f"<span style='color:#c9a94a;'>📜 历史记录（上次保存）</span>")
                item = OutputItem(title, method_color)
                item.menu_requested.connect(self._on_output_item_menu)
                self._add_output_item(item)
                bl = item.content_layout
                tree = SourceJSONTreeWidget()
                tree.populate(rj)
                tree._source_node = node
                tree.drag_started.connect(self._on_output_drag_started)
                bl.addWidget(tree, 1)
                bl.addWidget(_OutputResizeHandle(item))
                # ---- 数据处理标签页：日志条目 ----
                desc = self._describe_result(rj)
                self.api_log_text.append(
                    f"📜 [历史记录] [{method}] {path} — 上次保存的响应数据（{desc}）"
                )
                restored += 1
            elif isinstance(node, DataProcessNode):
                pd = getattr(node, 'processed_data', None)
                sd = getattr(node, 'source_data', '')
                if pd is not None or sd:
                    self.api_log_text.append(
                        f"🔧 [历史记录] 数据处理: 源数据={str(sd)[:80]!r} → "
                        f"结果={str(pd)[:120]!r}"
                    )
        if restored:
            self.api_log_text.append(f"—— 共恢复 {restored} 条 API 历史记录 ——")
        return restored

    def _silent_load(self, data):
        """静默加载已保存的状态 —— 复用已有 CSV 节点避免重复读文件""",
        old_cb = self.scene._undo_save_cb
        self.scene._undo_save_cb = None

        # ---- 1) 收集已有 CsvDataNode 以便复用 ----
        existing_csv_map = {}  # file_path → CsvDataNode
        for n in list(self.scene.nodes):
            if isinstance(n, CsvDataNode) and n.file_path:
                existing_csv_map[n.file_path] = n

        # ---- 2) 安全地清空场景（每个 item 只 remove 一次） ----
        # 先断开所有连线
        for conn in list(self.scene.connections):
            if conn.scene() == self.scene:
                self.scene.removeItem(conn)
        self.scene.connections.clear()
        self.scene.classes.clear()  # 类框随场景清空
        for nd in list(self.scene.nodes):
            try:
                nd._class = None  # 重置类归属，避免指向已删除类
            except Exception:
                pass
        # 移除所有非 CSV 节点
        nodes_to_keep = []
        removed_count = {'dp': 0, 'api': 0, 'csv': 0}
        for node in list(self.scene.nodes):
            if isinstance(node, CsvDataNode):
                nodes_to_keep.append(node)
                removed_count['csv'] += 1
            elif isinstance(node, DataProcessNode):
                if node.scene() == self.scene:
                    self.scene.removeItem(node)
                    removed_count['dp'] += 1
            elif isinstance(node, ContainerNode):
                if node.scene() == self.scene:
                    self.scene.removeItem(node)
                    removed_count['dp'] += 1
            else:
                if node.scene() == self.scene:
                    self.scene.removeItem(node)
                    removed_count['api'] += 1
        self.scene.nodes.clear()
        self.scene.nodes.extend(nodes_to_keep)

        # ---- 3) 重建所有节点 ----
        node_map = {}
        for idx, ndata in enumerate(data.get('nodes', [])):
            nd_type = ndata.get('type', '')
            # CSV 节点复用
            if nd_type == 'CsvDataNode':
                fp = ndata.get('file_path', '')
                reused = existing_csv_map.pop(fp, None) if fp else None
                if reused:
                    reused.setPos(ndata.get('pos_x', 0), ndata.get('pos_y', 0))
                    reused.current_row = ndata.get('current_row', 0)
                    reused.start_row = ndata.get('start_row', 0)
                    reused._set_row(reused.current_row)
                    if reused not in self.scene.nodes:
                        self.scene.nodes.append(reused)
                    node_map[idx] = reused
                    continue
            # 其他 → 正常反序列化创建
            node = self._deserialize_node(ndata, [])
            if node is None:
                continue
            self.scene.addItem(node)
            self.scene.nodes.append(node)
            node.setPos(ndata.get('pos_x', 0), ndata.get('pos_y', 0))
            node_map[idx] = node

        # 清理未被复用的旧 CSV 节点
        for leftover in existing_csv_map.values():
            if leftover in self.scene.nodes:
                self.scene.nodes.remove(leftover)
            if leftover.scene() == self.scene:
                self.scene.removeItem(leftover)

        # ---- 4) 恢复 APINode 的 data_cleared 信号 ----
        for n in node_map.values():
            if isinstance(n, APINode):
                n.data_cleared.connect(lambda param, nd=n: self.scene._on_param_data_cleared(nd, param))

        # ---- 5) 恢复 _csv_source ----
        for idx, ndata in enumerate(data.get('nodes', [])):
            if ndata.get('type') == 'DataProcessNode' and idx in node_map:
                dp = node_map[idx]
                csv_f = ndata.get('csv_source_file')
                csv_h = ndata.get('csv_source_header')
                if csv_f and csv_h:
                    for n in node_map.values():
                        if isinstance(n, CsvDataNode) and n.file_path == csv_f:
                            dp._csv_source = (n, csv_h)
                            val = n.get_current_value(csv_h)
                            dp.set_source_data(val)
                            break

        # ---- 5.5) 恢复拼合信息栏（_silent_load）----
        for idx, ndata in enumerate(data.get('nodes', [])):
            if ndata.get('type') == 'APINode' and idx in node_map:
                api = node_map[idx]
                for cw_data in ndata.get('concat_widgets', []):
                    cid = api.add_concat_field(anchor_param=cw_data.get('anchor_param', ''))
                    for cw in api._concat_widgets:
                        if cw.concat_id == cid:
                            if cw_data.get('source_path'):
                                cw.source_path = cw_data['source_path']
                            if cw_data.get('value'):
                                cw.edit.setText(cw_data['value'])
                                cw.concat_value = cw_data['value']
                                cw.clear_btn.show()
                            break

        # 类嵌套方案：先恢复类框（类间连线需要 start_obj/end_obj 是类对象）
        self._restore_classes(data, node_map)
        # ---- 6) 重建连线 ----
        conn_ok = 0
        for cdata in data.get('connections', []):
            si, ei = cdata.get('start_node'), cdata.get('end_node')
            ep = cdata.get('end_param')
            sh = cdata.get('start_header')
            if si in node_map and ei in node_map:
                sn, en = node_map[si], node_map[ei]
                if isinstance(sn, CsvDataNode):
                    hd = sh or ''
                    sf = lambda n=sn, h=hd: n.output_port_for_header(h)
                elif isinstance(sn, DataProcessNode):
                    sf = sn.outputPort
                else:
                    sf = sn.outputPort if hasattr(sn, 'outputPort') else (lambda: QPointF())
                if isinstance(en, APINode):
                    if ep and ep.startswith('concat_'):
                        ef = lambda n=en, c=ep: n.concat_input_port(c)
                    else:
                        ef = lambda n=en, p=ep: n.param_input_port(p)
                elif isinstance(en, DataProcessNode):
                    ef = en.inputPort
                elif isinstance(en, ContainerNode):
                    if ep and ep.startswith('rule_'):
                        ef = lambda n=en, p=ep: n.rule_part_input_port(p)
                    elif ep == 'root':
                        ef = en.root_path_input_port
                    else:
                        ef = en.inputPort
                elif isinstance(en, StepperNode):
                    if ep and ep != 'main':
                        ef = lambda n=en, k=ep: n.input_port_for_key(k)
                    else:
                        ef = en.inputPort
                elif isinstance(en, SiteParserNode):
                    ef = en.inputPort
                elif isinstance(en, CsvDataNode):
                    # 数据库框的输入端（上游数据流入本表）
                    ef = en.inputPort
                elif isinstance(en, ClassNode):
                    ef = en.inputPort if not getattr(en, '_is_main', False) else en.outputPort
                else:
                    ef = lambda: QPointF()
                conn = ConnectionPath(sf, ef, sn, en, ep, start_param=sh)
                self.scene.addItem(conn)
                self.scene.connections.append(conn)
                conn_ok += 1
        # 实时预览推送：让下游元素框（如站点解析识别预览）在静默加载后立即更新
        for nd in self.scene.nodes:
            if isinstance(nd, (DataProcessNode, CsvDataNode)):
                self.scene.push_preview_downstream(nd)
        self.scene._undo_save_cb = old_cb

    def _clear_scene(self):
        """清空场景中所有节点和连线"""
        # 清除拖拽虚影，避免 drag 警告
        if hasattr(self.view, '_hide_drag_ghost'):
            self.view._hide_drag_ghost()
        for conn in list(self.scene.connections):
            self.scene.removeItem(conn)
        self.scene.connections.clear()
        for node in list(self.scene.nodes):
            if not isinstance(node, (DataProcessNode, ContainerNode)):
                self.scene.remove_node(node)
        for node in list(self.scene.nodes):
            self.scene.removeItem(node)
        self.scene.nodes.clear()
        self.scene.classes.clear()
        self.output_layout_clear()
        self.api_log_text.clear()

    def _serialize_flow(self, include_transfer=True):
        """将当前场景序列化为字典。

        include_transfer=False 时不写 `transfer` 键（保存时用户选了「不更新下载参数」）。
        """
        nodes_data = []
        node_idx = {n: i for i, n in enumerate(self.scene.nodes)}

        for node in self.scene.nodes:
            nd = {'type': type(node).__name__, 'pos_x': node.pos().x(), 'pos_y': node.pos().y()}
            if isinstance(node, APINode):
                nd.update({
                    'method': node.method,
                    'url_path': node.url_path,
                    'params': node.params,
                    'headers': node.headers,
                    'result_json': node.result_json,
                    'api_ref': node.api_ref,
                    'concat_widgets': [
                        {'id': cw.concat_id, 'value': cw.get_value(),
                         'source_path': cw.source_path,
                         'anchor_param': getattr(cw, 'anchor_param', '')}
                        for cw in node._concat_widgets
                    ],
                })
            elif isinstance(node, CsvDataNode):
                nd.update({
                    'file_path': node.file_path,
                    'current_row': node.current_row,
                    'start_row': node.start_row,
                    # 输入端写入设置（老 .wbt 没有这两键 → 加载时用默认值）
                    'ingest_mode': getattr(node, 'ingest_mode', 'upsert'),
                    'key_column': getattr(node, 'key_column', ''),
                })
            elif isinstance(node, DataProcessNode):
                nd.update({
                    'regex': node.regex_edit.text() if hasattr(node, 'regex_edit') and node.regex_edit else '',
                    'replacement': node.replace_edit.text() if hasattr(node, 'replace_edit') and node.replace_edit else '',
                    'source_data': node.source_data,
                    'processed_data': node.processed_data,
                    'source_path': getattr(node, 'source_path', ''),
                    'csv_source_file': node._csv_source[0].file_path if node._csv_source else None,
                    'csv_source_header': node._csv_source[1] if node._csv_source else None,
                })
            elif isinstance(node, ContainerNode):
                nd.update({
                    'storage_path': node.storage_path,
                    'stored_bytes': node.stored_bytes,
                    'total_files': node.total_files,
                    'rule_enabled': node._rule_enabled,
                    'rule_creation_path': node._rule_creation_path,
                    'rule_time_enabled': node._rule_time_enabled,
                    'rule_time_format': node._rule_time_format,
                    'rule_parts': [dict(pd) for pd in node._rule_parts],
                    'rule_part_counter': node._rule_part_counter,
                })
            elif isinstance(node, StepperNode):
                nd.update({
                    'step_len': node.step_len,
                    'formula': node.formula,
                    'inputs': [dict(x) for x in node._inputs],
                    'input_counter': node._input_counter,
                    'output_value': node.output_value,
                    'output_int': node.output_int,
                })
            elif isinstance(node, SiteParserNode):
                nd.update({
                    'url': node.get_url(),
                    'parser_name': node.parser_name,
                })
            elif isinstance(node, ClassNode):
                nd.update({
                    'class_id': node._class_id,
                    'class_name': node.class_name,
                    'is_main': node._is_main,
                    'poll_count': node.poll_count,
                    'poll_auto': bool(getattr(node, 'poll_auto', False)),
                    'bg_color': _color_to_str(node._bg_color),
                    'border_color': _color_to_str(node._border_color),
                })
            elif isinstance(node, ImageNode):
                nd.update({
                    'image_path': node.image_path,
                    'display_w': getattr(node, '_display_w', 200),
                    'image_b64': node.to_base64(),
                })
            nodes_data.append(nd)

        conns_data = []
        for conn in self.scene.connections:
            si = node_idx.get(conn.start_obj)
            ei = node_idx.get(conn.end_obj)
            if si is not None and ei is not None:
                cd = {'start_node': si, 'end_node': ei, 'end_param': conn.end_param}
                # 记录 CsvDataNode 输出的 header 名
                if isinstance(conn.start_obj, CsvDataNode) and conn.start_param:
                    cd['start_header'] = conn.start_param
                conns_data.append(cd)

        # ---- 类嵌套方案：序列化类框（成员按节点索引） ----
        classes_data = []
        for cls in self.scene.classes:
            members_idx = [node_idx[m] for m in cls._members if m in node_idx]
            classes_data.append({
                'class_id': cls._class_id,
                'class_name': cls.class_name,
                'is_main': cls._is_main,
                'poll_count': cls.poll_count,
                'poll_auto': bool(getattr(cls, 'poll_auto', False)),
                'members': members_idx,
            })

        out = {'version': 1, 'nodes': nodes_data, 'connections': conns_data,
               'classes': classes_data}
        # 数据传递参数：随图纸走（老图纸无此键 → 加载时用默认值并标记待落盘）
        if include_transfer:
            _d = self._active_dash()
            if _d is not None:
                try:
                    out['transfer'] = _d.config().to_dict()
                except Exception:
                    pass
        return out

    # ---------- 类嵌套方案：恢复类框（加载/粘贴/静默加载共用） ----------
    def _restore_classes(self, data, node_map):
        """从序列化数据恢复类框（类按 class_id 匹配，成员按节点索引映射）。

        ClassNode 已在 _deserialize_node 中创建（在 node_map 内），
        这里把成员填回并让类框贴合；重复 main 转普通类。
        """
        cls_by_id = {}
        for n in node_map.values():
            if isinstance(n, ClassNode):
                cls_by_id[getattr(n, '_class_id', None)] = n
        has_main = any(getattr(cc, '_is_main', False) for cc in self.scene.classes)
        for cdata in data.get('classes', []):
            cls = cls_by_id.get(cdata.get('class_id'))
            if cls is None:
                continue
            member_nodes = [node_map[i] for i in cdata.get('members', []) if i in node_map]
            if not member_nodes:
                continue
            if getattr(cls, '_is_main', False):
                if has_main:
                    cls.set_as_regular()
                else:
                    has_main = True
            # 自增轮询开关（旧图纸没有该字段 → 默认关闭）
            try:
                auto = bool(cdata.get('poll_auto', getattr(cls, 'poll_auto', False)))
                cls.poll_auto = auto
                chk = getattr(cls, '_poll_auto_chk', None)
                if chk is not None:
                    chk.setChecked(auto)
                    cls._poll_edit.setEnabled(not auto)
            except Exception:
                pass
            if cls not in self.scene.classes:
                self.scene.classes.append(cls)
            for m in member_nodes:
                cls.add_member(m)
            cls._refit()

    # ---------- 图纸插入：合并另一张流程图(.wbt) ----------
    @staticmethod
    def _flow_content_bbox(data):
        """估算流程图内容边界（节点位置 + 类型估算尺寸），用于拖入虚影与放置偏移。"""
        est = {
            'APINode': (260, 110), 'DataProcessNode': (260, 120),
            'StepperNode': (240, 150), 'SiteParserNode': (250, 90),
            'ContainerNode': (260, 130), 'ImageNode': (240, 130),
            'CsvDataNode': (240, 130), 'ClassNode': (280, 160),
        }
        rect = QRectF()
        for nd in data.get('nodes', []):
            x = nd.get('pos_x', 0)
            y = nd.get('pos_y', 0)
            w, h = est.get(nd.get('type', ''), (240, 110))
            rect = rect.united(QRectF(x, y, w, h))
        if rect.isNull():
            rect = QRectF(0, 0, 400, 300)
        return rect

    def _merge_flow_file_at(self, filepath, scene_pos):
        """把另一张流程图(.wbt 图纸)合并进当前画布：读取→计算范围→平移到放置点→插入。"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "插入失败", f"无法读取流程图:\n{e}")
            return
        bbox = self._flow_content_bbox(data)
        offset = QPointF(scene_pos.x() - bbox.left(), scene_pos.y() - bbox.top())
        n0 = len(self.scene.nodes)
        self._merge_flow_data(data, offset)
        self.api_log_text.append(
            f"📋 已插入图纸: {os.path.basename(filepath)}"
            f"（+{len(self.scene.nodes) - n0} 个元素，已放至放置点）")

    def _merge_flow_data(self, data, offset):
        """把另一张流程图的节点/连线/类合并进当前场景，整体平移 offset。"""
        node_map = {}
        for idx, ndata in enumerate(data.get('nodes', [])):
            node = self._deserialize_node(ndata, [])
            if node is None:
                continue
            self.scene.addItem(node)
            self.scene.nodes.append(node)
            node.setPos(ndata.get('pos_x', 0) + offset.x(),
                        ndata.get('pos_y', 0) + offset.y())
            node_map[idx] = node
        # 恢复 APINode 的 data_cleared 信号
        for n in node_map.values():
            if isinstance(n, APINode):
                n.data_cleared.connect(lambda param, nd=n: self.scene._on_param_data_cleared(nd, param))
        # 恢复 _csv_source
        for idx, ndata in enumerate(data.get('nodes', [])):
            if ndata.get('type') == 'DataProcessNode' and idx in node_map:
                dp = node_map[idx]
                csv_f = ndata.get('csv_source_file')
                csv_h = ndata.get('csv_source_header')
                if csv_f and csv_h:
                    for n in node_map.values():
                        if isinstance(n, CsvDataNode) and n.file_path == csv_f:
                            dp._csv_source = (n, csv_h)
                            val = n.get_current_value(csv_h)
                            dp.set_source_data(val)
                            break
        # 恢复拼合信息栏
        for idx, ndata in enumerate(data.get('nodes', [])):
            if ndata.get('type') == 'APINode' and idx in node_map:
                api = node_map[idx]
                for cw_data in ndata.get('concat_widgets', []):
                    cid = api.add_concat_field(anchor_param=cw_data.get('anchor_param', ''))
                    for cw in api._concat_widgets:
                        if cw.concat_id == cid:
                            if cw_data.get('source_path'):
                                cw.source_path = cw_data['source_path']
                            if cw_data.get('value'):
                                cw.edit.setText(cw_data['value'])
                                cw.concat_value = cw_data['value']
                                cw.clear_btn.show()
                            break
        # 类嵌套：先恢复类（类间连线需要类对象）
        self._restore_classes(data, node_map)
        # 重建连线
        for cdata in data.get('connections', []):
            si, ei = cdata.get('start_node'), cdata.get('end_node')
            ep = cdata.get('end_param')
            sh = cdata.get('start_header')
            if si in node_map and ei in node_map:
                sn, en = node_map[si], node_map[ei]
                if isinstance(sn, CsvDataNode):
                    hd = sh or ''
                    sf = lambda n=sn, h=hd: n.output_port_for_header(h)
                elif isinstance(sn, DataProcessNode):
                    sf = sn.outputPort
                else:
                    sf = sn.outputPort if hasattr(sn, 'outputPort') else (lambda: QPointF())
                if isinstance(en, APINode):
                    if ep and ep.startswith('concat_'):
                        ef = lambda n=en, c=ep: n.concat_input_port(c)
                    else:
                        ef = lambda n=en, p=ep: n.param_input_port(p)
                elif isinstance(en, DataProcessNode):
                    ef = en.inputPort
                elif isinstance(en, ContainerNode):
                    if ep and ep.startswith('rule_'):
                        ef = lambda n=en, p=ep: n.rule_part_input_port(p)
                    elif ep == 'root':
                        ef = en.root_path_input_port
                    else:
                        ef = en.inputPort
                elif isinstance(en, StepperNode):
                    if ep and ep != 'main':
                        ef = lambda n=en, k=ep: n.input_port_for_key(k)
                    else:
                        ef = en.inputPort
                elif isinstance(en, SiteParserNode):
                    ef = en.inputPort
                elif isinstance(en, CsvDataNode):
                    # 数据库框的输入端（上游数据流入本表）
                    ef = en.inputPort
                elif isinstance(en, ClassNode):
                    ef = en.inputPort if not getattr(en, '_is_main', False) else en.outputPort
                else:
                    ef = lambda: QPointF()
                conn = ConnectionPath(sf, ef, sn, en, ep, start_param=sh)
                self.scene.addItem(conn)
                self.scene.connections.append(conn)
        # 实时预览推送
        for nd in self.scene.nodes:
            if isinstance(nd, (DataProcessNode, CsvDataNode)):
                self.scene.push_preview_downstream(nd)
        self.scene._save_undo()

    # ---------- 复制 / 粘贴 ----------
    _clipboard = None  # 类级剪贴板

    def _copy_selected_nodes(self):
        """复制选中的元素框到内部剪贴板"""
        sel_nodes = [n for n in self.scene.nodes if n.isSelected()]
        if not sel_nodes:
            return
        # 类嵌套方案：若某类的成员全部被选中，则一并复制该类框本身
        sel_set = set(sel_nodes)
        for cls in self.scene.classes:
            if cls._members and all(m in sel_set for m in cls._members):
                if cls not in sel_set:
                    sel_nodes.append(cls)
        node_idx = {n: i for i, n in enumerate(sel_nodes)}
        nodes_data = []
        for node in sel_nodes:
            nd = {'type': type(node).__name__, 'pos_x': node.pos().x(), 'pos_y': node.pos().y()}
            if isinstance(node, APINode):
                nd.update({
                    'method': node.method,
                    'url_path': node.url_path,
                    'params': [dict(p) for p in node.params],
                    'headers': dict(node.headers),
                    'result_json': node.result_json,
                    'api_ref': dict(node.api_ref),
                    'concat_widgets': [
                        {'id': cw.concat_id, 'value': cw.get_value(),
                         'source_path': cw.source_path,
                         'anchor_param': getattr(cw, 'anchor_param', '')}
                        for cw in node._concat_widgets
                    ],
                })
            elif isinstance(node, CsvDataNode):
                nd.update({
                    'file_path': node.file_path,
                    'current_row': node.current_row,
                    'start_row': node.start_row,
                    # 输入端写入设置（老 .wbt 没有这两键 → 加载时用默认值）
                    'ingest_mode': getattr(node, 'ingest_mode', 'upsert'),
                    'key_column': getattr(node, 'key_column', ''),
                })
            elif isinstance(node, DataProcessNode):
                nd.update({
                    'regex': node.regex_edit.text() if hasattr(node, 'regex_edit') and node.regex_edit else '',
                    'replacement': node.replace_edit.text() if hasattr(node, 'replace_edit') and node.replace_edit else '',
                    'source_data': node.source_data,
                    'processed_data': node.processed_data,
                    'source_path': getattr(node, 'source_path', ''),
                })
            elif isinstance(node, ContainerNode):
                nd.update({
                    'storage_path': node.storage_path,
                    'stored_bytes': node.stored_bytes,
                    'total_files': node.total_files,
                    'rule_enabled': node._rule_enabled,
                    'rule_creation_path': node._rule_creation_path,
                    'rule_time_enabled': node._rule_time_enabled,
                    'rule_time_format': node._rule_time_format,
                    'rule_parts': [dict(pd) for pd in node._rule_parts],
                    'rule_part_counter': node._rule_part_counter,
                })
            elif isinstance(node, StepperNode):
                nd.update({
                    'step_len': node.step_len,
                    'formula': node.formula,
                    'inputs': [dict(x) for x in node._inputs],
                    'input_counter': node._input_counter,
                    'output_value': node.output_value,
                    'output_int': node.output_int,
                })
            elif isinstance(node, SiteParserNode):
                nd.update({
                    'url': node.get_url(),
                    'parser_name': node.parser_name,
                })
            elif isinstance(node, ClassNode):
                nd.update({
                    'class_id': node._class_id,
                    'class_name': node.class_name,
                    'is_main': node._is_main,
                    'poll_count': node.poll_count,
                    'poll_auto': bool(getattr(node, 'poll_auto', False)),
                    'bg_color': _color_to_str(node._bg_color),
                    'border_color': _color_to_str(node._border_color),
                })
            elif isinstance(node, ImageNode):
                nd.update({
                    'image_path': node.image_path,
                    'display_w': getattr(node, '_display_w', 200),
                    'image_b64': node.to_base64(),
                })
            nodes_data.append(nd)
        # 复制选中节点间的内部连线
        conns_data = []
        for conn in self.scene.connections:
            si = node_idx.get(conn.start_obj)
            ei = node_idx.get(conn.end_obj)
            if si is not None and ei is not None:
                cd = {'start_node': si, 'end_node': ei, 'end_param': conn.end_param}
                if isinstance(conn.start_obj, CsvDataNode) and conn.start_param:
                    cd['start_header'] = conn.start_param
                conns_data.append(cd)
        # 复制选中的类（其成员全部在选中范围内才整体复制）
        classes_data = []
        for cls in self.scene.classes:
            if cls._members and all(m in node_idx for m in cls._members):
                classes_data.append({
                    'class_id': cls._class_id,
                    'class_name': cls.class_name,
                    'is_main': cls._is_main,
                    'poll_count': cls.poll_count,
                    'poll_auto': bool(getattr(cls, 'poll_auto', False)),
                    'members': [node_idx[m] for m in cls._members],
                })
        FlowEditorDialog._clipboard = {
            'nodes': nodes_data,
            'connections': conns_data,
            'classes': classes_data,
        }

    def _paste_nodes(self):
        """从内部剪贴板粘贴元素框"""
        if FlowEditorDialog._clipboard is None:
            return
        data = FlowEditorDialog._clipboard
        center = self.view.mapToScene(self.view.viewport().rect().center())
        # 计算偏移：使粘贴的节点位于视图中心
        if data['nodes']:
            avg_x = sum(n['pos_x'] for n in data['nodes']) / len(data['nodes'])
            avg_y = sum(n['pos_y'] for n in data['nodes']) / len(data['nodes'])
        else:
            avg_x, avg_y = 0, 0
        offset_x = center.x() - avg_x
        offset_y = center.y() - avg_y

        node_map = {}
        for idx, ndata in enumerate(data['nodes']):
            node = self._deserialize_node(ndata, [])
            if node is None:
                continue
            node_map[idx] = node
            self.scene.add_node(node)
            node.setPos(ndata.get('pos_x', 0) + offset_x,
                        ndata.get('pos_y', 0) + offset_y)

        # 类嵌套方案：先恢复类框（类间连线需要类对象）
        self._restore_classes(data, node_map)
        # 重建选中节点间的内部连线
        for cdata in data.get('connections', []):
            si, ei = cdata.get('start_node'), cdata.get('end_node')
            ep = cdata.get('end_param')
            sh = cdata.get('start_header')
            if si in node_map and ei in node_map:
                sn, en = node_map[si], node_map[ei]
                if isinstance(sn, CsvDataNode):
                    hd = sh or ''
                    sf = lambda n=sn, h=hd: n.output_port_for_header(h)
                elif isinstance(sn, DataProcessNode):
                    sf = sn.outputPort
                else:
                    sf = sn.outputPort if hasattr(sn, 'outputPort') else (lambda: QPointF())
                if isinstance(en, APINode):
                    if ep and ep.startswith('concat_'):
                        ef = lambda n=en, c=ep: n.concat_input_port(c)
                    else:
                        ef = lambda n=en, p=ep: n.param_input_port(p)
                elif isinstance(en, DataProcessNode):
                    ef = en.inputPort
                elif isinstance(en, ContainerNode):
                    if ep and ep.startswith('rule_'):
                        ef = lambda n=en, p=ep: n.rule_part_input_port(p)
                    elif ep == 'root':
                        ef = en.root_path_input_port
                    else:
                        ef = en.inputPort
                elif isinstance(en, StepperNode):
                    if ep and ep != 'main':
                        ef = lambda n=en, k=ep: n.input_port_for_key(k)
                    else:
                        ef = en.inputPort
                elif isinstance(en, SiteParserNode):
                    ef = en.inputPort
                elif isinstance(en, CsvDataNode):
                    # 数据库框的输入端（上游数据流入本表）
                    ef = en.inputPort
                elif isinstance(en, ClassNode):
                    ef = en.inputPort if not getattr(en, '_is_main', False) else en.outputPort
                else:
                    ef = lambda: QPointF()
                conn = ConnectionPath(sf, ef, sn, en, ep, start_param=sh)
                self.scene.addItem(conn)
                self.scene.connections.append(conn)
        # 实时预览推送：粘贴后让下游元素框（如站点解析识别预览）立即更新
        for nd in self.scene.nodes:
            if isinstance(nd, (DataProcessNode, CsvDataNode)):
                self.scene.push_preview_downstream(nd)
        self.scene._save_undo()

    def _deserialize_node(self, ndata, warnings):
        """反序列化单节点，校验数据文件是否存在"""
        typ = ndata.get('type', '')
        try:
            if typ == 'APINode':
                params = ndata.get('params', [])
                node = APINode()
                node.api_ref = dict(ndata.get('api_ref', {}))
                # 请求头必须在 set_api 之前赋值：set_api 内部会 _build_ui()，
                # 而按钮文案/悬停提示是按 self.headers 画出来的，晚赋值会留下陈旧按钮。
                node.headers = dict(ndata.get('headers', {}) or {})
                node.set_api(ndata.get('method', 'GET'), ndata.get('url_path', '/'),
                             [dict(p) for p in params], node.headers)
                node.result_json = ndata.get('result_json')
                return node
            elif typ == 'CsvDataNode':
                fp = ndata.get('file_path', '')
                if fp and not os.path.exists(fp):
                    warnings.append(f"⚠ 数据文件不存在: {os.path.basename(fp)}\n  对应 CSV 节点已移除")
                    return None
                node = CsvDataNode(fp)
                node.current_row = ndata.get('current_row', 0)
                node.start_row = ndata.get('start_row', 0)
                # 输入端写入设置：老 .wbt 没有这两键 → 保持默认（upsert / 自动探测）
                node.ingest_mode = ndata.get('ingest_mode', 'upsert') or 'upsert'
                node.key_column = ndata.get('key_column', '') or ''
                node._set_row(node.current_row)
                # 重建 UI 让「写入 / 主键列」按钮显示恢复后的值
                if (node.ingest_mode != 'upsert') or node.key_column:
                    node._build_ui()
                return node
            elif typ == 'DataProcessNode':
                node = DataProcessNode()
                node.set_source_data(ndata.get('source_data', ''))
                node.source_path = ndata.get('source_path', '')
                if hasattr(node, 'regex_edit') and node.regex_edit:
                    node.regex_edit.setText(ndata.get('regex', ''))
                if hasattr(node, 'replace_edit') and node.replace_edit:
                    node.replace_edit.setText(ndata.get('replacement', ''))
                node.apply_regex()
                # 恢复上次保存的处理结果（供右侧"数据处理"历史记录显示）
                saved_proc = ndata.get('processed_data')
                if saved_proc is not None:
                    node.processed_data = saved_proc
                    if hasattr(node, 'result_label'):
                        node.result_label.setText(f"处理后: {saved_proc}")
                return node
            elif typ == 'ContainerNode':
                node = ContainerNode(ndata.get('storage_path', ''))
                node.stored_bytes = ndata.get('stored_bytes', 0)
                node.total_files = ndata.get('total_files', 0)
                node._rule_enabled = bool(ndata.get('rule_enabled', False))
                node._rule_creation_path = ndata.get('rule_creation_path', '')
                node._rule_time_enabled = bool(ndata.get('rule_time_enabled', False))
                node._rule_time_format = ndata.get('rule_time_format', 'yyyy-mm-dd')
                node._rule_parts = [dict(p) for p in ndata.get('rule_parts', [])]
                node._rule_part_counter = ndata.get('rule_part_counter', 0)
                node._build_ui()  # 重建以渲染规则区
                node._refresh_display()
                return node
            elif typ == 'StepperNode':
                node = StepperNode()
                node.step_len = float(ndata.get('step_len', 1.0))
                node.formula = ndata.get('formula', '') or ''
                node.output_int = bool(ndata.get('output_int', False))
                node._inputs = [dict(x) for x in ndata.get('inputs', [])]
                node._input_counter = int(ndata.get('input_counter', len(node._inputs)))
                node.output_value = float(ndata.get('output_value', 0.0))
                node._build_ui()  # 重建以渲染输入数据栏
                node._resize_to_fit()
                return node
            elif typ == 'SiteParserNode':
                node = SiteParserNode()
                node.set_url(ndata.get('url', '') or '')
                node.parser_name = ndata.get('parser_name', '') or '未识别'
                node._set_status_text(f"已识别: {node.parser_name}")
                node._resize_to_fit()
                return node
            elif typ == 'ClassNode':
                node = ClassNode(class_name=ndata.get('class_name', 'class'),
                                 is_main=bool(ndata.get('is_main', False)),
                                 poll_count=int(ndata.get('poll_count', 1)),
                                 poll_auto=bool(ndata.get('poll_auto', False)))
                node._class_id = ndata.get('class_id', node._class_id)
                node._bg_color = _color_from_str(ndata.get('bg_color'), node._bg_color)
                node._border_color = _color_from_str(ndata.get('border_color'), node._border_color)
                return node
            elif typ == 'ImageNode':
                node = ImageNode(image_path=ndata.get('image_path', '') or '',
                                 display_w=int(ndata.get('display_w', 200)))
                b64 = ndata.get('image_b64') or ''
                if b64:
                    node.from_base64(b64)
                else:
                    node.load_image()
                node._resize_to_fit()
                return node
        except Exception as e:
            warnings.append(f"⚠ 节点反序列化失败: {str(e)}")
            return None
        return None

    # ----------------- 拓扑排序与流程执行 -----------------
    def _collect_upstream_sources(self, obj, include_self=True, _seen=None, _depth=0):
        """沿连线向上游回溯，收集真正喂数据给它的元素框。

        include_self=True  —— obj 自身若是生产者就收下它（用于连线的**起点侧**：
                              判断"这条线的上游是谁"）。
        include_self=False —— 只看上游，不含 obj 自身（用于**轮询门控**：
                              "喂我的东西准备好了吗"）。

        返回 (apis, csvs, steppers)：
          apis     —— 直接/间接连到 obj 的 APINode（遇到即收下，不再继续向上）
          csvs     —— 路径上经过的 CsvDataNode（继续向上穿透，用于"新鲜数据"门控）
          steppers —— 路径上的 StepperNode / SiteParserNode（独立生产者，收下即止）

        关键点：**必须穿透 CsvDataNode**。此前拓扑构建不穿透数据库框，
        导致「接口 → 数据库框 → 数据处理框 → 接口」这条链被视为无依赖，
        下游接口会用数据库框里的老数据先跑一轮。容器/图片框是终点，不再向上。
        """
        if _seen is None:
            _seen = set()
        apis, csvs, steppers = [], [], []
        if obj is None or _depth > 32 or id(obj) in _seen:
            return apis, csvs, steppers
        _seen.add(id(obj))
        if include_self:
            # 独立生产者：收下即止（它们每轮开头统一执行，不作为层级依赖）
            if isinstance(obj, (StepperNode, SiteParserNode)):
                steppers.append(obj)
                return apis, csvs, steppers
            if isinstance(obj, APINode):
                apis.append(obj)
                return apis, csvs, steppers
            if isinstance(obj, CsvDataNode):
                csvs.append(obj)
            elif isinstance(obj, (ContainerNode, ImageNode)):
                # 存盘/图片终点，没有数据继续往上流
                return apis, csvs, steppers
        elif isinstance(obj, (ContainerNode, ImageNode)):
            return apis, csvs, steppers
        for conn in list(self.scene.connections):
            if conn.end_obj is not obj:
                continue
            a, c, s = self._collect_upstream_sources(
                conn.start_obj, True, _seen, _depth + 1)
            apis += [x for x in a if x not in apis]
            csvs += [x for x in c if x not in csvs]
            steppers += [x for x in s if x not in steppers]
        return apis, csvs, steppers

    def _node_round_gate(self, node, skipped):
        """上下轮询锁：判断 node 本轮是否"数据已就绪"。

        返回 (ready, reason)。不满足时调用方跳过本节点，并由 skipped 集合
        把"本轮没跑"继续传给它的下游，避免下游拿着上一轮的数据继续跑。
        """
        apis, csvs, _st = self._collect_upstream_sources(node, include_self=False)
        # ① 上游接口本轮被跳过 → 本节点数据必然没更新
        for up in apis:
            if up in skipped:
                return False, (f"上游元素 {up.method} {getattr(up, 'url_path', '')[:24]} "
                               f"本轮未执行（数据未更新）")
        # ② 上游驱动型数据库框：本轮还没有被写入过任何新数据 → 等下一轮
        for cnode in csvs:
            if not self._csv_has_upstream_ingest(cnode):
                continue      # 静态表（无输入端连线）不参与门控
            if not cnode._fresh_rows:
                return False, (f"数据库框[{os.path.basename(cnode.file_path or '')}] "
                               f"本轮还没有新数据写入")
        return True, ''

    def _csv_has_upstream_ingest(self, cnode):
        """该数据库框是否有上游连到它的输入端（= 上游驱动型，需要等新数据）"""
        for conn in list(self.scene.connections):
            if conn.end_obj is cnode and getattr(conn, 'end_param', '') == 'ingest':
                return True
        return False

    def _build_topological_levels(self, nodes=None):
        """根据连线构建 APINode 的拓扑层级（BFS 分层），返回 list[list[APINode]]。

        nodes 指定时只对给定节点集合内的 APINode 分层（类分组执行用）。

        依赖判定 = 沿连线向上游回溯（可穿透 DataProcessNode / CsvDataNode），
        因此「接口 → 数据库框 → 数据处理框 → 接口」也构成一条真实的层级依赖。
        """
        _src = self.scene.nodes if nodes is None else nodes
        api_nodes = [n for n in _src if isinstance(n, APINode)]
        if not api_nodes:
            return []

        # 邻接表 & 入度
        graph = {n: [] for n in api_nodes}
        in_degree = {n: 0 for n in api_nodes}

        for conn in list(self.scene.connections):
            down = conn.end_obj
            if not isinstance(down, APINode) or down not in graph:
                continue
            up_apis, _c, _s = self._collect_upstream_sources(conn.start_obj)
            for up in up_apis:
                if up is down or up not in graph:
                    continue
                if down in graph[up]:
                    continue          # 去重：同一对节点多条连线只算一次入度
                graph[up].append(down)
                in_degree[down] += 1

        # BFS 分层（Kahn）
        queue = [n for n in api_nodes if in_degree.get(n, 0) == 0]
        levels = []
        visited = set()
        while queue:
            level = list(queue)
            levels.append(level)
            visited.update(level)
            next_q = []
            for node in level:
                for nb in graph.get(node, []):
                    in_degree[nb] -= 1
                    if in_degree[nb] == 0 and nb not in visited:
                        next_q.append(nb)
            queue = next_q

        # ---- 环路兜底：仍有未访问的节点（互为上游）时放到最后一层，
        #      保证任何节点都不会被静默漏执行 ----
        leftover = [n for n in api_nodes if n not in visited]
        if leftover:
            levels.append(leftover)
        return levels

    def _get_csv_processed_values(self, for_node=None):
        """
        收集 DP 节点的正则处理后值，返回 {param_name: processed_val}。
        同时处理 CSV→DP→API 和 API→DP→API 链路。

        for_node 指定后：仅处理与该 API 节点【直接相连】的 DP（节点维度隔离）。
        否则当多个 API 节点共用同一参数名（如 /{string}）时，
        各自不同 DP 的处理值会在全局字典中相互覆盖：
        后处理的 DP 值覆盖先处理的，导致某节点使用别的节点的 DP 值，
        下载内容错误且（因源 DP 未被推进）同一 URL 反复下载。
        """
        result = {}
        for nd in self.scene.nodes:
            if not isinstance(nd, DataProcessNode):
                continue

            # ---- 仅收集直接连接到 for_node 的 DP，避免跨节点参数名覆盖 ----
            if for_node is not None:
                _linked = False
                for _c in self.scene.connections:
                    if _c.start_obj == nd and _c.end_obj == for_node:
                        _linked = True
                        break
                if not _linked:
                    continue

            # ---- 情况1：CSV→DP→API ----
            if hasattr(nd, '_csv_source') and nd._csv_source:
                csv_node, csv_hdr = nd._csv_source
                if csv_hdr in csv_node.headers:
                    raw = csv_node.get_current_value(csv_hdr)
                else:
                    raw = nd.source_data
            else:
                # ---- 情况2：API→DP→API（非 CSV 源） ----
                raw = nd.source_data

            # 应用正则处理
            rx = nd.regex_edit.text() if hasattr(nd, 'regex_edit') and nd.regex_edit else ''
            rp = nd.replace_edit.text() if hasattr(nd, 'replace_edit') and nd.replace_edit else ''
            if rx and raw:
                try:
                    proc = re.sub(rx, rp, raw)
                except Exception:
                    proc = raw
            else:
                proc = raw

            # 更新 DP 自身显示
            nd.source_data = raw
            nd.processed_data = proc
            if hasattr(nd, 'src_label') and nd.src_label:
                nd.src_label.setText(f"源数据: {raw}")
            if hasattr(nd, 'result_label') and nd.result_label:
                nd.result_label.setText(f"处理后: {proc}")

            # 找到此 DP 连到的 APINode 参数名 或 拼合栏
            for c in self.scene.connections:
                if c.start_obj == nd and isinstance(c.end_obj, APINode):
                    pn = c.end_param
                    if pn:
                        # 检查是否是拼合栏 ID
                        api = c.end_obj
                        if pn.startswith('concat_'):
                            for cw in api._concat_widgets:
                                if cw.concat_id == pn:
                                    cw.edit.setText(proc)
                                    cw.concat_value = proc
                                    break
                        else:
                            result[pn] = proc
                            # 调试：记录 DP → 参数的值
                            if hasattr(self, 'api_log_text') and self.api_log_text:
                                self.api_log_text.append(
                                    f"  🐛 DP({nd.source_data[:40]}) 正则→ 参数[{pn}] = {proc[:60]}")
        return result

    # ---------- 步进器（特殊元素框）轮询计算与注入 ----------
    def _resolve_stepper_input(self, stepper, key):
        """解析步进器某个输入的数值信号（强制 int/float）。

        有连线 → 上游数据；无连线 → 自变量（读取数据栏中的数字/公式）。
        """
        for conn in self.scene.connections:
            if conn.end_obj == stepper and conn.end_param == key:
                src = conn.start_obj
                if isinstance(src, DataProcessNode):
                    return _to_number(getattr(src, 'processed_data', ''))
                if isinstance(src, CsvDataNode):
                    h = conn.start_param or ''
                    if h in src.headers:
                        return _to_number(src.get_current_value(h))
                    return _to_number(getattr(src, 'source_data', ''))
                if isinstance(src, StepperNode):
                    return src.output_value
                if isinstance(src, APINode):
                    rj = getattr(src, 'result_json', None)
                    if isinstance(rj, (int, float)):
                        return float(rj)
                    if isinstance(rj, dict) and '_binary' not in rj:
                        for v in rj.values():
                            if isinstance(v, (int, float)):
                                return float(v)
                return 0.0
        return stepper.resolve_independent_input(key)

    def _inject_stepper_output(self, stepper, out):
        """把步进器输出立即注入下游元素框（API 参数/拼合栏，或写入 DP 源数据）。"""
        s = str(out)
        for c in self.scene.connections:
            if c.start_obj != stepper:
                continue
            if isinstance(c.end_obj, APINode):
                pn = c.end_param
                if not pn:
                    continue
                api = c.end_obj
                if pn.startswith('concat_'):
                    for cw in api._concat_widgets:
                        if cw.concat_id == pn:
                            cw.edit.setText(s)
                            cw.concat_value = s
                            break
                else:
                    for p in api.params:
                        if p.get('name') == pn:
                            p['value'] = s
                            break
                    if pn in api.param_widgets:
                        api.param_widgets[pn].edit.blockSignals(True)
                        api.param_widgets[pn].edit.setText(s)
                        api.param_widgets[pn].edit.blockSignals(False)
            elif isinstance(c.end_obj, DataProcessNode):
                # 步进器 → DP → 下游：写入 DP 源数据并重算正则
                dp = c.end_obj
                dp.set_source_data(s)

    def _compute_steppers(self, step_count, nodes=None):
        """每个轮询：所有步进器步进数+1，解析输入并计算输出，注入下游元素框。

        按拓扑顺序计算（上游步进器先算）：stepper→stepper 链中，
        下游能读到上游本轮的输出，而不是上一轮的旧值。
        nodes 指定时只计算该组内的步进器（类分组：跟随该类轮询次数）。
        """
        _src = self.scene.nodes if nodes is None else nodes
        steppers = [nd for nd in _src if isinstance(nd, StepperNode)]
        ordered = []
        visited = set()

        def _visit(nd, stack):
            if nd in stack or nd in visited:
                return
            stack.add(nd)
            for c in self.scene.connections:
                if c.start_obj is nd and isinstance(c.end_obj, StepperNode):
                    _visit(c.end_obj, stack)
            stack.discard(nd)
            visited.add(nd)
            ordered.append(nd)  # 后序：下游先入列

        for nd in steppers:
            _visit(nd, set())
        ordered.reverse()      # 反转 → 上游步进器优先计算

        for nd in ordered:
            input_values = {}
            for x in nd._inputs:
                key = x.get('key', '')
                input_values[key] = self._resolve_stepper_input(nd, key)
            out = nd.compute(step_count, input_values)
            self._inject_stepper_output(nd, out)
            if hasattr(self, 'api_log_text') and self.api_log_text:
                self.api_log_text.append(
                    f"🔢 [步进器] 第{step_count}轮 步数={step_count} "
                    f"输出={out} 输入={ {k: v for k, v in input_values.items()} }")

    # ---------- 站点解析元素框执行 ----------
    def _resolve_site_parser_url(self, node):
        """站点解析元素框 URL 来源：输入点连线（上游文本）优先，否则节点自身输入预览框。"""
        for conn in self.scene.connections:
            if conn.end_obj == node:
                src = conn.start_obj
                if isinstance(src, DataProcessNode):
                    return str(getattr(src, 'processed_data', '') or '').strip()
                if isinstance(src, CsvDataNode):
                    h = conn.start_param or ''
                    if h in src.headers:
                        return str(src.get_current_value(h)).strip()
                    return str(getattr(src, 'source_data', '') or '').strip()
                if isinstance(src, SiteParserNode):
                    return str(getattr(src, 'url', '') or '').strip()
                if isinstance(src, StepperNode):
                    return str(getattr(src, 'output_value', '') or '').strip()
                if isinstance(src, APINode):
                    rj = getattr(src, 'result_json', None)
                    if isinstance(rj, str):
                        return rj.strip()
                    if isinstance(rj, dict) and '_binary' not in rj:
                        for v in rj.values():
                            if isinstance(v, str):
                                return v.strip()
        return node.get_url()

    def _on_site_parse_file_done(self, node, name, path, size, ok, err):
        """站点解析一个文件下载完成（主线程）：记录节点状态，存盘走统一存储管线。"""
        node._last_ok = ok
        node._last_msg = err or '成功'
        node._last_size = size
        node._last_path = path or ''
        if ok and path:
            self.api_log_text.append(f"  📥 站点解析下载完成: {name}（{size / 1024:.1f} KB）")
        else:
            self.api_log_text.append(f"⚠ 站点解析失败: {name} — {err}")

    def _execute_site_parsers(self, round_idx, group_nodes=None):
        """每轮：执行站点解析元素框。

        流程与其他 GET 下载完全一致：
        1) 下载前查下载清单（manifest）去重——同一 URL 已下载且本地文件有效则跳过；
        2) 解析器后台下载到临时目录（自动代理端口，无需手改）；
        3) 下载成功后走统一存储管线：SHA-256 内容寻址命名 + 规则模式子文件夹
           创建时机 + 把 sha/地址/大小写入下载清单，供后续轮次去重。
        group_nodes 指定时只执行该组内的站点解析（类分组）。
        """
        _src = self.scene.nodes if group_nodes is None else group_nodes
        nodes = [nd for nd in _src if isinstance(nd, SiteParserNode)]
        if not nodes:
            return
        try:
            from site_parser import manager as sp_manager, SiteParseWorker
        except Exception as e:
            self.api_log_text.append(f"⚠ 站点解析模块不可用: {e}")
            return
        self.api_log_text.append(f"  🌐 第{round_idx}轮 检测到 {len(nodes)} 个站点解析元素框")
        # 站点解析下载暂存目录：先下载到临时目录，再走统一存储管线转储到容器
        tmp_dl_dir = os.path.join(tempfile.gettempdir(), 'site_parse_dl')
        try:
            os.makedirs(tmp_dl_dir, exist_ok=True)
            for _f in os.listdir(tmp_dl_dir):
                try:
                    os.remove(os.path.join(tmp_dl_dir, _f))
                except OSError:
                    pass
        except Exception:
            pass
        for nd in nodes:
            if self._inner_cancel:
                break
            url = self._resolve_site_parser_url(nd)
            # 有输入点连线：以上游推送文本为准（只更新识别预览，不覆盖手填 URL）；
            # 否则更新预览框 URL 与识别状态。
            if nd._has_input_connection():
                nd.set_input_text(url)
            else:
                nd.set_url(url)
            if not url:
                self.api_log_text.append("  🌐 站点解析: URL 为空，跳过")
                continue
            if getattr(nd, '_executed_url', '') == url and getattr(nd, '_executed_ok', False):
                self.api_log_text.append(f"  ⏭ 站点解析已处理过该 URL，跳过: {url[:60]}")
                continue
            parser = sp_manager.detect(url)
            if parser is None:
                self.api_log_text.append(f"  ⚠ 站点解析: 无法识别站点: {url[:60]}")
                continue
            containers = self._downstream_containers(nd)
            if not containers:
                self.api_log_text.append(
                    "  ⚠ 站点解析: 输出点未连到容器框，跳过（请把输出点连到容器框根文件夹）")
                continue
            container = containers[0]
            storage_path = getattr(container, 'storage_path', '') or ''
            if not storage_path:
                self.api_log_text.append("  ⚠ 站点解析: 容器未设置存储路径，跳过")
                continue
            # ---- 下载清单去重：同一 URL 已下载且本地文件有效则跳过（与普通 GET 一致）----
            # 清单与实际存盘同根：接了「根目录输入点」时清单在 <storage_path>/<dyn_root>/，
            # 若这里用 container.storage_path 会查空清单 → 每轮重复下载（已修）。
            source_key = url
            _sp_ok, _sp_why = manifest_check(self._manifest_root(container), source_key)
            if _sp_ok:
                self.api_log_text.append(f"  ⏭ 站点解析: 清单显示已下载，跳过: {url[:60]}")
                nd._executed_url = url
                nd._executed_ok = True
                continue
            if _sp_why:
                self.api_log_text.append(
                    f"  ↻ 站点解析: 清单命中但作废（{_sp_why}），重新下载: {url[:60]}")
            # ---- 规则模式：构建规则配置（子文件夹创建时机，与普通 GET 一致）----
            rule_config = None
            rule_data = None
            if getattr(container, '_rule_enabled', False):
                rule_config = {
                    'enabled': True,
                    'creation_path': getattr(container, '_rule_creation_path', '') or '',
                    'time_enabled': bool(getattr(container, '_rule_time_enabled', False)),
                    'time_format': getattr(container, '_rule_time_format', 'yyyy-mm-dd'),
                    'parts': [
                        self._rule_part_config(container, pd)
                        for pd in getattr(container, '_rule_parts', [])
                    ],
                }
                try:
                    _fn = _compute_rule_folder_name(rule_config, rule_data)
                    self.api_log_text.append(f"  📁 站点解析规则子文件夹: {_fn}")
                except Exception:
                    pass
            # 创建输出条目（[GET] + 实时进度条）
            fname = url.split('/')[-1].split('?')[0] or 'file'
            set_progress, set_done, set_title, set_target = \
                self._create_site_parse_entry(fname, url)
            set_progress(None, "⏳ 调用解析器...")
            self.api_log_text.append(f"  🌐 [{parser.name}] 解析: {url[:80]}")
            QApplication.processEvents()

            worker = SiteParseWorker(parser, url, tmp_dl_dir, parent=self)
            # ⚠ 跨线程 file_done 是排队信号：worker 线程结束的瞬间信号可能尚未被
            # 主线程处理，若只按 isRunning 退出会把 _last_ok/_last_path 判空 → 漏存盘。
            # 用标志位确保 file_done 已被主线程处理后再继续。
            _dl_flag = {'done': False}
            worker.file_found.connect(
                lambda nm, n=nd, st=set_title: (
                    st(nm),
                    n._set_status_text(f"已识别: {n.parser_name} 下载 {nm}")))
            worker.progress.connect(
                lambda nm, pct: set_progress(pct, f"⬇️ {nm} {pct}%"))
            worker.file_done.connect(
                lambda nm, path, size, ok, err, n=nd, f=_dl_flag:
                (f.__setitem__('done', True),
                 self._on_site_parse_file_done(n, nm, path, size, ok, err)))
            worker.log.connect(self.api_log_text.append)
            worker.start()
            # 等待完成（保持 UI 响应，可被取消按钮打断）；
            # 必须等 file_done 被主线程处理完，否则 _last_ok/_last_path 未写入会漏存盘
            while (worker.isRunning() or not _dl_flag['done']) and not self._inner_cancel:
                QApplication.processEvents()
                time.sleep(0.02)
            if self._inner_cancel:
                worker.cancel()
                try:
                    worker.wait(3000)
                except Exception:
                    pass
            # ---- 下载成功 → 统一存储管线（SHA-256 内容寻址 + 规则子文件夹 + 清单）----
            if getattr(nd, '_last_ok', False) and getattr(nd, '_last_path', ''):
                self._store_site_parse_file(
                    container, nd, source_key, rule_config, rule_data,
                    set_done, set_target)
            else:
                set_done(False, getattr(nd, '_last_msg', '已取消') or '失败')
            self._safe_delete_worker(worker)

    def _store_site_parse_file(self, container, node, source_key, rule_config,
                               rule_data, set_done, set_target=None):
        """站点解析下载完成后：走与其他 GET 一致的内容寻址存盘管线。

        由 StoreWorker 后台计算 SHA-256、按内容寻址命名、应用规则子文件夹；
        成功后更新容器计数，并把 sha/相对地址/大小写入下载清单（manifest），
        供后续轮次去重（manifest_should_skip）。
        """
        tmp_path = getattr(node, '_last_path', '')
        if not tmp_path or not os.path.isfile(tmp_path):
            set_done(False, '下载文件不存在')
            return
        self._store_counter += 1
        tid = self._store_counter
        # ---- 根目录输入点：解析本轮该用的根目录（轮询锁：上游没就绪就不写）----
        _root_name, _root_ok, _root_why = self._container_root_value(container)
        if not _root_ok:
            self.api_log_text.append(
                f"⏸ 跳过站点解析存盘：{_root_why}")
            set_done(False, '容器根目录未就绪，已跳过')
            return
        container.dyn_root = _root_name
        _storage = container.ensure_active_storage_path()
        # 规则段上游 DP 的显示同步（站点解析存盘路径同样刷新）
        self._sync_rule_part_dp_display(container, rule_config, rule_data)
        # mega 等站点链接的 URL path 不含文件名（如 /file/AbCd#key 或 /#!ID!KEY），
        # 把实际下载的文件名插入链接（# 之前），拼成 api_url，
        # 让 _io_store_data_from_file 正确推断扩展名（urlparse 在 # 处截断路径）
        dl_name = os.path.basename(tmp_path) or 'file'
        # ---- 让下载条目显示落位（根目录 / 规则子文件夹 / 文件名），并支持点击直达 ----
        if set_target is not None:
            try:
                _sub_fn = ''
                if rule_config and rule_config.get('enabled') and rule_data is not None:
                    _sub_fn = _compute_rule_folder_name(rule_config, rule_data)
                set_target(_storage, _sub_fn, dl_name)
            except Exception as e:
                self.api_log_text.append(f"  ⚠ 刷新下载目标位置失败: {e}")
        if '#' in node.url:
            _head, _, _frag = node.url.partition('#')
            api_url = f"{_head}/{dl_name}#{_frag}"
        else:
            api_url = f"{node.url}/{dl_name}"
        worker = StoreWorker(
            tid, _io_store_data_from_file,
            (tmp_path, api_url, '', _storage, rule_config, rule_data),
            parent=self)
        self._store_workers.add(worker)

        _store_flag = {'done': False}

        def _on_stored(ok, msg, fp, sha):
            try:
                if ok and fp:
                    try:
                        size = os.path.getsize(fp)
                        container.stored_bytes += size
                        container.total_files += 1
                        container._latest_filename = os.path.basename(fp)
                        container._refresh_display()
                        # 写入下载清单（SHA-256 + 相对地址 + 大小），供后续去重
                        try:
                            local_rel = os.path.relpath(fp, _storage or container.storage_path)
                        except Exception:
                            local_rel = os.path.basename(fp)
                        manifest_add(source_key, _storage or container.storage_path, local_rel, size, sha or '')
                        node._executed_url = node.url
                        node._executed_ok = True
                        node._last_size = size
                        self.api_log_text.append(
                            f"📦 站点解析 → 容器: {local_rel}  "
                            f"(sha256: {sha[:12]}…, {size / 1024:.1f} KB)")
                        set_done(True, f"完成 {size / 1024:.1f} KB", fp)
                    except Exception:
                        set_done(True, '已存储')
                else:
                    self.api_log_text.append(f"⚠ 站点解析存储失败: {msg}")
                    set_done(False, msg or '存储失败')
            finally:
                _store_flag['done'] = True
            self._store_workers.discard(worker)
            self._safe_delete_worker(worker)

        worker.done.connect(lambda tid, ok, msg, fp, sha: _on_stored(ok, msg, fp, sha))
        worker.start()
        # 等待存盘完成（保持 UI 响应，可被取消按钮打断）；
        # 同样等 done 被主线程处理完，避免 _on_stored 延迟执行
        while (worker.isRunning() or not _store_flag['done']) and not self._inner_cancel:
            QApplication.processEvents()
            time.sleep(0.02)

    # ---------- JSON 路径解析 & 数组迭代 ----------
    @staticmethod
    def _parse_json_path(path):
        """将 $.a.b[0].c 解析为 [('a', None), ('b', 0), ('c', None)] 段列表"""
        if not path or not path.startswith('$.'):
            return []
        segments = []
        for part in path[2:].split('.'):
            if '[' in part and part.endswith(']'):
                name, idx_str = part[:-1].split('[')
                segments.append((name, int(idx_str)))
            else:
                segments.append((part, None))
        return segments

    @staticmethod
    def _resolve_path_generalized(data, segments, wildcard_mode=False):
        """
        解析路径段，返回匹配的值列表。
        wildcard_mode=True 时，遇到含索引的段也会尝试遍历所有数组元素。
        """
        results = []

        def _walk(current, seg_idx):
            if seg_idx >= len(segments):
                results.append(current)
                return
            name, idx = segments[seg_idx]

            if isinstance(current, list):
                if not name and idx is not None:
                    # 直接索引访问列表：$[N] 或 $[N].xxx
                    if wildcard_mode:
                        for elem in current:
                            _walk(elem, seg_idx + 1)
                    elif 0 <= idx < len(current):
                        _walk(current[idx], seg_idx + 1)
                    else:
                        return
                else:
                    # 当前是列表：对每个元素继续解析剩余路径
                    for item in current:
                        if isinstance(item, dict):
                            _walk(item, seg_idx)
                        elif isinstance(item, list):
                            _walk(item, seg_idx)
                        else:
                            _walk(item, seg_idx)
            elif isinstance(current, dict):
                if name:
                    # 普通键名访问
                    if name in current:
                        val = current[name]
                        if idx is not None:
                            # 有指定索引
                            if isinstance(val, list) and 0 <= idx < len(val):
                                if wildcard_mode:
                                    for elem in val:
                                        _walk(elem, seg_idx + 1)
                                else:
                                    _walk(val[idx], seg_idx + 1)
                            else:
                                _walk(val, seg_idx + 1)
                        else:
                            _walk(val, seg_idx + 1)
                    else:
                        return
                else:
                    # name 为空表示直接访问数组索引（如 $[0].name）
                    if idx is not None and isinstance(current, list) and 0 <= idx < len(current):
                        if wildcard_mode:
                            for elem in current:
                                _walk(elem, seg_idx + 1)
                        else:
                            _walk(current[idx], seg_idx + 1)
                    else:
                        return
            else:
                # 标量值，尝试作为最后一段处理
                if seg_idx == len(segments) - 1:
                    results.append(current)

        _walk(data, 0)
        return results

    @staticmethod
    def _resolve_source_list(data, segments):
        """沿路径走到数组段位置，返回该源数组（列表）。找不到返回 None。

        用于数组轮询时按数组项解析规则（嵌套数组如 $.data[0] 也能识别）。
        """
        cur = data
        for name, idx in segments:
            if isinstance(cur, list):
                if not name and idx is not None:
                    return cur  # $[N] 段：返回该数组本身
                # 数组内继续下钻：取首个元素
                if cur:
                    cur = cur[0]
                else:
                    return None
            elif isinstance(cur, dict):
                if name in cur:
                    val = cur[name]
                    if idx is not None and isinstance(val, list):
                        return val  # name[idx] 段：返回该数组本身
                    cur = val
                else:
                    return None
            else:
                return None
        return None

    @staticmethod
    def _resolve_leaf_naming_data(data, segments):
        """与 _resolve_path_generalized(wildcard=True) 一一对应，返回每个拍平叶子的
        “命名数据”（用于规则文件夹命名），保证文件夹与下载项索引对齐：

        - 拍平项本身是 dict（$.items[0]、$.groups[0].items[0]）→ 该项自身
        - 拍平项是标量（如 $.[0].attachments.[0].path 拍平成路径字符串）→ 返回其
          所属最外层数组元素 dict（帖子）：同一帖子的多个附件落到同一文件夹，
          且与下载索引一一对应，不再因外层数组长度与拍平长度不同而取模错位
        - 找不到 dict → None（调用方回退 result_json / 参数值）
        """
        out = []

        def _walk(cur, seg_idx, outer):
            if seg_idx >= len(segments):
                out.append(cur if isinstance(cur, dict) else outer)
                return
            name, idx = segments[seg_idx]
            if isinstance(cur, list):
                if not name and idx is not None:
                    # $[N] 数组段（通配展开所有元素）
                    for elem in cur:
                        _walk(elem, seg_idx + 1,
                              outer if outer is not None
                              else (elem if isinstance(elem, dict) else None))
                else:
                    # 列表：逐项继续解析（与 _resolve_path_generalized 一致）
                    for item in cur:
                        _walk(item, seg_idx, outer)
            elif isinstance(cur, dict):
                if name:
                    if name in cur:
                        val = cur[name]
                        if idx is not None:
                            if isinstance(val, list) and 0 <= idx < len(val):
                                for elem in val:
                                    _walk(elem, seg_idx + 1,
                                          outer if outer is not None else cur)
                            else:
                                _walk(val, seg_idx + 1, outer)
                        else:
                            _walk(val, seg_idx + 1, outer)
                    else:
                        return
                else:
                    # 数组下标段（$[0]）：数据已是数组元素时跳过，继续按后续键解析
                    if idx is not None and isinstance(cur, list) and 0 <= idx < len(cur):
                        _walk(cur[idx], seg_idx + 1, outer)
                    else:
                        return
            else:
                # 标量：尝试作为最后一段处理
                if seg_idx == len(segments) - 1:
                    out.append(outer)

        _walk(data, 0, None)
        return out

    def _has_connected_stepper(self):
        """场景中是否存在已连线的步进器（用于判断是否为步进器驱动的轮询）。"""
        for conn in self.scene.connections:
            if isinstance(conn.start_obj, StepperNode) or isinstance(conn.end_obj, StepperNode):
                return True
        return False

    def _resolve_array_values(self, api_node, resp_data):
        """解析当前 API 节点的响应数据，为下游连线的参数提取数组值用于迭代"""
        # 响应代次：每次调用（每个 API 节点的一次响应）递增。
        # 下游节点的 _array_source_list 只在本代次内做“取最长”，
        # 跨响应一律以本次为准（防止轮次变少时沿用上一轮的源数据列表导致错位）。
        self._resolve_generation = getattr(self, '_resolve_generation', 0) + 1
        dbg = []
        for conn in self.scene.connections:
            if conn.start_obj != api_node:
                continue
            # 收集下游节点 + 中间 DP 节点
            targets = []       # (api_node, param_name, dp_node_or_None)
            if isinstance(conn.end_obj, APINode):
                targets.append((conn.end_obj, conn.end_param, None))
            elif isinstance(conn.end_obj, DataProcessNode):
                dp_mid = conn.end_obj
                for c2 in self.scene.connections:
                    if c2.start_obj == dp_mid and isinstance(c2.end_obj, APINode):
                        targets.append((c2.end_obj, c2.end_param, dp_mid))
            for api, pname, dp_mid in targets:
                if pname and pname.startswith('concat_'):
                    # 拼合栏(source_path)数组绑定也纳入轮询：让「手动主输入 +
                    # 拖动 hash 到拼合栏」的节点（如 img 缩略图链）与主参数数组
                    # 一样按全量数组项逐项内轮询下载。
                    self._resolve_concat_array_values(api, pname, dp_mid, resp_data, dbg)
                    continue
                if pname and pname in api.param_widgets:
                    pw = api.param_widgets[pname]
                    src = getattr(pw, 'source_path', '')
                    # 回退1：从 API 节点 params 持久化数据读取
                    if not src or not src.startswith('$.'):
                        for p in api.params:
                            if p.get('name') == pname and p.get('source_path'):
                                src = p['source_path']
                                pw.source_path = src
                                break
                    # 回退2：从中间 DP 节点的路径读取
                    if (not src or not src.startswith('$.')) and dp_mid:
                        dp_src = getattr(dp_mid, 'source_path', '')
                        dbg.append(f"    [回退] DP节点 source_path='{dp_src}'")
                        if dp_src and dp_src.startswith('$.'):
                            src = dp_src
                            pw.source_path = dp_src
                    dbg.append(f"  参数[{pname}] source_path='{src}'  dp_mid={'有' if dp_mid else '无'}")
                    
                    # 如果 source_path 仍为空但有 DP 数据，提示重新拖拽
                    if (not src or not src.startswith('$.')) and dp_mid and getattr(dp_mid, 'source_data', ''):
                        self.api_log_text.append(
                            f"  ⚠ 参数 '{pname}' 缺少路径绑定！请重新从输出树拖拽数据到该参数栏以建立绑定。\n"
                            f"    当前仅使用缓存值: {str(dp_mid.source_data)[:60]}")
                        continue  # 跳过数组解析，因为无路径
                    
                    if src and src.startswith('$.'):
                        segments = self._parse_json_path(src)
                        # 先用准确路径取单个值
                        single = self._resolve_path_generalized(resp_data, segments, wildcard_mode=False)
                        # 再用通配模式取所有可能值（展开数组）
                        all_vals = self._resolve_path_generalized(resp_data, segments, wildcard_mode=True)
                        dbg.append(f"    路径 {src} → 通配值 {len(all_vals)} 个: {str(all_vals)[:120]}")
                        if len(all_vals) > 1:
                            api._array_values[pname] = all_vals
                            # 记录源数据列表（供下游下载存盘时按当前项解析规则）。
                            # 用 _resolve_leaf_naming_data 得到与 all_vals（实际迭代的
                            # 拍平列表）一一对应的“命名数据”，保证文件夹与下载项索引对齐：
                            #   - 拍平项是 dict（$.items[0]、$.groups[0].items[0]）→ 该项本身
                            #   - 拍平项是标量（如 $.[0].attachments.[0].path 拍平成路径
                            #     字符串）→ 其所属最外层数组元素 dict（帖子），
                            #     使同一帖子的多个附件都落到同一文件夹且不与下载错位
                            src_list = self._resolve_leaf_naming_data(resp_data, segments)
                            # 调试：若外层源数组与迭代数组不一致（嵌套数组），打印差异
                            outer = resp_data if isinstance(resp_data, list) \
                                else self._resolve_source_list(resp_data, segments)
                            if isinstance(outer, list) and len(outer) != len(all_vals):
                                self.api_log_text.append(
                                    f"  [调试] 嵌套数组: 参数 '{pname}' 迭代(拍平)={len(all_vals)}项 "
                                    f"外层源数组={len(outer)}项 → 文件夹按迭代项命名（防错位）")
                            if isinstance(src_list, list) and src_list:
                                # 源数据列表必须与【当前响应】对齐：每轮都刷新。
                                # 旧逻辑仅在 len(all_vals) >= 上一轮长度 时更新，
                                # 轮次变少时（如封面 50→47 项、附件 236→109 项）会
                                # 保留上一轮的帖子列表，使 _build_rule_data 的文件夹名
                                # 与 _current_source_key 的清单主键解析到上一轮帖子，
                                # 文件落入旧帖子命名的文件夹（错位）。
                                # 多个数组参数时，取【当前响应内】最大源列表为准
                                # （与 max_inner 轮询项数对齐）；跨响应一律以本次为准。
                                gen = self._resolve_generation
                                if getattr(api, '_array_src_gen', None) != gen:
                                    api._array_src_gen = gen
                                    api._resp_src_best_len = -1
                                if len(all_vals) >= getattr(api, '_resp_src_best_len', -1):
                                    api._resp_src_best_len = len(all_vals)
                                    api._array_source_list = src_list
                            # 保留已有索引（不重置），仅在首次或数组长度变化时重置
                            old_idx = api._array_index.get(pname, 0)
                            old_total = api._array_rounds.get(pname, 0)
                            if old_total != len(all_vals) or not api._array_values.get(pname):
                                api._array_index[pname] = 0
                            else:
                                # 数组不变，保留当前进度索引
                                api._array_index[pname] = old_idx % len(all_vals)
                            api._array_rounds[pname] = len(all_vals)
                            dbg.append(f"    索引保持: old={old_idx} new={api._array_index[pname]} total={len(all_vals)}")
                            self.api_log_text.append(
                                f"🔄 参数 '{pname}' 检测到 {len(all_vals)} 个数组值，将逐一轮询")
                            self.api_log_text.append(
                                f"💡 提示: 请在顶部将「轮询次数」设为 ≥{len(all_vals)} 以遍历全部数据")
                            # 自动设置轮询次数（取所有数组值中最大长度）
                            # 步进器驱动轮询时跳过：轮询轮次由步进器/用户手动设置决定，
                            # 避免数组检测把左上角「轮询次数」自动改大
                            if not self._has_connected_stepper():
                                needed = max(len(all_vals), self.poll_count_spin.value())
                                if needed > self.poll_count_spin.value():
                                    self.poll_count_spin.setValue(needed)
                        elif single:
                            # 只有一个值，用第一个
                            api._array_values[pname] = single
                            api._array_index[pname] = 0
                            api._array_rounds[pname] = 1
                        # ---- 如果有中间 DP 节点，将当前值推入其 source_data 用于后续正则处理 ----
                        if dp_mid:
                            cur_val = str(all_vals[api._array_index.get(pname, 0)]) if all_vals else str(single[0]) if single else ''
                            dp_mid.source_data = cur_val
                            dp_mid.src_label.setText(f"源数据: {cur_val}")
                            # 重新应用 DP 的正则
                            rx = dp_mid.regex_edit.text() if hasattr(dp_mid, 'regex_edit') and dp_mid.regex_edit else ''
                            rp = dp_mid.replace_edit.text() if hasattr(dp_mid, 'replace_edit') and dp_mid.replace_edit else ''
                            if rx and cur_val:
                                try:
                                    dp_mid.processed_data = re.sub(rx, rp, cur_val)
                                except Exception:
                                    dp_mid.processed_data = "正则错误"
                            else:
                                dp_mid.processed_data = cur_val
                            if hasattr(dp_mid, 'result_label') and dp_mid.result_label:
                                dp_mid.result_label.setText(f"处理后: {dp_mid.processed_data}")
        # 输出调试信息（仅当有下游连接时）
        if dbg:
            self.api_log_text.append(f"  🐛 数组解析调试:")
            for _line in dbg:
                self.api_log_text.append(_line)

    def _resolve_concat_array_values(self, api, concat_id, dp_mid, resp_data, dbg):
        """拼合栏(source_path)数组解析：拖到拼合栏的数组字段也驱动该 API 节点
        的内轮询，使「手动主输入 + 拖动 hash 到拼合栏」的节点（如 img 缩略图链）
        能像主参数数组节点一样按全量数组项（44/185…）逐项轮询下载。

        - 从拼合栏控件 source_path 读路径（缺省回退到挂载该 concat 的 DP）
        - 通配模式取全量值 → api._concat_array_values/_concat_array_index
        - 把当前项推入 DP source_data（正则后由 _get_csv_processed_values 写入
          拼合栏）或直接写入拼合栏控件，保证本轮 URL 立即生效
        """
        cw = next((c for c in getattr(api, '_concat_widgets', []) if c.concat_id == concat_id), None)
        if cw is None:
            return
        src = getattr(cw, 'source_path', '') or ''
        # 回退：从挂在该 concat 上的 DP 读取路径（拖动绑定总会建 DP 链）
        if (not src or not src.startswith('$.')) and dp_mid is not None:
            dp_src = getattr(dp_mid, 'source_path', '') or ''
            if dp_src.startswith('$.'):
                src = dp_src
                try:
                    cw.source_path = dp_src
                except Exception:
                    pass
        dbg.append(f"  拼合栏[{concat_id}] source_path='{src}'  dp_mid={'有' if dp_mid else '无'}")
        if not src.startswith('$.'):
            return
        segments = self._parse_json_path(src)
        single = self._resolve_path_generalized(resp_data, segments, wildcard_mode=False)
        all_vals = self._resolve_path_generalized(resp_data, segments, wildcard_mode=True)
        if not isinstance(all_vals, (list, tuple)) or not all_vals:
            return
        dbg.append(f"    拼合路径 {src} → 通配值 {len(all_vals)} 个: {str(all_vals)[:120]}")
        if len(all_vals) > 1:
            api._concat_array_values[concat_id] = list(all_vals)
            # 记录源数据列表（供规则文件夹命名与下载项索引对齐，同主参数分支）
            src_list = self._resolve_leaf_naming_data(resp_data, segments)
            if isinstance(src_list, list) and src_list:
                gen = self._resolve_generation
                if getattr(api, '_array_src_gen', None) != gen:
                    api._array_src_gen = gen
                    api._resp_src_best_len = -1
                if len(all_vals) >= getattr(api, '_resp_src_best_len', -1):
                    api._resp_src_best_len = len(all_vals)
                    api._array_source_list = src_list
            # 保留已有索引（不重置），仅在首次或数组长度变化时重置
            old_idx = api._concat_array_index.get(concat_id, 0)
            old_total = api._concat_array_rounds.get(concat_id, 0)
            if old_total != len(all_vals) or not api._concat_array_values.get(concat_id):
                api._concat_array_index[concat_id] = 0
            else:
                api._concat_array_index[concat_id] = old_idx % len(all_vals)
            api._concat_array_rounds[concat_id] = len(all_vals)
            dbg.append(f"    拼合索引保持: old={old_idx} new={api._concat_array_index[concat_id]} total={len(all_vals)}")
            self.api_log_text.append(
                f"🔄 拼合栏 '{concat_id}' 检测到 {len(all_vals)} 个数组值，将逐一轮询")
            if not self._has_connected_stepper():
                needed = max(len(all_vals), self.poll_count_spin.value())
                if needed > self.poll_count_spin.value():
                    self.poll_count_spin.setValue(needed)
        else:
            api._concat_array_values[concat_id] = list(all_vals)
            api._concat_array_index[concat_id] = 0
            api._concat_array_rounds[concat_id] = 1
        # 将当前项推入 DP（经正则后写入拼合栏）或直接写入拼合栏控件
        cur = ''
        idx = api._concat_array_index.get(concat_id, 0)
        if isinstance(all_vals, (list, tuple)) and idx < len(all_vals):
            cur = str(all_vals[idx])
        elif single is not None:
            cur = str(single) if not isinstance(single, (list, dict)) else str(single)
        if cur:
            if dp_mid is not None:
                dp_mid.source_data = cur
                if hasattr(dp_mid, 'src_label') and dp_mid.src_label:
                    dp_mid.src_label.setText(f"源数据: {cur}")
                rx = dp_mid.regex_edit.text() if hasattr(dp_mid, 'regex_edit') and dp_mid.regex_edit else ''
                rp = dp_mid.replace_edit.text() if hasattr(dp_mid, 'replace_edit') and dp_mid.replace_edit else ''
                if rx and cur:
                    try:
                        dp_mid.processed_data = re.sub(rx, rp, cur)
                    except Exception:
                        dp_mid.processed_data = "正则错误"
                else:
                    dp_mid.processed_data = cur
                if hasattr(dp_mid, 'result_label') and dp_mid.result_label:
                    dp_mid.result_label.setText(f"处理后: {dp_mid.processed_data}")
            else:
                try:
                    cw.edit.setText(cur)
                    cw.concat_value = cur
                except Exception:
                    try:
                        cw.concat_value = cur
                    except Exception:
                        pass

    def is_binary_content(self, content_type):
        """检测是否为二进制媒体内容"""
        ct = content_type.lower()
        binary_patterns = [
            'image/', 'video/', 'audio/',
            'application/zip', 'application/x-rar', 'application/x-tar',
            'application/gzip', 'application/x-7z', 'application/x-bzip',
            'application/octet-stream', 'application/pdf',
            'application/vnd', 'application/x-ms',
            'application/x-www-form-urlencoded',  # 有时用于 POST 二进制
            'multipart/',
        ]
        # 没有 Content-Type 或通用类型也视为二进制（保守策略）
        if not ct or ct in ('application/octet-stream', 'binary/octet-stream', ''):
            return True
        return any(p in ct for p in binary_patterns)

    def _threaded_request(self, method, url, timeout=DOWNLOAD_TIMEOUT, file_id=0,
                          progress_cb=None, headers=None):
        """
        后台线程执行单个文件的下载，主线程持续处理事件保持 UI 响应。
        使用窗口左下角的状态栏进度条（非模态，绝不阻塞窗口）。
        timeout 为 (连接超时, 读取超时) 元组，读取超时是每次读取间隔而非总时长。
        progress_cb(file_id, received, total) 可选：实时回调（用于右侧详情进度条）。
        headers：自定义请求头（None/空 = 只用内置默认浏览器头）。

        返回 (result, is_tmp_path, used_curl, error_msg, content_type)：
        - is_tmp_path=False：result 为 response 对象（小文件，内存）
        - is_tmp_path=True ：result 为 .dlpack 临时文件路径（大文件，需转储后清理）
        """
        # 已取消：不再发起新请求
        if self._inner_cancel:
            return None, False, False, "已取消", ''

        _cfg = self._run_transfer()
        # 显式传快照：本 worker 全程只认这一份参数，不读任何全局
        worker = DownloadWorker(method, url, headers=dict(headers or {}),
                                timeout=timeout, file_id=file_id, parent=self,
                                cfg=_cfg)
        self._current_worker = worker
        result = {'res': None, 'tmp': False, 'curl': False, 'err': None,
                  'ct': ''}
        done_flag = {'done': False}

        # 显示左下角进度条（非模态，不阻塞任何窗口）
        # 注意：只在首次调用时显示，整个流程期间保持显示，不反复隐藏避免跳行
        self._show_status()
        self._status_bar.setRange(0, 0)
        self._status_bar.setFormat("正在请求...")
        self._status_label.setText("⬇️ 请求中...")

        def _on_progress(fid, received, total):
            self._update_status_progress(received, total)
            if progress_cb is not None:
                try:
                    progress_cb(fid, received, total)
                except Exception:
                    pass

        def _on_done(fid, res, is_tmp, used_curl, err, ct):
            result['res'] = res
            result['tmp'] = is_tmp
            result['curl'] = used_curl
            result['err'] = err
            result['ct'] = ct
            done_flag['done'] = True

        worker.progress.connect(_on_progress)
        worker.done.connect(_on_done)
        worker.start()

        # 主线程循环处理事件直到收到完成信号（窗口保持响应，不会卡死）
        import time as _t
        while not done_flag['done']:
            QApplication.processEvents()
            if self._inner_cancel or (self._status_cancel_btn.isVisible()
                                      and getattr(self, '_cancel_pressed', False)):
                self._cancel_pressed = False
                worker.stop()
                # 取消后继续处理事件直到线程真正结束（防止销毁运行中的线程）。
                # 带 10 秒上限：若线程仍不退出（如阻塞在 socket recv），
                # 强制 terminate 兜底，确保下载进程完全退出、不再续传。
                _deadline = _t.time() + 10
                while worker.isRunning() and not done_flag['done'] and _t.time() < _deadline:
                    QApplication.processEvents()
                    _t.sleep(0.02)
                if worker.isRunning() and not done_flag['done']:
                    try:
                        worker.terminate()
                        worker.wait(3000)
                    except Exception:
                        pass
                    # 强制终止后 worker 未 emit done，这里补上取消结果
                    if not done_flag['done']:
                        result['res'] = None
                        result['tmp'] = False
                        result['err'] = '已取消'
                        done_flag['done'] = True
                break
            _t.sleep(0.01)

        # 确保线程已完全结束再清理（防止 QThread: Destroyed while running）
        if worker.isRunning():
            worker.wait(5000)
        # 注意：这里不隐藏状态栏——由 run_flow 结束时统一 _reset_status()，
        # 避免内部小轮询每个 item 显示/隐藏导致底部空行反复跳动
        self._safe_delete_worker(worker)
        if self._current_worker is worker:
            self._current_worker = None
        return result['res'], result['tmp'], result['curl'], result['err'], result['ct']

    def _compute_api_url(self, node, round_index, inner_idx=None):
        """计算本次请求的完整 URL 与方法（参数替换 + 拼合）。

        副作用：inner_idx 提供时更新 node._array_index；更新参数控件显示。
        返回 (full_url, method, values)。
        """
        # 只取本节点上游直接相连的 DP 值（避免与其他节点共用参数名时相互覆盖）
        dp_values = self._get_csv_processed_values(for_node=node)
        if inner_idx is not None:
            for pname in node._array_values:
                arr = node._array_values[pname]
                if len(arr) > 1:
                    node._array_index[pname] = inner_idx % len(arr)
        # 调试：显示数组迭代状态
        if node._array_values:
            self.api_log_text.append(
                f"  🐛 数组状态: values={ {k: len(v) for k, v in node._array_values.items()} } "
                f"index={dict(node._array_index)} rounds={dict(node._array_rounds)}")
        values = {}
        for p in node.params:
            raw = p.get('value', '')
            pname = p['name']

            # ---- 1) 优先使用 DP 处理后的值（正则在 DP 中已应用） ----
            # 手动输入的主参数视为「锁定值」（常态保持存在）：即使残留 DP 直连也不覆盖；
            # DP/CSV 动态内容应通过拼合栏承载（连线时已自动引导过去）。
            if pname in dp_values and not _api_param_is_manual_input(node, pname):
                val = dp_values[pname]
                values[pname] = val
                p['value'] = val
                if pname in node.param_widgets:
                    node.param_widgets[pname].edit.blockSignals(True)
                    node.param_widgets[pname].edit.setText(val)
                    node.param_widgets[pname].edit.blockSignals(False)
                self.api_log_text.append(f"  🐛 参数[{pname}] 使用 DP 处理值: {val}")

            # ---- 2) 数组迭代轮询：使用当前索引的值 ----
            elif pname in node._array_values and pname in node._array_index:
                idx = node._array_index[pname]
                arr = node._array_values[pname]
                val = str(arr[idx]) if idx < len(arr) else raw
                values[pname] = val
                p['value'] = val
                if pname in node.param_widgets:
                    node.param_widgets[pname].edit.blockSignals(True)
                    node.param_widgets[pname].edit.setText(val)
                    node.param_widgets[pname].edit.blockSignals(False)
                self.api_log_text.append(
                    f"  🐛 参数[{pname}] 使用数组索引 {idx}/{len(arr)} 值: {val}")
            else:
                values[pname] = raw
        url_path = node.url_path
        for k, v in values.items():
            url_path = url_path.replace(f'{{{k}}}', str(v))
        # ---- 拼合值：追加到 URL 路径末尾 ----
        concat_vals = node.get_all_concat_values()
        if concat_vals:
            cleaned = [str(v).lstrip('/') for v in concat_vals if str(v).strip()]
            if cleaned:
                for v in cleaned:
                    # 自动修复：如果值中包含 /? 替换为 ?（去掉路径与查询参数之间多余的斜杠）
                    v = re.sub(r'/\?', '?', v, count=1)
                    if v.startswith(('?', '&')):
                        url_path = url_path.rstrip('/') + v
                    elif url_path in ('', '/'):
                        url_path = v
                    elif url_path.endswith('/'):
                        url_path += v
                    else:
                        url_path += '/' + v
                self.api_log_text.append(f"🔗 拼合值: {' / '.join(cleaned)}")
        full_url = node.api_ref.get('base_url', '') + url_path
        method = node.method.upper()

        round_tag = f" [第{round_index}轮]" if round_index else ""
        req_header = f"{'='*60}\n▶ 请求{round_tag}: {method} {full_url}\n{'='*60}"
        self.api_log_text.append(req_header)
        if values:
            self.api_log_text.append(
                f"参数: {json.dumps(values, ensure_ascii=False, indent=2)}")
        _mh = getattr(node, 'headers', None) or {}
        if _mh:
            # 只打键名与打码后的值，绝不把 Cookie/Token 原样写进日志
            self.api_log_text.append(f"请求头: {describe_headers(_mh)}")
        return full_url, method, values

    def _execute_api_node(self, node, round_index, progress_cb=None):
        """执行单个 APINode 的 HTTP 请求，返回响应数据"""
        full_url, method, _values = self._compute_api_url(node, round_index)
        # 节点级自定义请求头：${ENV:VAR} 取环境变量，${DATE:时区} 现取当前时间。
        # 空值 = 显式不发该头（可用来屏蔽内置默认 UA）。
        node_headers, missing_env, dyn_notes = resolve_header_placeholders(
            getattr(node, 'headers', None) or {})
        _raw_h = getattr(node, 'headers', None) or {}
        if not missing_env and any('${' in str(_v) for _v in _raw_h.values()):
            # 配置里用了占位符时，把**解析后真正发出去的头**也打一行（值已打码）。
            # 否则日志里只有 'cookie(***)'，用户没法判断到底取到真值没有。
            self.api_log_text.append(
                f"请求头(实际发送): {describe_headers_sent(node_headers)}")
        if missing_env:
            self.api_log_text.append(
                f"⚠️ 请求头中的环境变量未取到: {', '.join(missing_env)}（对应请求头按空值处理）")
        for _n in dyn_notes:
            self.api_log_text.append(f"🕒 实时时间头: {_n}")
        dl_result, is_tmp, used_curl, err, content_type = self._threaded_request(
            method, full_url, timeout=self._run_transfer().api_timeout_tuple,
            progress_cb=progress_cb, headers=node_headers)
        return self._process_api_result(
            node, full_url, method, round_index,
            dl_result, is_tmp, used_curl, err, content_type,
            progress_cb=progress_cb)

    def _process_api_result(self, node, full_url, method, round_index,
                            dl_result, is_tmp, used_curl, err, content_type,
                            progress_cb=None, bl=None, dl_progress=None,
                            item_index=None):
        """处理一次下载结果：解析响应、路由容器、填充输出栏。返回 data。

        批量并行模式可预创建输出块后传入 bl / dl_progress（实时进度）。
        item_index：数组轮询时的当前项索引（用于规则模式解析当前轮询数据）。
        """
        content_type = content_type or ""
        raw_content = None
        tmp_path = None
        if bl is None or dl_progress is None:
            _block, bl, dl_progress = self._create_output_block(node, method, round_index)

        def _on_item_progress(fid, received, total):
            if dl_progress is not None:
                if total > 0:
                    dl_progress.setRange(0, 100)
                    dl_progress.setValue(min(100, int(received * 100 / total)))
                    dl_progress.setFormat(
                        f"⬇️ 下载中  {received/1024:.0f}/{total/1024:.0f} KB")
                else:
                    dl_progress.setRange(0, 0)
                    dl_progress.setFormat(f"⬇️ 下载中  {received/1024:.0f} KB")
            if progress_cb is not None:
                try:
                    progress_cb(fid, received, total)
                except Exception:
                    pass

        try:
            if err or dl_result is None:
                raise RuntimeError(err or "请求失败")

            if is_tmp:
                # ---- 大文件：分块/流式已写入 .dlpack 临时文件 ----
                tmp_path = dl_result
                # 大文件也可能是 JSON（超大列表响应等）：content-type 含 json 时先尝试解析，
                # 避免数组数据（拖入的 source_path 绑定）因被当二进制而在首个轮询丢失
                recovered = None
                if content_type and 'json' in content_type.lower():
                    try:
                        with open(tmp_path, 'rb') as _f:
                            _raw = _f.read()
                        import gzip as _gz
                        if _raw[:2] == b'\x1f\x8b':
                            _raw = _gz.decompress(_raw)
                        recovered = json.loads(_raw.decode('utf-8-sig'))
                    except Exception:
                        recovered = None
                if recovered is not None and not isinstance(recovered, (str, bytes)):
                    data = recovered
                    raw_content = json.dumps(recovered, ensure_ascii=False)
                    node._binary_content = False
                    # 已读入内存，清理临时文件，走内存存盘路径
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    tmp_path = None
                    self.api_log_text.append(
                        f"响应体(JSON 大文件解析):\n"
                        f"{json.dumps(recovered, ensure_ascii=False, indent=2)[:5000]}")
                else:
                    node._binary_content = True
                    try:
                        node._binary_size = os.path.getsize(tmp_path)
                    except Exception:
                        node._binary_size = 0
                    node._binary_type = content_type
                    node._binary_filename = full_url.split('/')[-1].split('?')[0]
                    data = {"_binary": True, "filename": node._binary_filename,
                            "size": node._binary_size, "type": content_type}
                    size_str = f"{node._binary_size / 1024:.1f} KB" if node._binary_size else "未知大小"
                    self.api_log_text.append(
                        f"📦 媒体文件(分块/流式): {node._binary_filename}  "
                        f"({size_str}, {content_type})")
            else:
                resp = dl_result
                tag_curl = " [🛡️ curl_cffi]" if used_curl else ""
                resp_header = f"\n◀ 响应{tag_curl}: {resp.status_code} {resp.reason}"
                self.api_log_text.append(resp_header)
                resp_headers = dict(resp.headers)
                content_type = _header_get(resp_headers, 'Content-Type', '')
                self.api_log_text.append(
                    f"响应头:\n{json.dumps(resp_headers, ensure_ascii=False, indent=2)}")
                try:
                    resp_body = resp.json()
                    data = resp_body
                    raw_content = json.dumps(resp_body, ensure_ascii=False)
                    self.api_log_text.append(
                        f"响应体:\n{json.dumps(resp_body, ensure_ascii=False, indent=2)}")
                    node._binary_content = False
                except Exception:
                    # resp.json() 失败但内容可能是 JSON（gzip 未解压 / 编码异常）：
                    # 手动 gzip 解压 + json.loads 再试一次，避免 JSON 被误判为二进制，
                    # 导致拖入的数组数据（source_path 绑定）在首个轮询中丢失
                    recovered = None
                    body_bytes = None
                    try:
                        body_bytes = getattr(resp, 'content', None) or getattr(resp, '_content', None)
                    except Exception:
                        body_bytes = None
                    if body_bytes:
                        try:
                            import gzip as _gz
                            if body_bytes[:2] == b'\x1f\x8b':
                                body_bytes = _gz.decompress(body_bytes)
                        except Exception:
                            pass
                        try:
                            recovered = json.loads(body_bytes.decode('utf-8-sig'))
                        except Exception:
                            recovered = None
                    if recovered is not None and not isinstance(recovered, (str, bytes)):
                        data = recovered
                        raw_content = json.dumps(recovered, ensure_ascii=False)
                        node._binary_content = False
                        self.api_log_text.append(
                            f"响应体(JSON 解压解码):\n"
                            f"{json.dumps(recovered, ensure_ascii=False, indent=2)[:5000]}")
                    else:
                        # 判断是否为二进制媒体（先用 Content-Type，再回退检测内容特征）
                        is_bin = self.is_binary_content(content_type)
                        if not is_bin and body_bytes:
                            try:
                                sample = body_bytes[:512]
                                sample.decode('utf-8')
                            except (UnicodeDecodeError, UnicodeError):
                                is_bin = True
                        if is_bin:
                            content_len = _header_get(resp_headers, 'Content-Length', '')
                            body_len = len(body_bytes or b'')
                            declared = int(content_len) if str(content_len).isdigit() else 0
                            if declared > 0 and body_len == 0:
                                # 防御：声明了大小却收到 0 字节（curl_cffi 流响应内容丢失）
                                # → 拒绝落盘，绝不产生 e3b0c442… 空文件
                                raise RuntimeError(
                                    f"下载内容为空（声明 {declared} 字节，收到 0 字节），拒绝存盘")
                            node._binary_content = True
                            node._binary_size = body_len or declared
                            node._binary_type = content_type
                            node._binary_filename = full_url.split('/')[-1].split('?')[0]
                            data = {"_binary": True, "filename": node._binary_filename,
                                    "size": node._binary_size, "type": content_type}
                            raw_content = body_bytes
                            size_str = f"{node._binary_size / 1024:.1f} KB" if node._binary_size else "未知大小"
                            self.api_log_text.append(
                                f"📦 媒体文件: {node._binary_filename}  "
                                f"({size_str}, {content_type})")
                        else:
                            data = {"raw": (body_bytes or b'').decode('utf-8', errors='replace')}
                            raw_content = body_bytes
                            node._binary_content = False
                            self.api_log_text.append(
                                f"响应体(文本):\n{(body_bytes or b'').decode('utf-8', errors='replace')[:5000]}")
            self.api_log_text.append("")
        except Exception as e:
            data = {"error": str(e)}
            msg = str(e)
            # 记录本轮本组的下载层错误：自增轮询据此判定「该停下来了」
            try:
                self._round_api_errors.append((node, msg))
            except Exception:
                pass
            if 'curl_cffi' in msg:
                self.api_log_text.append(f"✕ {msg}")
            else:
                self.api_log_text.append(f"✕ 请求失败: {msg}\n")
            # 提示安装 curl_cffi
            if 'timeout' in msg.lower() and not HAS_CURL_CFFI:
                self.api_log_text.append(
                    "💡 提示: 服务器可能启用了 DDoS-Guard 防护，"
                    "安装 curl_cffi 可绕过: pip install curl_cffi\n"
                )
        node.result_json = data
        # ---- 解析数组数据用于迭代轮询 ----
        self._resolve_array_values(node, data)

        # ---- 数据流入下游数据库框（输入端）：写入 + 刷新显示 ----
        # 放在 _resolve_array_values 之后：数组索引/子轮询已经就绪，
        # 且此时结果已完整解析，写入是幂等的（upsert 按主键去重）。
        self._fanout_to_csv(node, data)

        # ---- 路由到容器：提交后台存盘（下载与存盘并行流水线，不阻塞主线程）----
        container = None
        for conn in self.scene.connections:
            if conn.start_obj == node and isinstance(conn.end_obj, ContainerNode):
                container = conn.end_obj
                break

        if container is not None:
            # 下载失败/空响应：没有可存的内容，直接跳过存盘（不再制造「存储失败」并终止整条流程）。
            if tmp_path is None and raw_content is None:
                self.api_log_text.append("  ⏸ 无可存内容（下载失败或空响应），已跳过存盘")
                if dl_progress is not None:
                    try:
                        dl_progress.setRange(0, 100)
                        dl_progress.setValue(0)
                        dl_progress.setFormat("⏸ 无内容，未存盘")
                    except Exception:
                        pass
            else:
                self._submit_store(container, bl, dl_progress, node, data,
                                   tmp_path, raw_content, full_url, content_type,
                                   item_index=item_index)
        else:
            # 无容器：进度条设为完成，直接填充输出栏
            if dl_progress is not None and getattr(node, '_binary_content', False):
                dl_progress.setRange(0, 100)
                dl_progress.setValue(100)
                if node._binary_size > 0:
                    dl_progress.setFormat(
                        f"✅ 已下载  {node._binary_size/1024:.1f} KB")
                else:
                    dl_progress.setRange(0, 0)
                    dl_progress.setFormat("✅ 下载完成（大小未知）")
            self._finalize_output_block(bl, dl_progress, node, data)
        QApplication.processEvents()
        return data

    def _fanout_to_csv(self, node, data):
        """把接口结果送到下游数据库框（直接连线的，以及经数据处理框中转的）。
        写入失败只记日志，绝不影响主流程。"""
        try:
            targets = self.scene.csv_ingest_targets(node)
        except Exception:
            return
        if not targets:
            return
        src = os.path.basename(getattr(node, 'url_path', '') or '') or '接口'
        for csv_node, path in targets:
            try:
                ok, msg = csv_node.ingest_json(data, source_label=src)
            except Exception as e:
                ok, msg = False, f"异常：{e}"
            # 同一节点重复相同结果不刷屏（轮询时很常见）
            last = getattr(csv_node, '_last_ingest_log', '')
            if msg == last:
                continue
            csv_node._last_ingest_log = msg
            try:
                tag = "📊" if ok else "⚠"
                fname = os.path.basename(csv_node.file_path or '')
                self.api_log_text.append(
                    f"{tag} 数据库框[{fname}]（{path}）: {msg}")
            except Exception:
                pass

    def _build_rule_data(self, node, data, item_index=None):
        """构建规则解析用的当前轮询 JSON 数据。

        数组轮询时用当前项索引从数组值构建 {param_name: value}；
        否则回退到最近响应 JSON / 当前数据。
        """
        av = getattr(node, '_array_values', None)
        if av and any(len(a) > 1 for a in av.values()):
            # 优先：源数据列表的当前项（数组轮询时逐项下载，规则按该项解析）
            src_list = getattr(node, '_array_source_list', None)
            if isinstance(src_list, list) and src_list:
                idx = item_index if item_index is not None else 0
                idx = idx % len(src_list)
                item = src_list[idx]
                if isinstance(item, dict):
                    return item
            # 其次：最近响应是列表（原始数据源）时，取当前项作为规则解析数据，
            # 使规则里的 $.title / $.file.name 等路径能按该项解析。
            rj = getattr(node, 'result_json', None)
            if isinstance(rj, list) and rj:
                idx = item_index if item_index is not None else 0
                idx = idx % len(rj)
                item = rj[idx]
                if isinstance(item, dict):
                    return item
            # 回退：用当前数组值构建 {param_name: value}
            rd = {}
            for pname, arr in av.items():
                if not arr:
                    continue
                idx = item_index if item_index is not None else node._array_index.get(pname, 0)
                idx = idx % len(arr)
                rd[pname] = arr[idx]
            if rd:
                return rd
        rj = getattr(node, 'result_json', None)
        if isinstance(rj, dict) and '_binary' not in rj:
            return rj
        if isinstance(rj, list) and rj:
            return rj[0]
        if isinstance(data, dict) and '_binary' not in data:
            return data
        return None

    def _rule_part_source_path(self, container, part_id):
        """规则拼合段的源路径：优先 _rule_parts 记录，否则从连到该段的 DP 节点恢复。

        兼容旧保存的流程图（拖入时未把路径写进 _rule_parts 的情况）。
        """
        for pd in getattr(container, '_rule_parts', []):
            if pd.get('id') == part_id and pd.get('source_path'):
                return pd['source_path']
        for conn in self.scene.connections:
            if conn.end_obj == container and conn.end_param == part_id:
                if isinstance(conn.start_obj, DataProcessNode) \
                        and getattr(conn.start_obj, 'source_path', ''):
                    return conn.start_obj.source_path
        return ''

    def _rule_part_config(self, container, pd):
        """构建单个规则拼合段的命名配置：携带上游数据处理框的正则/替换规则。

        文件夹命名时对该段的源值应用上游 DP 的处理规则，使"数据处理结果输入
        文本栏"真正生效（此前处理成功但未参与命名即源于缺失此配置）。
        """
        part_cfg = {
            'text': pd.get('text', ''),
            'source_path': self._rule_part_source_path(container, pd.get('id', '')),
        }
        for conn in self.scene.connections:
            if conn.end_obj == container and conn.end_param == pd.get('id'):
                if isinstance(conn.start_obj, DataProcessNode):
                    rx = ''
                    rp = ''
                    if hasattr(conn.start_obj, 'regex_edit') and conn.start_obj.regex_edit:
                        rx = conn.start_obj.regex_edit.text()
                    if hasattr(conn.start_obj, 'replace_edit') and conn.start_obj.replace_edit:
                        rp = conn.start_obj.replace_edit.text()
                    if rx:
                        part_cfg['regex'] = rx
                        part_cfg['replacement'] = rp
                break
        return part_cfg

    def _sync_rule_part_dp_display(self, container, rule_config, rule_data):
        """把规则拼合段上游数据处理框的「源数据 / 处理后」刷新为本轮的实际值。

        背景：文件夹命名由 _compute_rule_folder_name 每轮现算，一直是准的；但 DP 的
        两个标签只在设计期拖拽连线那一刻被写过一次（_handle_rule_part_drop 里的
        set_source_data），此后没有任何运行期入口刷新它 —— 表现为"命名正确、显示却
        停在老内容"。这里用与命名**完全相同**的解析 / 正则算法把值回写到控件，
        不新增任何计算逻辑，也不改动命名路径。
        """
        try:
            for pd in (getattr(container, '_rule_parts', None) or []):
                pid = pd.get('id')
                if not pid:
                    continue
                src = self._rule_part_source_path(container, pid)
                if not src:
                    continue
                val = _resolve_rule_json_value(rule_data, src)
                if val is None or isinstance(val, (dict, list)):
                    continue
                raw = str(val)
                if raw in ('', 'None'):
                    continue
                dp = None
                for conn in self.scene.connections:
                    if conn.end_obj == container and conn.end_param == pid:
                        if isinstance(conn.start_obj, DataProcessNode):
                            dp = conn.start_obj
                        break
                if dp is None:
                    continue
                rx = ''
                rp = ''
                if getattr(dp, 'regex_edit', None) is not None:
                    rx = dp.regex_edit.text()
                if getattr(dp, 'replace_edit', None) is not None:
                    rp = dp.replace_edit.text()
                proc = raw
                if rx:
                    try:
                        proc = re.sub(rx, rp, raw)
                    except Exception:
                        proc = raw  # 正则错误时保留原值（与命名逻辑一致）
                dp.source_data = raw
                dp.processed_data = proc
                if getattr(dp, 'src_label', None) is not None:
                    dp.src_label.setText(f"源数据: {raw}")
                if getattr(dp, 'result_label', None) is not None:
                    dp.result_label.setText(f"处理后: {proc}")
                if getattr(dp, 'src_label', None) is not None:
                    dp.src_label.setToolTip(f"路径: {src}")
                try:
                    dp._resize_to_fit()
                except Exception:
                    pass
        except Exception as e:
            # 显示刷新失败绝不能中断下载/存盘，但要让它可见（不要静默吞掉）
            try:
                self.api_log_text.append(f"⚠ 刷新规则段数据处理框显示失败: {e}")
            except Exception:
                pass

    def _downstream_containers(self, node):
        """查找当前节点下游的容器（直接连线或经 DP / 站点解析节点中转）。"""
        containers = []
        for conn in self.scene.connections:
            if not isinstance(conn.end_obj, ContainerNode):
                continue
            if conn.start_obj == node:
                containers.append(conn.end_obj)
            elif isinstance(conn.start_obj, (DataProcessNode, SiteParserNode)):
                # 经 DP / 站点解析节点中转连到容器
                for c2 in self.scene.connections:
                    if c2.end_obj == conn.start_obj and c2.start_obj == node:
                        containers.append(conn.end_obj)
                        break
        return containers

    def _container_root_value(self, container):
        """解析容器框「根目录输入点」本轮该用的文件夹名。

        返回 (名字, 就绪, 原因)。没有接输入点时返回 ('', True, '')（保持原行为）。
        就绪判定复用轮询锁：上游本轮被跳过（或它自己的上游没就绪）→ 不就绪，
        此时绝不用上一轮的老名字往磁盘里写。
        """
        for conn in self.scene.connections:
            if conn.end_obj is container and conn.end_param == 'root':
                src = conn.start_obj
                try:
                    # _node_output_text 挂在 NodeScene 上（不是对话框），必须显式取 scene
                    val = (self.scene._node_output_text(src, conn.start_param) or '').strip()
                except Exception:
                    val = ''
                skip_set = getattr(self, '_round_skipped', None) or set()
                if src in skip_set:
                    return val, False, f"上游 {type(src).__name__} 本轮未执行（数据未更新）"
                try:
                    ready, why = self._node_round_gate(src, skip_set)
                except Exception:
                    ready, why = True, ''
                if not ready:
                    return val, False, why
                if not val:
                    return '', False, f"上游 {type(src).__name__} 本轮没有产出根目录名"
                return val, True, ''
        return '', True, ''

    def _manifest_root(self, container):
        """下载清单（download_manifest.json）所在根目录——必须与存盘写入侧同根。

        存盘时用的是 container.active_storage_path()（= <storage_path>/<dyn_root>），
        清单也写在那里；而此前的去重读取点用的是 container.storage_path（少了
        dyn_root），于是接了「根目录输入点」之后必然查空清单、每轮重复下载。
        这里按存盘同款方式重新解析一次根目录，保证写读同根。

        根目录本轮未就绪（上游未执行/未产出）时回退到配置的 storage_path：
        保持旧行为，宁可多下一次，也不会误判"已下载"而漏存。
        """
        try:
            name, ok, _why = self._container_root_value(container)
            if ok and name:
                container.dyn_root = name
        except Exception:
            pass
        try:
            return container.active_storage_path() or \
                getattr(container, 'storage_path', '') or ''
        except Exception:
            return getattr(container, 'storage_path', '') or ''

    def _precreate_rule_folders(self, node, max_inner):
        """规则模式：数组轮询开始时，按创建时机识别出的数组项一次性预创建全部子文件夹。

        每个数组项对应一个文件夹（命名规则与存盘一致：创建时机值 + 文本拼合段），
        下载时文件按当前项解析出的规则名落入对应文件夹（存盘时 makedirs 幂等兜底）。
        """
        containers = self._downstream_containers(node)
        if not containers:
            self.api_log_text.append(
                f"  [调试] 未找到当前节点 {getattr(node, 'url_path', '')[:24]} 下游的容器连线，跳过预创建")
        seen = set()
        for container in containers:
            if id(container) in seen:
                continue
            seen.add(id(container))
            if not getattr(container, '_rule_enabled', False):
                continue
            # 根目录输入点：配置路径可能只是"上级目录"，实际根目录由上游给的名字决定
            _croot_name, _croot_ok, _croot_why = self._container_root_value(container)
            if not _croot_ok:
                self.api_log_text.append(
                    f"  [调试] 容器根目录本轮未就绪，跳过预创建：{_croot_why}")
                continue
            container.dyn_root = _croot_name
            storage_path = container.ensure_active_storage_path()
            if not storage_path or not os.path.isdir(storage_path):
                self.api_log_text.append(
                    "⚠ 规则容器未设置存储路径，跳过预创建文件夹")
                continue
            rule_config = {
                'enabled': True,
                'creation_path': getattr(container, '_rule_creation_path', '') or '',
                'time_enabled': bool(getattr(container, '_rule_time_enabled', False)),
                'time_format': getattr(container, '_rule_time_format', 'yyyy-mm-dd'),
                'parts': [
                    self._rule_part_config(container, pd)
                    for pd in getattr(container, '_rule_parts', [])
                ],
            }
            # 规则段上游 DP 的显示同步（命名本身现算，这里只修"显示停在老内容"）
            self._sync_rule_part_dp_display(
                container, rule_config, self._build_rule_data(node, None, None))
            # ---- 调试：规则配置与源数据状态（定位只生成 1 个文件夹的问题）----
            src_list = getattr(node, '_array_source_list', None)
            self.api_log_text.append(
                f"  [调试] 规则容器存储: {os.path.basename(storage_path) if storage_path else '未设置'}  "
                f"创建时机='{rule_config['creation_path']}'")
            part_desc = " | ".join(
                f"[{p.get('text', '')} @ {p.get('source_path') or '无路径'}]"
                for p in rule_config['parts'])
            self.api_log_text.append(f"  [调试] 拼合段: {part_desc or '(空)'}")
            if src_list is None:
                self.api_log_text.append(
                    f"  [调试] 源数据列表: None（未设置 _array_source_list！）")
            else:
                first = src_list[0] if src_list else None
                self.api_log_text.append(
                    f"  [调试] 源数据列表: list[{len(src_list)}] 首项类型="
                    f"{type(first).__name__} 键={list(first.keys())[:6] if isinstance(first, dict) else '—'}")
            sample_idxs = sorted(set([0, 1, 2, max_inner - 1]))
            for sidx in sample_idxs:
                if sidx >= max_inner:
                    continue
                rd = self._build_rule_data(node, None, sidx)
                fn = _compute_rule_folder_name(rule_config, rd)
                rd_s = str(rd)[:80] if rd is not None else 'None'
                self.api_log_text.append(
                    f"  [调试] 第{sidx}项 rule_data={rd_s} → 文件夹='{fn}'")
            created = set()
            for idx in range(max_inner):
                rule_data = self._build_rule_data(node, None, idx)
                folder = _compute_rule_folder_name(rule_config, rule_data)
                sub = os.path.join(storage_path, folder)
                try:
                    os.makedirs(sub, exist_ok=True)
                    created.add(folder)
                except Exception as e:
                    self.api_log_text.append(f"⚠ 预创建规则文件夹失败 {folder}: {e}")
            if created:
                self.api_log_text.append(
                    f"  预创建 {len(created)} 个规则文件夹（数组共 {max_inner} 项）")

    def _current_source_key(self, node, full_url, item_index=None):
        """推导当前下载项的来源路径主键（内容寻址）。

        优先取【本节点实际下载的数组值】——即 node._array_values 中当前索引项：
          - 封面节点（绑定 $.[0].file.path）→ 各帖封面路径
          - 附件节点（绑定 $.[0].attachments.[0].path）→ 各附件路径
        使两个节点即使共用同一套上游数组、同一参数名、同一 URL 模板，
        主键也各自不同，不再因都用 _array_source_list 的 file.path（封面）
        而互撞：否则封面节点存盘后，附件节点的每一项都会被清单误判为
        “已下载”而整体跳过（94 个 URL 全部不被检索）。

        回退：_array_source_list 的 file.path → URL 路径。
        下载前跳过检查与下载后写清单均用同一逻辑，保证主键一致。
        """
        try:
            if item_index is not None:
                tag = (getattr(node, 'url_path', '') or '').strip('/')
                # 1) 本节点数组值（真正下载的内容路径）
                av = getattr(node, '_array_values', None)
                if av:
                    for pname, arr in av.items():
                        if not isinstance(arr, (list, tuple)) or not arr:
                            continue
                        idx = item_index % len(arr)
                        v = arr[idx]
                        if isinstance(v, str) and v.strip():
                            base = v.strip().lstrip('/')
                            return f"{tag}|{base}" if tag else base
                # 1b) 拼合栏数组绑定（手动主输入 + concat 拖动驱动内轮询的节点，
                #     如 img 缩略图链：URL 模板相同但每项拼合 hash 不同，
                #     用实际下载的 concat 数组值做主键，防止与 file 封面键互撞）
                cav = getattr(node, '_concat_array_values', None)
                if cav:
                    for cid, arr in cav.items():
                        if not isinstance(arr, (list, tuple)) or not arr:
                            continue
                        idx = item_index % len(arr)
                        v = arr[idx]
                        if isinstance(v, str) and v.strip():
                            base = v.strip().lstrip('/')
                            return f"{tag}|concat|{base}" if tag else f"concat|{base}"
                # 2) 源数据列表的 file.path（回退）
                src_list = getattr(node, '_array_source_list', None)
                if isinstance(src_list, list) and src_list:
                    idx = item_index % len(src_list)
                    item = src_list[idx]
                    if isinstance(item, dict):
                        fp = item.get('file')
                        if isinstance(fp, dict) and fp.get('path'):
                            base = str(fp['path']).lstrip('/')
                            return f"{tag}|{base}" if tag else base
        except Exception:
            pass
        if not full_url:
            return ''
        try:
            p = urlparse(full_url).path.lstrip('/')
            if p.startswith('data/'):
                p = p[len('data/'):]
            return p
        except Exception:
            return full_url or ''

    def _submit_store(self, container, bl, dl_progress, node, data,
                      tmp_path, raw_content, full_url, content_type,
                      item_index=None):
        """提交一个存盘任务到后台线程，立即返回（不阻塞下载调度）。

        下载完成 → 立即后台写盘；主线程继续处理其他下载完成事件。
        """
        if dl_progress is not None:
            dl_progress.setRange(0, 0)
            dl_progress.setFormat("💾 存盘中...")

        self._store_counter += 1
        tid = self._store_counter
        # ---- 根目录输入点：解析本轮该用的根目录（轮询锁：上游没就绪就不写）----
        _root_name, _root_ok, _root_why = self._container_root_value(container)
        if not _root_ok:
            self.api_log_text.append(
                "⏸ 跳过存盘（容器["
                f"{os.path.basename((getattr(container, 'storage_path', '') or '').rstrip('/\\\\'))}"
                f"]）：{_root_why}")
            if dl_progress is not None:
                dl_progress.setRange(0, 100)
                dl_progress.setValue(0)
                dl_progress.setFormat("⏸ 根目录未就绪，已跳过")
            return
        container.dyn_root = _root_name
        _storage = container.ensure_active_storage_path()
        if _root_name:
            self.api_log_text.append(
                f"📁 容器根目录（本轮由上游更新）: {_storage}")
        # ---- 规则模式：构建规则配置与当前轮询数据 ----
        rule_config = None
        rule_data = None
        if getattr(container, '_rule_enabled', False):
            rule_config = {
                'enabled': True,
                'creation_path': getattr(container, '_rule_creation_path', '') or '',
                'time_enabled': bool(getattr(container, '_rule_time_enabled', False)),
                'time_format': getattr(container, '_rule_time_format', 'yyyy-mm-dd'),
                'parts': [
                    self._rule_part_config(container, pd)
                    for pd in getattr(container, '_rule_parts', [])
                ],
            }
            rule_data = self._build_rule_data(node, data, item_index)
            # 规则段上游 DP 的显示同步（跟随内轮询逐项刷新）
            self._sync_rule_part_dp_display(container, rule_config, rule_data)
            # 调试：每次下载的落位文件夹名
            try:
                fname = _compute_rule_folder_name(rule_config, rule_data)
                self.api_log_text.append(
                    f"  [调试] 存盘第{item_index}项 → 文件夹='{fname}'  "
                    f"rule_data={str(rule_data)[:60] if rule_data is not None else 'None'}")
            except Exception:
                pass
        # ---- 下载条目：显示落位（根目录 / 子文件夹 / 文件名），支持点击直达 ----
        _dl_target = (getattr(dl_progress, '_target', None)
                      if dl_progress is not None else None)
        if _dl_target is not None and getattr(node, '_binary_content', False):
            try:
                _sub_fn = ''
                if rule_config and rule_data is not None:
                    _sub_fn = _compute_rule_folder_name(rule_config, rule_data)
                _dl_target.set_dir(
                    _storage, _sub_fn,
                    getattr(node, '_binary_filename', '') or
                    (os.path.basename(tmp_path) if tmp_path else ''))
            except Exception as e:
                self.api_log_text.append(f"  ⚠ 刷新下载目标位置失败: {e}")
        if tmp_path is not None:
            worker = StoreWorker(
                tid, _io_store_data_from_file,
                (tmp_path, full_url, content_type, _storage,
                 rule_config, rule_data),
                parent=self)
        else:
            worker = StoreWorker(
                tid, _io_store_data,
                (raw_content, full_url, content_type, _storage,
                 rule_config, rule_data),
                parent=self)
        # 来源路径主键（下载清单去重用）
        source_key = self._current_source_key(node, full_url, item_index)
        self._store_workers.add(worker)
        worker.done.connect(
            lambda tid, ok, msg, fp, sh, c=container, b=bl, p=dl_progress,
                   n=node, d=data, w=worker, sk=source_key:
            self._on_store_done(ok, msg, fp, c, b, p, n, d, w, sk, sh))
        worker.start()

    def _on_store_done(self, ok, msg, fp, container, bl, dl_progress,
                       node, data, worker, source_key='', sha256=''):
        """存盘完成（主线程）：更新容器 UI、写入下载清单、填充输出栏、清理 worker。"""
        if ok and fp:
            try:
                size = os.path.getsize(fp)
                container.stored_bytes += size
                container.total_files += 1
                container._latest_filename = os.path.basename(fp)
                container._refresh_display()
                # 写入下载清单（内容寻址主键 + SHA-256 + 大小校验）
                if source_key:
                    try:
                        local_rel = os.path.relpath(fp, container.active_storage_path() or container.storage_path)
                    except Exception:
                        local_rel = os.path.basename(fp)
                    manifest_add(source_key, container.active_storage_path() or container.storage_path, local_rel, size, sha256 or '')
            except Exception:
                pass
        self.api_log_text.append(f"📦 容器: {msg}")
        if not ok:
            # 「⏸」前缀＝主动跳过（如无内容可存储），属预期情况，不能当作容器故障终止整条流程。
            if str(msg).lstrip().startswith('⏸'):
                self.api_log_text.append(f"  ⏸ 已跳过本次存盘: {msg}")
            else:
                self.api_log_text.append(f"⚠ 容器存储失败: {msg}")
                self._flow_stopped = True
        # 输出栏进度条显示保存结果
        if dl_progress is not None and getattr(node, '_binary_content', False):
            if ok:
                dl_progress.setRange(0, 100)
                dl_progress.setValue(100)
                dl_progress.setFormat("✅ 已保存")
            else:
                dl_progress.setRange(0, 100)
                dl_progress.setValue(0)
                dl_progress.setFormat("⚠ 存储失败")
            # 成功 → 记住最终文件，进度条与信息行点击可直达该文件
            _dl_target = getattr(dl_progress, '_target', None)
            if _dl_target is not None:
                if ok and fp:
                    try:
                        _dl_target.set_dir(container.active_storage_path()
                                           or container.storage_path)
                    except Exception:
                        pass
                    _dl_target.set_file(fp)
        # 填充输出栏
        self._finalize_output_block(bl, dl_progress, node, data)
        QApplication.processEvents()
        # 清理 worker（线程结束后安全删除）
        self._store_workers.discard(worker)
        self._safe_delete_worker(worker)

    def _safe_delete_worker(self, w):
        """线程完全结束后再删除 QThread。

        绝不 deleteLater 运行中的 QThread（会导致 "QThread: Destroyed while
        thread is still running" 崩溃）。若线程仍在运行，延迟到结束后删除。
        """
        if w is None:
            return
        if w.isRunning():
            QTimer.singleShot(500, lambda: self._safe_delete_worker(w))
        else:
            try:
                w.deleteLater()
            except Exception:
                pass

    def _download_batch(self, tasks, on_item_progress=None, on_item_done=None):
        """并行下载一批文件（小文件多线程并行）。

        tasks: [{method, url, uid}]
        on_item_progress(uid, received, total): 可选，实时进度
        on_item_done(uid, result, is_tmp, used_curl, err, content_type): 可选，
            每完成一个立即在主线程调用。调用方应在此及时转储到磁盘并释放内存，
            避免所有文件下载完才处理导致的内存堆积。
        返回按输入顺序的 [(result, is_tmp, used_curl, err, content_type), ...]；
        若提供 on_item_done，result 不再累积大对象（内存及时释放）。
        左下角进度条显示全部任务的汇总总进度。
        """
        n = len(tasks)
        if n == 0:
            return []
        results = [None] * n
        finished = [False] * n
        workers = []
        state = [{'r': 0, 't': 0} for _ in range(n)]

        self._show_status()
        self._status_bar.setRange(0, 0)
        self._status_bar.setFormat("准备批量下载...")
        self._status_label.setText(f"⬇️ 批量下载 0/{n}")

        # 本批共用一个快照：批内每个 worker 拿到的是**同一个不可变对象**，
        # 因此批与批之间、图纸与图纸之间不会互相串参数。
        _cfg = self._run_transfer()
        for i, t in enumerate(tasks):
            uid = t.get('uid', i)
            w = DownloadWorker(t['method'], t['url'],
                               headers=dict(t.get('headers') or {}),
                               timeout=_cfg.timeout,
                               file_id=uid, parent=self, cfg=_cfg)

            def _on_progress(fid, received, total, i=i, uid=uid):
                state[i] = {'r': received, 't': total}
                tot = sum(s['t'] for s in state)
                rec = sum(s['r'] for s in state)
                done_cnt = sum(1 for f in finished if f)
                if tot > 0:
                    self._status_bar.setRange(0, 100)
                    self._status_bar.setValue(min(100, int(rec * 100 / tot)))
                    self._status_bar.setFormat(
                        f"总进度 {rec/1024:.0f}/{tot/1024:.0f} KB ({done_cnt}/{n})")
                else:
                    self._status_bar.setRange(0, 0)
                    self._status_bar.setFormat(
                        f"下载中 {rec/1024:.0f} KB ({done_cnt}/{n})")
                self._status_label.setText(f"⬇️ 批量下载 {done_cnt}/{n}")
                if on_item_progress is not None:
                    try:
                        on_item_progress(uid, received, total)
                    except Exception:
                        pass

            def _on_done(fid, res, is_tmp, curl, err, ct, i=i):
                if on_item_done is not None:
                    # 每完成一个立即交给调用方转储/释放，不在这里累积大对象
                    try:
                        on_item_done(fid, res, is_tmp, curl, err, ct)
                    except Exception:
                        pass
                    results[i] = (None, is_tmp, curl, err, ct)
                else:
                    results[i] = (res, is_tmp, curl, err, ct)
                finished[i] = True
                done_cnt = sum(1 for f in finished if f)
                self._status_label.setText(f"⬇️ 批量下载 {done_cnt}/{n}")

            w.progress.connect(_on_progress)
            w.done.connect(_on_done)
            workers.append(w)

        # 调度：分批启动，最多 _cfg.batch_workers 个并发（默认 4，与改动前一致）
        import time as _t
        running = []
        idx = 0
        while idx < n or running:
            while idx < n and len(running) < _cfg.batch_workers:
                workers[idx].start()
                running.append(workers[idx])
                idx += 1
            QApplication.processEvents()
            if self._inner_cancel:
                # 一整路全部取消：停止所有已启动的 worker
                for w in running:
                    w.stop()
                # 等待所有 worker 结束（带 10 秒上限），超时强制 terminate
                _deadline = _t.time() + 10
                while any(w.isRunning() for w in workers) and _t.time() < _deadline:
                    QApplication.processEvents()
                    _t.sleep(0.02)
                for w in workers:
                    if w.isRunning():
                        try:
                            w.terminate()
                            w.wait(3000)
                        except Exception:
                            pass
                for i in range(n):
                    if not finished[i]:
                        results[i] = (None, False, False, "已取消", '')
                        if on_item_done is not None:
                            try:
                                on_item_done(tasks[i].get('uid', i),
                                             None, False, False, "已取消", '')
                            except Exception:
                                pass
                break
            _t.sleep(0.02)
            running = [w for w in running if w.isRunning()]
            QApplication.processEvents()

        # 确保所有 done 信号已处理
        for _ in range(5):
            QApplication.processEvents()
        # 清理线程：先 terminate 兜底，再用安全删除（线程结束后才 deleteLater）
        for w in workers:
            if w.isRunning():
                try:
                    w.terminate()
                    w.wait(3000)
                except Exception:
                    pass
            self._safe_delete_worker(w)
        return results

    # ---------- 输出面板：下载前创建块 + 下载中实时进度 + 完成后填充 ----------
    def _create_output_block(self, node, method, round_index):
        """下载前创建输出条目（占位 + 详情进度条），返回 (item, content_layout, dl_progress)。

        输出条目支持历史保留、右键删除/多选、钉子固定（上限 200，固定不计入、不释放）。
        """
        method = method or (node.method.upper() if hasattr(node, 'method') else 'GET')
        path = node.url_path if hasattr(node, 'url_path') else '/'
        method_color = {
            'GET': '#61af55', 'POST': '#55aaff', 'PUT': '#e5a53b',
            'DELETE': '#e06c75', 'PATCH': '#c678dd'
        }.get(method, '#aaaaaa')
        label = f"[{method}] {path}"
        if round_index:
            idx = 0
            for existing in self._output_items:
                if label in existing.title_label.text():
                    idx += 1
            label = f"[{idx}] {label}"

        item = OutputItem(f"<b>{label}</b>", method_color)
        item.menu_requested.connect(self._on_output_item_menu)
        self._add_output_item(item)
        bl = item.content_layout

        # 子标题：显示当前数组索引（如果有）
        sub_info_parts = []
        if round_index:
            sub_info_parts.append(f"🔄 第{round_index}轮")
        for pname in node._array_values:
            if pname in node._array_index:
                idx = node._array_index[pname] + 1
                total = len(node._array_values[pname])
                sub_info_parts.append(f"📌 {pname}: [{idx}/{total}]")
        if sub_info_parts:
            sub_header = QLabel(" | ".join(sub_info_parts))
            sub_header.setStyleSheet(
                "color:#888; background:#252525; padding:1px 6px; "
                "font-size:10px; border-radius:2px;"
            )
            bl.addWidget(sub_header)

        # 详情进度条占位（下载中实时更新；JSON 响应完成后移除）
        # 可点击：存盘完成后点它 → 资源管理器直达该文件
        dl_progress = ClickableProgressBar()
        dl_progress.setRange(0, 0)
        dl_progress.setFormat("⏳ 等待下载...")
        dl_progress.setFixedHeight(20)
        dl_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #3a5a3a; border-radius: 3px; "
            "background: #1e1e1e; text-align: center; font-size:10px; color:#ccc; }"
            "QProgressBar::chunk { background-color: #4a9a4a; border-radius: 2px; }"
        )
        bl.addWidget(dl_progress)

        # 目标位置信息行（根目录 / 子文件夹 / 文件），存盘后点击也可直达
        dl_target_lbl = ClickableLabel("")
        dl_target_lbl.setStyleSheet("color:#aaa; font-size:11px; padding:2px;")
        bl.addWidget(dl_target_lbl)
        # 挂在进度条上，便于存盘路径拿到（不改函数签名，避免动所有调用点）
        dl_progress._target = _DownloadTarget(
            dl_progress, dl_target_lbl, logger=self.api_log_text.append)

        QApplication.processEvents()
        return item, bl, dl_progress

    def _finalize_output_block(self, bl, dl_progress, node, data):
        """下载完成后填充输出块内容（进度条状态由调用方设置，此处不覆盖）。"""
        if getattr(node, '_binary_content', False):
            # 二进制：附加文件信息行（进度条状态由调用方：下载完成/已保存/失败）
            info_row = QHBoxLayout()
            file_label = QLabel(f"📁 {node._binary_filename}")
            file_label.setStyleSheet("color:#aaa; font-size:11px; padding:2px;")
            info_row.addWidget(file_label)
            type_label = QLabel((node._binary_type or '').split(';')[0][:30])
            type_label.setStyleSheet("color:#888; font-size:10px; padding:2px;")
            type_label.setFixedWidth(80)
            info_row.addWidget(type_label)
            info_row.addStretch()
            bl.addLayout(info_row)
            bl.addWidget(_OutputResizeHandle(bl.parentWidget()))
        else:
            # 非二进制：移除占位进度条与目标位置行，显示键值树
            if dl_progress is not None:
                try:
                    bl.removeWidget(dl_progress)
                    _detach_and_delete(dl_progress)
                except Exception:
                    pass
            _tgt = getattr(dl_progress, '_target', None) if dl_progress is not None else None
            if _tgt is not None and _tgt.label is not None:
                try:
                    bl.removeWidget(_tgt.label)
                    _detach_and_delete(_tgt.label)
                except Exception:
                    pass
            tree = SourceJSONTreeWidget()
            tree.populate(data)
            # 记录输出树对应的 API 节点：从输出栏拖拽数据时，DP 上游跟随该节点
            tree._source_node = node
            tree.drag_started.connect(self._on_output_drag_started)
            bl.addWidget(tree, 1)  # 拉伸填充剩余空间，配合底部手柄调整高度
            bl.addWidget(_OutputResizeHandle(bl.parentWidget()))

    def _on_output_drag_started(self, node):
        """输出栏拖拽开始 → 记录数据来源 API 节点（供拖入容器/拼合栏时确定 DP 上游）"""
        self.scene._last_output_drag_node = node

    def _get_connected_pairs(self, levels):
        """从拓扑层级中提取相邻层级间的连接对，用于等待间隔"""
        pairs = []
        for i in range(len(levels) - 1):
            for up in levels[i]:
                for down in levels[i + 1]:
                    # 检查是否存在连线路径 up → ... → down
                    for conn in self.scene.connections:
                        start = conn.start_obj
                        if isinstance(start, APINode) and start == up:
                            # 看看是否直接或间接连到 down
                            if self._can_reach(up, down):
                                pairs.append((up, down))
                                break
        return pairs

    def _can_reach(self, start_node, target_node, visited=None):
        """判断从 start_node 出发能否到达 target_node（透过 DataProcessNode 间接）"""
        if visited is None:
            visited = set()
        if start_node == target_node:
            return True
        if start_node in visited:
            return False
        visited.add(start_node)
        for conn in self.scene.connections:
            if conn.start_obj == start_node:
                end = conn.end_obj
                if isinstance(end, APINode):
                    if self._can_reach(end, target_node, visited):
                        return True
                elif isinstance(end, DataProcessNode):
                    if self._can_reach(end, target_node, visited):
                        return True
        return False

    # ---------- 跨进程交接：下载完成 → 交给图库检索管理器（image-search）----------
    def _collect_download_roots(self):
        """收集全部流程图选项卡里容器框已设置的存储目录（存在、去重）。"""
        roots, seen = [], set()

        def _scan(sc):
            for n in (getattr(sc, 'nodes', None) or []):
                if isinstance(n, ContainerNode):
                    p = getattr(n, 'storage_path', '') or ''
                    if p and os.path.isdir(p) and p not in seen:
                        seen.add(p)
                        roots.append(p)

        _scan(self.scene)
        for tab in (getattr(self, '_tabs', None) or []):
            _scan(tab.get('scene') if isinstance(tab, dict) else None)
        return roots

    def _on_handoff_clicked(self):
        """按钮回调：选交接方式 → 写交接文件 → 分离启动过渡进程 → 安全退出主程序。

        顺序不可颠倒：先原子写 request JSON，再 Popen handoff_launcher.py
        （detached，不阻塞），最后退出本进程让出内存。此后由 image-search
        自动打开图库检索管理器增量建库并回写 result_<id>.json。

        第十九轮改动：原来的「确定 / 取消」单选框换成 HandoffModeDialog ——
        用户勾选交接方式（整图 full / 子图 tiles，对应 request 的 modes 字段），
        没勾 / 点「暂不处理」/ 直接关窗的批次落进「未处理项目列表」暂存。
        前置校验链路（运行中 / 无下载根 / 找不到 launcher）与退出语义不变。
        """
        if getattr(self, '_running', False):
            QMessageBox.information(
                self, '跨进程交接', '流程正在执行中，请先停止 / 等待完成后，再点击交接。')
            return
        roots = self._collect_download_roots()
        if not roots:
            QMessageBox.warning(
                self, '跨进程交接',
                '未找到已设置存储路径的容器框（下载根目录）。\n'
                '请先在容器框上设置存储路径并完成下载，再点击交接。')
            return
        if not os.path.isfile(HANDOFF_LAUNCHER):
            QMessageBox.warning(
                self, '跨进程交接',
                f'找不到过渡启动器:\n{HANDOFF_LAUNCHER}\n\n'
                '请确认 image-search（含 handoff_launcher.py）已就位。')
            return
        dirty_note = ''
        if getattr(self, '_dirty', False):
            dirty_note = '⚠ 当前流程有未保存的更改，退出前请先点「💾 保存流程」。'
        dlg = HandoffModeDialog(roots, parent=self, host=self, dirty_note=dirty_note)
        dlg.exec()

    def _quit_after_handoff(self):
        """写文件 + 启动过渡进程之后调用：请求主程序安全退出（让出 80%+ 内存）。

        端口画板有两种运行形态，这条路径两种都要能安全走完：

          * **独立运行**（打包出来的 `端口画板.exe`）：自己就是唯一窗口，关掉即可；
          * **嵌在主程序里**（从「全栈图库管理器」打开）：Qt 关父窗口会**级联**关掉子窗口，
            所以"关掉端口画板"这件事会被做两遍（一遍是我们自己调的，一遍是 MainWindow
            级联下来的），外加 MainWindow 自己的关闭清理 —— 这时很容易踩到
            **`RuntimeError: Internal C++ object already deleted`**。

        所以这里的写法是「**每一步各自 try 住 + 整体幂等**」：
        任何一步失败都不影响已经写好的交接请求文件、也不影响已经拉起的过渡进程 ——
        那两样才是真正要保住的东西。
        """
        if getattr(self, '_handoff_quitting', False):
            return                      # 幂等：重复调用直接返回
        self._handoff_quitting = True

        app = QApplication.instance()

        def _safe(fn):
            try:
                fn()
            except RuntimeError:
                pass                    # C++ 对象已被删除（父子级联关闭时很常见）
            except Exception:
                pass

        # ① 先关掉"别的顶层窗口"（嵌入式时这一步会关掉外围 MainWindow）
        others = []
        if app is not None:
            try:
                others = [w for w in app.topLevelWidgets()
                          if w is not self and w.isVisible()]
            except RuntimeError:
                others = []
        for w in others:
            _safe(w.close)
        # ② 再关自己（可能已经被级联关掉了 → RuntimeError，直接吞）
        _safe(self.close)
        # ③ 退出事件循环
        if app is not None:
            _safe(app.quit)
        # ④ 兜底：事件循环已退但进程仍因非守护线程残留时，限时强制结束
        #    （否则 handoff_launcher 的 psutil 轮询无法确认本进程已退出）
        def _force_exit():
            try:
                os._exit(0)
            except Exception:
                pass
        threading.Timer(3.0, _force_exit).start()

    def closeEvent(self, event):
        """关窗。

        嵌入主程序时，外围 MainWindow 的关闭会**级联**关掉本窗口，那时本窗口的
        C++ 对象可能已被销毁；这里每一步都留了退路，绝不让关窗本身报错。
        """
        try:
            if getattr(self, '_handoff_quitting', False):
                # 交接退出流程中：请求文件已写好、过渡进程已拉起，关窗不再做别的事
                super().closeEvent(event)
                return
        except RuntimeError:
            return
        try:
            if getattr(self, '_running', False):
                self._inner_cancel = True     # 关窗即取消，避免后台线程继续跑
        except Exception:
            pass
        try:
            super().closeEvent(event)
        except RuntimeError:
            pass

    def _advance_array_indices(self, executed_node):
        """
        深度优先推进数组迭代索引。
        从 executed_node 开始，仅推进下游链中最深（叶子）节点的索引。
        当叶子节点的索引绕回 0 时，向上游传递进位，推进上一级节点的索引。
        """
        # 构建下游链：executed_node → ... → leaf
        chain = []
        def _build_chain(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return
            visited.add(node)
            chain.append(node)
            for conn in self.scene.connections:
                if conn.start_obj == node:
                    end = conn.end_obj
                    if isinstance(end, APINode):
                        _build_chain(end, visited)
                    elif isinstance(end, DataProcessNode):
                        for c2 in self.scene.connections:
                            if c2.start_obj == end:
                                _build_chain(c2.end_obj, visited)
        _build_chain(executed_node)

        # 从链尾（最深的下游）开始推进，向上传递进位
        carry = True  # 第一级总是推进
        for nd in reversed(chain):
            if not carry:
                break
            # 仅 APINode 有数组迭代状态（链上可能含容器框/DP，需跳过）
            if not isinstance(nd, APINode) or not nd._array_values:
                continue
            carry = False
            for pname in list(nd._array_values.keys()):
                total = len(nd._array_values[pname])
                if total <= 1:
                    continue
                old_idx = nd._array_index.get(pname, 0)
                new_idx = (old_idx + 1) % total
                nd._array_index[pname] = new_idx
                self.api_log_text.append(
                    f"  📍 [{nd.method} {nd.url_path[:20]}] 参数 '{pname}' "
                    f"索引推进: {old_idx + 1}/{total} → {new_idx + 1}/{total}"
                )
                # 如果索引绕回 0，表示本级一轮结束，向上传递进位
                if new_idx == 0:
                    carry = True

    def _advance_csv_rows(self, nodes=None):
        """轮询时推进 CsvDataNode 的行。

        上游驱动型（本轮被上游写入过内容的表）：只在"新鲜行"之间循环，
        绝不回头读运行前就存在的老行——那些行只作为图纸占位符存在。
        表里还没有任何新鲜行（静态表 / 上游本轮没产出）时保持原有整表推进。

        nodes 给定时只推进这个范围内的数据库框（按执行分组隔离；
        不传则维持旧的「全部数据库框」语义）。

        :return: (是否绕回起点, 绕回的数据库框名) —— 游标变小即说明数据已递归一轮，
                 自增轮询用它作为「数据走完一整圈」的终止依据。
        """
        scope = list(nodes) if nodes is not None else list(self.scene.nodes)
        wrapped, wname = False, ''
        for node in scope:
            if isinstance(node, CsvDataNode):
                old_row = node.current_row
                fresh = sorted(node._fresh_rows) if node._fresh_rows else []
                if fresh:
                    nxt = next((r for r in fresh if r > old_row), fresh[0])
                    node.current_row = nxt
                    node._sync_combos_to_row(nxt)
                    guard = '（新鲜行内循环）'
                else:
                    node.advance_row()
                    guard = ''
                # 绕回起点判定：游标变小 ⇒ 一圈走完。
                # 仅当确实存在可循环的数据（新鲜行 ≥2 或整表 ≥2 行）时才认，
                # 单行表/空表不会误判成「已走完」。
                if node.current_row < old_row and (len(fresh) >= 2
                                                   or getattr(node, '_row_count', 0) >= 2):
                    wrapped = True
                    wname = os.path.basename(getattr(node, 'file_path', '') or '') or '数据库框'
                self.api_log_text.append(
                    f"[Bucket: {os.path.basename(node.file_path)}]: 第 {old_row + 1} 行 → 第 {node.current_row + 1} 行{guard}"
                )
        return wrapped, wname

    def _rewind_csv_rows(self, nodes=None):
        """把数据库框退回起点，但不清 `_fresh_rows`。

        与 `_reset_csv_rows` 的区别：本函数用于**非全局执行组**开始跑之前，
        只把游标拨回起点（有新鲜行则回到第一行新鲜数据），保留上游已经写入的
        「新鲜行」记录，否则 `_node_round_gate` 会因为 `_fresh_rows` 为空
        而拦住本组所有下游元素。

        :return: 拨回后的起点行号列表（与 nodes 一一对应，缺失为 None）
        """
        scope = list(nodes) if nodes is not None else list(self.scene.nodes)
        rows = []
        for node in scope:
            if isinstance(node, CsvDataNode):
                fresh = sorted(getattr(node, '_fresh_rows', None) or [])
                if fresh:
                    node.current_row = fresh[0]
                else:
                    node.reset_row()
                try:
                    node._sync_combos_to_row(node.current_row)
                except Exception:
                    pass
                node._ingest_backed_up = False
                node._last_ingest_log = ''
                rows.append(node.current_row)
                self.api_log_text.append(
                    f"[Bucket: {os.path.basename(node.file_path)}]: 游标复位到 "
                    f"第 {node.current_row + 1} 行（保留 {len(fresh)} 条新鲜行）"
                )
            else:
                rows.append(None)
        return rows

    def _reset_csv_rows(self):
        """重置所有 CsvDataNode 到起始行；同时重置"写入"相关的每轮状态"""
        for node in self.scene.nodes:
            if isinstance(node, CsvDataNode):
                node.reset_row()
                node._ingest_backed_up = False   # 新的一轮 → 允许再备份一次原文件
                node._last_ingest_log = ''       # 允许重新打印写入结果
                node._fresh_rows = set()         # 新的一次运行 → 之前的"新鲜行"全部作废

    def _refresh_all_csv_dp_display(self):
        """刷新所有关联CSV的DataProcessNode显示为实际数据值（含正则处理）"""
        for node in self.scene.nodes:
            if isinstance(node, DataProcessNode) and hasattr(node, '_csv_source') and node._csv_source:
                csv_node, header = node._csv_source
                if header in csv_node.headers:
                    raw_val = csv_node.get_current_value(header)
                    node.source_data = raw_val
                    if hasattr(node, 'src_label') and node.src_label:
                        node.src_label.setText(f"源数据: {raw_val}")
                    # 手动计算正则
                    regex_text = node.regex_edit.text() if hasattr(node, 'regex_edit') and node.regex_edit else ''
                    replace_text = node.replace_edit.text() if hasattr(node, 'replace_edit') and node.replace_edit else ''
                    if regex_text and raw_val:
                        try:
                            node.processed_data = re.sub(regex_text, replace_text, raw_val)
                        except Exception:
                            node.processed_data = raw_val
                    else:
                        node.processed_data = raw_val
                    if hasattr(node, 'result_label') and node.result_label:
                        node.result_label.setText(f"处理后: {node.processed_data}")

    def run_flow(self):
        """按照拓扑顺序执行流程，支持多轮轮询、元素间等待间隔、CSV逐行输出。

        类嵌套方案：按执行分组运行 —— 全局 → main 类 → 触发链上的非 main 类。
        每个分组拥有自己的大轮询次数；非 main 类仅在输入触发点被上游类输出信号
        触发后才执行（未触发则静默：只接收数据，不执行下载/存盘/站点解析等指令）。
        """
        # ---- 重入守卫 ----
        # 流程执行期间主线程靠 QApplication.processEvents() 保持 UI 响应，此时再点
        # 「▶ 执行」或按 Enter（对话框的默认按钮就是它）会被 Qt 重新投递进来，
        # 于是 run_flow 被**递归调用**：日志被清空、_flow_stopped/_inner_cancel 被重置、
        # 执行分组重新计算 —— 表现为「流程跑到一半又从头开始算一遍」。
        # 这里直接拦下并提示，不做任何状态改动。
        if getattr(self, '_running', False):
            _msg = "该流程还在运行，请点击左下角取消加载后再重新启动"
            try:
                self.api_log_text.append(f"⛔ {_msg}")
            except Exception:
                pass
            try:
                self._status_label.setText("⛔ 流程运行中，已忽略重复启动")
            except Exception:
                pass
            QMessageBox.information(self, "流程正在运行", _msg)
            return

        # 先刷新所有CSV关联DP的显示（兼容旧连线/旧文件）
        self._refresh_all_csv_dp_display()

        # 输出面板按顺序累积保留（不清空），由 200 条上限自动裁剪最旧的未固定条目
        self.api_log_text.clear()
        self._flow_stopped = False
        self._inner_cancel = False   # 每次执行前重置取消标志
        self._cancel_pressed = False
        self._show_status()          # 流程执行期间持续显示底部进度条（不闪烁）
        self._running = True         # 运行锁：执行期间禁止切换/关闭选项卡

        # ---- 数据传递参数：执行时按当前图纸的仪表盘值**快照一次** ----
        # 运行中再改仪表盘不影响本轮（下一轮/下次执行才生效）；下载链路的每个
        # worker 拿到的都是这一份不可变快照，不读全局，为多图纸并发留好隔离。
        _dash0 = self._active_dash()
        self._run_cfg = (_dash0.config() if _dash0 is not None
                         else TransferConfig.defaults())
        # 轮询次数 / 自增 / 元素间等待以快照为准（仪表盘与工具栏双向同步，取值一致）；
        # 万一仪表盘没建出来，退回直接读工具栏控件，保证行为不变。
        if _dash0 is not None:
            poll_count = self._run_cfg.poll_count
            wait_time = self._run_cfg.wait_time
            global_auto = bool(self._run_cfg.poll_auto)
        else:
            poll_count = self.poll_count_spin.value()
            wait_time = self.wait_time_spin.value()
            global_auto = bool(self._global_poll_auto())

        # 检查是否存在 CsvDataNode
        csv_nodes = [n for n in self.scene.nodes if isinstance(n, CsvDataNode)]
        has_csv = len(csv_nodes) > 0

        # 本轮本组的下载层错误（自增轮询的终止依据），每轮开头清空
        self._round_api_errors = []
        # 双层循环：本组「由步进器驱动的翻页接口」集合（空响应＝内层翻完），每轮重设
        self._paged_page_apis = set()

        # ---- 类嵌套方案：构建执行分组（全局 → main → 触发链上的非 main 类） ----
        groups = self._build_group_tree(poll_count, global_auto)
        if not groups:
            self.api_log_text.append(
                "⚠ 未检测到可执行的元素（API/步进器/站点解析），请在画布上搭建后重试。")
            self._reset_status()
            # 空图纸提前返回：快照同样要作废，否则会一直留着上一张图纸的参数
            self._run_cfg = None
            return

        self._mark_dirty()  # 执行流程会更新节点数据 → 标记为已更改

        # 拓扑顺序日志（全部 API 节点）
        all_levels = self._build_topological_levels()
        all_ordered = []
        seen = set()
        for level in all_levels:
            for n in level:
                if n not in seen:
                    all_ordered.append(n)
                    seen.add(n)

        self.api_log_text.append(
            f"▶ 开始执行 — 全局大轮询 "
            + (f"自增（1 → {AUTO_POLL_MAX}）" if global_auto else f"{poll_count} 次")
            + f"，元素间等待 {wait_time} 秒\n"
            f"拓扑顺序: {' → '.join([f'{n.method} {n.url_path}'[:30] for n in all_ordered])}\n"
        )
        if has_csv:
            self.api_log_text.append(f"📊 检测到 {len(csv_nodes)} 个数据库节点，每轮自动推进一行\n")
        QApplication.processEvents()

        # ---- 进入分组执行（内含子类的层层嵌套）----
        # try/except/finally 兜底：执行期间任何未捕获异常都必须走到 _reset_status()，
        # 否则 _running 会永远停在 True —— 有了上面的重入守卫，「卡住的运行锁」
        # 就等于「流程再也启动不了」。
        _ok = False
        try:
            _ok = self._run_group_loop(groups, wait_time, csv_nodes, has_csv)
        except Exception as _ex:
            self.api_log_text.append(f"\n❌ 流程执行异常，已终止: {_ex}")
        finally:
            if self._log_rich_mode:
                self._rich_log_text.verticalScrollBar().setValue(
                    self._rich_log_text.verticalScrollBar().maximum())
            else:
                self.api_log_text.verticalScrollBar().setValue(
                    self.api_log_text.verticalScrollBar().maximum())
            if _ok:
                self.api_log_text.append("\n✅ 全部轮询执行完成")
            if self._inner_cancel:
                self.api_log_text.append("\n⛔ 已被用户取消")
            self._reset_status()
            # 快照作废：流程结束后再发起的单次请求按仪表盘当前值走
            self._run_cfg = None

    def _run_group_loop(self, groups, wait_time, csv_nodes, has_csv, _depth=0):
        """执行【同一层】的分组；每组每轮跑完自己后，递归进入它的子层。

        嵌套语义：某一层跑完一轮就把控制权交给子层，自己不再动作，等子层按
        自己的规则（自增 / 固定轮数）把**整段循环**跑完，才回到本层下一轮。
        子层终止的信号（自增结束、404 等下载层错误）层层向上传递。

        返回 False 表示流程已被取消或因容器存储错误终止，调用方须立刻上抛。
        """
        for g_label, g_nodes, g_rounds, g_auto, g_children in groups:
            if self._inner_cancel or self._flow_stopped:
                break
            is_global = (g_label == '全局')
            # 分组拓扑层级（仅本组 API 节点）+ 组内步进器/站点解析
            levels = self._build_topological_levels(g_nodes)
            g_steppers = [n for n in g_nodes if isinstance(n, StepperNode)]
            g_site = [n for n in g_nodes if isinstance(n, SiteParserNode)]
            # 本组「数据源」数据库框：自增模式下判定「数据是否走完一整圈」的依据
            g_csv = self._group_csv_nodes(g_nodes)
            self.api_log_text.append(
                f"\n{'　' * _depth}▶ 执行分组: {g_label} — "
                + (f"自增轮询（1 → {AUTO_POLL_MAX}，遇错或数据走完即停）"
                   if g_auto else f"大轮询 {g_rounds} 次")
                + ("（脱离全局轮询控制，类处理）" if not is_global else "")
                + (f"　→ 内含 {len(g_children)} 个子层" if g_children else "")
            )
            if g_auto and g_csv:
                # 注意：不能用生成表达式的循环变量拼后半句 —— 生成表达式在 Py3 里
                # 有自己的作用域，`c` 出了括号就未定义（这里曾直接 NameError 打死流程）。
                _csv_names = "、".join(
                    os.path.basename(getattr(_cc, 'file_path', '') or '') or '数据库框'
                    for _cc in g_csv)
                _csv_rows = "、".join(str(getattr(_cc, '_row_count', 0)) for _cc in g_csv)
                self.api_log_text.append(
                    f"  🔎 自增终止依据: {_csv_names}（各 {_csv_rows} 行）")

            # ---- 类内分页：步进器 = 内层（页），数据库框数据行 = 外层（作者）----
            # 启用条件：本组「勾了自增 + 不是全局组 + 有步进器 + 有数据源」。
            # 为什么必须这样分：步进器与数据库框原本在**同一轮**里各推进一次，
            # 于是第 N 轮 = 「第 N 个作者 + 第 N 页」，数据行与页码同步前进 ——
            # 表现为「每个作者都只下一页，而且页码还跟轮次走」。
            # 分两层后：外层每换一个数据行，内层从第 1 页重新翻到翻不动为止。
            g_page_apis = [n for n in g_nodes if isinstance(n, APINode)
                           and any(c.start_obj in g_steppers and c.end_obj is n
                                   for c in self.scene.connections)]
            _paged = bool(g_auto and not is_global and g_steppers and g_csv)
            _row_cap = max([int(getattr(_c, '_row_count', 0) or 0)
                            for _c in g_csv] + [1]) + 1
            if _paged:
                self.api_log_text.append(
                    f"  🗂 双层循环：步进器=内层（页，单行上限 {AUTO_POLL_MAX} 页），"
                    f"数据库框=外层（数据行，上限 {_row_cap} 行）；"
                    f"内层遇下载层报错/空响应即换下一个数据行")
            QApplication.processEvents()

            # 非全局组：把本组数据库框游标拨回起点（保留上游写入的「新鲜行」），
            # 否则类组永远只消费第 1 行 —— 这就是「标准样板无法自轮询走完全程」的直接原因。
            if not is_global and g_csv:
                self._rewind_csv_rows(g_csv)

            # 自增模式下轮数在循环内决定，用一个足够大的上界驱动 while
            _max_rounds = AUTO_POLL_MAX if g_auto else g_rounds
            round_idx = 0
            _outer_idx = 0      # 双层循环：外层（数据库框数据行）已完成计数
            while round_idx < _max_rounds:
                if self._inner_cancel or self._flow_stopped:
                    break
                round_idx += 1
                # 双层循环：轮头带上「当前数据行」，一眼看出在翻哪个数据行
                _row_tag = ''
                if _paged and g_csv:
                    _rc = g_csv[0]
                    _row_tag = (f"　·{os.path.basename(getattr(_rc, 'file_path', '') or '') or '数据库框'}"
                                f" 第 {int(getattr(_rc, 'current_row', 0)) + 1}"
                                f"/{max(1, int(getattr(_rc, '_row_count', 0) or 0))} 行"
                                f"（第 {_outer_idx + 1} 个）")
                round_header = (f"\n{'#'*60}\n{'#'*60}\n"
                                f"## {'　' * _depth}[{g_label}] 第 {round_idx}"
                                + (f"/{AUTO_POLL_MAX} 轮轮询（自增）" if g_auto
                                   else f"/{g_rounds} 轮轮询")
                                + _row_tag
                                + f"\n{'#'*60}")
                self.api_log_text.append(round_header)
                self._round_api_errors = []      # 自增轮询：每轮重新统计下载层错误
                self._csv_wrap = (False, '')     # 自增轮询：本轮的「数据绕回起点」信号
                # 双层循环：本轮哪些「翻页接口」返回空响应才算内层结束
                # （空响应会计入 _round_api_errors 一并作为内层终止依据）
                self._paged_page_apis = set(g_page_apis) if _paged else set()
                QApplication.processEvents()

                # 每轮开始前：清空"本轮未就绪元素框"集合（容器框根目录门控要用同一把锁；
                # 步进器 / 站点解析在层级循环之前执行，先给它一个干净的空集合）
                self._round_skipped = set()

                # 每轮开始前：数据库框行进位
                # ⚠ 与下面的步进器推进放在同一时机——该节点若同时被
                #   「步进器」和「数据库框」两条支路喂参数（多支路合并），
                #   两条支路必须在轮内同一阶段各推进一次，否则一端已是新值、
                #   另一端还是上一轮的值，下游就会拿错配的参数去跑。
                if is_global:
                    if round_idx == 1:
                        self._reset_csv_rows()
                    elif has_csv:
                        # 先用上一轮累积的"新鲜行"决定本轮游标走哪一行……
                        self._csv_wrap = self._advance_csv_rows()
                    # ……然后把新鲜行清空，让"本轮"成为字面意义的一轮：
                    # 上游接口这一轮若抓不到数据，库框就不会重新写入，
                    # 下游的就绪门控便会拦住它，而不是拿上一轮的行继续跑。
                    # （清空发生在 ingest 之前，不会影响 upsert 后 current_row 的保持——
                    #   ingest_json 只在 current_row 不在新鲜行里时才把它拉回最小新鲜行）
                    if round_idx > 1:
                        for _cn in csv_nodes:
                            _cn._fresh_rows = set()
                elif g_csv and round_idx > 1 and not _paged:
                    # 类组同样要逐轮进位消费数据（此前完全没接，是「跑不完全程」的根因之一）
                    # ⚠ 双层循环（_paged）下**绝不能**逐轮进位：数据行由内层翻完后进位，
                    #   否则数据行与页码在同一轮里各走一步，变成「每个作者只下一页」。
                    self._csv_wrap = self._advance_csv_rows(g_csv)

                # 自增轮询：游标已绕回起点 ⇒ 数据走完一整圈，本轮不再执行
                if g_auto and self._csv_wrap[0]:
                    self.api_log_text.append(
                        f"\n🛑 [{g_label}] 自增轮询结束于第 {round_idx - 1} 轮: "
                        f"数据库框[{self._csv_wrap[1]}] 数据已走完一整圈（游标绕回第 1 行），"
                        f"本轮不重复消费")
                    QApplication.processEvents()
                    break

                # 每轮：组内步进器步进数+1（跟随本组轮询次数），计算输出并注入下游
                # 注意参数顺序：_compute_steppers(step_count, nodes)
                if g_steppers:
                    self._compute_steppers(round_idx, g_steppers)

                # 每轮：执行组内站点解析元素框（round_idx, group_nodes）
                if g_site:
                    self._execute_site_parsers(round_idx, g_site)

                # 按拓扑层级逐层执行
                round_skipped = set()      # 本轮"因数据未就绪而跳过"的元素框
                # 挂到 self 上：容器框存盘时要按同一把轮询锁判断根目录够不够新
                self._round_skipped = round_skipped
                for level_idx, level_nodes in enumerate(levels):
                    for node in level_nodes:
                        if not isinstance(node, APINode):
                            continue
                        if self._inner_cancel:
                            break

                        # ---- 上下轮询锁：数据没就绪就不跑，绝不消费上一轮/图纸里的老数据 ----
                        _ready, _why = self._node_round_gate(node, round_skipped)
                        if not _ready:
                            round_skipped.add(node)
                            self.api_log_text.append(
                                f"  ⏸ 跳过 {node.method} {getattr(node, 'url_path', '')[:30]}"
                                f"（第 {round_idx} 轮）: {_why}")
                            QApplication.processEvents()
                            continue

                        inner_polled = False
                        # ---- 内部小轮询：检测数组值（含拼合栏拖动绑定），批量并行下载 ----
                        _carrm = getattr(node, '_concat_array_values', None) or {}
                        _has_param_arr = bool(node._array_values) and any(
                            len(arr) > 1 for arr in node._array_values.values())
                        _has_concat_arr = any(
                            isinstance(a, (list, tuple)) and len(a) > 1
                            for a in _carrm.values())
                        if _has_param_arr or _has_concat_arr:
                            _lengths = [len(a) for a in node._array_values.values() if len(a) > 1]
                            _lengths += [len(a) for a in _carrm.values()
                                         if isinstance(a, (list, tuple)) and len(a) > 1]
                            max_inner = max(_lengths)
                            inner_header = (f"\n  🔄 内部小轮询: 共 {max_inner} 项待下载 "
                                            f"(并行 {self._run_transfer().batch_workers} 路)")
                            self.api_log_text.append(inner_header)
                            QApplication.processEvents()

                            # ---- 规则模式：下载前按创建时机识别出的数组项一次性预创建全部子文件夹 ----
                            self._precreate_rule_folders(node, max_inner)

                            # ---- 阶段A：收集所有待下载项（设索引/刷新DP/算URL/预建输出块）----
                            items = []
                            skipped_cnt = 0
                            # 下游容器（用于读取其下载根目录下的清单做去重）
                            down_containers = self._downstream_containers(node)
                            # 清单根＝实际下载根（<storage_path>/<dyn_root>），与存盘同根。
                            # 逐项循环外只解析一次，避免重复遍历连线、也避免反复改 dyn_root。
                            _mf_roots = {}
                            for _dc in down_containers:
                                try:
                                    _mf_roots[id(_dc)] = self._manifest_root(_dc)
                                except Exception:
                                    _mf_roots[id(_dc)] = ''
                            for inner_idx in range(max_inner):
                                if self._inner_cancel:
                                    break
                                # 设置每个参数的当前索引
                                for pname in node._array_values:
                                    arr = node._array_values[pname]
                                    if len(arr) > 1:
                                        node._array_index[pname] = inner_idx % len(arr)
                                # 设置每个拼合栏数组的当前索引（拖动绑定也纳入轮询）
                                for _cid, _cvals in (getattr(node, '_concat_array_values', None) or {}).items():
                                    if isinstance(_cvals, (list, tuple)) and len(_cvals) > 1:
                                        node._concat_array_index[_cid] = inner_idx % len(_cvals)
                                # ---- 下载去重：查清单，已下载且本地有效则跳过 ----
                                # 与存盘时同一主键推导（含端点 URL 前缀），
                                # 保证两个节点共享同一数组时不互撞、不误跳过
                                src_key = self._current_source_key(node, '', inner_idx)
                                # 数组一个可用值都推不出来时 src_key 为空（极少数：数组元素
                                # 不是非空字符串等）。这时不能就此放行——留到下面 URL 算出来
                                # 之后用同一函数再判一次（兜底取 URL 路径），否则这些项
                                # 每一轮都会重新下载。
                                _key_pending = not src_key
                                if src_key:
                                    _skip = False
                                    _skip_why = ''
                                    for _dc in down_containers:
                                        _st = _mf_roots.get(id(_dc), '')
                                        if _st:
                                            _ok_c, _skip_why = manifest_check(_st, src_key)
                                            if _ok_c:
                                                _skip = True
                                                break
                                    if _skip:
                                        self.api_log_text.append(
                                            f"  ⏭ 第{round_idx}.{inner_idx + 1}项 已下载过，跳过: "
                                            f"{src_key[-45:]}")
                                        skipped_cnt += 1
                                        QApplication.processEvents()
                                        continue
                                    if _skip_why:
                                        self.api_log_text.append(
                                            f"  ↻ 第{round_idx}.{inner_idx + 1}项 清单命中但作废"
                                            f"（{_skip_why}），重新下载: {src_key[-45:]}")
                                        QApplication.processEvents()
                                # ---- 刷新中间 DP 节点的 source_data（否则每轮都使用第一个值）----
                                for conn in self.scene.connections:
                                    if conn.end_obj == node and conn.end_param in node._array_values:
                                        if isinstance(conn.start_obj, DataProcessNode):
                                            dp = conn.start_obj
                                            pn = conn.end_param
                                            idx = node._array_index.get(pn, 0)
                                            arr = node._array_values.get(pn, [])
                                            if idx < len(arr):
                                                dp.source_data = str(arr[idx])
                                                dp.src_label.setText(f"源数据: {str(arr[idx])}")
                                                # 重新应用正则
                                                rx = dp.regex_edit.text() if hasattr(dp, 'regex_edit') and dp.regex_edit else ''
                                                rp = dp.replace_edit.text() if hasattr(dp, 'replace_edit') and dp.replace_edit else ''
                                                if rx and dp.source_data:
                                                    try:
                                                        dp.processed_data = re.sub(rx, rp, dp.source_data)
                                                    except:
                                                        dp.processed_data = dp.source_data
                                                else:
                                                    dp.processed_data = dp.source_data
                                                if hasattr(dp, 'result_label') and dp.result_label:
                                                    dp.result_label.setText(f"处理后: {dp.processed_data}")
                                # ---- 刷新拼合栏数组：挂载 DP 逐项换 source_data（正则后由
                                # _compute_api_url 内的 _get_csv_processed_values 写入拼合栏），
                                # 无 DP 直挂时直接写拼合栏控件，保证每项 URL 拼合 hash 不同 ----
                                for _cid, _cvals in (getattr(node, '_concat_array_values', None) or {}).items():
                                    if not (isinstance(_cvals, (list, tuple)) and len(_cvals) > 1):
                                        continue
                                    _cvi = node._concat_array_index.get(_cid, 0) % len(_cvals)
                                    _cv = str(_cvals[_cvi])
                                    _cdp = None
                                    for _cc in self.scene.connections:
                                        if (_cc.end_obj == node and _cc.end_param == _cid
                                                and isinstance(_cc.start_obj, DataProcessNode)):
                                            _cdp = _cc.start_obj
                                            break
                                    if _cdp is not None:
                                        _cdp.source_data = _cv
                                        if hasattr(_cdp, 'src_label') and _cdp.src_label:
                                            _cdp.src_label.setText(f"源数据: {_cv}")
                                        _crx = _cdp.regex_edit.text() if hasattr(_cdp, 'regex_edit') and _cdp.regex_edit else ''
                                        _crp = _cdp.replace_edit.text() if hasattr(_cdp, 'replace_edit') and _cdp.replace_edit else ''
                                        if _crx and _cv:
                                            try:
                                                _cdp.processed_data = re.sub(_crx, _crp, _cv)
                                            except Exception:
                                                _cdp.processed_data = _cv
                                        else:
                                            _cdp.processed_data = _cv
                                        if hasattr(_cdp, 'result_label') and _cdp.result_label:
                                            _cdp.result_label.setText(f"处理后: {_cdp.processed_data}")
                                    else:
                                        # 无 DP 直挂：直接把数组值写进拼合栏（无正则）
                                        for _cww in getattr(node, '_concat_widgets', []):
                                            if _cww.concat_id == _cid:
                                                try:
                                                    _cww.edit.setText(_cv)
                                                    _cww.concat_value = _cv
                                                except Exception:
                                                    try:
                                                        _cww.concat_value = _cv
                                                    except Exception:
                                                        pass
                                                break
                                # 计算 URL（会更新 node._array_index）并预创建输出块
                                round_tag = f"{round_idx}.{inner_idx + 1}"
                                full_url, method, _v = self._compute_api_url(node, round_tag, inner_idx)
                                # ---- 下载去重（补判）：数组推不出主键时用刚算出的 URL
                                # 路径兜底，与写清单时同一函数、同一入参 —— 否则这些项
                                # 每一轮都会被重新下载（整段跳过检查）
                                if _key_pending:
                                    src_key = self._current_source_key(node, full_url, inner_idx)
                                    if src_key:
                                        _skip2 = False
                                        _skip2_why = ''
                                        for _dc in down_containers:
                                            _st = _mf_roots.get(id(_dc), '')
                                            if _st:
                                                _ok2, _skip2_why = manifest_check(_st, src_key)
                                                if _ok2:
                                                    _skip2 = True
                                                    break
                                        if _skip2:
                                            self.api_log_text.append(
                                                f"  ⏭ 第{round_idx}.{inner_idx + 1}项 已下载过，跳过: "
                                                f"{src_key[-45:]}")
                                            skipped_cnt += 1
                                            QApplication.processEvents()
                                            continue
                                        if _skip2_why:
                                            self.api_log_text.append(
                                                f"  ↻ 第{round_idx}.{inner_idx + 1}项 清单命中但作废"
                                                f"（{_skip2_why}），重新下载: {src_key[-45:]}")
                                            QApplication.processEvents()
                                _block, bl, dl_progress = self._create_output_block(
                                    node, method, round_tag)
                                _hdrs, _missing, _dyn = resolve_header_placeholders(
                                    getattr(node, 'headers', None) or {})
                                _raw_h2 = getattr(node, 'headers', None) or {}
                                if not _missing and any('${' in str(_v)
                                                        for _v in _raw_h2.values()):
                                    self.api_log_text.append(
                                        f"请求头(实际发送): {describe_headers_sent(_hdrs)}")
                                if _missing and _missing != getattr(node, '_warned_missing_env', None):
                                    node._warned_missing_env = list(_missing)
                                    self.api_log_text.append(
                                        f"⚠️ 请求头中的环境变量未取到: {', '.join(_missing)}"
                                        f"（对应请求头按空值处理）")
                                for _n in _dyn:
                                    self.api_log_text.append(f"🕒 实时时间头: {_n}")
                                items.append({'url': full_url, 'method': method,
                                              'node': node, 'round': round_tag,
                                              'bl': bl, 'dl_progress': dl_progress,
                                              'headers': _hdrs,
                                              'uid': inner_idx})
                                QApplication.processEvents()

                            if skipped_cnt:
                                self.api_log_text.append(
                                    f"  ⏭ 本次跳过 {skipped_cnt}/{max_inner} 项（已在下载清单中）")

                            # ---- 阶段B：批量并行下载，每完成一个立即转储到磁盘 ----
                            # （避免等全部下载完才处理导致的内存堆积）
                            def _item_progress(uid, received, total):
                                for it in items:
                                    if it['uid'] == uid and it['dl_progress'] is not None:
                                        if total > 0:
                                            it['dl_progress'].setRange(0, 100)
                                            it['dl_progress'].setValue(
                                                min(100, int(received * 100 / total)))
                                            it['dl_progress'].setFormat(
                                                f"⬇️ 下载中  {received/1024:.0f}/{total/1024:.0f} KB")
                                        else:
                                            it['dl_progress'].setRange(0, 0)
                                            it['dl_progress'].setFormat(
                                                f"⬇️ 下载中  {received/1024:.0f} KB")
                                        break

                            def _item_done(uid, res, is_tmp, curl, err, ct):
                                # 每完成一个立即处理：解析、转储到容器、填充输出栏，随后释放内存
                                if self._flow_stopped:
                                    return
                                for it in items:
                                    if it['uid'] == uid:
                                        if err == '已取消' or (err and res is None):
                                            # 区分真正的用户取消与网络/服务器失败，输出真实原因
                                            reason = "用户取消" if err == '已取消' \
                                                else (err or "未知错误")
                                            if it['dl_progress'] is not None:
                                                it['dl_progress'].setRange(0, 100)
                                                it['dl_progress'].setValue(0)
                                                it['dl_progress'].setFormat(f"⛔ {reason[:30]}")
                                            self.api_log_text.append(
                                                f"  ⛔ 第{it['round']}项 下载失败: {reason}")
                                            # 记录本轮本组的下载层错误（自增轮询的终止依据）
                                            try:
                                                self._round_api_errors.append(
                                                    (it['node'], reason))
                                            except Exception:
                                                pass
                                            return
                                        self._process_api_result(
                                            it['node'], it['url'], it['method'], it['round'],
                                            res, is_tmp, curl, err, ct,
                                            bl=it['bl'], dl_progress=it['dl_progress'],
                                            item_index=uid)
                                        # 检测下载是否失败：记录错误供自增轮询判定，
                                        # 但不再让单个 404 直接打死整条流程（存盘环节已跳过空内容）。
                                        if isinstance(it['node'].result_json, dict) and it['node'].result_json.get('error'):
                                            _em = it['node'].result_json['error']
                                            self.api_log_text.append(
                                                f"  ⛔ 下载中断: {_em}")
                                            try:
                                                self._round_api_errors.append(
                                                    (it['node'], str(_em)))
                                            except Exception:
                                                pass
                                        # 双层循环：由步进器驱动的「翻页接口」返回空 ⇒
                                        # 本数据行的页已翻完，记为内层终止依据（换下一个数据行）。
                                        # 非分页组该集合为空，行为不变。
                                        self._note_page_empty(it['node'])
                                        return

                            self._download_batch(
                                [{'method': it['method'], 'url': it['url'],
                                  'uid': it['uid']} for it in items],
                                on_item_progress=_item_progress,
                                on_item_done=_item_done)

                            if self._inner_cancel:
                                self._flow_stopped = True
                                self.api_log_text.append("\n  ⛔ 已被用户取消，终止流程")
                            inner_polled = True
                        else:
                            self._execute_api_node(node, round_idx)

                        if self._flow_stopped:
                            if self._inner_cancel:
                                self.api_log_text.append("\n⛔ 流程已被用户取消")
                            else:
                                self.api_log_text.append("\n⛔ 流程因容器存储错误终止")
                            self._reset_status()
                            return False

                        # ---- 推进数组迭代索引（仅正常模式，内轮询已自行遍历完全） ----
                        if not inner_polled:
                            self._advance_array_indices(node)

                        # 查找当前节点在本层之后的下一个相连节点（跨层级）
                        if level_idx + 1 < len(levels):
                            has_downstream = False
                            for next_node in levels[level_idx + 1]:
                                if self._can_reach(node, next_node):
                                    has_downstream = True
                                    break
                            if has_downstream and wait_time > 0:
                                wait_tag = f"⏳ 等待 {wait_time} 秒后走向下一连线元素..."
                                self.api_log_text.append(wait_tag)
                                QApplication.processEvents()
                                if not self._wait_interruptible(wait_time):
                                    self.api_log_text.append("\n⛔ 等待期间被取消，终止流程")
                                    self._reset_status()
                                    return False

                # （数据库框的行进位移到"每轮开始"了——见本轮开头的 _advance_csv_rows，
                #   与步进器推进同一时机，保证多支路合并时两端同步换新值）

                # ---- 类嵌套：本层本轮动作已完成，把控制权交给子层 ----
                # 本层由此「不再动作」，一直等子层按自己的规则（自增或固定轮数）
                # 把整段循环跑完，才回到本层下一轮 —— 层层嵌套。
                # 子层会重写 _round_api_errors / _csv_wrap / _round_skipped（它们
                # 是本层的本轮账本），进入前保存、回来时还原，否则本层的自增判定被冲掉。
                if g_children:
                    _sv_errs = self._round_api_errors
                    _sv_wrap = self._csv_wrap
                    _sv_skip = self._round_skipped
                    self.api_log_text.append(
                        f"\n{'　' * (_depth + 1)}⏸ [{g_label}] 第 {round_idx} 轮动作完成，"
                        f"暂停本层，移交 {len(g_children)} 个子层执行...")
                    QApplication.processEvents()
                    _sub_ok = self._run_group_loop(g_children, wait_time, csv_nodes,
                                                   has_csv, _depth + 1)
                    self._round_api_errors = _sv_errs
                    self._csv_wrap = _sv_wrap
                    self._round_skipped = _sv_skip
                    if not _sub_ok:
                        return False
                    self.api_log_text.append(
                        f"{'　' * (_depth + 1)}▶ 子层全部结束，"
                        f"[{g_label}] 恢复本层判定")
                    QApplication.processEvents()

                # ---- 自增轮询：本轮结束后判定是否该停 ----
                if _paged:
                    # 双层循环：本组本轮只是内层的一「页」。内层翻完（下载层报错 /
                    # 空响应 / 单行页数达上限）只做两件事：数据库框进位换下一个数据行、
                    # 步进器归零让内层从第 1 页重新开始。**不结束本组**。
                    # 只有数据库框整圈走完（游标绕回）或外层达上限，才结束本组自增。
                    _in_err, _in_why = self._auto_download_error(self._round_api_errors)
                    if _in_err or round_idx >= AUTO_POLL_MAX:
                        _tag = (_in_why if _in_err
                                else f"已达单行上限 {AUTO_POLL_MAX} 页")
                        _cur = (f"{os.path.basename(getattr(g_csv[0], 'file_path', '') or '') or '数据库框'}"
                                f" 第 {int(getattr(g_csv[0], 'current_row', 0)) + 1} 行")
                        self._csv_wrap = self._advance_csv_rows(g_csv)
                        if self._csv_wrap[0]:
                            self.api_log_text.append(
                                f"\n🛑 [{g_label}] 自增轮询结束: 数据库框[{self._csv_wrap[1]}] "
                                f"数据已走完一整圈（游标绕回第 1 行），"
                                f"共翻了 {_outer_idx + 1} 个数据行")
                            QApplication.processEvents()
                            break
                        _outer_idx += 1
                        if _outer_idx >= _row_cap:
                            self.api_log_text.append(
                                f"\n🛑 [{g_label}] 自增轮询已达单圈上限 {_row_cap} 个数据行，"
                                f"停止递增（如需更多请取消「自增」并用拨盘手动指定）")
                            QApplication.processEvents()
                            break
                        self.api_log_text.append(
                            f"  🗂 [{g_label}] {_cur} 翻完（{_tag}）→ 步进器归零，"
                            f"换下一个数据行（第 {_outer_idx + 1} 个）")
                        QApplication.processEvents()
                        round_idx = 0                    # 内层：步进器从第 1 页重新开始
                        self._round_api_errors = []
                        self._csv_wrap = (False, '')
                        sep = (f"\n--- [{g_label}] 第 {_outer_idx} 个数据行完成，"
                               f"准备下一行 ---\n")
                        self.api_log_text.append(sep)
                        QApplication.processEvents()
                        if wait_time > 0:
                            if not self._wait_interruptible(wait_time):
                                self.api_log_text.append("\n⛔ 等待期间被取消，终止流程")
                                self._reset_status()
                                return False
                        continue
                    # 内层未翻完 → 落到下面的通用尾巴（分隔 + 等待），继续下一页
                elif g_auto:
                    _stop, _why = self._auto_download_error(self._round_api_errors)
                    if not _stop:
                        _stop, _why = self._auto_rows_exhausted(g_csv)
                    if _stop:
                        self.api_log_text.append(
                            f"\n🛑 [{g_label}] 自增轮询结束于第 {round_idx} 轮: {_why}")
                        QApplication.processEvents()
                        break
                    if round_idx >= AUTO_POLL_MAX:
                        self.api_log_text.append(
                            f"\n🛑 [{g_label}] 自增轮询已达上限 {AUTO_POLL_MAX} 轮，停止递增"
                            f"（如需更多请取消「自增」并用拨盘手动指定）")
                        QApplication.processEvents()
                        break

                # 轮次间隔（非最后一轮）
                if round_idx < _max_rounds:
                    sep = f"\n--- [{g_label}] 第 {round_idx} 轮完成，准备下一轮 ---\n"
                    self.api_log_text.append(sep)
                    QApplication.processEvents()
                    if wait_time > 0:
                        if not self._wait_interruptible(wait_time):
                            self.api_log_text.append("\n⛔ 等待期间被取消，终止流程")
                            self._reset_status()
                            return False

        return not (self._inner_cancel or self._flow_stopped)

    # ---------- 类嵌套方案：执行分组 ----------
    def _class_direct_children(self, cls):
        """直接挂在 cls 输出点上的下一级类（只取一层，按连线顺序去重）。"""
        out = []
        for conn in list(self.scene.connections):
            if conn.start_obj is not cls:
                continue
            end = conn.end_obj
            if isinstance(end, ClassNode) and end is not cls and end not in out:
                out.append(end)
        return out

    def _build_class_group(self, cls, all_exec, _seen=None):
        """递归构建一个类分组：(label, 节点, 轮数, 自增, [子类分组...])。"""
        _seen = set() if _seen is None else _seen
        _seen.add(id(cls))
        label = 'main' if getattr(cls, '_is_main', False) else f"类:{cls.class_name}"
        nodes = [n for n in all_exec if getattr(n, '_class', None) is cls]
        children = []
        for sub in self._class_direct_children(cls):
            if id(sub) in _seen:
                continue          # 环形触发线：只保留第一次出现的位置，避免无限递归
            children.append(self._build_class_group(sub, all_exec, _seen))
        return (label, nodes, cls.poll_count,
                bool(getattr(cls, 'poll_auto', False)), children)

    def _build_group_tree(self, global_rounds, global_auto=False):
        """构建【嵌套】执行分组树。

        返回 [(label, 可执行节点, 轮询次数, 是否自增, [子层分组...]), ...]：
        - 全局：不在任何类框内的 API/步进器/站点解析，轮询次数 = UI 全局轮询；无子层
        - main：类名固定、必定触发；其**直接子类**作为子层递归嵌套
        - 类:X：由上游类的输出信号触发的类；同样可以再往下嵌套

        嵌套语义：某一层跑完自己的一轮后，把控制权交给它的子层，自己**不再动作**，
        等子层按自己的规则（自增或固定轮数）把**整段循环**跑完，才回到本层下一轮。
        子层停下的信号（自增结束 / 404 等下载层错误）层层向上传递。

        第 4 项 auto=True 时该层走「自增轮询」：不再按固定次数跑，而是从 1 递增到
        AUTO_POLL_MAX，直到该层出现下载层错误或该层数据库框数据走完一整圈为止。
        """
        groups = []
        all_exec = [n for n in self.scene.nodes
                    if isinstance(n, (APINode, StepperNode, SiteParserNode))]

        # 1) 全局分组（不属于任何类框，保持旧语义：不在嵌套体系内）
        global_nodes = [n for n in all_exec if getattr(n, '_class', None) is None]
        if global_nodes:
            groups.append(('全局', global_nodes, global_rounds, bool(global_auto), []))

        # 2) main 类（必定触发）+ 其下的类嵌套子树
        main_cls = next((cc for cc in self.scene.classes if getattr(cc, '_is_main', False)),
                        None)
        if main_cls is not None:
            groups.append(self._build_class_group(main_cls, all_exec))

        def _prune(g):
            lab, nds, r, au, ch = g
            ch = [_prune(c) for c in ch]
            ch = [c for c in ch if c[1] or c[4]]
            if not nds and not ch:
                return (lab, nds, r, au, [])
            if not nds and not self._group_csv_nodes(nds):
                # 本层没有任何可执行元素、也没有自己要消费的数据源：
                # 它只是一条「下传通道」，跑一轮就够，避免空转 AUTO_POLL_MAX 次子层
                return (lab, nds, 1, False, ch)
            return (lab, nds, r, au, ch)

        groups = [_prune(g) for g in groups]
        return [g for g in groups if g[1] or g[4]]

    def _build_execution_groups(self, global_rounds, global_auto=False):
        """扁平化的执行分组（兼容旧调用/测试）。

        先全局，再 main，随后按「父 → 子」的深度优先顺序展开类嵌套子树，
        只保留含可执行元素的层。
        """
        out = []

        def _flat(tree):
            for lab, nds, r, au, ch in tree:
                if nds:
                    out.append((lab, nds, r, au))
                _flat(ch)

        _flat(self._build_group_tree(global_rounds, global_auto))
        return out

    # ---------- 自增轮询：本组数据范围与终止判定 ----------
    def _group_csv_nodes(self, g_nodes):
        """本组「消费」的数据库框：本组元素自身，或沿连线从数据库框向下游可达本组元素。

        用下游 BFS 而不是邻接判断，是因为数据库框与本组 API 之间往往隔着
        数据处理框（数据库框 → 数据处理框 → API），直接看一跳会漏。
        """
        ex = set(g_nodes)
        if not ex:
            return []
        out = []
        for cnode in self.scene.nodes:
            if not isinstance(cnode, CsvDataNode):
                continue
            if cnode in ex:
                out.append(cnode)
                continue
            seen = {cnode}
            queue = [cnode]
            hit = False
            while queue and not hit:
                cur = queue.pop(0)
                for conn in self.scene.connections:
                    if conn.start_obj is not cur:
                        continue
                    nxt = conn.end_obj
                    if nxt in ex:
                        hit = True
                        break
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            if hit:
                out.append(cnode)
        return out

    def _auto_rows_exhausted(self, cnodes):
        """自增轮询的「数据走完一整圈」判定结果（由本轮的行进位阶段写入）。

        `_csv_wrap` 由 `_advance_csv_rows` 的返回值填充：
        游标从末行绕回起点即视为一圈走完。
        """
        _hit, _why = getattr(self, '_csv_wrap', (False, ''))
        if _hit:
            return True, f"数据库框[{_why}] 数据已走完一整圈（游标绕回第 1 行）"
        return False, ''

    def _auto_download_error(self, errors):
        """本组本轮是否出现下载层错误（404 / 空数组解析 / 请求异常）。"""
        if not errors:
            return False, ''
        last = errors[-1]
        if isinstance(last, (tuple, list)) and len(last) >= 2:
            node, msg = last[0], last[1]
        else:
            node, msg = None, str(last)
        url = (getattr(node, 'url_path', '') or '')[:60]
        return True, f"下载层报错 {len(errors)} 次，最后一次: {msg}（{url}）"

    def _note_page_empty(self, node):
        """双层循环：若 node 是本组「由步进器驱动的翻页接口」且本次返回空，
        记为内层终止依据（本数据行的页已翻完）。

        返回 True 表示记了一笔。非分页组 `_paged_page_apis` 为空集合，
        这里恒不动作，既有行为完全不变。
        """
        try:
            if node in getattr(self, '_paged_page_apis', ()) \
                    and not getattr(node, 'result_json', None):
                self.api_log_text.append("  📄 本页无数据（空响应）")
                self._round_api_errors.append((node, '本页无数据（空响应）'))
                return True
        except Exception:
            pass
        return False

    def _build_class_trigger_chain(self, main_cls):
        """从 main 输出点出发，沿类间触发连线 BFS 收集可触发的非 main 类（有序）。

        main 的类大轮询全部遍历完成后，其输出信号沿这些触发连线逐级传递，
        驱动下一个类框启动；未被触达的非 main 类静默不执行。
        """
        chain = []
        visited = {main_cls}
        queue = [main_cls]
        while queue:
            cur = queue.pop(0)
            for conn in self.scene.connections:
                if conn.start_obj is cur:
                    end = conn.end_obj
                    if isinstance(end, ClassNode) and end not in visited:
                        visited.add(end)
                        queue.append(end)
                        chain.append(end)
        return chain

    # ================= 多流程图选项卡 =================
    _TAB_STATE_KEYS = [
        'scene', 'view', 'undo_mgr', 'log_tabs',
        'output_layout', 'output_scroll', 'output_container',
        '_output_items', '_output_select_mode', '_output_max_items',
        '_output_top_bar', '_output_bottom_bar',
        'btn_output_select_all', 'btn_output_delete_checked',
        'api_log_text', '_log_lines', '_log_rich_mode', '_rich_log_text',
        '_rich_window_start', '_rich_window_end', '_log_follow_tail',
        '_rich_block_cache', '_rich_refresh_timer', '_rich_doc_segs',
        '_plain_buf', '_plain_flush_timer',
        '_suppress_scroll_load', '_current_file', '_dirty',
    ]

    def _update_tab_title(self, idx=None):
        """按选项卡名称与更改标记刷新标题（更改：*前缀 + 斜体）。"""
        if idx is None:
            idx = self._active_tab
        if not (0 <= idx < len(self._tabs)):
            return
        tab = self._tabs[idx]
        dirty = self._dirty if idx == self._active_tab else tab.get('_dirty', False)
        name = tab.get('name', '') or ''
        self.tab_bar.setTabText(idx, ('*' + name) if dirty else name)
        self.tab_bar.set_tab_dirty(idx, dirty)

    def _mark_dirty(self):
        """标记当前选项卡已更改（标题 *前缀 + 斜体）。"""
        if not (0 <= self._active_tab < len(self._tabs)):
            return
        if not self._dirty:
            self._dirty = True
            self._tabs[self._active_tab]['_dirty'] = True
            self._update_tab_title(self._active_tab)

    def _clear_dirty(self):
        """清除当前选项卡更改标记（保存/加载后）。"""
        if self._dirty:
            self._dirty = False
            self._tabs[self._active_tab]['_dirty'] = False
            self._update_tab_title(self._active_tab)

    def _on_undo_saved(self):
        """撤销栈保存回调：记录历史 + 标记选项卡已更改。"""
        try:
            self.undo_mgr.save()
        except Exception:
            pass
        self._mark_dirty()

    # ---------------- 数据传递参数（仪表盘 / 快照 / 落盘） ----------------
    def _active_dash(self):
        """当前选项卡的数据传递仪表盘（尚未建立时返回 None）。"""
        try:
            if 0 <= self._active_tab < len(self._tabs):
                return self._tabs[self._active_tab].get('transfer_dash')
        except Exception:
            pass
        return None

    def _run_transfer(self):
        """本次运行使用的传递参数**快照**。

        优先用 run_flow 开头定下的 `_run_cfg`（执行时快照一次：运行中再动仪表盘
        不影响本轮）；流程没在跑时回落到当前图纸的仪表盘值；都没有则用默认值。
        下载链路只认这份快照，所以将来多张图纸并发时彼此不会串参数。
        """
        cfg = getattr(self, '_run_cfg', None)
        if isinstance(cfg, TransferConfig):
            return cfg
        d = self._active_dash()
        return d.config() if d is not None else TransferConfig.defaults()

    def _on_transfer_changed(self):
        """仪表盘被改动：打上「下载参数待落盘」标记 + 标脏。"""
        try:
            if 0 <= self._active_tab < len(self._tabs):
                self._tabs[self._active_tab]['transfer_pending'] = True
        except Exception:
            pass
        self._mark_dirty()

    def _transfer_pending(self):
        """本图纸的下载参数是否需要落盘（老图纸没有 transfer 键 → 首次为 True）。"""
        try:
            if 0 <= self._active_tab < len(self._tabs):
                return bool(self._tabs[self._active_tab].get('transfer_pending', False))
        except Exception:
            pass
        return False

    def _clear_transfer_pending(self):
        try:
            if 0 <= self._active_tab < len(self._tabs):
                self._tabs[self._active_tab]['transfer_pending'] = False
        except Exception:
            pass

    def _ask_update_transfer(self):
        """保存流程时的**第二次**询问：是否把数据传递参数一并写进图纸。

        返回 True＝写进 .wbt；False＝本次不写（图纸里仍缺参数，下次保存还会再问）。
        """
        if not self._transfer_pending():
            return True

        def _fmt(v):
            if isinstance(v, bool):
                return '开' if v else '关'
            try:
                return f'{float(v):g}'
            except Exception:
                return str(v)

        d = self._active_dash()
        _diff = d.config().diff() if d is not None else {}
        if _diff:
            _detail = '\n'.join(
                f"　· {TRANSFER_SPEC_BY_KEY[k]['label']}：{_fmt(a)} → {_fmt(b)}"
                for k, (a, b) in _diff.items())
            _tip = (f"本图纸有 {len(_diff)} 项数据传递参数与默认值不同：\n"
                    f"{_detail}\n\n")
        else:
            _tip = ("本图纸是在「数据传递参数」功能之前保存的，里面还没有参数信息。\n"
                    "现在可以把当前（默认）参数一并写进图纸，以后打开就带着它。\n\n")
        reply = QMessageBox.question(self, "下载参数", _tip + "是否更新下载参数？",
                                     QMessageBox.Yes | QMessageBox.No)
        return reply == QMessageBox.Yes

    def _apply_transfer_from_data(self, data):
        """把图纸里的 `transfer` 参数套到当前选项卡的仪表盘上。

        老图纸没有该键 → 用默认值在内存里**缓冲更新**一份，并标记为待落盘，
        这样保存时会再问一次是否写进图纸。
        """
        _tdata = data.get('transfer') if isinstance(data, dict) else None
        _has = isinstance(_tdata, dict)
        try:
            if 0 <= self._active_tab < len(self._tabs):
                self._tabs[self._active_tab]['transfer_pending'] = not _has
        except Exception:
            pass
        d = self._active_dash()
        if d is not None:
            try:
                d.set_config(TransferConfig.from_dict(_tdata) if _has
                             else TransferConfig.defaults(), notify=False)
            except Exception:
                pass
        # 快照失效：下次执行重新按仪表盘取一次
        self._run_cfg = None

    def _build_tab_content(self, tab):
        """构建一个选项卡的内容区：中间视图 + 右侧日志/输出面板。返回 QSplitter。"""
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        # ---- 数据传递仪表盘：夹在「图纸选项卡」与「可视化画板」之间 ----
        # 每个选项卡一份 ⇒ 切页即显示该图纸自己的参数，天然实时。
        # 收起时只有一行标题（约 20px），不影响其它组件尺寸。
        dash = TransferDashboard(self, mid)
        tab['transfer_dash'] = dash
        dash.changed.connect(self._on_transfer_changed)
        mv.addWidget(dash)
        mv.addWidget(tab['view'])
        # ---- 右侧：数据处理日志 + 输出 ----
        right = QTabWidget()
        process_tab = QWidget()
        process_layout = QVBoxLayout(process_tab)
        process_label = QLabel("API 收发日志")
        process_layout.addWidget(process_label)
        tab['api_log_text'] = LogTextEdit(owner=self)
        process_layout.addWidget(tab['api_log_text'])
        tab['_rich_log_text'] = QTextEdit()
        tab['_rich_log_text'].setReadOnly(True)
        tab['_rich_log_text'].setPlaceholderText("富文本模式：JSON 以树状着色显示，向下滚动加载更多...")
        tab['_rich_log_text'].setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;")
        tab['_rich_log_text'].hide()
        process_layout.addWidget(tab['_rich_log_text'])
        tab['_rich_log_text'].verticalScrollBar().valueChanged.connect(self._on_rich_scrolled)
        # 纯文本视图同样翻页：滑到上/下边缘加载相邻页（与富文本共用一个窗口）
        tab['api_log_text'].verticalScrollBar().valueChanged.connect(self._on_plain_scrolled)
        # 富文本追加防抖定时器（长流程每行都重渲整页会卡，合并到 200ms 一次）
        tab['_rich_refresh_timer'] = QTimer(self)
        tab['_rich_refresh_timer'].setSingleShot(True)
        tab['_rich_refresh_timer'].setInterval(200)
        tab['_rich_refresh_timer'].timeout.connect(self._flush_rich_refresh)
        # 纯文本追加去抖定时器：把逐行 insertText 合并成一次批量插入
        # （回调绑定 tab：切选项卡后旧定时器不该去刷别人的缓冲，见 _flush_plain_append）
        tab['_plain_buf'] = []
        tab['_plain_flush_timer'] = QTimer(self)
        tab['_plain_flush_timer'].setSingleShot(True)
        tab['_plain_flush_timer'].setInterval(PLAIN_FLUSH_MS)
        tab['_plain_flush_timer'].timeout.connect(lambda t=tab: self._flush_plain_append(t))
        tab['log_tabs'] = right
        tab['log_tabs'].setTabBar(LogTabBar())
        tab['log_tabs'].tabBarClicked.connect(self._on_log_tab_clicked)
        tab['_log_lines'] = []
        tab['_log_rich_mode'] = False
        tab['_rich_window_start'] = 0
        tab['_rich_window_end'] = 0
        tab['_log_follow_tail'] = True
        tab['_rich_block_cache'] = {}
        tab['_rich_doc_segs'] = []
        tab['_suppress_scroll_load'] = False
        right.addTab(process_tab, "数据处理")
        output_tab = QWidget()
        ov = QVBoxLayout(output_tab)
        tab['_output_top_bar'] = QWidget()
        _top_l = QHBoxLayout(tab['_output_top_bar'])
        _top_l.setContentsMargins(0, 0, 0, 0)
        tab['btn_output_select_all'] = QPushButton("☑ 全选")
        tab['btn_output_select_all'].setStyleSheet(
            "QPushButton { background-color:#1e5aa8; color:white; border-radius:4px; "
            "padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background-color:#184a8a; }")
        tab['btn_output_select_all'].clicked.connect(self._select_all_outputs)
        _top_l.addWidget(tab['btn_output_select_all'])
        _top_l.addStretch()
        tab['_output_top_bar'].setVisible(False)
        ov.addWidget(tab['_output_top_bar'])
        tab['output_scroll'] = QScrollArea()
        tab['output_scroll'].setWidgetResizable(True)
        tab['output_container'] = QWidget()
        tab['output_layout'] = QVBoxLayout(tab['output_container'])
        tab['output_layout'].setAlignment(Qt.AlignTop)
        tab['output_scroll'].setWidget(tab['output_container'])
        tab['output_scroll'].viewport().installEventFilter(self)
        ov.addWidget(tab['output_scroll'], 1)
        tab['_output_bottom_bar'] = QWidget()
        _bot_l = QHBoxLayout(tab['_output_bottom_bar'])
        _bot_l.setContentsMargins(0, 0, 0, 0)
        _bot_l.addStretch()
        tab['btn_output_delete_checked'] = QPushButton("✕ 删除已选")
        tab['btn_output_delete_checked'].setStyleSheet(
            "QPushButton { background-color:#c0392b; color:white; border-radius:4px; "
            "padding:4px 14px; font-weight:bold; }"
            "QPushButton:hover { background-color:#a93226; }")
        tab['btn_output_delete_checked'].clicked.connect(self._delete_checked_outputs)
        _bot_l.addWidget(tab['btn_output_delete_checked'])
        tab['_output_bottom_bar'].setVisible(False)
        ov.addWidget(tab['_output_bottom_bar'])
        right.addTab(output_tab, "输出")
        tab['_output_items'] = []
        tab['_output_select_mode'] = False
        tab['_output_max_items'] = 200
        # ---- 右侧栏 = [内边缘竖条][标签页本体]，竖条贴左边缘（靠画板那侧）----
        # 收起时整栏缩成竖条宽，把空间让给画板；三角停在原地不用找。
        right_wrap = QWidget()
        rwh = QHBoxLayout(right_wrap)
        rwh.setContentsMargins(0, 0, 0, 0)
        rwh.setSpacing(0)
        _right_bar, _right_bar_lay = _make_collapse_bar()
        tab['right_tri'] = CollapseTriangle(
            right_wrap, right, _right_bar, collapse_to_width=COLLAPSE_BAR_W,
            sym_expanded='▶', sym_collapsed='◀',
            tip='收起 / 放出整个右侧栏（给画板腾出宽度）')
        _right_bar_lay.insertWidget(1, tab['right_tri'])
        rwh.addWidget(_right_bar)
        rwh.addWidget(right, 1)
        tab['right_wrap'] = right_wrap

        content = QSplitter(Qt.Horizontal)
        content.addWidget(mid)
        content.addWidget(right_wrap)
        # 右侧栏默认收窄 + 拉伸因子归 0：窗口变宽时它保持原宽，多出来的全给画板。
        # （原来 setSizes 给了 320，但按比例摊下来实测到 364px，画板被挤到不到一半。）
        content.setSizes([900, 268])
        content.setStretchFactor(0, 1)
        content.setStretchFactor(1, 0)
        content.setChildrenCollapsible(False)
        tab['widget'] = content
        tab['mid_widget'] = mid
        tab['right_widget'] = right
        return content

    def _save_tab_state(self, tab):
        # 仅复制"已存在的实例属性"：注册首选项卡时 self.* 尚未建立，
        # 若用 getattr(...,None) 会把 _build_tab_content 刚构建的
        # api_log_text/output_layout/output_scroll 等全部覆盖成 None
        for k in self._TAB_STATE_KEYS:
            if not hasattr(self, k):
                continue
            cur = getattr(self, k)
            # 反向保护：self 侧还是 None（__init__ 里占位的 _rich_log_text /
            # _rich_refresh_timer），而 tab 里已经建好控件 —— 不能用 None 覆盖。
            if cur is None and tab.get(k) is not None:
                continue
            tab[k] = cur

    def _load_tab_state(self, tab):
        for k in self._TAB_STATE_KEYS:
            if k in tab:
                setattr(self, k, tab[k])
        if self.scene is not None and self.undo_mgr is not None:
            try:
                self.scene._undo_save_cb = self._on_undo_saved
            except Exception:
                pass
        self._update_log_tab_style()
        # 切回该选项卡时，把它自己还没插进视图的日志缓冲补上（见 _flush_plain_append）
        try:
            self._flush_plain_append()
        except Exception:
            pass

    def _register_initial_tab(self, name='新图纸', filepath=None):
        """注册首个选项卡（使用 __init__ 已建好的场景/视图）。"""
        tab = {
            'name': name,
            'file': filepath,
            'scene': self.scene,
            'view': self.view,
            'undo_mgr': self.undo_mgr,
            '_current_file': self._current_file,
            # 新图纸里还没有参数信息 → 首次保存时会问一次「是否更新下载参数？」
            'transfer_pending': True,
        }
        self._build_tab_content(tab)
        self._content_stack.addWidget(tab['widget'])
        self._tabs.append(tab)
        self._active_tab = 0
        self.tab_bar.blockSignals(True)
        self.tab_bar.addTab(name)
        self.tab_bar.setCurrentIndex(0)
        self.tab_bar.blockSignals(False)
        self._content_stack.setCurrentIndex(0)
        self._save_tab_state(tab)
        self._load_tab_state(tab)

    def _new_tab(self, name=None, filepath=None, load_file=False):
        """新建选项卡（独立场景/视图/日志/输出，常加载）。load_file=True 时从文件加载。"""
        if self._running:
            QMessageBox.information(self, "提示", "流程执行中，请先停止再新建/切换。")
            return None
        name = name or '新图纸'
        tab = {
            'name': name,
            'file': filepath,
            'scene': NodeScene(),
            'view': None,
            'undo_mgr': None,
            '_current_file': filepath,
            # 新图纸里还没有参数信息 → 首次保存时会问一次「是否更新下载参数？」
            # （若随即 _load_flow_from 加载了图纸，会按图纸里有没有 transfer 键重设）
            'transfer_pending': True,
        }
        tab['view'] = NodeView(tab['scene'])
        tab['view'].set_hover_enabled(getattr(self, '_hover_enabled', True))
        tab['undo_mgr'] = UndoManager(self, max_steps=20)
        tab['scene']._undo_save_cb = self._on_undo_saved
        self._build_tab_content(tab)
        self._content_stack.addWidget(tab['widget'])
        self._tabs.append(tab)
        self.tab_bar.blockSignals(True)
        self.tab_bar.addTab(name)
        self.tab_bar.blockSignals(False)
        idx = len(self._tabs) - 1
        self._activate_tab(idx)
        if load_file and filepath and os.path.isfile(filepath):
            self._load_flow_from(filepath)
        return tab

    def _activate_tab(self, idx):
        if self._running:
            return
        if idx < 0 or idx >= len(self._tabs):
            return
        if self._active_tab >= 0 and self._active_tab < len(self._tabs):
            # 切走之前先把当前选项卡的日志缓冲冲刷掉（此时 self.* 仍绑定在它上面）
            try:
                self._flush_plain_append()
            except Exception:
                pass
            self._save_tab_state(self._tabs[self._active_tab])
        self._active_tab = idx
        tab = self._tabs[idx]
        self._load_tab_state(tab)
        self._content_stack.setCurrentIndex(idx)
        self.tab_bar.blockSignals(True)
        self.tab_bar.setCurrentIndex(idx)
        self.tab_bar.blockSignals(False)

    def _on_tab_changed(self, idx):
        if idx < 0:
            return
        if self._running:
            if 0 <= self._active_tab < self.tab_bar.count():
                self.tab_bar.blockSignals(True)
                self.tab_bar.setCurrentIndex(self._active_tab)
                self.tab_bar.blockSignals(False)
            return
        self._activate_tab(idx)

    def _close_tab(self, idx):
        """关闭选项卡：提醒保存；若没有选项卡了则新建一张图纸。"""
        if self._running:
            QMessageBox.information(self, "提示", "流程执行中，请先停止再关闭。")
            return
        if idx < 0 or idx >= len(self._tabs):
            return
        tab = self._tabs[idx]
        # 无更改 → 不弹保存提示直接关闭；有更改 → 询问是否保存
        if tab.get('_dirty', False):
            reply = QMessageBox.question(
                self, "关闭选项卡",
                f"是否保存流程图 \"{tab['name']}\"？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Yes:
                self._activate_tab(idx)
                # 仅当画布有节点时才要求保存（空图纸直接关闭；取消保存则中止关闭）
                if self.scene.nodes and not self._save_flow():
                    return
        # 先脱离激活状态，避免 _activate_tab 把已关选项卡的状态写进别的选项卡
        was_active = (idx == self._active_tab)
        if was_active:
            self._active_tab = -1
        elif idx < self._active_tab:
            self._active_tab -= 1  # 关闭的选项卡在激活项之前 → 激活索引前移
        self._tabs.pop(idx)
        self.tab_bar.blockSignals(True)
        self.tab_bar.removeTab(idx)
        self.tab_bar.blockSignals(False)
        self.tab_bar._on_tab_removed(idx)  # 重排 dirty 标记索引
        w = tab.get('widget')
        if w is not None:
            self._content_stack.removeWidget(w)
            # 卸载事件过滤器后再销毁，防止 eventFilter 访问已删除的 QScrollArea
            osc = tab.get('output_scroll')
            if osc is not None:
                try:
                    osc.viewport().removeEventFilter(self)
                except RuntimeError:
                    pass
            # 立刻脱离 QStackedWidget，避免关闭后仍作为子控件被绘制（叠在别的选项卡上）
            _detach_and_delete(w)
        if not self._tabs:
            # 没有选项卡了 → 新建一张图纸（占位）
            self._new_tab()
            return
        new_idx = min(idx, len(self._tabs) - 1)
        if was_active:
            self._activate_tab(new_idx)
        else:
            self.tab_bar.blockSignals(True)
            self.tab_bar.setCurrentIndex(self._active_tab)
            self.tab_bar.blockSignals(False)

    def _open_flow_in_tab(self, filepath):
        """在选项卡中打开流程图（已打开则切换过去；否则新建选项卡加载）。"""
        for i, t in enumerate(self._tabs):
            if t.get('file') == filepath:
                self._activate_tab(i)
                return
        name = os.path.splitext(os.path.basename(filepath))[0]
        self._new_tab(name=name, filepath=filepath, load_file=True)

    def _on_new_drawing_clicked(self):
        """新建图纸：先问名字，确认后创建选项卡并立即保存占位到流程图栏。

        命名框**预填**了 `新图纸_月日_时分秒` —— 不想取名的直接回车就走，
        和以前"一键新建"一样快；想取名的改一下即可。
        点「取消」则**什么都不做**（免得留下一个用户没打算要的空图纸）。
        """
        if self._running:
            QMessageBox.information(self, "提示", "流程执行中，请先停止。")
            return
        default = f"新图纸_{time.strftime('%m%d_%H%M%S')}"
        name, ok = QInputDialog.getText(
            self, "新建图纸", "给这张图纸起个名字：", text=default)
        if not ok:
            return
        name = (name or '').strip() or default
        # 与「另存为」用同一套清洗规则，避免写出带路径分隔符的文件名
        name = name.replace('.', '_').replace('/', '_').replace('\\', '_')
        filepath = os.path.join(self._get_webtree_dir(), f"{name}.wbt")
        if os.path.exists(filepath):
            # 默认按钮给 No：同名覆盖是破坏性的，不该一不小心就按下去
            reply = QMessageBox.question(
                self, "重名确认",
                f"{name}.wbt 已存在。\n继续会以这张新图纸覆盖它，确定吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        self._new_tab(name=name, filepath=filepath)
        self._save_flow_to(filepath, show_success=False)
        self._refresh_flow_list()

    def _on_import_drawing_clicked(self):
        """导入图纸：把非默认文件夹的 .wbt 导入到默认流程图文件夹。"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入流程图", os.path.expanduser("~"),
            "流程图文件 (*.wbt);;所有文件 (*.*)")
        if not filepath:
            return
        name = os.path.splitext(os.path.basename(filepath))[0]
        dest = os.path.join(self._get_webtree_dir(), f"{name}.wbt")
        if os.path.exists(dest):
            reply = QMessageBox.question(self, "覆盖确认",
                                         f"{name}.wbt 已存在，是否覆盖？",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        try:
            import shutil
            shutil.copyfile(filepath, dest)
            self._refresh_flow_list()
            QMessageBox.information(self, "导入成功",
                                    f"已导入 {os.path.basename(filepath)}")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def output_layout_clear(self):
        # 先 takeAt 出布局（不再参与排版），再 _detach_and_delete 立刻脱离父控件：
        # 只 deleteLater() 的话 DeferredDelete 在流程执行期间不会执行，旧条目会留在
        # output_container 上继续绘制并与新条目重叠（图层出错）。
        while self.output_layout.count():
            item = self.output_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                _detach_and_delete(w)
        self._output_items.clear()
        self._output_select_mode = False
        if hasattr(self, '_output_top_bar'):
            self._output_top_bar.setVisible(False)
            self._output_bottom_bar.setVisible(False)

    # ================= 输出面板：历史保留 / 多选 / 钉子 / 上限 =================
    def _add_output_item(self, item):
        """注册输出条目：追加到布局与顺序列表，并执行上限裁剪。"""
        self.output_layout.addWidget(item)
        self._output_items.append(item)
        self._enforce_output_cap()
        return item

    def _create_site_parse_entry(self, filename, url):
        """站点解析：在输出选项卡创建 [GET] 下载条目（来源 + 实时进度条）。

        返回 (set_progress, set_done, set_title, set_target) 控制函数：
          set_progress(pct, fmt)：pct<0 或 None 时显示不确定进度
          set_done(ok, msg, file_path=None)：完成/失败后固化进度条；
              成功时传 file_path → 进度条与信息行可点击直达该文件
          set_title(text)：更新标题（识别到真实文件名时调用）
          set_target(root, sub, filename)：告知落位（根目录 / 规则子文件夹 / 文件名），
              存盘前即可显示，点击则定位到目标文件夹
        """
        item = OutputItem(f"<b>[GET] {filename}</b>", '#61af55')
        item.menu_requested.connect(self._on_output_item_menu)
        self._add_output_item(item)
        bl = item.content_layout

        # 来源链接行
        url_lbl = QLabel(f"🔗 {url}")
        url_lbl.setWordWrap(True)
        url_lbl.setStyleSheet(
            "color:#888; background:#1a2a1a; padding:1px 6px; "
            "font-size:10px; border-radius:2px;")
        bl.addWidget(url_lbl)

        # 实时进度条（可点击：下载完成后直达文件位置）
        bar = ClickableProgressBar()
        bar.setRange(0, 0)
        bar.setFormat("⏳ 等待 megadown...")
        bar.setFixedHeight(20)
        bar.setStyleSheet(
            "QProgressBar { border: 1px solid #3a5a3a; border-radius: 3px; "
            "background: #1e1e1e; text-align: center; font-size:10px; color:#ccc; }"
            "QProgressBar::chunk { background-color: #4a9a4a; border-radius: 2px; }"
        )
        bl.addWidget(bar)

        info = ClickableLabel("")
        info.setStyleSheet("color:#aaa; font-size:11px; padding:2px;")
        bl.addWidget(info)

        # 目标位置显示 + 点击直达（根目录 / 子文件夹 / 文件）
        target = _DownloadTarget(bar, info, logger=self.api_log_text.append)
        bar._target = target   # 与 _create_output_block 保持一致，便于统一取用/排查
        _state = {'done': False}

        def set_progress(pct, fmt=None):
            if pct is None or pct < 0:
                bar.setRange(0, 0)
            else:
                bar.setRange(0, 100)
                bar.setValue(min(100, int(pct)))
            if fmt:
                bar.setFormat(fmt)
            QApplication.processEvents()

        def set_target(root='', sub='', filename=''):
            target.set_dir(root, sub, filename)
            QApplication.processEvents()

        def set_done(ok, msg, file_path=None):
            bar.setRange(0, 100)
            bar.setValue(100 if ok else 0)
            bar.setFormat(("✅ 完成: %s" if ok else "✕ %s") % msg)
            # 成功且有最终路径 → 记入目标（信息行保持三行落位显示不被覆盖）
            if ok and file_path:
                target.set_file(file_path)
            elif not ok:
                info.setText(("✕ " if not str(msg).startswith('✕') else '') + str(msg))
            # 一次性守卫：set_done 可能被多次调用，避免重复叠加缩放手柄
            if not _state['done']:
                _state['done'] = True
                bl.addWidget(_OutputResizeHandle(bl.parentWidget()))
            QApplication.processEvents()

        def set_title(text):
            item.title_label.setText(f"<b>[GET] {text}</b>")
            QApplication.processEvents()

        return set_progress, set_done, set_title, set_target

    def _enforce_output_cap(self):
        """存储上限：仅统计未固定条目，超过上限时删除最开始的未固定条目。

        固定条目（📌）不参与上限计数，也不被自动释放。
        """
        unpinned = [it for it in self._output_items if not it._pinned]
        over = len(unpinned) - self._output_max_items
        idx = 0
        while over > 0 and idx < len(self._output_items):
            it = self._output_items[idx]
            if not it._pinned:
                self._remove_output_item(it)
                over -= 1
            else:
                idx += 1

    def _remove_output_item(self, item):
        """从面板移除一个输出条目（立即脱离父控件，避免残留控件继续绘制）。"""
        if item in self._output_items:
            self._output_items.remove(item)
        self.output_layout.removeWidget(item)
        _detach_and_delete(item)

    def _on_output_item_menu(self, item, global_pos):
        """输出条目右键菜单：删除 / 多选。"""
        menu = QMenu(self)
        act_del = menu.addAction("🗑️ 删除")
        act_multi = menu.addAction("☑ 多选")
        action = menu.exec(global_pos)
        if action == act_del:
            self._remove_output_item(item)
        elif action == act_multi:
            self._enter_output_select_mode()

    def _enter_output_select_mode(self):
        """进入多选模式：每个条目左侧出现复选框，顶部全选与底部删除已选显示。"""
        self._output_select_mode = True
        for it in self._output_items:
            it.set_checkbox_visible(True)
        self._output_top_bar.setVisible(True)
        self._output_bottom_bar.setVisible(True)

    def _exit_output_select_mode(self):
        """退出多选模式：隐藏复选框与工具条。"""
        self._output_select_mode = False
        for it in self._output_items:
            it.set_checkbox_visible(False)
        self._output_top_bar.setVisible(False)
        self._output_bottom_bar.setVisible(False)

    def _select_all_outputs(self):
        """全选：勾选所有输出条目。"""
        for it in self._output_items:
            it.checkbox.setChecked(True)

    def _delete_checked_outputs(self):
        """删除已勾选的输出条目，并退出多选模式。"""
        to_del = [it for it in list(self._output_items) if it._checked]
        for it in to_del:
            self._remove_output_item(it)
        self._exit_output_select_mode()

    def eventFilter(self, obj, event):
        # 输出区域按 Esc → 退出多选模式
        # 防御：_build_tab_content 构建期间 self.output_scroll 可能尚未赋值，
        # 或已关闭选项卡的 output_scroll 底层 C++ 对象已被销毁（deleteLater）
        scroll = getattr(self, 'output_scroll', None)
        if scroll is not None:
            try:
                vp = scroll.viewport()
            except RuntimeError:
                vp = None
            if vp is not None and obj is vp:
                if (event.type() == QEvent.KeyPress
                        and event.key() == Qt.Key_Escape
                        and self._output_select_mode):
                    self._exit_output_select_mode()
                    return True
        return super().eventFilter(obj, event)


def open_platform_integrator(parent=None):
    dlg = FlowEditorDialog(parent)
    dlg.setAttribute(Qt.WA_DeleteOnClose, False)
    dlg.show()
    return dlg


# ================= 跨进程交接：下载完成 → 图库检索管理器（image-search） =================
# 协议全文: image-search/docs/HANDOFF_PROTOCOL.md（schema v2：+modes 字段；
# 索引器侧对 schema 1 / 无 modes 的老请求仍然兼容，缺省 = 两个都做）
HANDOFF_DIR = os.path.join(
    _project_root(), 'handoff') if '__file__' in globals() else 'handoff'
HANDOFF_LAUNCHER = os.path.join(
    _project_root(),
    'image-search', 'handoff_launcher.py') if '__file__' in globals() else 'image-search/handoff_launcher.py'

# 交接方式枚举：顺序即执行顺序（先整图、后子图），写进 request 的 modes 字段
HANDOFF_MODES = ('full', 'tiles')
HANDOFF_MODE_LABELS = {
    'full': '整图增量入库（full）',
    'tiles': '子图增量入库（512px 切块，tiles）',
}
# 未处理批次的暂存原因（pending_*.json 的 reason 字段）
HANDOFF_PENDING_REASONS = ('none_selected', 'deferred', 'dialog_closed')
HANDOFF_PENDING_REASON_LABELS = {
    'none_selected': '未勾选任何方式',
    'deferred': '点了「暂不处理」',
    'dialog_closed': '直接关闭弹框',
}
# 未处理暂存的清理策略（打开弹框时自动执行一次，也可点「清理过期」手动跑）
HANDOFF_PENDING_TTL_DAYS = 30       # 超过这个天数的 pending_*.json 自动清理
HANDOFF_PENDING_MAX_RECORDS = 200   # pending 最多保留条数（超出按时间删最旧的）
HANDOFF_PENDING_PAGE_SIZE = 20      # 未处理列表每页条数
HANDOFF_TMP_TTL_DAYS = 1            # 崩溃残留的 *.json.tmp 超过这个天数清理


def _normalize_handoff_modes(modes):
    """把任意输入归一成 ['full', 'tiles'] 的子集：过滤非法值 + 去重 + 顺序固定。

    modes 传 None（缺省）＝ 两个都做（与老版本 schema 1 行为一致）。
    """
    if modes is None:
        return list(HANDOFF_MODES)
    if isinstance(modes, str):
        modes = [modes]
    want = {str(m).strip().lower() for m in modes}
    return [m for m in HANDOFF_MODES if m in want]


def _handoff_now_stamp():
    """当前秒级时间戳（与 request_id / pending 文件名同一格式）。"""
    return time.strftime('%Y%m%d_%H%M%S')


def _free_handoff_stamp(prefix, dirpath=None, want_ts=None, ext='.json'):
    """取一个不撞文件的 `<prefix>_<时间戳><ext>` 时间戳（秒级不够就往后顺延）。

    返回 (stamp, fp)。stamp 形如 20261002_153012，与 request_id 保持一致，
    避免同秒内连点两次交接 / 连存两条未处理记录时互相 os.replace 覆盖。
    """
    d = dirpath or HANDOFF_DIR
    base = want_ts or _handoff_now_stamp()
    try:
        t = time.mktime(time.strptime(base, '%Y%m%d_%H%M%S'))
    except Exception:
        base = _handoff_now_stamp()
        t = time.mktime(time.strptime(base, '%Y%m%d_%H%M%S'))
    for i in range(120):
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(t + i))
        fp = os.path.join(d, f'{prefix}_{stamp}{ext}')
        if not os.path.exists(fp):
            return stamp, fp
    return base, os.path.join(d, f'{prefix}_{base}{ext}')


def _stamp_to_iso(stamp):
    """20261002_153012 → 2026-10-02T15:30:12（文件名与内容里的时间保持同源）。"""
    try:
        return time.strftime('%Y-%m-%dT%H:%M:%S', time.strptime(str(stamp), '%Y%m%d_%H%M%S'))
    except Exception:
        return time.strftime('%Y-%m-%dT%H:%M:%S')


def _build_handoff_request(roots, extra_pids=(), note='', open_mode='gui',
                           modes=None, request_id=None):
    """构造交接 request JSON（schema v2：多了 modes）。

    roots: list[str] 下载内容根目录。
    modes: 交接方式，取值 'full'（整图增量入库）/ 'tiles'（子图/512px 瓦片增量入库），
           顺序固定 full 在前 tiles 在后；缺省 None = 两个都做（兼容老调用方）。
    request_id: 缺省按秒级时间戳生成 batch_%Y%m%d_%H%M%S。
    """
    _modes = _normalize_handoff_modes(modes)
    if not _modes:
        raise ValueError('modes 至少需要一个交接方式（full / tiles）')
    return {
        'schema': 2,
        'kind': 'download_batch_complete',
        'request_id': request_id or ('batch_' + time.strftime('%Y%m%d_%H%M%S')),
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'source': 'img_server',
        'roots': [{'path': p, 'note': note or 'batch'} for p in roots],
        'prefix': '',
        'open_mode': open_mode,
        'modes': _modes,
        # names 禁止写 python.exe 等普遍进程名；源码运行只填 pids（自身进程）
        'expect_exit': {'pids': [os.getpid()] + list(extra_pids), 'names': []},
        'note': note,
    }


def _write_handoff_request_json(req, dirpath=None):
    """把已构造好的 request 字典原子写成 request_<id>.json；返回 (fp, request_id)。"""
    d = dirpath or HANDOFF_DIR
    os.makedirs(d, exist_ok=True)
    rid = req['request_id']
    fp = os.path.join(d, f'request_{rid}.json')
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(req, f, ensure_ascii=False, indent=2)
    os.replace(tmp, fp)
    return fp, rid


def _write_handoff_request_file(roots, extra_pids=(), note='', open_mode='gui', dirpath=None,
                                modes=None, request_id=None):
    """原子写 request_<id>.json 到交接目录；返回 (fp, request_id)。

    先写 <fp>.tmp 再 os.replace 原子落盘，避免接收方读到半截文件。
    modes 缺省 = 两个都做，老调用方不传参数时行为不变（只是 schema 升到 2）。
    """
    req = _build_handoff_request(roots, extra_pids=extra_pids, note=note, open_mode=open_mode,
                                 modes=modes, request_id=request_id)
    return _write_handoff_request_json(req, dirpath)


def _write_pending_record(roots, modes, reason, dirpath=None, request=None, ts=None):
    """把一批「未处理」下载根原子落盘成 pending_<时间戳>.json；返回 (fp, record)。

    三种触发场景：一个都没勾却点「开始交接」(none_selected) / 点「暂不处理」(deferred)
    / 直接关窗(X、Esc、关闭)(dialog_closed)。一个批次一个文件。
    modes 存的是「这批将来实际要执行的交接方式」：用户勾了就用勾的，一个都没勾就按协议
    缺省两个都做（与索引器「无 modes = 两个都做」一致），保证「立即交接」有可执行口径。
    """
    if reason not in HANDOFF_PENDING_REASONS:
        raise ValueError(f'未知的暂存原因: {reason!r}')
    d = dirpath or HANDOFF_DIR
    os.makedirs(d, exist_ok=True)
    _modes = _normalize_handoff_modes(modes) or list(HANDOFF_MODES)
    stamp, fp = _free_handoff_stamp('pending', d, want_ts=ts)
    rid = f'batch_{stamp}'
    req = request if isinstance(request, dict) else _build_handoff_request(
        roots, modes=_modes, request_id=rid)
    if not isinstance(request, dict):
        # 内嵌请求体与批次同一个时间戳，回读时不用再去猜是哪一秒的批次
        req['ts'] = _stamp_to_iso(stamp)
    rec = {
        'schema': 2,
        'kind': 'download_batch_pending',
        'request_id': rid,
        'ts': _stamp_to_iso(stamp),
        'source': 'img_server',
        'reason': reason,
        'modes': _normalize_handoff_modes(req.get('modes')) or _modes,
        'roots': [{'path': p, 'note': 'batch'} for p in (roots or [])],
        'request': req,
    }
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, fp)
    return fp, rec


def _list_pending_records(dirpath=None):
    """读回全部 pending_*.json 记录，按时间倒序（最新在前）。坏文件跳过、不抛异常。"""
    d = dirpath or HANDOFF_DIR
    out = []
    try:
        names = os.listdir(d)
    except Exception:
        return out
    for name in names:
        if not (name.startswith('pending_') and name.endswith('.json')):
            continue
        fp = os.path.join(d, name)
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                continue
        except Exception:
            continue
        rec = dict(rec)
        rec['_file'] = fp
        rec['_stamp'] = name[len('pending_'):-len('.json')]
        out.append(rec)
    out.sort(key=lambda r: str(r.get('_stamp') or r.get('ts') or ''), reverse=True)
    return out


def _delete_pending_file(fp):
    """删除一条未处理记录（pending_*.json）。成功 True，文件不存在 / 失败 False。"""
    try:
        if fp and os.path.isfile(fp):
            os.remove(fp)
            return True
    except Exception:
        pass
    return False


def _write_handoff_request_from_record(rec, dirpath=None):
    """把一条未处理记录里的 request 体直接落成 request_<id>.json；返回 (fp, request_id)。

    request_id 优先沿用原批次 id；该 id 的 request 文件已存在（重复点击）就顺延一个新的
    秒级时间戳 id，避免 os.replace 覆盖掉别人的请求。expect_exit 回填「此刻」的本进程 pid。
    """
    d = dirpath or HANDOFF_DIR
    if not isinstance(rec, dict):
        raise ValueError('未处理记录不是字典')
    roots = [str(x.get('path')) for x in (rec.get('roots') or [])
             if isinstance(x, dict) and x.get('path')]
    modes = _normalize_handoff_modes(rec.get('modes'))
    req = rec.get('request') if isinstance(rec.get('request'), dict) else None
    if not roots and req:
        roots = [str(x.get('path')) for x in (req.get('roots') or [])
                 if isinstance(x, dict) and x.get('path')]
    if not roots:
        raise ValueError('未处理记录里没有可用的下载根目录')
    if not req:
        req = _build_handoff_request(roots, modes=modes or None)
    req = dict(req)
    want = str(rec.get('request_id') or '').strip()
    if not want.startswith('batch_'):
        want = ''
    if want and not os.path.exists(os.path.join(d, f'request_{want}.json')):
        rid = want
    else:
        # 落盘文件名是 request_<request_id>.json，即 request_batch_<时间戳>.json
        stamp, _fp = _free_handoff_stamp('request_batch', d)
        rid = f'batch_{stamp}'
    req['schema'] = 2
    req['kind'] = 'download_batch_complete'
    req['request_id'] = rid
    req['ts'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    req['source'] = req.get('source') or 'img_server'
    req['modes'] = modes or list(HANDOFF_MODES)
    req['roots'] = ([{'path': p, 'note': 'batch'} for p in roots]
                    if not req.get('roots') else req.get('roots'))
    req['expect_exit'] = {'pids': [os.getpid()], 'names': []}
    req.setdefault('prefix', '')
    req.setdefault('open_mode', 'gui')
    req.setdefault('note', '')
    return _write_handoff_request_json(req, d)


# ---------------- pending 清理策略（TTL + 条数上限） ----------------
def _pending_record_epoch(rec, use_mtime=True):
    """把一条 pending 记录的时间解析成 epoch 秒：文件名时间戳 → ts 字段 → 文件 mtime。

    解析不出来返回 None；这种记录**不会**被按天数清理（只参与条数上限）。
    """
    if not isinstance(rec, dict):
        return None
    for value, fmt in ((rec.get('_stamp'), '%Y%m%d_%H%M%S'),
                       (rec.get('ts'), '%Y-%m-%dT%H:%M:%S'),
                       (rec.get('ts'), '%Y-%m-%d %H:%M:%S')):
        s = str(value or '').strip()
        if not s:
            continue
        try:
            return time.mktime(time.strptime(s, fmt))
        except Exception:
            continue
    if use_mtime:
        fp = rec.get('_file')
        try:
            if fp and os.path.isfile(fp):
                return os.path.getmtime(fp)
        except Exception:
            pass
    return None


def _pending_record_age_days(rec, now=None):
    """记录已存在多少天；时间不可知返回 None。"""
    ep = _pending_record_epoch(rec)
    if ep is None:
        return None
    return ((time.time() if now is None else float(now)) - ep) / 86400.0


def _cleanup_handoff_tmp_files(dirpath, ttl_days=None, now=None):
    """清掉崩溃残留的 <名字>.json.tmp（超过 ttl_days 天）；返回删除个数。

    只认 `.json.tmp` 后缀，request_/result_/pending 的正式文件一律不碰。
    """
    ttl = HANDOFF_TMP_TTL_DAYS if ttl_days is None else float(ttl_days)
    now_v = time.time() if now is None else float(now)
    n = 0
    try:
        names = os.listdir(dirpath)
    except Exception:
        return 0
    for name in names:
        if not name.endswith('.json.tmp'):
            continue
        fp = os.path.join(dirpath, name)
        try:
            if (now_v - os.path.getmtime(fp)) / 86400.0 > ttl:
                os.remove(fp)
                n += 1
        except Exception:
            continue
    return n


def _cleanup_pending_records(dirpath=None, ttl_days=None, max_records=None, now=None):
    """按「TTL 天数 + 条数上限」清理 pending_*.json；返回统计 dict。

    策略（默认 HANDOFF_PENDING_TTL_DAYS / HANDOFF_PENDING_MAX_RECORDS，可显式覆盖，测试用）：
    - 超过 ttl_days 天的记录按天数删；
    - 剩下的按时间倒序保留最新 max_records 条，多出来的（最旧的）按条数删；
    - 只删 pending_*.json 与崩溃残留的 *.json.tmp，**绝不碰** request_*.json、
      result_*.json 与任何图片文件。
    """
    d = dirpath or HANDOFF_DIR
    ttl = HANDOFF_PENDING_TTL_DAYS if ttl_days is None else float(ttl_days)
    cap = HANDOFF_PENDING_MAX_RECORDS if max_records is None else int(max_records)
    recs = _list_pending_records(d)          # 已按时间倒序（最新在前）
    by_age, by_cap, keep = [], [], []
    for r in recs:
        age = _pending_record_age_days(r, now=now)
        if ttl >= 0 and age is not None and age > ttl:
            by_age.append(r)
        else:
            keep.append(r)
    if 0 <= cap < len(keep):
        by_cap = keep[cap:]
        keep = keep[:cap]
    removed, n_age, n_cap = [], 0, 0
    for r in by_age:
        if _delete_pending_file(r.get('_file')):
            removed.append(os.path.basename(str(r.get('_file') or '')))
            n_age += 1
    for r in by_cap:
        if _delete_pending_file(r.get('_file')):
            removed.append(os.path.basename(str(r.get('_file') or '')))
            n_cap += 1
    n_tmp = _cleanup_handoff_tmp_files(d, ttl_days=HANDOFF_TMP_TTL_DAYS, now=now)
    stat = {'removed': removed, 'by_age': n_age, 'by_count': n_cap,
            'kept': len(keep), 'tmp_removed': n_tmp,
            'ttl_days': ttl, 'max_records': cap, 'dir': d}
    if removed or n_tmp:
        print('[交接] 自动清理未处理暂存 %d 条（超期 %d / 超量 %d）+ 临时文件 %d 个: %s'
              % (len(removed), n_age, n_cap, n_tmp, ', '.join(removed) or '-'), flush=True)
    return stat


# ---------------- 交接结果回读（result_*.json → 弹框展示） ----------------
_RESULT_CACHE = {}


def _result_file_for(request_id, dirpath=None):
    """result_<request_id>.json 的完整路径；request_id 为空返回 ''。"""
    rid = str(request_id or '').strip()
    if not rid:
        return ''
    return os.path.join(dirpath or HANDOFF_DIR, 'result_%s.json' % rid)


def _load_handoff_result(request_id, dirpath=None, cache=True):
    """回读 result_<request_id>.json；不存在 / 坏 JSON / 非字典都返回 None（不抛异常）。

    按 (路径, mtime, 大小) 做小缓存：文件没变就不重复解析，结果文件被覆盖会自动失效。
    """
    fp = _result_file_for(request_id, dirpath)
    if not fp:
        return None
    try:
        st = os.stat(fp)
    except Exception:
        return None
    key = (fp, int(st.st_mtime), int(st.st_size))
    if cache and key in _RESULT_CACHE:
        return _RESULT_CACHE[key]
    res = None
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data['_file'] = fp
            data['_mtime'] = int(st.st_mtime)
            res = data
    except Exception:
        res = None
    if cache:
        if len(_RESULT_CACHE) > 128:
            _RESULT_CACHE.clear()
        _RESULT_CACHE[key] = res
    return res


def _list_handoff_results(dirpath=None):
    """回读交接目录里全部 result_*.json，按时间倒序（新 → 旧）；坏文件也列出来。"""
    d = dirpath or HANDOFF_DIR
    out = []
    try:
        names = os.listdir(d)
    except Exception:
        return out
    for name in names:
        if not (name.startswith('result_') and name.endswith('.json')):
            continue
        rid = name[len('result_'):-len('.json')]
        res = _load_handoff_result(rid, d)
        if isinstance(res, dict):
            out.append(res)
            continue
        fp = os.path.join(d, name)
        try:
            mt = int(os.path.getmtime(fp))
        except Exception:
            mt = 0
        out.append({'request_id': rid, '_file': fp, '_mtime': mt, '_broken': True})
    out.sort(key=lambda r: (str(r.get('finished_at') or ''), int(r.get('_mtime') or 0)),
             reverse=True)
    return out


def _short_text(s, n=60):
    """把可能很长/多行的错误信息压成一行短文本（给列表与弹框用）。"""
    t = str(s or '').replace('\r', ' ').replace('\n', ' ').strip()
    return t if len(t) <= n else t[:max(1, n - 1)] + '…'


def _summarize_handoff_result(res):
    """把一条 result 概括成 (图标, 一句话)；res 为 None/坏文件时给「无结果」。"""
    if not isinstance(res, dict):
        return '－', '无结果（未执行或结果未落盘）'
    if res.get('_broken'):
        return '❌', '结果文件损坏（JSON 解析失败）'
    errors = res.get('errors') or []
    fatal = str(res.get('fatal_error') or '').strip()
    n_add = int(res.get('total_added') or 0)
    n_tile = int(res.get('total_tiles_added') or 0)
    secs = res.get('total_secs')
    if fatal:
        return '❌', '失败：%s' % _short_text(fatal)
    if not res.get('ok'):
        first = ''
        if errors and isinstance(errors[0], dict):
            first = errors[0].get('error') or ''
        return '❌', '失败：%s' % _short_text(first or '原因见详情')
    parts = ['新增 %d 张' % n_add]
    if n_tile or 'tiles' in (res.get('modes') or []):
        parts.append('%d 瓦片' % n_tile)
    body = ' / '.join(parts)
    if errors:
        return '⚠', '部分成功：%s（%d 个阶段失败）' % (body, len(errors))
    txt = body
    try:
        if secs is not None:
            txt += '（%.1fs）' % float(secs)
    except Exception:
        pass
    return '✅', txt


def _format_handoff_result_detail(res, rec=None):
    """把一条 result 展开成多行明细（给「查看详情」弹窗 / 行 tooltip 用）。"""
    if not isinstance(res, dict):
        lines = ['状态：－ 无结果（未执行，或结果文件还没落盘）']
        if isinstance(rec, dict):
            lines.append('批次：%s' % (rec.get('request_id') or rec.get('_stamp') or ''))
            reason = str(rec.get('reason') or '')
            lines.append('原因：%s' % HANDOFF_PENDING_REASON_LABELS.get(reason, reason))
            lines.append('交接方式：%s' % ('/'.join(rec.get('modes') or []) or 'full/tiles'))
        return '\n'.join(lines)
    icon, short = _summarize_handoff_result(res)
    lines = ['状态：%s %s' % (icon, short),
             'request_id：%s' % (res.get('request_id') or ''),
             '时间：%s → %s（耗时 %ss）' % (res.get('started_at') or '?',
                                            res.get('finished_at') or '?',
                                            res.get('total_secs')),
             '交接方式：%s' % ('/'.join(res.get('modes') or []) or '?'),
             '图库前缀：%s' % (res.get('prefix') or ''),
             '合计：新增 %d 张 / %d 瓦片' % (int(res.get('total_added') or 0),
                                             int(res.get('total_tiles_added') or 0))]
    steps = res.get('steps') or []
    if steps:
        lines.append('逐根目录明细：')
        for st in steps:
            if not isinstance(st, dict):
                continue
            stages = st.get('stages') or []
            if not stages:
                lines.append('  · %s｜新增 %s 张｜%ss'
                             % (st.get('root'), st.get('added', 0), st.get('secs', '?')))
                continue
            for sg in stages:
                if not isinstance(sg, dict):
                    continue
                tail = ('（%s）' % sg.get('build_mode')) if sg.get('build_mode') else ''
                extra = ''
                if sg.get('total_in_index') is not None:
                    extra = '｜库内 %s' % sg.get('total_in_index')
                elif sg.get('total_tiles') is not None:
                    extra = '｜库内 %s 瓦片' % sg.get('total_tiles')
                line = ('  · [%s] %s｜新增 %s 张%s%s｜%ss'
                        % (sg.get('mode') or '?', st.get('root'), sg.get('added', 0),
                           tail, extra, sg.get('secs', '?')))
                if sg.get('error'):
                    line += '｜❌ %s' % _short_text(sg.get('error'), 80)
                lines.append(line)
    notices = res.get('notices') or []
    if notices:
        lines.append('提示：')
        lines += ['  · %s' % _short_text(x, 160) for x in notices]
    errors = res.get('errors') or []
    if errors:
        lines.append('错误：')
        for e in errors:
            if isinstance(e, dict):
                lines.append('  · [%s] %s｜%s'
                             % (e.get('mode') or '?', e.get('root'),
                                _short_text(e.get('error'), 160)))
    if res.get('fatal_error'):
        lines.append('致命错误：%s' % res.get('fatal_error'))
    return '\n'.join(lines)


def _pending_search_haystack(rec, res=None):
    """把一条 pending 记录（连同它回读到的结果）拼成小写可搜索文本。"""
    if not isinstance(rec, dict):
        return ''
    roots = [str(x.get('path')) for x in (rec.get('roots') or [])
             if isinstance(x, dict) and x.get('path')]
    modes = [str(m) for m in (rec.get('modes') or [])]
    reason = str(rec.get('reason') or '')
    parts = [str(rec.get('_stamp') or ''), str(rec.get('ts') or ''),
             str(rec.get('request_id') or ''), reason,
             HANDOFF_PENDING_REASON_LABELS.get(reason, ''),
             ' '.join(modes), ' '.join(HANDOFF_MODE_LABELS.get(m, m) for m in modes),
             str(rec.get('_file') or ''), '\n'.join(roots)]
    # 结果那部分始终拼进去（没结果时就是「－ 无结果…」），与列表里「结果」列所见一致，
    # 这样搜「无结果 / 失败 / ✅」都能命中表格上看得见的那些字。
    icon, short = _summarize_handoff_result(res)
    _res = res if isinstance(res, dict) else {}
    parts += [icon, short, str(_res.get('prefix') or ''),
              str(_res.get('fatal_error') or '')]
    return '\n'.join(parts).lower()


def _launch_handoff_launcher(req_path, wait=90):
    """以分离进程（DETACHED_PROCESS）方式启动 handoff_launcher.py，不阻塞、不等待。

    本程序退出不影响过渡进程继续等待（img_server 退出后再拉起 GUI 建库）。
    返回 Popen 对象；launcher / 请求文件缺失或启动失败返回 None。
    """
    if not req_path or not os.path.isfile(req_path) or not os.path.isfile(HANDOFF_LAUNCHER):
        return None
    # 解释器：源码运行 = 当前 python；若本程序打包成 exe（sys.executable 非 python）
    # 则回退到同目录 pythonw.exe / python.exe 来跑 launcher（它是 .py）
    py = sys.executable
    if not os.path.basename(py).lower().startswith('python'):
        for cand in ('pythonw.exe', 'python.exe'):
            c = os.path.join(os.path.dirname(py), cand)
            if os.path.exists(c):
                py = c
                break
    DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(
            [py, '-X', 'utf8', HANDOFF_LAUNCHER, req_path, '--wait', str(wait)],
            cwd=os.path.dirname(HANDOFF_LAUNCHER),
            creationflags=DETACHED,
            close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


class HandoffModeDialog(QDialog):
    """交接方式选择弹框（第十九轮；第二十轮加回读 / 清理 / 分页搜索）：勾选 full / tiles +
    未处理项目列表 + 交接结果回读。

    取代原来 `_on_handoff_clicked` 里的「确定 / 取消」确认框。三个出口：

    - 「开始交接」：按勾选方式写 request_<id>.json（modes 字段）→ 分离启动过渡进程
      → 交由 host._quit_after_handoff() 退出主程序（launcher 启动失败则不退出，
      与原实现一致，避免用户既没下成库又丢了程序）；
    - 「暂不处理（加入未处理列表）」：把本批次原子落盘成 pending_<时间戳>.json，
      弹框不关、列表立刻刷新，用户可稍后再点该行的「立即交接」；
    - 关闭（X / Esc / 「关闭」按钮）：本批次还没处理过就按 dialog_closed 暂存一次，
      已暂存 / 已交接过则不重复落盘。

    第二十轮新增：
    - **结果回读**：回读 handoff\\result_*.json → 顶部「最近一次交接结果」摘要 + 历史下拉
      + 「查看详情」逐根目录明细；未处理列表每行也显示该批次的结果（－ 无结果 / ✅ / ⚠ / ❌）；
    - **pending 清理策略**：打开弹框时按「超过 HANDOFF_PENDING_TTL_DAYS 天 或 超过
      HANDOFF_PENDING_MAX_RECORDS 条」自动清理最旧的 pending（可点「清理过期」手动再跑），
      只删 pending_*.json / *.json.tmp，不碰 request_/result_/图片；
    - **分页 + 搜索**：未处理列表按 HANDOFF_PENDING_PAGE_SIZE 分页，搜索框支持时间戳 /
      下载根路径 / 原因 / 交接方式 / 结果状态（空格分隔 = 同时满足）。

    roots: 本次交接的下载根目录列表（沿用原确认框展示的信息）。
    host:  提供 `_quit_after_handoff()` 的对象，真实调用传主窗口 self；测试传桩即可
           拦截真实启动与退出。
    dirpath: 落盘目录，默认 HANDOFF_DIR；测试传 tempfile 目录，绝不写真实 handoff\\。
    """

    def __init__(self, roots, parent=None, host=None, dirpath=None,
                 launcher_path=None, dirty_note=''):
        super().__init__(parent)
        self._roots = [str(p) for p in (roots or [])]
        self._host = host
        self._dirpath = dirpath or HANDOFF_DIR
        self._launcher = launcher_path or HANDOFF_LAUNCHER
        self._dirty_note = dirty_note or ''
        self._staged = False       # 本弹框是否已暂存过（防止关窗重复落盘）
        self._handed_off = False   # 是否已成功发起交接（关窗时不再暂存）
        self._pending = []         # 读回的全部未处理记录（时间倒序，含被搜索隐藏的）
        self._filtered = []        # 当前搜索条件下可见的记录
        self._row_btns = []        # [(立即交接按钮, 删除按钮, 记录), ...]（只含当前页）
        self._row_view_btns = []   # [(查看结果按钮, 记录), ...]（只含当前页）
        self._page = 1
        self._page_size = HANDOFF_PENDING_PAGE_SIZE
        self._cleanup_done = False
        self._cleanup_stat = None
        self._results = []         # 回读到的历史 result（时间倒序）
        self.setWindowTitle('跨进程交接 · 下载完成 → 图库检索')
        self.setMinimumWidth(780)
        self.setMinimumHeight(600)
        self._build_ui()
        self.reload_pending()
        self.reload_results()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        head = QLabel(
            '选择本次交给「图库检索管理器」的交接方式。'
            '两个都勾 = 按顺序先整图、后子图各做一遍；只勾一个就只做那个；'
            '一个都不勾则本次不交接，落盘信息进下面的「未处理项目列表」。')
        head.setWordWrap(True)
        head.setStyleSheet('color: #555; font-size: 12px;')
        root.addWidget(head)

        box_roots = QGroupBox(f'本次下载根目录（{len(self._roots)} 个）')
        lay_roots = QVBoxLayout(box_roots)
        lay_roots.setContentsMargins(10, 8, 10, 8)
        lay_roots.addWidget(QLabel(self._roots_summary()))
        root.addWidget(box_roots)

        box_mode = QGroupBox('交接方式')
        lay_mode = QVBoxLayout(box_mode)
        lay_mode.setContentsMargins(10, 8, 10, 8)
        self.chk_full = QCheckBox(HANDOFF_MODE_LABELS['full'])
        self.chk_full.setChecked(True)
        self.chk_full.setToolTip('整图（原图）增量入库：按下载根扫描图片文件，整张入库。')
        self.chk_tiles = QCheckBox(HANDOFF_MODE_LABELS['tiles'])
        self.chk_tiles.setChecked(True)
        self.chk_tiles.setToolTip('子图增量入库：把整图按 512px 切成瓦片后入库，供以图搜图命中局部。')
        lay_mode.addWidget(self.chk_full)
        lay_mode.addWidget(self.chk_tiles)
        hint = QLabel(
            '两个都勾：先整图入库一遍，再子图入库一遍（顺序固定，写入 request 的 modes）。\n'
            '只勾一个：只做勾选的这一种。一个都不勾：点「开始交接」不会写 request，'
            '本批次只落进未处理项目列表。')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #888; font-size: 11px;')
        lay_mode.addWidget(hint)
        if self._dirty_note:
            lbl_dirty = QLabel(self._dirty_note)
            lbl_dirty.setWordWrap(True)
            lbl_dirty.setStyleSheet('color: #c0392b; font-size: 11px;')
            lay_mode.addWidget(lbl_dirty)
        root.addWidget(box_mode)

        lay_btns = QHBoxLayout()
        self.btn_start = QPushButton('开始交接')
        self.btn_start.setToolTip('按勾选方式写交接文件 → 启动过渡进程 → 退出本程序。')
        self.btn_start.setStyleSheet(
            'QPushButton { background: #e67e22; color: white; font-weight: bold;'
            ' padding: 6px 16px; border-radius: 4px; }'
            'QPushButton:hover { background: #ca6f1e; }')
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_defer = QPushButton('暂不处理（加入未处理列表）')
        self.btn_defer.setToolTip('本次不交接：把这批下载根暂存成 pending 文件，稍后可在下面「立即交接」。')
        self.btn_defer.clicked.connect(self._on_defer_clicked)
        self.btn_close = QPushButton('关闭')
        self.btn_close.setToolTip('关闭弹框；本批次还没处理过的话会自动进未处理项目列表。')
        self.btn_close.clicked.connect(self.close)
        lay_btns.addWidget(self.btn_start)
        lay_btns.addWidget(self.btn_defer)
        lay_btns.addStretch(1)
        lay_btns.addWidget(self.btn_close)
        root.addLayout(lay_btns)

        box_res = QGroupBox('交接结果（回读 result_*.json）')
        lay_res = QVBoxLayout(box_res)
        lay_res.setContentsMargins(10, 8, 10, 8)
        row_res = QHBoxLayout()
        self.lbl_recent = QLabel('（还没读到任何交接结果）')
        self.lbl_recent.setWordWrap(True)
        self.lbl_recent.setStyleSheet('font-size: 12px;')
        row_res.addWidget(self.lbl_recent, 1)
        self.cmb_results = QComboBox()
        self.cmb_results.setMinimumWidth(320)
        self.cmb_results.setToolTip('历史交接结果（时间倒序）；选中后点「查看详情」看逐根目录明细。')
        self.cmb_results.currentIndexChanged.connect(self._on_result_combo_changed)
        row_res.addWidget(self.cmb_results)
        self.btn_result_detail = QPushButton('查看详情')
        self.btn_result_detail.setToolTip('看这条结果的逐根目录明细（新增张数 / 瓦片数 / 提示 / 错误）。')
        self.btn_result_detail.setStyleSheet('font-size: 11px; padding: 2px 8px;')
        self.btn_result_detail.clicked.connect(self._on_view_recent)
        row_res.addWidget(self.btn_result_detail)
        lay_res.addLayout(row_res)
        root.addWidget(box_res)

        box_pending = QGroupBox('未处理项目列表（一次落盘批次 = 一条）')
        lay_pending = QVBoxLayout(box_pending)
        lay_pending.setContentsMargins(10, 8, 10, 8)

        row_tool = QHBoxLayout()
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText(
            '搜索：时间戳 / 下载根路径 / 原因 / 交接方式 / 结果状态（空格分隔 = 同时满足）')
        self.edit_search.setClearButtonEnabled(True)
        self.edit_search.textChanged.connect(self._on_search_changed)
        row_tool.addWidget(self.edit_search, 1)
        self.btn_refresh = QPushButton('刷新')
        self.btn_refresh.setToolTip('重新读回 pending_*.json 与 result_*.json。')
        self.btn_refresh.setStyleSheet('font-size: 11px; padding: 2px 8px;')
        self.btn_refresh.clicked.connect(lambda _=False: self.reload_pending(cleanup=False))
        row_tool.addWidget(self.btn_refresh)
        self.btn_cleanup = QPushButton('清理过期')
        self.btn_cleanup.setToolTip(
            '按策略清理未处理暂存：超过 %d 天或超过 %d 条时删最旧的 pending 文件'
            '（不碰 request_ / result_ / 图片）。' % (HANDOFF_PENDING_TTL_DAYS,
                                                    HANDOFF_PENDING_MAX_RECORDS))
        self.btn_cleanup.setStyleSheet('font-size: 11px; padding: 2px 8px;')
        self.btn_cleanup.clicked.connect(self._on_cleanup_clicked)
        row_tool.addWidget(self.btn_cleanup)
        lay_pending.addLayout(row_tool)

        self.lbl_cleanup = QLabel('')
        self.lbl_cleanup.setWordWrap(True)
        self.lbl_cleanup.setStyleSheet('color: #b9770e; font-size: 11px;')
        self.lbl_cleanup.setVisible(False)
        lay_pending.addWidget(self.lbl_cleanup)

        self.lbl_empty = QLabel('（暂无未处理项目）')
        self.lbl_empty.setStyleSheet('color: #888; font-size: 11px;')
        lay_pending.addWidget(self.lbl_empty)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(['批次信息', '结果', '查看结果', '立即交接', '删除'])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hd = self.table.horizontalHeader()
        hd.setSectionResizeMode(0, QHeaderView.Stretch)
        for _col, _w in ((1, 210), (2, 84), (3, 96), (4, 44)):
            hd.setSectionResizeMode(_col, QHeaderView.Fixed)
            self.table.setColumnWidth(_col, _w)
        lay_pending.addWidget(self.table)

        row_page = QHBoxLayout()
        self.btn_prev = QPushButton('上一页')
        self.btn_prev.setStyleSheet('font-size: 11px; padding: 2px 8px;')
        self.btn_prev.clicked.connect(self._on_page_prev)
        self.lbl_page = QLabel('')
        self.lbl_page.setStyleSheet('color: #666; font-size: 11px;')
        self.btn_next = QPushButton('下一页')
        self.btn_next.setStyleSheet('font-size: 11px; padding: 2px 8px;')
        self.btn_next.clicked.connect(self._on_page_next)
        row_page.addWidget(self.btn_prev)
        row_page.addWidget(self.lbl_page, 1)
        row_page.addWidget(self.btn_next)
        lay_pending.addLayout(row_page)
        root.addWidget(box_pending, 1)

    def _roots_summary(self):
        """下载根目录摘要（多根时最多列 6 条，其余折叠）。"""
        if not self._roots:
            return '（无）'
        shown = self._roots[:6]
        txt = '\n'.join('  · ' + p for p in shown)
        if len(self._roots) > len(shown):
            txt += f'\n  · …等共 {len(self._roots)} 个'
        return txt

    # ---------------- 交接结果回读 ----------------
    def reload_results(self):
        """回读 handoff\\result_*.json：刷新「最近一次」摘要 + 历史下拉。返回结果条数。"""
        self._results = _list_handoff_results(self._dirpath)
        if hasattr(self, 'cmb_results'):
            self.cmb_results.blockSignals(True)
            self.cmb_results.clear()
            for res in self._results:
                icon, short = _summarize_handoff_result(res)
                stamp = str(res.get('finished_at') or '').replace('T', ' ')
                if not stamp:
                    try:
                        stamp = time.strftime('%Y-%m-%d %H:%M:%S',
                                              time.localtime(int(res.get('_mtime') or 0)))
                    except Exception:
                        stamp = '(时间未知)'
                self.cmb_results.addItem('%s · %s %s' % (stamp, icon, short))
            self.cmb_results.blockSignals(False)
            if self._results:
                self.cmb_results.setCurrentIndex(0)
            self._on_result_combo_changed()
        return len(self._results)

    def _selected_result(self):
        """当前下拉选中的历史结果（没有就返回 None）。"""
        if not self._results:
            return None
        idx = 0
        try:
            idx = int(self.cmb_results.currentIndex())
        except Exception:
            idx = 0
        if idx < 0 or idx >= len(self._results):
            idx = 0
        return self._results[idx]

    def _on_result_combo_changed(self, *_a):
        """历史结果下拉切换 → 更新「最近一次」摘要标签与详情按钮状态。"""
        res = self._selected_result()
        if not isinstance(res, dict):
            self.lbl_recent.setText('（还没读到任何交接结果）')
            self.lbl_recent.setToolTip('')
            self.btn_result_detail.setEnabled(False)
            return
        icon, short = _summarize_handoff_result(res)
        stamp = str(res.get('finished_at') or '').replace('T', ' ') or '(时间未知)'
        self.lbl_recent.setText('最近一次交接结果（%s）：%s %s' % (stamp, icon, short))
        self.lbl_recent.setToolTip(_format_handoff_result_detail(res))
        self.btn_result_detail.setEnabled(True)

    def _result_for(self, rec):
        """回读某条未处理批次对应的 result_<request_id>.json（没有返回 None）。"""
        if not isinstance(rec, dict):
            return None
        rid = str(rec.get('request_id') or '').strip()
        if not rid:
            req = rec.get('request') if isinstance(rec.get('request'), dict) else {}
            rid = str((req or {}).get('request_id') or '').strip()
        return _load_handoff_result(rid, self._dirpath) if rid else None

    def _on_view_recent(self):
        """「查看详情」：把下拉里选中的结果（缺省最近一次）展开成明细弹窗。"""
        res = self._selected_result()
        if not isinstance(res, dict):
            QMessageBox.information(self, '交接结果', '还没读到任何交接结果（result_*.json）。')
            return False
        return self._show_result_detail(res)

    def _show_result_detail(self, res, rec=None):
        """弹「交接结果详情」；返回明细文本（测试可直接断言文本）。"""
        detail = _format_handoff_result_detail(res, rec)
        QMessageBox.information(self, '交接结果详情', detail)
        return detail

    def _on_view_result(self, rec):
        """行内「查看结果」：回读该批次的 result 并弹详情；没有就说明原因。"""
        res = self._result_for(rec)
        if not isinstance(res, dict):
            rid = str((rec or {}).get('request_id') or '')
            QMessageBox.information(
                self, '交接结果',
                '还没读到该批次的结果文件：\n%s\n\n'
                '可能原因：还没开始执行 / 索引器还在跑 / 该批次最终没交接；'
                '也可能交接时因重名顺延了 request_id。'
                % (_result_file_for(rid, self._dirpath) or '（该记录没有 request_id）'))
            return False
        return bool(self._show_result_detail(res, rec))

    # ---------------- 未处理项目列表（搜索 + 分页） ----------------
    def reload_pending(self, cleanup=True, reset_page=False):
        """读回 pending_*.json（时间倒序）并按当前搜索词 / 分页重建列表。

        cleanup=True 只在第一次调用时按 TTL + 条数上限自动清理一次（self._cleanup_done
        去重），避免每次刷新都扫盘删除；返回读到的未处理记录条数（清理后）。
        """
        if cleanup and not self._cleanup_done:
            self._cleanup_done = True
            try:
                self._cleanup_stat = _cleanup_pending_records(self._dirpath)
            except Exception:
                self._cleanup_stat = None
            self._show_cleanup_notice()
        self._pending = _list_pending_records(self._dirpath)
        self._apply_filter(reset_page=reset_page)
        return len(self._pending)

    def _apply_filter(self, reset_page=False):
        """按搜索框内容过滤（空格分隔的多词 = 全部命中），再渲染当前页。返回可见条数。"""
        text = (self.edit_search.text() if hasattr(self, 'edit_search') else '') or ''
        terms = [t for t in text.lower().split() if t]
        if terms:
            self._filtered = []
            for rec in self._pending:
                # 每条记录的搜索文本只算一次（含回读结果摘要），避免逐键 stat 磁盘
                hay = rec.get('_hay')
                if hay is None:
                    hay = _pending_search_haystack(rec, self._result_for(rec))
                    rec['_hay'] = hay
                if all(t in hay for t in terms):
                    self._filtered.append(rec)
        else:
            self._filtered = list(self._pending)
        if reset_page:
            self._page = 1
        pages = max(1, (len(self._filtered) + self._page_size - 1) // self._page_size)
        try:
            self._page = max(1, min(int(self._page or 1), pages))
        except Exception:
            self._page = 1
        self._render_page()
        return len(self._filtered)

    def _on_search_changed(self):
        """搜索框变化：重算过滤结果并回到第 1 页。"""
        self._apply_filter(reset_page=True)

    def _on_page_prev(self):
        if self._page > 1:
            self._page -= 1
            self._render_page()

    def _on_page_next(self):
        pages = max(1, (len(self._filtered) + self._page_size - 1) // self._page_size)
        if self._page < pages:
            self._page += 1
            self._render_page()

    def _render_page(self):
        """按 self._page 渲染当前页的行（批次信息 / 结果 / 查看结果 / 立即交接 / ×）。"""
        self._row_btns = []
        self._row_view_btns = []
        self.table.setRowCount(0)
        total, shown = len(self._pending), len(self._filtered)
        pages = max(1, (shown + self._page_size - 1) // self._page_size)
        start = (self._page - 1) * self._page_size
        rows = self._filtered[start:start + self._page_size]
        for i, rec in enumerate(rows):
            self.table.insertRow(i)
            item = QTableWidgetItem(self._pending_row_text(rec))
            item.setToolTip(self._pending_row_tooltip(rec))
            self.table.setItem(i, 0, item)
            res = self._result_for(rec)
            icon, short = _summarize_handoff_result(res)
            item_res = QTableWidgetItem('%s %s' % (icon, short))
            item_res.setToolTip(_format_handoff_result_detail(res, rec))
            self.table.setItem(i, 1, item_res)
            btn_view = QPushButton('查看结果')
            btn_view.setToolTip('回读该批次的 result_<request_id>.json 看明细。')
            btn_view.setStyleSheet('font-size: 11px; padding: 2px 6px;')
            btn_view.setEnabled(isinstance(res, dict))
            btn_view.clicked.connect(lambda _=False, r=rec: self._on_view_result(r))
            btn_now = QPushButton('立即交接')
            btn_now.setStyleSheet('font-size: 11px; padding: 2px 6px;')
            btn_now.clicked.connect(lambda _=False, r=rec: self._on_handoff_record(r))
            btn_del = QPushButton('×')
            btn_del.setToolTip('删除这条未处理记录（只删 pending 文件）')
            btn_del.setStyleSheet('color: #d9534f; font-weight: bold; border: none;')
            btn_del.clicked.connect(lambda _=False, r=rec, row=i: self._on_delete_record(r, row))
            self.table.setCellWidget(i, 2, btn_view)
            self.table.setCellWidget(i, 3, btn_now)
            self.table.setCellWidget(i, 4, btn_del)
            self._row_btns.append((btn_now, btn_del, rec))
            self._row_view_btns.append((btn_view, rec))
        if not total:
            self.lbl_empty.setText('（暂无未处理项目）')
        else:
            self.lbl_empty.setText('（没有匹配的未处理项目：共 %d 条，换个关键词试试）' % total)
        self.lbl_empty.setVisible(shown == 0)
        self.table.setVisible(shown > 0)
        if shown:
            self.lbl_page.setText('共 %d 条%s ｜ 第 %d/%d 页（每页 %d 条）'
                                  % (shown, ('' if shown == total else '（筛自 %d 条）' % total),
                                     self._page, pages, self._page_size))
        elif total:
            self.lbl_page.setText('共 %d 条，当前筛选无匹配' % total)
        else:
            self.lbl_page.setText('共 0 条')
        self.btn_prev.setEnabled(self._page > 1)
        self.btn_next.setEnabled(self._page < pages)
        return len(rows)

    def _pending_row_text(self, rec):
        """行内信息：时间戳 + 下载根数量 + 下载根摘要 + 未处理原因。"""
        stamp = str(rec.get('_stamp') or '')
        ts = _stamp_to_iso(stamp).replace('T', ' ') if stamp else str(rec.get('ts') or '')
        roots = [str(x.get('path')) for x in (rec.get('roots') or [])
                 if isinstance(x, dict) and x.get('path')]
        shown = roots[:3]
        summary = '；'.join(shown) + (f'…等共 {len(roots)} 个' if len(roots) > len(shown) else '')
        reason = HANDOFF_PENDING_REASON_LABELS.get(str(rec.get('reason') or ''), str(rec.get('reason') or ''))
        modes = '/'.join(rec.get('modes') or []) or 'full/tiles'
        return f'{ts} ｜ {len(roots)} 个下载根：{summary} ｜ {reason} ｜ 将执行 {modes}'

    def _pending_row_tooltip(self, rec):
        roots = [str(x.get('path')) for x in (rec.get('roots') or [])
                 if isinstance(x, dict) and x.get('path')]
        res = self._result_for(rec)
        icon, short = _summarize_handoff_result(res)
        return ('未处理批次\n时间：%s\n原因：%s\n交接方式：%s\n结果：%s %s\n下载根：\n%s\n文件：%s'
                % (str(rec.get('ts') or ''), str(rec.get('reason') or ''),
                   '/'.join(rec.get('modes') or []), icon, short,
                   '\n'.join('  · ' + p for p in roots),
                   str(rec.get('_file') or '')))

    # ---------------- pending 清理策略 ----------------
    def _on_cleanup_clicked(self):
        """「清理过期」：按同一策略手动清理一次（超期 / 超量），并提示清理结果。

        策略只删「过期」与「超量」的 pending，所以不弹确认框；request_/result_/图片不碰。
        """
        stat = _cleanup_pending_records(self._dirpath)
        self._cleanup_stat = stat
        self._show_cleanup_notice()
        removed = stat.get('removed') or []
        if removed or stat.get('tmp_removed'):
            QMessageBox.information(
                self, '清理未处理暂存',
                '已清理 %d 条未处理暂存（超期 %d 条 / 超量 %d 条）+ 临时文件 %d 个。\n'
                '策略：超过 %s 天或超过 %s 条时，删最旧的 pending 文件。\n\n%s'
                % (len(removed), stat.get('by_age', 0), stat.get('by_count', 0),
                   stat.get('tmp_removed', 0), stat.get('ttl_days'), stat.get('max_records'),
                   '、'.join(removed) or '(仅临时文件)'))
        else:
            QMessageBox.information(
                self, '清理未处理暂存',
                '没有需要清理的暂存：都没超过 %s 天，也没超过 %s 条上限。'
                % (stat.get('ttl_days'), stat.get('max_records')))
        self.reload_pending(cleanup=False)
        return stat

    def _show_cleanup_notice(self):
        """把自动清理的结果显示成弹框里的一行提示；没有清理则隐藏。"""
        st = self._cleanup_stat or {}
        removed = st.get('removed') or []
        if not removed and not st.get('tmp_removed'):
            self.lbl_cleanup.setVisible(False)
            return False
        self.lbl_cleanup.setText(
            '已按策略自动清理 %d 条未处理暂存（超期 %d / 超量 %d；'
            '保留 %s 天、最多 %s 条）+ 临时文件 %d 个'
            % (len(removed), st.get('by_age', 0), st.get('by_count', 0),
               st.get('ttl_days'), st.get('max_records'), st.get('tmp_removed', 0)))
        self.lbl_cleanup.setToolTip('清理的文件：\n' + '\n'.join('  · ' + x for x in removed))
        self.lbl_cleanup.setVisible(True)
        return True

    # ---------------- 勾选 → 落盘 ----------------
    def selected_modes(self):
        """当前勾选的交接方式，顺序固定 full 在前 tiles 在后；都没勾返回 []。"""
        modes = []
        if self.chk_full.isChecked():
            modes.append('full')
        if self.chk_tiles.isChecked():
            modes.append('tiles')
        return modes

    def _stash(self, reason):
        """把本批次原子落盘成 pending_<时间戳>.json 并刷新列表；返回 (fp, record)。"""
        fp, rec = _write_pending_record(
            self._roots, self.selected_modes(), reason, dirpath=self._dirpath)
        self._staged = True
        self.reload_pending(reset_page=True)   # 新批次在最上面：跳回第 1 页
        return fp, rec

    def _launcher_ready(self):
        """启动器是否就位；不在就提示并返回 False（保持原「找不到 launcher 就不交接」的语义）。"""
        if self._launcher and not os.path.isfile(self._launcher):
            QMessageBox.warning(
                self, '跨进程交接',
                f'找不到过渡启动器:\n{self._launcher}\n\n'
                '请确认 image-search（含 handoff_launcher.py）已就位。')
            return False
        return True

    def _on_start_clicked(self):
        """「开始交接」：按勾选写 request → 启动 launcher → 退出主程序。"""
        modes = self.selected_modes()
        if not modes:
            # 一个都没勾：本次不交接，落进未处理项目列表（弹框不关，列表立刻可见）
            try:
                self._stash('none_selected')
            except Exception as e:
                QMessageBox.critical(self, '跨进程交接', f'写入未处理列表失败:\n{e}')
                return
            QMessageBox.information(
                self, '未勾选交接方式',
                '没有勾选任何交接方式，本次不交接。\n'
                '这批下载根已加入「未处理项目列表」，可在下面点「立即交接」随时补做。')
            return
        if not self._launcher_ready():
            return
        stamp, _fp = _free_handoff_stamp('request_batch', self._dirpath)
        try:
            fp, rid = _write_handoff_request_file(
                self._roots, dirpath=self._dirpath, modes=modes, request_id=f'batch_{stamp}')
        except Exception as e:
            QMessageBox.critical(self, '跨进程交接', f'写交接文件失败:\n{e}')
            return
        proc = _launch_handoff_launcher(fp, wait=90)
        if proc is None:
            QMessageBox.critical(
                self, '跨进程交接',
                '过渡启动器启动失败（未退出程序）。请检查 python 环境与 image-search 目录。')
            return
        self._quit_via_host()

    def _on_defer_clicked(self):
        """「暂不处理」：暂存本批次并刷新列表（同一批次不重复落盘）。"""
        if self._staged:
            self.reload_pending()
            QMessageBox.information(
                self, '未处理项目列表', '本批次已经在「未处理项目列表」里了，可直接点该行的「立即交接」。')
            return
        try:
            self._stash('deferred')
        except Exception as e:
            QMessageBox.critical(self, '跨进程交接', f'写入未处理列表失败:\n{e}')
            return
        QMessageBox.information(
            self, '已加入未处理列表',
            '本批次已暂存到「未处理项目列表」，尚未交接。\n可点该行的「立即交接」立刻补做，或点「关闭」稍后再来。')

    # ---------------- 未处理记录的两个操作 ----------------
    def _on_handoff_record(self, rec):
        """「立即交接」：按该批次存的 modes 写 request → 启动 launcher → 删 pending → 退出。"""
        if not self._launcher_ready():
            return
        try:
            fp, rid = _write_handoff_request_from_record(rec, self._dirpath)
        except Exception as e:
            QMessageBox.critical(self, '跨进程交接', f'写交接文件失败:\n{e}')
            return
        proc = _launch_handoff_launcher(fp, wait=90)
        if proc is None:
            QMessageBox.critical(
                self, '跨进程交接',
                '过渡启动器启动失败（未退出程序）。请检查 python 环境与 image-search 目录。')
            return
        # 交接已发起，这条未处理记录才算处理完：删掉它，避免下次重复交接
        _delete_pending_file(rec.get('_file'))
        self.reload_pending()
        self._quit_via_host()

    def _on_delete_record(self, rec, row=0):
        """红色 ×：确认后删除这条 pending 记录并刷新列表。"""
        info = self._pending_row_text(rec) if isinstance(rec, dict) else ''
        ans = QMessageBox.question(
            self, '删除未处理记录',
            f'确定要删除这条未处理记录吗？\n\n{info}\n\n'
            '（只删除该 pending 暂存文件，不会删除已下载的图片，也不会写交接文件。）',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        _delete_pending_file((rec or {}).get('_file'))
        self.reload_pending()
        return True

    def _quit_via_host(self):
        """交接发起成功后退出主程序（host 可换成测试桩以拦截真实退出）。"""
        self._handed_off = True
        try:
            self.accept()
        except Exception:
            pass
        fn = getattr(self._host, '_quit_after_handoff', None)
        if callable(fn):
            fn()

    # ---------------- 关窗 = 未处理 ----------------
    def _stash_on_close(self):
        """X / Esc / 「关闭」：本批次没处理过就按 dialog_closed 暂存一次，不重复。"""
        if self._handed_off or self._staged:
            return
        try:
            self._stash('dialog_closed')
        except Exception:
            pass

    def closeEvent(self, event):
        self._stash_on_close()
        super().closeEvent(event)

    def reject(self):
        # QDialog 的 Esc 走 reject()，不触发 closeEvent，所以这里也要暂存一次
        self._stash_on_close()
        super().reject()