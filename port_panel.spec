# -*- mode: python ; coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· PyInstaller 打包配置
# Copyright (C) 2026 zccored  ·  AGPL-3.0-only（见 LICENSE）
#
# 用法（在项目根目录执行）：
#     python -m PyInstaller --noconfirm --clean port_panel.spec
# 产物：
#     dist_panel/端口画板/端口画板.exe      ← 双击即用（onedir，整目录一起分发/压缩）
#
# 设计取舍：
#   * **onedir 而不是 onefile**：onefile 每次启动都要把 ~200MB 解到临时目录，
#     首启 10 秒起步；而且 PySide6 的插件目录在 onefile 下容易踩坑。onedir 启动快、
#     可增量替换、也方便 image-search 的「切换启动」按路径找到 exe。
#   * **排除用不到的 Qt 模块**：PySide6 装了几十个模块，端口画板只用到
#     Core/Gui/Widgets/Svg/SvgWidgets/Network 这几个；不排的话包会大三四百 MB。
#   * `data/` 一起打进去：`data/node_svg/*.svg` 是悬停提示框的模板（缺了就只剩空白框），
#     `data/webtree/*.wbt` 是随包送的样板图纸，方便新用户直接打开看效果。
# ---------------------------------------------------------------------------
import os

ROOT = os.path.dirname(os.path.abspath(SPEC))

# ---- 只打**纯资源**：SVG 模板 + 字体 ----
#
# ⚠ 明确**不打包任何用户数据**。「图纸 / API 入口内容 / 敏感信息一律不分发」是硬要求：
#
#   · data/webtree/*.wbt     图纸。里面既有 API 入口（api_ref）又有原样存盘的请求头
#                            （真实 Cookie / session），是最敏感的一类；
#   · data/api_config.json   真实图源配置（加密），本来就不能带；
#   · data/manifest/         下载清单 —— 含**用户本机的存储路径、内容标题、文件哈希**；
#   · data/webAPI/           抓取到的名录等（本项目里那份是 8 万多行创作者表）；
#   · data/fonts/…           字体是程序渲染要用的资源，属于"纯资源"，保留。
#
# 曾经踩过的坑：这里原来有一个 `for _extra in ('webAPI', 'manifest')` 的循环，
# 于是**第一次发布的压缩包里真的带上了用户的下载记录与 4.1 MB 创作者名录**。
# 教训：打包清单要**白名单**（只列必须的），不要**黑名单**（排除已知敏感的）——
# 因为 data/ 下面随时会长出新的用户数据目录。下面的写法就是白名单。
datas = []
for _sub in ('node_svg', 'fonts'):
    _p = os.path.join(ROOT, 'data', _sub)
    if os.path.isdir(_p):
        datas.append((_p, os.path.join('data', _sub)))

# 兜底闸门：万一以后有人往上面的白名单里加了用户数据目录，这里直接把构建**挡下来**，
# 而不是等它悄悄进了发布包才发现。
_FORBIDDEN = ('webtree', 'manifest', 'webapi', 'api_config.json')
_bad = [d for d in datas
        if any(f in os.path.basename(str(d[0])).lower() for f in _FORBIDDEN)]
if _bad:
    raise SystemExit(
        "\n❌ 打包被拒绝：datas 里出现了用户数据目录 %r\n"
        "   图纸 / API 入口内容 / 敏感信息一律不分发，请把它们从 datas 里拿掉。\n"
        % ([str(d[0]) for d in _bad],))

hiddenimports = [
    # Qt 侧：SVG 渲染是悬停提示框的命脉，PyInstaller 不会自动追
    'PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'PySide6.QtNetwork',
    'PySide6.QtPrintSupport',
    # 本项目的本地模块（有的在函数里才 import，静态分析追不到）
    'dark_theme', 'logger_manager', 'secure_store', 'api_config_dialog',
    'aliyun_client',
    # 第三方
    'bs4', 'requests', 'cryptography', 'openpyxl', 'websockets', 'aiohttp',
    'oss2', 'curl_cffi',
]

excludes = [
    # 用不到的 Qt 大件（每一项都是几十 MB 起）
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebView', 'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQuickWidgets',
    'PySide6.QtQml', 'PySide6.QtQuickControls2', 'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets', 'PySide6.Qt3DCore', 'PySide6.Qt3DRender',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras', 'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
    'PySide6.QtGraphs', 'PySide6.QtGraphsWidgets', 'PySide6.QtDesigner',
    'PySide6.QtUiTools', 'PySide6.QtTest', 'PySide6.QtHelp', 'PySide6.QtSql',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
    'PySide6.QtPositioning', 'PySide6.QtLocation', 'PySide6.QtSensors',
    'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtRemoteObjects',
    'PySide6.QtScxml', 'PySide6.QtStateMachine', 'PySide6.QtSpatialAudio',
    'PySide6.QtTextToSpeech', 'PySide6.QtWebSockets', 'PySide6.QtWebChannel',
    'PySide6.QtNetworkAuth', 'PySide6.QtHttpServer', 'PySide6.QtAxContainer',
    'PySide6.QtDBus', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    # 与本工具无关的重型库
    'tkinter', 'torch', 'torchvision', 'matplotlib', 'scipy', 'pandas',
    'IPython', 'jupyter', 'notebook', 'pytest', 'setuptools',
    'PyQt5', 'PyQt6', 'PySide2',
]

a = Analysis(
    ['port_panel.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='端口画板',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # GUI 程序：不要黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='端口画板',
)
