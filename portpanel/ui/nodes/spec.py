# -*- coding: utf-8 -*-
"""节点插件规范（Node Plugin Spec）——图纸元素框的统一契约。

为什么要有这一层
----------------
过去每个元素框（API / 数据处理 / 数据库 / 容器 / 步进器 / 端口 / 开关 …）
都写在 `flow_editor.py` 里，反序列化是一长串 `if typ == 'XxxNode':`。
节点一多，这个文件就既放不下也改不动。这里把它拆成两部分：

    1. **接口类型（InterfaceSpec）** —— 这个节点“怎么跟外面说话”：
       协议（HTTP/Modbus/PROFINET/WebSocket/本地文件/信号）、方法、body 形态、
       参数落点（path/query/header/body）。
    2. **数据传递（DataSpec）** —— 这个节点“怎么把数据给下游”：
       单值 / 数组（逐项轮询）/ 文件流 / 事件信号 / 无输出。

每个节点插件只要回答这两件事，外加自己的 `to_dict/from_dict`，就能被图纸识别、
被引擎调度，不需要再改 `flow_editor.py`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# =============================================================================
# 一、接口类型：节点对外“说话”的方式
# =============================================================================
class Protocol(str, Enum):
    """节点使用的传输协议。

    | 值          | 说明                                              | 典型节点 |
    |-------------|---------------------------------------------------|----------|
    | `http`      | HTTP/HTTPS 请求（GET/POST/PUT/PATCH/DELETE…）      | APINode  |
    | `modbus`    | Modbus/TCP（MBAP + PDU，FC1/3/4/5/6/15/16）        | PortNode |
    | `profinet`  | PROFINET RT 隧道帧（槽位表）                       | PortNode |
    | `websocket` | RFC6455 文本/二进制帧                              | PortNode |
    | `local`     | 本机进程/文件（如 WBI 签名服务 127.0.0.1:15200）    | APINode  |
    | `virtual`   | 不产生网络请求，只在画板上做数据/控制（数据处理框） | DP/开关  |
    """

    HTTP = 'http'
    MODBUS = 'modbus'
    PROFINET = 'profinet'
    WEBSOCKET = 'websocket'
    LOCAL = 'local'
    VIRTUAL = 'virtual'


class HttpMethod(str, Enum):
    """HTTP 方法。引擎对它们的处理差异：

    - `GET`：无 body；可作为“终端节点”的上游，也可下载文件；
    - `POST/PUT/PATCH`：默认视为**终端发信节点**（`is_terminal()` 为真），不生成输出点；
    - `DELETE`：可选 body，通常无输出；
    - `HEAD/OPTIONS`：只探活/预检，不产生可存盘内容。
    """

    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'
    PATCH = 'PATCH'
    DELETE = 'DELETE'
    HEAD = 'HEAD'
    OPTIONS = 'OPTIONS'


class BodyKind(str, Enum):
    """请求体形态（决定 Content-Type 与编码方式）。

    | 值        | Content-Type             | body 字段写法                     |
    |-----------|--------------------------|-----------------------------------|
    | `none`    | 不发 body                | 空串                              |
    | `json`    | application/json         | `{"k": "v"}`，坏 JSON 会在发送前报错 |
    | `form`    | x-www-form-urlencoded    | `k=v&k2=v2`                       |
    | `raw`     | 由请求头决定             | 原样字节                          |
    | `multipart` | multipart/form-data    | 预留（P1c），当前降级为 raw       |
    """

    NONE = 'none'
    JSON = 'json'
    FORM = 'form'
    RAW = 'raw'
    MULTIPART = 'multipart'


class ParamLocation(str, Enum):
    """参数值落到请求的哪个位置。

    - `path`  ：替换 `url_path` 里的 `{name}`（默认；不在 URL 里出现时只当值载体）；
    - `query` ：拼到查询串；
    - `header`：放进请求头；
    - `body`  ：放进请求体（配合 body_kind）；
    - `none`  ：只做值载体，哪里都不发（内部控制用，如签名器的 mid/offset）。
    """

    PATH = 'path'
    QUERY = 'query'
    HEADER = 'header'
    BODY = 'body'
    NONE = 'none'


class DataKind(str, Enum):
    """节点输出到下游的数据形态。

    这一项决定引擎怎么调度下游：

    - `value` ：单值（一个字符串/数字）→ 下游只跑一次；
    - `array` ：数组 → 下游**逐项轮询**（我们给 feed 节点开了 fanout 就是这个原因）；
    - `file`  ：文件/二进制流 → 走存盘通道（容器框），不进参数替换；
    - `signal`：只有事件（如开关的通断）→ 只影响是否执行，不带数据；
    - `none`  ：无输出（POST/PUT 这类终端节点）。
    """

    VALUE = 'value'
    ARRAY = 'array'
    FILE = 'file'
    SIGNAL = 'signal'
    NONE = 'none'


# =============================================================================
# 二、节点插件契约
# =============================================================================
@dataclass
class PortSpec:
    """一个输入/输出点。

    name  : 点位的标识，连线时用 `end_param` 对应；
    kind  : DataKind，决定这条线能传什么；
    note  : 人看的说明（会显示在悬停提示里）；
    multi : 是否允许接多条线（交换机式节点为 True）。
    """

    name: str
    kind: DataKind = DataKind.VALUE
    note: str = ''
    multi: bool = False


@dataclass
class NodeSpec:
    """节点插件的元信息（注册表里存的就是它）。

    type_name : `.wbt` 里 `type` 字段的值，必须全局唯一；
    title     : 画板上的显示名；
    protocol  : 对外协议（见 Protocol）；
    inputs    : 输入点列表；
    outputs   : 输出点列表；
    factory   : `factory(ndata: dict) -> QGraphicsItem`；
    to_dict   : `to_dict(node) -> dict`（序列化，缺省用节点自带的 `to_dict`）；
    doc       : 一段人话说明（会进开发者文档与悬停提示）。
    """

    type_name: str
    title: str
    protocol: Protocol
    inputs: List[PortSpec] = field(default_factory=list)
    outputs: List[PortSpec] = field(default_factory=list)
    factory: Optional[Callable[[dict], Any]] = None
    to_dict: Optional[Callable[[Any], dict]] = None
    doc: str = ''

    def make(self, ndata: dict) -> Any:
        """按 `.wbt` 节点数据创建控件。"""
        if not self.factory:
            raise NotImplementedError('插件 %s 未提供 factory' % self.type_name)
        return self.factory(ndata or {})

    def serialize(self, node: Any) -> dict:
        """把控件写回 `.wbt` 节点数据。"""
        if self.to_dict:
            return self.to_dict(node)
        fn = getattr(node, 'to_dict', None)
        if fn is None:
            raise NotImplementedError('插件 %s 无法序列化：缺少 to_dict' % self.type_name)
        return fn()


class NodeRegistry:
    """节点插件注册表。

    用法::

        registry.register(NodeSpec(type_name='SwitchNode', ...))
        node = registry.create('SwitchNode', ndata)      # 反序列化
        data = registry.serialize('SwitchNode', node)    # 序列化

    未注册的类型返回 None，调用方可以回落到旧的分支逻辑——
    这也是本次重构能与老图纸共存的原因。
    """

    def __init__(self) -> None:
        self._specs: Dict[str, NodeSpec] = {}

    def register(self, spec: NodeSpec) -> NodeSpec:
        self._specs[spec.type_name] = spec
        return spec

    def get(self, type_name: str) -> Optional[NodeSpec]:
        return self._specs.get(type_name)

    def create(self, type_name: str, ndata: dict):
        """创建控件；只登记了文档、还没有 factory 的插件返回 None（调用方回落老分支）。"""
        spec = self._specs.get(type_name)
        if spec is None or spec.factory is None:
            return None
        return spec.make(ndata)

    def serialize(self, type_name: str, node):
        spec = self._specs.get(type_name)
        return spec.serialize(node) if spec else None

    def names(self) -> List[str]:
        return sorted(self._specs)

    def describe(self) -> List[dict]:
        """给文档/调试用：把注册表导出成可读表格数据。"""
        out = []
        for name in self.names():
            s = self._specs[name]
            out.append({
                'type': s.type_name, 'title': s.title,
                'protocol': s.protocol.value,
                'inputs': [(p.name, p.kind.value) for p in s.inputs],
                'outputs': [(p.name, p.kind.value) for p in s.outputs],
                'doc': s.doc,
            })
        return out


#: 全局注册表：所有插件在 import 时把自己登记进来
REGISTRY = NodeRegistry()
