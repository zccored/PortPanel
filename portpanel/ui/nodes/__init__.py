# -*- coding: utf-8 -*-
"""元素框节点插件包。

目录约定
--------
    nodes/
    ├── spec.py             契约：Protocol / HttpMethod / BodyKind / ParamLocation /
    │                       DataKind / PortSpec / NodeSpec / NodeRegistry
    ├── switch_plugin.py    逻辑开关（signal）
    ├── port_plugin.py      工业端口（modbus / profinet / websocket）
    └── __init__.py         汇总注册表

新增一种元素框的步骤（不需要再改 flow_editor.py）
--------------------------------------------------
    1. 在 `portpanel/ui/` 下写节点类（画法 + to_dict/from_dict）；
    2. 在本目录加一个 `xxx_plugin.py`，用 `REGISTRY.register(NodeSpec(...))`
       填清楚：type_name（.wbt 里的 type）、title、protocol、inputs/outputs
       （每个点声明 DataKind）、factory、to_dict、doc；
    3. 在下面的 import 列表里加一行，让它在启动时被注册；
    4. 老图纸不用改：没有注册的类型仍走 flow_editor 里原来的分支。
"""
from __future__ import annotations

from .spec import (  # noqa: F401  （对外导出，方便插件与测试引用）
    BodyKind,
    DataKind,
    HttpMethod,
    NodeRegistry,
    NodeSpec,
    ParamLocation,
    PortSpec,
    Protocol,
    REGISTRY,
)

# ---- 插件注册（import 即注册）----
from . import api_plugin  # noqa: F401,E402
from . import data_process_plugin  # noqa: F401,E402
from . import csv_plugin  # noqa: F401,E402
from . import stepper_plugin  # noqa: F401,E402
from . import site_parser_plugin  # noqa: F401,E402
from . import class_plugin  # noqa: F401,E402
from . import container_plugin  # noqa: F401,E402
from . import image_plugin  # noqa: F401,E402
from . import switch_plugin  # noqa: F401,E402
from . import port_plugin    # noqa: F401,E402

__all__ = [
    'BodyKind', 'DataKind', 'HttpMethod', 'NodeRegistry', 'NodeSpec',
    'ParamLocation', 'PortSpec', 'Protocol', 'REGISTRY',
]
