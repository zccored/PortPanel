# -*- coding: utf-8 -*-
"""把样板图纸导出成**可公开发布**的版本。

为什么需要这一步：`.wbt` 流程文件里会**原样保存请求头**，而 API 入口的请求头里常常
带着真实 Cookie / session。公开发布（GitHub 仓库、Release 压缩包）前必须抹掉 ——
但**绝不能改用户自己的工作文件**，所以这里是"读原始 → 写副本"。

替换规则：
  * `headers` 里键名命中敏感词（cookie / authorization / token / session / key /
    password / secret）且值非空的 → 换成 `${ENV:<原名>_COOKIE}` 这类占位符，
    保留键名（流程照旧能跑，用户在自己的环境变量里填回真值即可）；
  * 值里已经写着 `${ENV:...}` 的（本身就没泄漏）→ 原样保留；
  * 其它字段一律不动。

用法：
    python tools/make_public_samples.py <源目录> <目标目录>
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

SENSITIVE = re.compile(
    r"cookie|authorization|token|secret|api[-_]?key|session|passwd|password", re.I)
ENV_PLACEHOLDER = re.compile(r"^\$\{ENV:[^}]+\}$")


def _placeholder(key: str) -> str:
    """按请求头键名生成占位符：cookie → ${ENV:COOKIE}，其它 → ${ENV:<大写键>}。"""
    k = re.sub(r"[^A-Za-z0-9]+", "_", str(key)).strip("_").upper() or "VALUE"
    return f"${{ENV:{k}}}"


def sanitize_headers(headers) -> tuple:
    """返回 (新字典, 被替换的键名列表)。"""
    if not isinstance(headers, dict):
        return headers, []
    out, changed = {}, []
    for k, v in headers.items():
        sv = "" if v is None else str(v)
        if SENSITIVE.search(str(k)) and sv.strip() and not ENV_PLACEHOLDER.match(sv.strip()):
            out[k] = _placeholder(k)
            changed.append(str(k))
        else:
            out[k] = v
    return out, changed


def sanitize_flow(data: dict) -> tuple:
    """就地净化一份流程数据，返回 (改动条目数, 明细列表)。"""
    n, detail = 0, []
    for i, nd in enumerate(data.get("nodes", []) or []):
        if not isinstance(nd, dict):
            continue
        nd["headers"], ch = sanitize_headers(nd.get("headers"))
        for k in ch:
            n += 1
            detail.append(f"node[{i}] {k}")
        ref = nd.get("api_ref")
        if isinstance(ref, dict):
            for j, ep in enumerate(ref.get("endpoints") or []):
                if not isinstance(ep, dict):
                    continue
                ep["headers"], ch2 = sanitize_headers(ep.get("headers"))
                for k in ch2:
                    n += 1
                    detail.append(f"node[{i}].api_ref.ep[{j}] {k}")
        # 类框 / 输出历史里也可能夹带，一并扫一遍
        nd["result_json"], ch3 = _scrub_text(nd.get("result_json"))
        if ch3:
            n += 1
            detail.append(f"node[{i}].result_json")
    return n, detail


def _scrub_text(v):
    """对字符串字段做一次保守扫描：命中 session=eyJ… 这类真值就整体置空。"""
    if not isinstance(v, str) or len(v) < 20:
        return v, False
    if re.search(r"session=[A-Za-z0-9._%\-]{16,}", v) or \
       re.search(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", v):
        return "", True
    return v, False


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "data/webtree"
    dst = sys.argv[2] if len(sys.argv) > 2 else "samples"
    os.makedirs(dst, exist_ok=True)
    total = 0
    for fn in sorted(os.listdir(src)):
        if not fn.lower().endswith(".wbt"):
            continue
        raw = io.open(os.path.join(src, fn), encoding="utf-8").read()
        try:
            data = json.loads(raw)
        except Exception as e:                                     # noqa: BLE001
            print(f"  跳过 {fn}（解析失败：{e}）")
            continue
        n, detail = sanitize_flow(data)
        outp = os.path.join(dst, fn)
        io.open(outp, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, indent=2, ensure_ascii=False))
        print(f"  {fn:<34} 净化 {n} 处   {os.path.getsize(outp):>7} B")
        for d in detail[:4]:
            print(f"        · {d}")
        total += n
    print(f"\n合计净化 {total} 处 → {os.path.abspath(dst)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
