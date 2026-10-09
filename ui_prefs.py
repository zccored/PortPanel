# -*- coding: utf-8 -*-
"""本机 UI 偏好（写在运行期目录，不入库）。

现在只有一项 `hover_detail`（画布悬停详情总闸）。**为什么单独立一个模块**，
而不是塞进 `img_server.py`：

- `img_server.py` 正在按既定方向**拆分**（见 `docs/_local/method-split-2026-10-10.md`），
  新的小状态不该再往里加；
- 亮 / 暗主题等后续 UI 偏好也要落在同一处（`docs/_local/backlog-2026-10-10.md` §三），
  一次加一个文件、后面共用；
- 没有 Qt 依赖，Web 侧（阶段 2/3）以后读同一份偏好，不会出现两套设置。

落点：`data/ui_prefs.json`（已加进 `.gitignore`）。坏文件 / 缺失一律按默认值处理，
**永不抛异常** —— 偏好读不出来不该拦住程序启动。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# 默认值集中在这里（新增偏好先加这一处，再在调用处 get 取用）
DEFAULTS = {
    'hover_detail': True,   # 画布悬停详情框（工具栏「👁 悬停详情」）
}


def path() -> str:
    """偏好文件路径：`data/ui_prefs.json`（相对本模块所在目录，与图纸 / 日志同结构）。"""
    return os.path.join(_HERE, 'data', 'ui_prefs.json')


def load() -> dict:
    """读全部偏好；文件缺失 / 损坏 / 不是对象 → 返回 {}（用 get() 兜默认值）。"""
    try:
        with open(path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get(key: str, default=None):
    """取一个偏好：显式 default 优先，其次 DEFAULTS[key]，最后 None。"""
    if default is None:
        default = DEFAULTS.get(key)
    try:
        val = load().get(key, default)
    except Exception:
        return default
    return default if val is None else val


def set(key: str, value) -> bool:
    """写一个偏好（其余键保留）。**原子写**：先写 `.tmp` 再 `os.replace`。

    返回是否写入成功；失败只返回 False、不抛 —— 偏好写不进去不影响使用。
    """
    try:
        data = load()
        data[key] = value
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False
