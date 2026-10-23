# 01 · Transport 契约

状态：**FROZEN**（2026-10-23）
适用范围：所有出站 HTTP 请求（下载链路暂时除外，见 §4）

## 1. 数据结构

```python
@dataclass(frozen=True)
class HttpRequest:
    method: str                     # 大写：GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS
    url: str                        # 已含 query string 的完整 URL
    headers: dict[str, str]
    body: bytes | None = None
    body_kind: Literal['none','json','form','multipart','raw'] = 'none'
    timeout: tuple[float, float] = (10, 30)   # (connect, read)
    stream: bool = False            # True 时 HttpResult.body 为 None，走 tmp_path

@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes | None
    tmp_path: str | None            # stream=True 时的落盘路径
    via: Literal['requests', 'curl_cffi']
    elapsed_ms: int
    error: str = ""

class Transport(Protocol):
    def send(self, req: HttpRequest, on_progress=None) -> HttpResult: ...
```

## 2. 编码规则（发送方不许自己猜）

| body_kind | Content-Type | 编码 |
|---|---|---|
| none | 不设置 | 不发送 body |
| json | `application/json; charset=utf-8` | `json.dumps(ensure_ascii=False).encode('utf-8')` |
| form | `application/x-www-form-urlencoded` | `urllib.parse.urlencode(doseq=True)` |
| multipart | `multipart/form-data; boundary=…` | 流式生成器，按块喂，不整体进内存 |
| raw | 用户指定，默认 `application/octet-stream` | 原样 bytes，零编码 |

规则：`body is None` 且 `body_kind != 'none'` → 视为空 body，不报错；`body_kind='none'` 但 `body` 非空 → 抛 `ValueError`（契约违例，早暴露）。

## 3. 双适配器

- `RequestsTransport` / `CurlCffiTransport` 实现同一 Protocol；
- 回退策略沿用现状：403/429/503 + 挑战页特征 → 换另一条；
- **语义统一**：超时、重定向、stream 行为必须在两适配器间一致，差异用契约测试兜住。

## 4. 下载链路豁免（重要）

`DownloadWorker` 的 Range 分块 / `.dlpack` 流式 / 跨线程取消**本轮不动**。
只让它调用同一个"请求构建器"（`build_request(...) -> HttpRequest`），发送仍走它自己的链路。

理由：它有一套跨线程取消的既有 hack，整体迁移是回归高发区。

## 5. 验收

1. 同一 `HttpRequest` 分别经两个适配器发出，本地 echo 服务收到的 **method / path / query / headers / body 逐字节一致**（UA 除外）；
2. 5 种 body_kind 各一条用例，字节断言通过；
3. 下载功能回归：Range 分块、断点续传、取消三项手动验证通过。

## 6. 风险

| 风险 | 处置 |
|---|---|
| curl_cffi 的 stream/重定向语义与 requests 不同 | 契约测试双通道各跑一遍 |
| 迁移时误改 DownloadWorker | 只抽构建器，worker 文件级不改 |
