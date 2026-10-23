# 06 · 目录结构

状态：**FROZEN**（2026-10-23）

## 1. 目标结构

```
portpanel/
├─ core/            # 无 Qt：模型、参数解析、location 解析
│   ├─ params.py        # 新：location ∈ {path,query,header,body}
│   └─ wbt.py           # 新：.wbt 读写 + 版本策略（见 04）
├─ engine/
│   ├─ transport.py     # 新：HttpRequest / Transport / adapter
│   ├─ intercept.py     # 新：拦截器链 + 敏感信息检测
│   ├─ nodes/           # 新：纯逻辑节点（port_listener.py …）
│   └─ run_loop.py      # 后续从 flow_editor 外迁
├─ ui/
│   ├─ flow_editor.py   # 现有画板主体（**只减不增**）
│   └─ adapters/        # 新：NodeAdapter（持有 58 个属性）
└─ integration/    # 主题 / 日志 / secure_store / aliyun
```

## 2. 硬规则

- `flow_editor.py` 在 P1–P3 期间**行数只减不增**（新增逻辑以 import 调用）；
- `core/` 与 `engine/` **禁止 import PySide6**；
- 新代码一律不进 `flow_editor.py`，违者回退。

## 3. 验收

1. 每个 PR 统计 `flow_editor.py` 行数，必须 ≤ 基线；
2. `grep -rn "PySide6" portpanel/core portpanel/engine` = 0；
3. 目录树与本文件一致（新增目录需更新本文件）。
