# -*- coding: utf-8 -*-
"""项目根的**唯一**判定处。

## 为什么需要它

拆包（2026-10-11）前，这些模块都平铺在仓库根目录，所以
`os.path.dirname(os.path.abspath(__file__))` **恰好**等于项目根，大家就这么写了。
搬进 `portpanel/...` 之后它变成包内目录 —— 于是 data/logs/temp/handoff 全被读写到
`portpanel/ui/` 或 `portpanel/integration/` 下面，表现为：

* 读不到 `data/api_config.json` -> API 入口列表空、下载跑不起来
* 读不到 `data/node_svg/*.svg`   -> 节点悬浮的 SVG 出不来
* `data/secure_session.json` 找不到 -> 每次都要重输密码；解不开就整片空
* 保存时写进包内那个副本，主窗口再刷新自然还是空

## 为什么不写死层数

源码运行：`<root>/portpanel/ui/x.py`（往上 3 层）
PyInstaller：`<app>/_internal/portpanel/ui/x.py`（往上 3 层，但根是 `_internal`）

两种布局的根都能靠「谁下面有 `data/`」认出来，比数层数稳（数层数在打包后就错了）。

## 用法

    from portpanel.core.paths import project_root
    p = os.path.join(project_root(), 'data', 'node_svg', fname)
"""
from __future__ import annotations

import os

__all__ = ["project_root"]


def project_root() -> str:
    """返回 `data/` 所在的那一层（源码运行 = 仓库根；打包后 = `_internal/`）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "data")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 兜底：<root>/portpanel/core/paths.py -> <root>
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
