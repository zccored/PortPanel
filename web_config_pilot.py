# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· 「图源配置」Web 化试点（阶段 0）
# Copyright (C) 2026 zccored  ·  AGPL-3.0-only（见 LICENSE）
#
# 零侵入：**不修改** port_panel.py / portpanel/ui/flow_editor.py / portpanel/ui/image_source.py，
# 独立启动：python web_config_pilot.py            看界面（正常窗口）
#           python web_config_pilot.py --selftest 自测：量 4 个数后自动退出
#           python web_config_pilot.py --real     尝试只读真实配置（默认用伪造样本）
#
# 判据（交付文档 §2）：① 首屏耗时 ② 桥接往返延迟 ③ 内存增量 ④ 体感分（人工）
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import os
import re
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

    零侵入：只**读用** portpanel/ui/flow_editor.py 与 portpanel/ui/image_source.py 的既有实现，不改它们。
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

    只读红线：走 portpanel.integration.secure_store.get_plaintext()（机器绑定密钥解密），**绝不写回** data/api_config.json。
    本机当前没有 data/api_config.json，所以这条路径属于「best-effort」，schema 以实际配置为准。
    """
    cfg = os.path.join(ROOT, 'data', 'api_config.json')
    if not os.path.exists(cfg):
        return None, '（未找到 data/api_config.json → 使用伪造样本）'
    try:
        from portpanel.integration import secure_store  # 延迟导入：只在真要看真实配置时才拉起
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


# ==================== 按需懒加载 ====================
# 这几个功能（Cookie 导入 / 导入分享 / 调试请求 / 云服务）要用 portpanel/ui/image_source.py 的**唯一实现**，
# 而那个模块顶部就 import requests / bs4 / cryptography / portpanel.integration.aliyun_client（冷启动约 6.5 s）——
# 试点判据 ①（冷启动）会直接废掉。所以：**第一次用到才 import**，
# 导入耗时记在 _api_import_ms 里并回给前端 —— 这个数正是阶段 2 的 EngineAPI 最关心的。
_api_mod = None
_api_import_ms = 0.0
# 调试任务表（A3-3）：`debugEndpoint` 起线程后把结果落到这里，前端用 debugResult(jobId) 轮询。
_JOBS: dict = {}


def _api():
    """懒加载并返回 image_source 模块（只 import，不建任何窗口、不碰配置）。"""
    global _api_mod, _api_import_ms
    if _api_mod is None:
        t0 = time.perf_counter()
        from portpanel.ui import image_source as m          # noqa: PLC0415 —— 故意延迟导入
        _api_import_ms = (time.perf_counter() - t0) * 1000.0
        print('[py] image_source 懒加载：%.0f ms' % _api_import_ms)
        sys.stdout.flush()
        _api_mod = m
    return _api_mod


def _import_ms() -> float:
    return round(_api_import_ms, 1)


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

    # ==================== Cookie 导入（A3-1）====================
    # 复用 portpanel.ui.image_source 的 parse_cookie_text / describe_cookie / suggest_cookie_env_name /
    # env_status_text / env_is_persisted / reload_env_from_system —— **不重写第二套**。
    #
    # 明文边界（与交付文档 §3 凭据红线对齐）：
    #   · 用户粘进页面的原文本来就在页面里 → parseCookie 回 `value`（规范化后的 cookie 串），
    #     是为了让前端不必重写"7 种格式"的解析；**界面只渲染 describe（名字 + 长度），不回显明文**；
    #   · 存储态的明文**永不**回前端：getState() 照旧只给掩码值；
    #   · envApply 与 Qt 侧同口径：只写进程内 os.environ + 把 setx 抄到剪贴板，
    #     **明文不回前端**（要显示命令得显式点一下，见 setxCommand）。
    @Slot(str, str, result=str)
    def parseCookie(self, text: str, base_url: str) -> str:
        """解析粘进来的 cookie 文本 → JSON（条数 / 摘要 / 环境变量提示）。"""
        try:
            m = _api()
            value, count, warns = m.parse_cookie_text(text or '')
            env_name = m.suggest_cookie_env_name(base_url or '')
            out = {
                'ok': bool(value),
                'value': value,
                'count': count,
                'length': len(value or ''),
                'describe': m.describe_cookie(value) if value else '',
                'warnings': list(warns or []),
                'envName': env_name,
                'envReady': bool(os.environ.get(env_name)),
                'envText': m.env_status_text(env_name),
                'placeholder': '${ENV:%s}' % env_name,
                'importMs': _import_ms(),
            }
        except Exception as e:                      # noqa: BLE001
            out = {'ok': False, 'value': '', 'count': 0, 'length': 0, 'describe': '',
                   'warnings': ['解析失败：%r' % e], 'envName': '', 'envReady': False,
                   'envText': '', 'placeholder': '', 'importMs': _import_ms()}
        return json.dumps(out, ensure_ascii=False)

    @Slot(str, result=str)
    def envStatus(self, name: str) -> str:
        """环境变量状态（只读）：当前进程读不读得到 + 有没有写进系统（注册表）。"""
        try:
            m = _api()
            out = {'ok': True, 'ready': bool(os.environ.get(str(name or ''))),
                   'persisted': bool(m.env_is_persisted(str(name or ''))),
                   'text': m.env_status_text(str(name or '')), 'importMs': _import_ms()}
        except Exception as e:                      # noqa: BLE001
            out = {'ok': False, 'ready': False, 'persisted': False,
                   'text': '状态读取失败：%r' % e}
        return json.dumps(out, ensure_ascii=False)

    @Slot(str, str, result=str)
    def envApply(self, name: str, value: str) -> str:
        """把值写进**当前进程** + 把 setx 命令放进剪贴板（**明文不回前端**）。

        与 Qt 侧 `CookieImportDialog._apply_env_now` 同口径：只写进程内 `os.environ`，
        不替用户写注册表（往 HKCU\\Environment 写变量是杀软眼里的持久化行为）。
        """
        n = str(name or '').strip()
        if not n or not value:
            return json.dumps({'ok': False, 'text': '变量名或 cookie 值为空'},
                              ensure_ascii=False)
        try:
            m = _api()
            os.environ[n] = value
            cmd = 'setx %s "%s"' % (n, value)
            copied = False
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(cmd)
                copied = True
            except Exception:                       # noqa: BLE001
                pass
            tail = ('setx 命令已复制到剪贴板。' if copied
                    else '剪贴板不可用 —— 可点「显示 setx 命令」手动复制。')
            out = {'ok': True, 'copied': copied, 'length': len(value),
                   'too_long': len(cmd) > 1024,
                   'persisted': bool(m.env_is_persisted(n)),
                   'text': '已写入本程序（%s，长度 %d）。%s' % (n, len(value), tail),
                   'importMs': _import_ms()}
        except Exception as e:                      # noqa: BLE001
            out = {'ok': False, 'text': '写入失败：%r' % e}
        return json.dumps(out, ensure_ascii=False)

    @Slot(str, str, result=str)
    def setxCommand(self, name: str, value: str) -> str:
        """**显式点按钮才调用**：回一条 setx 命令（含明文）供手动复制。

        为什么要单独一个槽：默认路径（envApply）已经把命令抄进剪贴板、不回明文；
        只有用户主动要求"显示给我看"时才把明文交到页面 —— 边界清楚、可审计。
        """
        n = str(name or '').strip()
        cmd = 'setx %s "%s"' % (n, value) if (n and value) else ''
        return json.dumps({'ok': bool(cmd), 'cmd': cmd, 'too_long': len(cmd) > 1024},
                          ensure_ascii=False)

    @Slot(result=str)
    def envReload(self) -> str:
        """从注册表 `HKCU\\Environment` 把 setx 过的变量读回当前进程（免重启）。"""
        try:
            m = _api()
            updated, msg = m.reload_env_from_system()
            out = {'ok': True, 'updated': list(updated or []), 'text': msg,
                   'importMs': _import_ms()}
        except Exception as e:                      # noqa: BLE001
            out = {'ok': False, 'updated': [], 'text': '回读失败：%r' % e}
        return json.dumps(out, ensure_ascii=False)

    # ==================== 入口导入 / 分享（A3-2）====================
    # 契约的唯一实现在 portpanel.ui.image_source.APIConfigDialog：
    #   导出：{"kind":"tianji.api_entry","version":1,"exported_at":…,"entry":{name,base_url,endpoints}}
    #   导入兼容三种形态：① 上面的 ② 裸入口 {name,base_url,endpoints} ③ 整包配置 {image_sources:[…]}
    #   重名自动加 " (2)" / " (3)"…
    # 复用方式：`_extract_entries_from_file` / `_unique_entry_name` 是 **staticmethod**，直接调；
    # `_entry_file_payload` 虽带 self 但**只拼 kind/version/exported_at、不碰实例状态** →
    # 传 None 复用（在试点里再写一份格式定义最容易漂移，坚决不干）。
    @Slot(str, str, result=str)
    def parseEntry(self, text: str, existing_json: str) -> str:
        """解析导入文本（入口文件 / 裸入口 / 整包配置）→ 入口列表 + 重命名提示。"""
        try:
            cls = _api().APIConfigDialog
            data = json.loads(text or 'null')
            entries = cls._extract_entries_from_file(data)
            if not entries:
                return json.dumps(
                    {'ok': False, 'entries': [], 'renames': [],
                     'error': '这不是 API 入口文件（缺 entry / image_sources / base_url）'},
                    ensure_ascii=False)
            try:
                existing = set(json.loads(existing_json or '[]') or [])
            except Exception:                       # noqa: BLE001
                existing = set()
            renames = []
            for e in entries:
                old = str(e.get('name') or '')
                new = cls._unique_entry_name(old, existing)
                existing.add(new)
                e['name'] = new
                if new != old:
                    renames.append({'from': old or '（空名）', 'to': new})
            return json.dumps({'ok': True, 'entries': entries, 'renames': renames,
                               'error': '', 'importMs': _import_ms()}, ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'entries': [], 'renames': [],
                               'error': '解析失败：%r' % e}, ensure_ascii=False)

    @Slot(str, result=str)
    def buildEntry(self, source_json: str) -> str:
        """把一个入口打包成可分享的 `.apientry.json` 内容 + 建议文件名 + 可复制文本。"""
        try:
            cls = _api().APIConfigDialog
            src = json.loads(source_json or '{}')
            payload = cls._entry_file_payload(None, src)
            safe = re.sub(r'[\\/:*?"<>|]+', '_', str(src.get('name') or 'api_entry')).strip()
            return json.dumps({'ok': True, 'payload': payload,
                               'filename': (safe or 'api_entry') + cls.ENTRY_FILE_SUFFIX,
                               'text': json.dumps(payload, ensure_ascii=False, indent=2),
                               'importMs': _import_ms()}, ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'error': '打包失败：%r' % e}, ensure_ascii=False)

    @Slot(str, str, result=str)
    def writeEntry(self, payload_json: str, filename: str) -> str:
        """把入口文件写进试点临时目录（`%TEMP%/portpanel_pilot/`），返回落盘路径。

        为什么默认不弹原生「另存为」：试点要能被脚本**无人值守**地验证
        （本机私有检查脚本；要挑目录就走 saveEntryAs）。
        """
        try:
            payload = json.loads(payload_json or '{}')
            suffix = _api().APIConfigDialog.ENTRY_FILE_SUFFIX
            name = os.path.basename(str(filename or '')) or ('api_entry' + suffix)
            os.makedirs(EXPORT_DIR, exist_ok=True)
            path = os.path.join(EXPORT_DIR, name)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return json.dumps({'ok': True, 'path': path, 'bytes': os.path.getsize(path)},
                              ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'error': '写入失败：%r' % e}, ensure_ascii=False)

    @Slot(str, result=str)
    def saveEntryAs(self, payload_json: str) -> str:
        """原生「另存为」对话框（与 Qt 侧 `share_image_source` 同口径）；取消不算错。"""
        try:
            from PySide6.QtWidgets import QFileDialog
            suffix = _api().APIConfigDialog.ENTRY_FILE_SUFFIX
            default = os.path.join(os.path.expanduser('~'), 'api_entry' + suffix)
            path, _f = QFileDialog.getSaveFileName(
                None, '分享 API 入口', default,
                'API 入口文件 (*%s);;JSON 文件 (*.json)' % suffix)
            if not path:
                return json.dumps({'ok': False, 'canceled': True}, ensure_ascii=False)
            if not path.lower().endswith('.json'):
                path += suffix
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(json.loads(payload_json or '{}'), f, indent=2, ensure_ascii=False)
            return json.dumps({'ok': True, 'path': path, 'bytes': os.path.getsize(path)},
                              ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'error': '保存失败：%r' % e}, ensure_ascii=False)

    @Slot(str, result=str)
    def copyText(self, text: str) -> str:
        """把文本放进系统剪贴板（`file://` 下前端拿不到系统剪贴板 → 走桥，与 Qt 侧同口径）。"""
        try:
            from PySide6.QtWidgets import QApplication
            s = str(text or '')
            QApplication.clipboard().setText(s)
            return json.dumps({'ok': True, 'length': len(s)}, ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'error': '%r' % e}, ensure_ascii=False)

    # ==================== 子端口调试（A3-3）====================
    # 与 Qt 侧 `_debug_endpoint_by_index` 同链路：EndpointDebugWorker（requests → 被风控/403 时
    # 降级 curl_cffi impersonate=chrome124）+ `_format_debug_info` 渲染，**不重写**。
    # 明文边界：真实请求头**只在 Python 侧**解析（前端只给 sourceName + path）；
    #   回给前端的 `info` 是 `_format_debug_info` 的成品 —— 敏感头已由 mask_header_value 打码。
    # 异步原因：一次调试最长 30 s 超时，不能阻塞 GUI 线程 → 返回 jobId，前端用 debugResult 轮询。
    @Slot(str, result=str)
    def debugEndpoint(self, payload_json: str) -> str:
        """启动一次调试 → `{jobId}`；结果用 `debugResult(jobId)` 取。"""
        try:
            p = json.loads(payload_json or '{}')
            m = _api()
            method = str(p.get('method') or 'GET').upper()
            url = str(p.get('url') or '')
            headers = p.get('headers')
            hint = ''
            if not isinstance(headers, dict):
                headers = {}
                cfg_headers = self._real_headers(p.get('sourceName'), p.get('path'))
                if cfg_headers is None:
                    hint = '（没有找到真实配置 → 本次不带配置里的请求头；只读动作，不会写回）'
                else:
                    headers = cfg_headers
                    if not headers:
                        hint = '（配置里这个子端口没有请求头）'
            if not url:
                return json.dumps({'ok': False, 'error': '缺少 url'}, ensure_ascii=False)
            body = p.get('body') or ''
            job = {'state': 'running', 'statusCode': 0, 'error': '', 'body': '',
                   'info': '', 'hint': hint, 'method': method, 'url': url,
                   'started': time.strftime('%H:%M:%S')}
            job_id = 'job-%d' % (len(_JOBS) + int(time.time() * 1000) % 100000)
            _JOBS[job_id] = job

            worker = m.EndpointDebugWorker(method, url, headers,
                                           p.get('params') or {}, body, None)
            worker.debug_completed.connect(
                lambda resp, code, err, meta, j=job: self._on_debug_done(j, resp, code, err, meta))
            job['worker'] = worker            # 保住引用（QThread 被 GC 会崩）
            worker.start()
            return json.dumps({'ok': True, 'jobId': job_id, 'method': method, 'url': url,
                               'hint': hint}, ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'error': '启动调试失败：%r' % e}, ensure_ascii=False)

    @staticmethod
    def _on_debug_done(job: dict, resp, code, err, meta) -> None:
        """worker 回来时把结果落到 job 里（GUI 线程执行：信号是跨线程队列投递的）。"""
        try:
            m = _api()
            meta = dict(meta or {})
            meta['status_code'] = code
            job['state'] = 'done'
            job['statusCode'] = code
            job['error'] = err or ''
            job['body'] = (resp or '')[:200000]
            job['info'] = m.APIConfigDialog._format_debug_info(meta)
            job['via'] = 'curl_cffi' if meta.get('used_curl') else 'requests'
            job['elapsedMs'] = meta.get('elapsed_ms', 0)
            job['attempts'] = meta.get('attempts') or []
        except Exception as e:                      # noqa: BLE001
            job['state'] = 'done'
            job['error'] = '结果整理失败：%r' % e
        finally:
            job.pop('worker', None)

    @Slot(str, result=str)
    def debugResult(self, job_id: str) -> str:
        """取一次调试的结果（`state=running` 时前端继续轮询）。"""
        job = _JOBS.get(str(job_id or ''))
        if not job:
            return json.dumps({'ok': False, 'state': 'missing', 'error': '没有这个 jobId'},
                              ensure_ascii=False)
        out = {k: v for k, v in job.items() if k != 'worker'}
        out['ok'] = True
        return json.dumps(out, ensure_ascii=False)

    def _real_config(self):
        """**只读**读真实配置（返回 dict；读不到返回 None）。

        与 `try_load_real_state` 同一条路：`portpanel.integration.secure_store.get_plaintext()`（机器绑定密钥解密），
        **绝不写回** data/api_config.json。云服务与请求头都从这里取，避免两处各写一份读取逻辑。
        """
        try:
            cfg = os.path.join(ROOT, 'data', 'api_config.json')
            if not os.path.exists(cfg):
                return None
            from portpanel.integration import secure_store  # 延迟导入：只在真要读配置时拉起
            raw = secure_store.get_plaintext(cfg)
            if isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw).decode('utf-8', 'replace')
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:                              # noqa: BLE001
            return None

    def _real_headers(self, source_name, path):
        """从**真实配置**里取某个入口/子端口的请求头（只读；取不到返回 None）。

        与 Qt 侧一致：请求头挂在**子端口**上（`endpoint['headers']`）。
        """
        data = self._real_config()
        if data is None:
            return None
        for src in (data.get('image_sources') or []):
            if str(src.get('name') or '') != str(source_name or ''):
                continue
            for ep in (src.get('endpoints') or []):
                if str(ep.get('path') or '') == str(path or ''):
                    h = ep.get('headers')
                    return dict(h) if isinstance(h, dict) else {}
        return {}

    # ==================== 云服务（A3-4）====================
    # 与 Qt 侧「阿里云配置」页同口径：配置键 `aliyun_access_key_id` / `aliyun_access_key_secret` /
    # `aliyun_endpoint` / `aliyun_bucket` / `aliyun_region`（与 image_sources 存在同一个 config 里）。
    # 测试链路复用 `AliyunTestWorker`（initialize_aliyun_services → test_aliyun_connection），不重写。
    # 明文边界：AccessKeySecret **不回前端**（只回"有没有设置"），KeyId 只回前 6 位 + 掩码。
    @Slot(result=str)
    def cloudState(self) -> str:
        """只读：云服务配置现状（不回密钥明文）。"""
        try:
            m = _api()
            data = self._real_config()
            cfg = data or {}
            key_id = str(cfg.get('aliyun_access_key_id') or '')
            secret = str(cfg.get('aliyun_access_key_secret') or '')
            out = {
                'ok': True,
                'found': data is not None,
                'configured': bool(key_id and secret),
                'keyId': (key_id[:6] + '••••') if key_id else '',
                'hasSecret': bool(secret),
                'endpoint': str(cfg.get('aliyun_endpoint') or 'oss-cn-hangzhou.aliyuncs.com'),
                'bucket': str(cfg.get('aliyun_bucket') or ''),
                'region': str(cfg.get('aliyun_region') or 'cn-hangzhou'),
                'clientAvailable': bool(getattr(m, 'HAS_ALIYUN', False)),
                'importMs': _import_ms(),
            }
        except Exception as e:                      # noqa: BLE001
            out = {'ok': False, 'error': '读取云服务配置失败：%r' % e}
        return json.dumps(out, ensure_ascii=False)

    @Slot(result=str)
    def aliyunTest(self) -> str:
        """起一次阿里云连接测试（异步 job；结果用 aliyunResult 轮询）。"""
        cfg = self._real_config()
        if cfg is None:
            return json.dumps({'ok': False, 'error': '没有找到 data/api_config.json'
                               '（真实配置）→ 无法测试；这是只读动作，不会写回'}, ensure_ascii=False)
        need = {
            'access_key_id': str(cfg.get('aliyun_access_key_id') or ''),
            'access_key_secret': str(cfg.get('aliyun_access_key_secret') or ''),
            'endpoint': str(cfg.get('aliyun_endpoint') or ''),
            'bucket_name': str(cfg.get('aliyun_bucket') or ''),
            'region_id': str(cfg.get('aliyun_region') or ''),
        }
        missing = [k for k, v in need.items() if not v]
        if missing:
            return json.dumps({'ok': False, 'error': '配置不完整，缺少：%s' % '、'.join(missing)},
                              ensure_ascii=False)
        try:
            m = _api()
            job = {'state': 'running', 'kind': 'aliyun', 'success': False, 'message': ''}
            job_id = 'aliyun-%d' % (len(_JOBS) + int(time.time() * 1000) % 100000)
            _JOBS[job_id] = job
            worker = m.AliyunTestWorker(need, None)
            worker.test_completed.connect(lambda res, j=job: self._on_aliyun_done(j, res))
            job['worker'] = worker            # 保住引用（QThread 被 GC 会崩）
            worker.start()
            return json.dumps({'ok': True, 'jobId': job_id, 'endpoint': need['endpoint'],
                               'bucket': need['bucket_name']}, ensure_ascii=False)
        except Exception as e:                      # noqa: BLE001
            return json.dumps({'ok': False, 'error': '启动测试失败：%r' % e}, ensure_ascii=False)

    @staticmethod
    def _on_aliyun_done(job: dict, res) -> None:
        """测试线程回来（GUI 线程执行）：只留 success / message，别的都不落。"""
        try:
            r = res if isinstance(res, dict) else {}
            job['state'] = 'done'
            job['success'] = bool(r.get('success'))
            job['message'] = str(r.get('message') or '')
        except Exception as e:                      # noqa: BLE001
            job['state'] = 'done'
            job['message'] = '结果整理失败：%r' % e
        finally:
            job.pop('worker', None)

    @Slot(str, result=str)
    def aliyunResult(self, job_id: str) -> str:
        """取一次云服务测试结果（`state=running` 时前端继续轮询）。"""
        job = _JOBS.get(str(job_id or ''))
        if not job:
            return json.dumps({'ok': False, 'state': 'missing', 'error': '没有这个 jobId'},
                              ensure_ascii=False)
        out = {'ok': True, 'state': job.get('state'), 'success': bool(job.get('success')),
               'message': str(job.get('message') or '')}
        return json.dumps(out, ensure_ascii=False)


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

# 敏感头的判定（与 portpanel.ui.image_source.is_sensitive_header 同口径的本地简化版：
# 试点不去 import 那个模块，避免拉起 portpanel.integration.aliyun_client 等无关依赖）
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


