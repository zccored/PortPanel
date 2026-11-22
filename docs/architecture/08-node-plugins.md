# 08 · 元素框插件与接口/数据契约

> 状态：**已落地（第一步）**。节点插件层、接口类型枚举、数据传递类型枚举、注册表与文档注释全部就位；
> 老的 8 个大节点目前是「只登记元信息」（`factory=None`），创建仍走 `flow_editor.py` 原分支，
> 因此老图纸行为不变。后续把它们逐个搬进 `portpanel/ui/nodes/` 并补上 `factory` 即可。

## 1. 目录结构

```
portpanel/ui/nodes/
├── spec.py               契约层：Protocol / HttpMethod / BodyKind / ParamLocation /
│                         DataKind / PortSpec / NodeSpec / NodeRegistry
├── __init__.py           汇总注册表（import 即注册）
├── api_plugin.py         接口元素框（仅登记）
├── data_process_plugin.py 数据处理框（仅登记）
├── csv_plugin.py         数据库框（仅登记）
├── stepper_plugin.py     步进器（仅登记）
├── site_parser_plugin.py 站点解析（仅登记）
├── class_plugin.py       类框（仅登记）
├── container_plugin.py   容器框（仅登记）
├── image_plugin.py       图片框（仅登记）
├── port_plugin.py        端口节点（**已接入**：factory 实装）
└── switch_plugin.py      逻辑开关（**已接入**：factory 实装）
```

## 2. 接口类型（Protocol）

| 值 | 说明 | 典型节点 |
|---|---|---|
| `http` | HTTP/HTTPS 请求 | 接口元素框、站点解析 |
| `modbus` | Modbus/TCP（MBAP+PDU，FC1/3/4/5/6/15/16） | 端口节点 |
| `profinet` | PROFINET RT 隧道帧（槽位表） | 端口节点 |
| `websocket` | RFC6455 文本/二进制帧 | 端口节点 |
| `local` | 本机进程/文件（如 WBI 签名 127.0.0.1:15200） | 接口元素框 |
| `virtual` | 不产生网络请求，只做数据/控制 | 数据处理框、开关、容器 |

## 3. HTTP 方法语义（HttpMethod）

| 方法 | 引擎差异 |
|---|---|
| `GET` | 无 body；可下载文件；可作为其它节点的上游 |
| `POST` / `PUT` / `PATCH` | 默认视为**终端发信节点**：`is_terminal()` 为真，不生成输出点 |
| `DELETE` | 可选 body，通常无输出 |
| `HEAD` / `OPTIONS` | 只探活/预检，不产生可存盘内容 |

## 4. 请求体形态（BodyKind）

| 值 | Content-Type | body 写法 | 备注 |
|---|---|---|---|
| `none` | 不发 body | 空串 | 默认 |
| `json` | application/json | `{"k":"v"}` | 坏 JSON 在发送前报错，不会发出去 |
| `form` | x-www-form-urlencoded | `k=v&k2=v2` | |
| `raw` | 由请求头决定 | 原样字节 | |
| `multipart` | multipart/form-data | 预留（P1c） | 当前降级为 raw |

## 5. 参数落点（ParamLocation）

| 值 | 行为 |
|---|---|
| `path` | 替换 `url_path` 里的 `{name}`；不在 URL 中出现时只当**值载体**（如签名器的 mid/offset） |
| `query` | 拼到查询串（自动 urlencode） |
| `header` | 放进请求头 |
| `body` | 放进请求体（配合 body_kind） |
| `none` | 只做值载体，哪里都不发 |

> 值载体是关键设计：签名器要在 `query` 模板里用 `{mid}`/`{offset}`，但 mid 本身不该出现在
> 它自己的 URL 上——用 `location=path` 且不在 `url_path` 里出现即可。

## 6. 数据传递形态（DataKind）

| 值 | 含义 | 引擎行为 |
|---|---|---|
| `value` | 单值 | 下游只跑一次 |
| `array` | 数组 | 下游**逐项轮询**；节点开 `fanout_downstream` 时改为「每次响应就带下游跑一轮」 |
| `file` | 文件/二进制流 | 走容器存盘通道，不参与参数替换 |
| `signal` | 只有事件（通断） | 只影响是否执行，不带数据 |
| `none` | 无输出 | 终端节点（POST/PUT） |

## 7. 插件契约（NodeSpec）

```python
NodeSpec(
    type_name='SwitchNode',      # .wbt 里的 type，全局唯一
    title='逻辑开关',             # 画板显示名
    protocol=Protocol.VIRTUAL,   # 见第 2 节
    inputs=[PortSpec('in', DataKind.SIGNAL, '上游数据')],
    outputs=[PortSpec('out', DataKind.SIGNAL, '放行时继续往下传')],
    factory=_factory,            # ndata -> QGraphicsItem
    to_dict=_serialize,          # node -> dict
    doc='关闭时下游整条支路本轮跳过…',
)
```

注册表 API：

| 方法 | 用途 |
|---|---|
| `REGISTRY.register(spec)` | 注册（import 即生效） |
| `REGISTRY.create(type, ndata)` | 反序列化；**没有 factory 的返回 None**，调用方回落老分支 |
| `REGISTRY.serialize(type, node)` | 序列化 |
| `REGISTRY.describe()` | 导出表格（调试/文档） |

## 8. 新增一种元素框（不需要改 flow_editor.py）

1. 在 `portpanel/ui/` 写节点类（画法 + `to_dict/from_dict`）；
2. 在 `portpanel/ui/nodes/` 加 `xxx_plugin.py`，填 `NodeSpec`（含 inputs/outputs 的 DataKind）；
3. 在 `nodes/__init__.py` 加一行 import；
4. 老图纸无需改动：未注册类型继续走原分支。

## 9. 兼容性红线

- `REGISTRY.create()` 对「只登记、无 factory」的类型返回 `None`，调用方必须能回落；
- 新增枚举值只能**追加**，不得改名或改语义（`.wbt` 里已写入的值要能读回）；
- 任何节点属性新增，序列化与反序列化必须同时给默认值，且默认值等于改动前的行为。
