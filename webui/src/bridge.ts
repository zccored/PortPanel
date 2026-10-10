import type {
  AliyunTestResult,
  AliyunTestStart,
  ApiSource,
  CloudState,
  CookieParse,
  DebugResult,
  DebugStart,
  DebugStartRequest,
  EntryBuild,
  EntryParse,
  EntryPayload,
  EntryWriteResult,
  EnvApplyResult,
  EnvReloadResult,
  EnvStatusResult,
  PilotState,
  SetxCommandResult,
} from './types'
import { sampleState } from './mockData'

/** 前端唯一的数据出入口。形态 A = QWebChannel；打包/浏览器开发 = mock（同一套调用签名）。 */
export interface PilotBridge {
  mode: 'qt' | 'mock'
  getState(): Promise<PilotState>
  exportState(state: PilotState): Promise<string>
  echo(n: number): Promise<number>
  report(kind: string, value: number): Promise<void>
  onStateChanged(cb: (s: PilotState) => void): void
  // ---- A3：Cookie 导入 / 环境变量（Python 侧复用 api_config_dialog 的唯一实现，按需懒加载）----
  parseCookie(text: string, baseUrl: string): Promise<CookieParse>
  envStatus(name: string): Promise<EnvStatusResult>
  envApply(name: string, value: string): Promise<EnvApplyResult>
  setxCommand(name: string, value: string): Promise<SetxCommandResult>
  envReload(): Promise<EnvReloadResult>
  // ---- A3-2：入口导入 / 分享（.apientry.json；契约见 api_config_dialog.APIConfigDialog）----
  parseEntry(text: string, existingNames: string[]): Promise<EntryParse>
  buildEntry(source: ApiSource | EntryPayload): Promise<EntryBuild>
  writeEntry(payload: unknown, filename: string): Promise<EntryWriteResult>
  saveEntryAs(payload: unknown): Promise<EntryWriteResult>
  copyText(text: string): Promise<{ ok: boolean; length?: number; error?: string }>
  // ---- A3-3：子端口调试（异步 job + 轮询；真实请求头只在 Python 侧解析）----
  debugEndpoint(req: DebugStartRequest): Promise<DebugStart>
  debugResult(jobId: string): Promise<DebugResult>
  // ---- A3-4：云服务（只读配置 + 阿里云连接测试，同样是 job + 轮询）----
  cloudState(): Promise<CloudState>
  aliyunTest(): Promise<AliyunTestStart>
  aliyunResult(jobId: string): Promise<AliyunTestResult>
}

/** 桥接里返回 JSON 字符串的方法，统一在这里解一次。 */
function pj<T>(p: Promise<string>): Promise<T> {
  return p.then((s) => JSON.parse(s) as T)
}

/** 浏览器 mock 用：从 URL 猜环境变量名（与 Python 侧 suggest_cookie_env_name 同口径）。 */
function guessEnvName(url: string): string {
  const host = (url || '').replace(/^[a-z]+:\/\//i, '').split(/[/?#]/)[0].split('@').pop() || 'api'
  const name = host.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase()
  return `${name || 'API'}_COOKIE`
}


function parseState(raw: unknown): PilotState {
  return typeof raw === 'string' ? (JSON.parse(raw) as PilotState) : (raw as PilotState)
}

/** 把 QWebChannel 的「回调式」方法包成 Promise。 */
function call<T>(obj: Record<string, any>, method: string, ...args: unknown[]): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const fn = obj[method]
    if (typeof fn !== 'function') {
      reject(new Error(`bridge 上没有方法 ${method}`))
      return
    }
    try {
      // 调试日志默认关闭：在页面上设 window.__pilotDebug = true 才输出
      if ((window as any).__pilotDebug) {
        // eslint-disable-next-line no-console
        console.log('[call] ->', method, JSON.stringify(args))
      }
      fn.call(obj, ...args, (res: T) => {
        if ((window as any).__pilotDebug) {
          // eslint-disable-next-line no-console
          console.log('[call] <-', method, String(res).slice(0, 60))
        }
        resolve(res)
      })
    } catch (e) {
      reject(e as Error)
    }
  })
}

/** 浏览器里（无 Qt）用的内存实现：让 `npm run dev` 能独立调 UI。 */
function mockBridge(): PilotBridge {
  let state: PilotState = JSON.parse(JSON.stringify(sampleState))
  const listeners: Array<(s: PilotState) => void> = []
  const emit = () => listeners.forEach((f) => f(JSON.parse(JSON.stringify(state))))
  return {
    mode: 'mock',
    getState: async () => JSON.parse(JSON.stringify(state)),
    exportState: async () => {
      emit()
      return '（mock 模式：没有写文件，仅演示）'
    },
    echo: async (n) => n,
    report: async () => undefined,
    onStateChanged: (cb) => listeners.push(cb),
    // A3 的 mock：只做"够调 UI"的近似 —— 真解析在 Python 侧 parse_cookie_text（认 7 种格式）
    parseCookie: async (text, baseUrl) => {
      const envName = guessEnvName(baseUrl)
      const pairs = (text || '')
        .split(/[;\n\r]+/)
        .map((s) => s.trim().replace(/^cookie:\s*/i, ''))
        .filter((s) => s.includes('='))
      const value = pairs.join('; ')
      const names = pairs.map((p) => p.split('=')[0]!.trim())
      return {
        ok: pairs.length > 0,
        value,
        count: pairs.length,
        length: value.length,
        describe: pairs.length
          ? `${names.length} 个 cookie（${names.slice(0, 6).join('、')}），长度 ${value.length}`
          : '',
        warnings: pairs.length ? ['（mock 近似解析，真解析在 Python 侧）'] : ['（mock）没解析出 cookie'],
        envName,
        envReady: false,
        envText: `（mock）环境变量 ${envName} 的真实状态读不到`,
        placeholder: '${ENV:' + envName + '}',
        importMs: 0,
      }
    },
    envStatus: async (name) => ({
      ok: true, ready: false, persisted: false,
      text: `（mock）环境变量 ${name} 的真实状态读不到`,
    }),
    envApply: async (name, value) => ({
      ok: true, copied: false, length: value.length, persisted: false,
      text: `（mock）已"写入" ${name}（长度 ${value.length}），未动剪贴板`,
    }),
    setxCommand: async (name, value) => ({
      ok: true, cmd: `setx ${name} "${value}"`, too_long: false,
    }),
    envReload: async () => ({ ok: true, updated: [], text: '（mock）没有注册表可读' }),
    // A3-2 的 mock：够调 UI 的近似（真契约在 api_config_dialog.APIConfigDialog）
    parseEntry: async (text, existingNames) => {
      let data: any = null
      try {
        data = JSON.parse(text || 'null')
      } catch {
        return { ok: false, entries: [], renames: [], error: '（mock）JSON 解析失败' }
      }
      const raw: EntryPayload[] = data?.entry
        ? [data.entry]
        : Array.isArray(data?.image_sources)
          ? data.image_sources
          : data && (data.base_url || data.endpoints)
            ? [data]
            : []
      if (!raw.length) {
        return { ok: false, entries: [], renames: [], error: '（mock）不是 API 入口文件' }
      }
      const used = new Set(existingNames)
      const renames: Array<{ from: string; to: string }> = []
      const entries = raw.map((e) => {
        const old = String(e?.name ?? '')
        let name = old || '导入的入口'
        if (used.has(name)) {
          let i = 2
          while (used.has(`${name} (${i})`)) i++
          name = `${name} (${i})`
          renames.push({ from: old || '（空名）', to: name })
        }
        used.add(name)
        return { name, base_url: String(e?.base_url ?? ''), endpoints: e?.endpoints ?? [] }
      })
      return { ok: true, entries, renames, error: '' }
    },
    buildEntry: async (source) => {
      const src = source as EntryPayload
      const safe = String(src?.name ?? 'api_entry').replace(/[\\/:*?"<>|]+/g, '_').trim()
      const payload = {
        kind: 'tianji.api_entry',
        version: 1,
        exported_at: new Date().toLocaleString('sv-SE'),
        entry: { name: src?.name ?? '', base_url: src?.base_url ?? '', endpoints: src?.endpoints ?? [] },
      }
      return {
        ok: true,
        payload,
        filename: `${safe || 'api_entry'}.apientry.json`,
        text: JSON.stringify(payload, null, 2),
      }
    },
    writeEntry: async (_payload, filename) => ({
      ok: true, path: `（mock 未写盘）${filename}`, bytes: 0,
    }),
    saveEntryAs: async () => ({ ok: false, canceled: true }),
    copyText: async (text) => {
      try {
        await navigator.clipboard.writeText(text)
        return { ok: true, length: text.length }
      } catch {
        return { ok: false, error: '（mock）浏览器拒绝剪贴板访问' }
      }
    },
    // A3-3 的 mock：不真的发包，回一段假结果让 UI 能调
    debugEndpoint: async (req) => ({
      ok: true, jobId: 'mock-job', method: req.method, url: req.url,
      hint: '（mock）浏览器里不会真的发包；Qt 壳里走 EndpointDebugWorker',
    }),
    debugResult: async (jobId) => ({
      ok: true,
      state: 'done',
      statusCode: 200,
      via: 'requests',
      elapsedMs: 12,
      body: JSON.stringify({ mock: true, jobId, note: '（mock）没有真实响应' }, null, 2),
      info: [
        '耗时: 12 ms    通道: requests',
        '',
        '--- 诊断 ---',
        '· （mock）没有真实诊断',
        '',
        '--- 实际发出的请求头 (0) ---',
        '',
        '--- 响应头 (0) ---',
      ].join('\n'),
    }),
    // A3-4 的 mock
    cloudState: async () => ({
      ok: true, found: false, configured: false, keyId: '', hasSecret: false,
      endpoint: 'oss-cn-hangzhou.aliyuncs.com', bucket: '', region: 'cn-hangzhou',
      clientAvailable: false,
    }),
    aliyunTest: async () => ({ ok: false, error: '（mock）浏览器里没有真实配置可测' }),
    aliyunResult: async () => ({ ok: false, state: 'missing', success: false, message: '' }),
  }
}

export async function connectBridge(): Promise<PilotBridge> {
  const hasQt = typeof window.qt !== 'undefined' && typeof window.QWebChannel === 'function'
  if (!hasQt) return mockBridge()

  const channel: any = await new Promise((resolve, reject) => {
    try {
      new window.QWebChannel!(window.qt!.webChannelTransport, (ch: any) => resolve(ch))
    } catch (e) {
      reject(e as Error)
    }
  })
  const b = channel?.objects?.bridge
  if (!b) return mockBridge()

  return {
    mode: 'qt',
    getState: () => call<string>(b, 'getState').then(parseState),
    exportState: (s) => call<string>(b, 'exportState', JSON.stringify(s)),
    echo: (n) => call<number>(b, 'echo', n),
    report: (kind, value) => call<boolean>(b, 'report', kind, value).then(() => undefined),
    onStateChanged: (cb) => {
      if (typeof b.stateChanged?.connect === 'function') {
        b.stateChanged.connect((raw: string) => cb(parseState(raw)))
      }
    },
    // A3：这些槽都回 JSON 字符串 → 统一走 pj() 解一次
    parseCookie: (text, baseUrl) => pj<CookieParse>(call<string>(b, 'parseCookie', text, baseUrl)),
    envStatus: (name) => pj<EnvStatusResult>(call<string>(b, 'envStatus', name)),
    envApply: (name, value) => pj<EnvApplyResult>(call<string>(b, 'envApply', name, value)),
    setxCommand: (name, value) => pj<SetxCommandResult>(call<string>(b, 'setxCommand', name, value)),
    envReload: () => pj<EnvReloadResult>(call<string>(b, 'envReload')),
    // A3-2：入口导入 / 分享
    parseEntry: (text, names) =>
      pj<EntryParse>(call<string>(b, 'parseEntry', text, JSON.stringify(names))),
    buildEntry: (source) => pj<EntryBuild>(call<string>(b, 'buildEntry', JSON.stringify(source))),
    writeEntry: (payload, filename) =>
      pj<EntryWriteResult>(call<string>(b, 'writeEntry', JSON.stringify(payload), filename)),
    saveEntryAs: (payload) =>
      pj<EntryWriteResult>(call<string>(b, 'saveEntryAs', JSON.stringify(payload))),
    copyText: (text) =>
      pj<{ ok: boolean; length?: number; error?: string }>(call<string>(b, 'copyText', text)),
    // A3-3：调试（异步 job + 轮询）
    debugEndpoint: (req) => pj<DebugStart>(call<string>(b, 'debugEndpoint', JSON.stringify(req))),
    debugResult: (jobId) => pj<DebugResult>(call<string>(b, 'debugResult', jobId)),
    // A3-4：云服务
    cloudState: () => pj<CloudState>(call<string>(b, 'cloudState')),
    aliyunTest: () => pj<AliyunTestStart>(call<string>(b, 'aliyunTest')),
    aliyunResult: (jobId) => pj<AliyunTestResult>(call<string>(b, 'aliyunResult', jobId)),
  }
}

/** 桥接往返延迟基准：返回中位数（ms）。统计口径 = 交付文档 §2 判据 #2。 */
export async function benchRoundTrip(b: PilotBridge, count = 200): Promise<number> {
  const samples: number[] = []
  for (let i = 0; i < count; i++) {
    const t0 = performance.now()
    await b.echo(i)
    samples.push(performance.now() - t0)
  }
  samples.sort((x, y) => x - y)
  return samples[Math.floor(samples.length / 2)] ?? 0
}
