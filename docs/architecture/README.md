# PortPanel 架构契约（P0 冻结件）

本目录是 P1 之后所有改动的**前置契约**。7 份文档冻结前，不写实现代码。

| # | 文件 | 冻结内容 | 违反后果 |
|---|---|---|---|
| 1 | [01-transport.md](01-transport.md) | HttpRequest / HttpResult / Transport.send | 两条链路继续漂移 |
| 2 | [02-interceptor.md](02-interceptor.md) | before_send / after_receive | 调试器、检测、留痕各插各的桩 |
| 3 | [03-engine-api.md](03-engine-api.md) | EngineAPI 六个面 + NodeAdapter 边界 | 引擎被 Qt 绑死，无法测试 |
| 4 | [04-wbt-versioning.md](04-wbt-versioning.md) | .wbt version 1→2、只读保护 | **用户图纸永久丢节点** |
| 5 | [05-test-baseline.md](05-test-baseline.md) | pytest + 本地 echo + 4 条固定命令 | 回归无感知 |
| 6 | [06-directory.md](06-directory.md) | core / engine / ui.adapters 目录边界 | flow_editor.py 继续膨胀 |
| 7 | [07-packaging.md](07-packaging.md) | datas 只留 node_svg + 发版三步 | **使用者数据进发行包** |

基线：`main@e71df8a`（31 commits，AGPL-3.0，Py3.12 + PySide6 6.9.1）

## 冻结规则

- 本目录文件一旦标记 `状态: FROZEN`，改动必须走"提案 → 评审 → 更新版"三步；
- 代码里引用契约要写文件名 + 小节号（例：`见 01-transport.md §2`）；
- 契约与实现冲突时，**以契约为准**，改实现或改契约，不允许"实现就这样"。
