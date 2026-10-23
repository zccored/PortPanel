# 05 · 测试基线

状态：**FROZEN**（2026-10-23）
现状：**仓库里一个测试都没有**（本轮新建）

## 1. 目录

```
tests/
├─ conftest.py
├─ echo_server.py          # http.server 绑 127.0.0.1:0，禁外网
├─ fixtures/
│   ├─ sample_v1.wbt       # 脱敏黄金样本（结构与假值）
│   ├─ request.json        # 请求黄金样本
│   └─ response.json
└─ test_transport_contract.py
```

## 2. 固定入口（CI / 本机都跑这 4 条）

```bash
pytest -q
python -X utf8 tools/check_no_secrets.py --all
python design/tools/gen_tokens.py --check && python design/tools/gen_icons.py --check
python -m compileall -q portpanel
```

## 3. 铁律

- 测试进程**不得发外网请求**（用 socket 断言拦截）；
- 黄金样本必须**脱敏**：无真实 url / 无 cookie / 无 token / 无本机路径；
- 新增节点/拦截器/body_kind **必须**同时加用例，否则不予合并。

## 4. 验收

4 条命令全绿，且 `pytest -q` 在无网络环境（断网）下同样全绿。

## 5. 风险

| 风险 | 处置 |
|---|---|
| 测试里硬编码本机路径 | fixtures 全用相对路径 + `tmp_path` |
| 黄金样本带真实数据 | 加进 `check_no_secrets` 扫描范围 |
