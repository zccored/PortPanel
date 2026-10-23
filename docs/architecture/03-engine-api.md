# 03 · EngineAPI 与节点边界

状态：**FROZEN**（2026-10-23）

## 1. 六个面（引擎与 UI 的唯一通道）

```python
inputs(node_id)          -> dict          # 上游喂进来的数据
emit(node_id, out)       -> None          # 产出给下游
progress(node_id, **kw)  -> None          # 进度/状态回报
store(node_id, req)      -> HttpResult    # 发请求（内部走 Transport）
manifest_check(digest)   -> bool          # 下载清单校验
run(plan, on_event)      -> None          # 执行整张图纸
```

## 2. 边界规则

- **引擎不 import 任何 Qt**：`grep -r "PySide6" portpanel/engine/ portpanel/core/` 必须为 0；
- 节点对象**不出包**：58 个语义属性由 `portpanel/ui/adapters/NodeAdapter` 持有；
- 引擎只认 `plan`（纯 dict/dataclass），不认 `QGraphicsItem`；
- 新增节点类型必须先有"纯逻辑实现 + fake adapter 测试"，再写 UI。

## 3. 验收

1. `engine/` 下 `import PySide6` 数量 = 0；
2. 用 fake adapter 在**无 Qt 进程**里跑完一次 `run(plan)`（CI 可跑）；
3. 现有 58 个属性在 NodeAdapter 里一一对应，属性表随文档更新。

## 4. 风险

| 风险 | 处置 |
|---|---|
| 一次迁太多导致大面积回归 | 按节点类型分批迁移，每批带 fake adapter 测试 |
| UI 侧偷偷直接调引擎内部函数 | 加 lint：`ui/` 只允许 import `engine.api` |
