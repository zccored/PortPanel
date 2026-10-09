# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· 「图源配置」Web 化试点（阶段 0）
# Copyright (C) 2026 zccored  ·  AGPL-3.0-only（见 LICENSE）
#
# 零侵入：**不修改** port_panel.py / img_server.py / api_config_dialog.py，
# 独立启动：python web_config_pilot.py            看界面（正常窗口）
#           python web_config_pilot.py --selftest 自测：量 4 个数后自动退出
#           python web_config_pilot.py --real     尝试只读真实配置（默认用伪造样本）
#
# 判据（交付文档 §2）：① 首屏耗时 ② 桥接往返延迟 ③ 内存增量 ④ 体感分（人工）
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

# ⚠️ WebEngine 必须在创建 QApplication **之前** import（Qt6 规则），否则黑屏/崩溃
from PySide6 import QtWebEngineWidgets  # noqa: F401
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel

ROOT = os.path.dirname(os.path.abspath(__file__))
_PROC_T0 = time.perf_counter()   # 进程级起点：用于与 Qt 版对照「冷启动总耗时」
DIST_DIR = os.path.join(ROOT, 'webui', 'dist')
INDEX_HTML = os.path.join(DIST_DIR, 'index.html')
EXPORT_DIR = os.path.join(os.environ.get('TEMP', ROOT), 'portpanel_pilot')


class _DiagPage(QWebEnginePage):
    """把页面里的 JS 报错转发到 Python 控制台（试点期诊断用）。"""

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        try:
            print('[js] %s:%s %s' % (source, line, message))
            sys.stdout.flush()
        except Exception:                       # noqa: BLE001
            pass


class PilotWindow(QDialog):
    """「图源配置」的 Web 试点窗口：一个 QWebEngineView + QWebChannel 桥。

    零侵入：本文件不 import img_server / api_config_dialog，也不会修改它们。
    """

    def __init__(self, state: dict, reason: str = '', selftest: bool = False):
        super().__init__(None)
        self.setWindowTitle('图源配置 · Web 试点（阶段 0）')
        self.resize(1180, 780)

        self._t0 = time.perf_counter()
        self._selftest = selftest
        self._marks: dict = {}
        self._reason = reason

        view = QWebEngineView(self)
        self._page = _DiagPage(view)
        view.setPage(self._page)
        s = view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        # 隐私红线：本地产物**不允许**访问远程 URL（离线可用 + 不偷跑网络）
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        # 拖文件进页面不导航（防误操作）；注意：Qt 6 的 WebAttribute 里**没有** DeveloperExtrasEnabled，
        # 关掉 devtools 的办法是「不打开 devtools 视图」而不是设这个开关。
        s.setAttribute(QWebEngineSettings.WebAttribute.NavigateOnDropEnabled, False)

        self.bridge = Bridge(state, self)
        channel = QWebChannel(view.page())
        channel.registerObject('bridge', self.bridge)
        view.page().setWebChannel(channel)

        js = _qwebchannel_js()
        if js:
            script = QWebEngineScript()
            script.setName('qwebchannel')
            script.setSourceCode(js)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            view.page().scripts().insert(script)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._fallback = QLabel(
            '正在加载 Web 界面…\n（若一直空白，说明 webui/dist 没构建：cd webui && npm run build）',
            self)
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._fallback)
        lay.addWidget(view)
        self.view = view

        self.bridge.reported.connect(self._on_reported)
        self.view.loadFinished.connect(self._on_load_finished)

        # 「桥接就绪」不在 JS 侧回调（JS 调 Python 的 void 槽会触发
        # qwebchannel.js:139 的 execCallbacks 报错），改为 Python 侧轮询探测。
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(50)
        self._probe_timer.timeout.connect(self._probe_pilot)

        if js:
            self.view.load(QUrl.fromLocalFile(INDEX_HTML))
        else:
            self._fallback.setText('读不到 qwebchannel.js（Qt 资源缺失）')

    # ---------- 判据采集 ----------
    def _on_load_finished(self, ok: bool) -> None:
        print('[load] finished ok=%s' % ok)
        sys.stdout.flush()
        probe = ("JSON.stringify({pilot: typeof window.__pilot, qwc: typeof window.QWebChannel, "
                 "qt: typeof window.qt, mode: (window.__pilot && window.__pilot.mode) || ''})")
        self.view.page().runJavaScript(
            probe, lambda r: (print('[probe] %s' % r), sys.stdout.flush()))
        self._probe_timer.start()

    def _probe_pilot(self) -> None:
        self.view.page().runJavaScript("window.__pilot ? window.__pilot.mode : ''", self._on_probe)

    def _on_probe(self, mode) -> None:
        if mode != 'qt':
            return
        self._probe_timer.stop()
        self._marks['ready_ms'] = (time.perf_counter() - self._t0) * 1000.0
        self._fallback.hide()
        # 触发桥接往返基准（300 次取中位）→ JS 侧算完经 bridge.report 回传
        self.view.page().runJavaScript('window.__pilot.benchRoundTrip(300)')

    def _on_reported(self, kind: str, value: float) -> None:
        self._marks['roundtrip_' + kind] = value
        self._finish()

    def _finish(self) -> None:
        self._marks.setdefault('mem_self_mb', _mem_mb(pid=os.getpid()))
        self._marks.setdefault('mem_webengine_mb', _mem_mb(image='QtWebEngineProcess.exe'))
        print('')
        print('==== 试点判据（交付文档 §2）====')
        print('  ① 冷启动总耗时（进程→桥接就绪） %8.1f ms' % ((time.perf_counter() - _PROC_T0) * 1000))
        print('     · 其中 窗口构造→就绪           %8.1f ms' % self._marks.get('ready_ms', -1))
        print('  ② 桥接往返（中位 / 300 次）      %8.2f ms' % self._marks.get('roundtrip_roundtrip', -1))
        print('  ③ 内存 · 主进程                 %8.1f MB' % self._marks.get('mem_self_mb', 0))
        print('     内存 · WebEngine 子进程       %8.1f MB' % self._marks.get('mem_webengine_mb', 0))
        print('  ④ 体感分                    （人工填写：现代感 / 满意度 1–5）')
        print('  数据源：%s' % self._reason)
        print('================================')
        sys.stdout.flush()
        if self._selftest:
            QTimer.singleShot(300, QApplication.instance().quit)


def main() -> int:
    selftest = '--selftest' in sys.argv
    want_real = '--real' in sys.argv

    app = QApplication.instance() or QApplication(sys.argv)

    if not os.path.exists(INDEX_HTML):
        print('缺少 Web 产物：%s' % INDEX_HTML)
        print('请先构建：cd webui && npm install && npm run build')
        return 2

    reason = ''
    state = None
    if want_real:
        state, reason = try_load_real_state()
    if state is None:
        state = sample_state()
        reason = reason or '（使用伪造样本，未读取任何真实配置）'

    win = PilotWindow(state, reason, selftest)
    win.show()
    if selftest:
        # 看门狗：即使桥接没回传，也要在不卡住的前提下退出
        QTimer.singleShot(40000, app.quit)
    return app.exec()


def try_load_real_state():
    """尝试**只读**真实配置并转成试点结构；失败或不存在则返回 (None, 原因)。

    只读红线：走 secure_store.get_plaintext()（机器绑定密钥解密），**绝不写回** data/api_config.json。
    本机当前没有 data/api_config.json，所以这条路径属于「best-effort」，schema 以实际配置为准。
    """
    cfg = os.path.join(ROOT, 'data', 'api_config.json')
    if not os.path.exists(cfg):
        return None, '（未找到 data/api_config.json → 使用伪造样本）'
    try:
        import secure_store  # 延迟导入：只在真要看真实配置时才拉起
        raw = secure_store.get_plaintext(cfg)
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode('utf-8', 'replace')
        data = json.loads(raw)
    except Exception as e:                      # noqa: BLE001 —— 试点要能容错降级
        return None, '（读取真实配置失败：%r → 使用伪造样本）' % e

    sources, headers = [], []
    for i, src in enumerate(data.get('image_sources', []) or []):
        eps = []
        for ep in src.get('endpoints', []) or []:
            params = []
            for p in ep.get('parameters', []) or []:
                params.append({
                    'name': str(p.get('name', '')),
                    'kind': str(p.get('type', 'string') or 'string'),
                    'value': str(p.get('value', ''))[:80],
                    'note': str(p.get('note', '') or '' )[:200],
                })
            eps.append({
                'path': str(ep.get('path', '') or ep.get('url_path', '')),
                'desc': str(ep.get('desc', '') or ep.get('description', ''))[:200],
                'params': params,
            })
        sources.append({
            'id': 'src-%d' % i,
            'name': str(src.get('name', '未命名入口')),
            'base_url': str(src.get('base_url', '')),
            'endpoints': eps or [{'path': '/', 'desc': '', 'params': []}],
        })
        for hn, hv in (src.get('headers') or {}).items():
            env = _env_name(str(hv))
            headers.append({
                'name': str(hn),
                'value': _mask_header_value(str(hn), hv),
                'sensitive': any(h in str(hn).lower() for h in _SENSITIVE_HINTS),
                'env': env or None,
                'env_set': bool(os.environ.get(env)) if env else None,
            })
    if not sources:
        return None, '（真实配置里没有入口 → 使用伪造样本）'
    return {
        'origin': 'real-readonly',
        'config_path': cfg,
        'notice': '真实配置的只读视图：请求头已掩码，明文未进入前端。',
        'sources': sources,
        'headers': headers,
    }, '已读取真实配置（只读）'


class Bridge(QObject):
    """JS ↔ Python 的唯一通道（对应交付文档 §5 的 EngineAPI 雏形）。"""

    stateChanged = Signal(str)
    reported = Signal(str, float)

    def __init__(self, state: dict, parent=None):
        super().__init__(parent)
        self._state = state

    @Slot(result=str)
    def getState(self) -> str:
        print('[py] getState()'); sys.stdout.flush()
        return json.dumps(self._state, ensure_ascii=False)

    @Slot(str, result=str)
    def exportState(self, payload: str) -> str:
        """试点阶段只导出到 %TEMP% 副本，**不写回** data/api_config.json。"""
        print('[py] exportState(%d bytes)' % len(payload)); sys.stdout.flush()
        try:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            path = os.path.join(EXPORT_DIR, 'api_config.pilot.export.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(json.loads(payload), f, ensure_ascii=False, indent=2)
            return path
        except Exception as e:                  # noqa: BLE001
            return '导出失败：%r' % e

    @Slot(int, result=int)
    def echo(self, n: int) -> int:
        """往返基准用（交付文档 §2 判据 #2）。"""
        return n

    @Slot(str, float, result=bool)
    def report(self, kind: str, value: float) -> bool:
        print('[py] report(%s, %s)' % (kind, value)); sys.stdout.flush()
        self.reported.emit(kind, value)
        return True


def _qwebchannel_js() -> str:
    """从 Qt 资源里读 qwebchannel.js —— 不把它 vendored 进仓库（交付文档 §6 决策 8）。

    试点里做一处**防御性补丁**：qwebchannel.js 的 handleResponse 直接调
    `channel.execCallbacks[message.id](...)`（第 139 行），而 Qt/PySide 在某些调用上会回一条
    **没有对应回调**的响应（孤儿响应）→ JS 抛 TypeError 并污染控制台。补丁把它降级为
    `console.warn`，既不吞掉问题、也不让整段脚本断掉。
    """
    f = QFile(':/qtwebchannel/qwebchannel.js')
    if not f.open(QIODevice.ReadOnly):
        return ''
    src = bytes(f.readAll().data()).decode('utf-8', 'replace')
    f.close()
    old = 'channel.execCallbacks[message.id](message.data);'
    # id=0 是 QWebChannel 的**握手响应**（Qt 自己发的，本来就没登记回调）→ 静默；
    # 其它孤儿响应仍然告警（那是真问题，不能吞）。
    new = (('(function () { var __cb = channel.execCallbacks[message.id]; '
            'if (typeof __cb === "function") { __cb(message.data); } '
            'else if (message.id !== 0) { console.warn("[qwc] orphan response id=" + message.id); } })();'))
    if old in src:
        src = src.replace(old, new, 1)
    return src


def _mem_mb(image: str = None, pid: int = None) -> float:
    """用 tasklist 取工作集（MB）。image=进程名 / pid=指定进程，两者取其一。"""
    if pid is not None:
        args = ['tasklist', '/FI', 'PID eq %d' % pid, '/FO', 'CSV', '/NH']
    else:
        args = ['tasklist', '/FI', 'IMAGENAME eq %s' % image, '/FO', 'CSV', '/NH']
    try:
        out = subprocess.check_output(args, text=True, errors='ignore')
    except Exception:                           # noqa: BLE001
        return 0.0
    total = 0.0
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 5 and parts[1].isdigit():
            mem = parts[4].replace(',', '').replace('K', '').strip()
            try:
                total += int(mem) / 1024.0
            except ValueError:
                pass
    return total

DIST_DIR = os.path.join(ROOT, 'webui', 'dist')
INDEX_HTML = os.path.join(DIST_DIR, 'index.html')
EXPORT_DIR = os.path.join(os.environ.get('TEMP', ROOT), 'portpanel_pilot')

# 敏感头的判定（与 api_config_dialog.is_sensitive_header 同口径的本地简化版：
# 试点不去 import 那个模块，避免拉起 aliyun_client 等无关依赖）
_SENSITIVE_HINTS = ('cookie', 'authorization', 'auth', 'token', 'session', 'key', 'secret')


def _mask_header_value(name: str, value: str) -> str:
    """敏感头只回掩码 —— 明文永不进前端（交付文档 §3 凭据红线）。"""
    v = '' if value is None else str(value)
    if any(h in (name or '').lower() for h in _SENSITIVE_HINTS):
        return (v[:6] + '••••') if len(v) > 6 else '••••'
    return v[:120]


def _env_name(value: str) -> str:
    """从 `${ENV:VAR}` 里取出变量名；不是这种写法返回空串。"""
    v = (value or '').strip()
    if v.startswith('${ENV:') and v.endswith('}'):
        return v[len('${ENV:'):-1]
    return ''


def sample_state() -> dict:
    """伪造样本：站点、路径、参数值全部编造，**不含任何真实凭据**。"""
    return {
        'origin': 'sample',
        'config_path': '（伪造样本，未读取真实配置）',
        'notice': '试点数据：全部为编造内容，不含任何真实站点或凭据。',
        'sources': [
            {
                'id': 'demo-gallery',
                'name': '示例图站',
                'base_url': 'https://example.invalid/api',
                'endpoints': [
                    {
                        'path': '/list',
                        'desc': '列表接口：按关键词分页取作品',
                        'params': [
                            {'name': 'keyword', 'kind': 'string', 'value': 'landscape',
                             'note': '搜索关键词，留空表示全部'},
                            {'name': 'page', 'kind': 'int', 'value': '1', 'note': '页码，从 1 开始'},
                            {'name': 'page_size', 'kind': 'int', 'value': '24',
                             'note': '每页条数，站点上限 60'},
                        ],
                    },
                    {
                        'path': '/detail',
                        'desc': '详情接口：取单个作品的下载地址',
                        'params': [
                            {'name': 'id', 'kind': 'string', 'value': '{id}',
                             'note': '作品 ID，由上游列表接口喂入'},
                            {'name': 'quality', 'kind': 'string', 'value': 'original',
                             'note': '画质：original / preview'},
                        ],
                    },
                ],
            },
            {
                'id': 'demo-forum',
                'name': '示例论坛（分页 + 作者）',
                'base_url': 'https://forum.example.invalid',
                'endpoints': [
                    {
                        'path': '/thread/{tid}',
                        'desc': '帖子接口：按作者与页码翻页',
                        'params': [
                            {'name': 'tid', 'kind': 'string', 'value': '{tid}', 'note': '帖子 ID'},
                            {'name': 'author', 'kind': 'string', 'value': '{author}',
                             'note': '作者名，由数据库框逐行提供'},
                            {'name': 'p', 'kind': 'int', 'value': '{page}',
                             'note': '页码，由步进器当内层循环'},
                        ],
                    },
                ],
            },
        ],
        'headers': [
            {'name': 'User-Agent', 'value': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) …',
             'sensitive': False},
            {'name': 'Accept', 'value': 'application/json, text/plain, */*', 'sensitive': False},
            {'name': 'Referer', 'value': 'https://example.invalid/', 'sensitive': False},
            {'name': 'Cookie', 'value': 'session=••••••••', 'sensitive': True,
             'env': 'DEMO_COOKIE', 'env_set': False},
        ],
    }


if __name__ == '__main__':
    sys.exit(main())


