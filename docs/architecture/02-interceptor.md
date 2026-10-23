# 02 · 拦截器契约

状态：**FROZEN**（2026-10-23）

## 1. 协议

```python
class Interceptor(Protocol):
    name: str
    def before_send(self, req: HttpRequest) -> HttpRequest | None: ...   # None = 中止本次请求
    def after_receive(self, req: HttpRequest, res: HttpResult) -> None: ...
```

挂在 `Transport.send()` **内部**，顺序即注册顺序。

## 2. 硬性约束

1. **默认空链、零开销**：无拦截器时不得产生额外拷贝、不得改变计时；
2. **异常隔离**：拦截器抛异常 → 记日志 + 该拦截器本轮跳过，**不影响主请求**，更不许吞掉主请求的异常；
3. **不可变输入**：`before_send` 返回新的 `HttpRequest`（frozen dataclass），**不修改入参**；
4. **禁止业务插桩**：调试器、敏感信息检测、抓包留痕只能实现本协议，不许在业务代码里各写一遍。

## 3. 内置实现（P2 交付）

| 名称 | 作用 | 写盘 |
|---|---|---|
| `DebuggerInterceptor` | 观察 / 断点 / 改包 | `logs/run_*/requests.jsonl` |
| `SecretScanInterceptor` | 敏感信息检测 | 仅日志 |
| `TraceInterceptor` | 请求耗时/状态码统计 | 仅内存 |

## 4. 验收

1. 空链 1000 次本地请求，耗时差异 < 1%；
2. 注入一个必然抛异常的拦截器，主请求仍成功、日志有记录；
3. 两个拦截器串联时顺序确定（按注册顺序），可观测。

## 5. 风险

| 风险 | 处置 |
|---|---|
| 拦截器改了 body 但没同步 Content-Length | Transport 在 send 前**强制重算** Content-Length |
| 拦截器阻塞（等人改包） | 只允许在 pre-send 阶段阻塞，且必须带超时（默认 120s） |
