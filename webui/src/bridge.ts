import type { PilotState } from './types'
import { sampleState } from './mockData'

/** 前端唯一的数据出入口。形态 A = QWebChannel；打包/浏览器开发 = mock（同一套调用签名）。 */
export interface PilotBridge {
  mode: 'qt' | 'mock'
  getState(): Promise<PilotState>
  exportState(state: PilotState): Promise<string>
  echo(n: number): Promise<number>
  report(kind: string, value: number): Promise<void>
  onStateChanged(cb: (s: PilotState) => void): void
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
