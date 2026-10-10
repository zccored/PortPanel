// 试点用的数据类型：只覆盖「实际会被 UI 使用」的那部分配置（对应交付文档 §5 的使用面驱动）
export type ParamKind = 'string' | 'int' | 'float' | 'bool' | 'array'

export interface ApiParam {
  name: string
  kind: ParamKind
  value: string
  /** 注释：会显示在画板的悬停提示里（图源配置的重点字段） */
  note: string
}

export interface ApiEndpoint {
  path: string
  desc: string
  params: ApiParam[]
}

export interface ApiSource {
  id: string
  name: string
  base_url: string
  endpoints: ApiEndpoint[]
}

export interface HeaderItem {
  name: string
  /** 值始终是**已掩码**的（明文永不进前端） */
  value: string
  sensitive: boolean
  /** 若该头来自 ${ENV:VAR}，这里是变量名 */
  env?: string
  /** 该环境变量当前是否已设置 */
  env_set?: boolean
}

export interface PilotState {
  sources: ApiSource[]
  headers: HeaderItem[]
  /** sample = 伪造样本；real-readonly = 真实配置的只读视图 */
  origin: 'sample' | 'real-readonly'
  config_path: string
  notice: string
}

// ==================== A3：Cookie 导入 / 环境变量（桥接契约见 web_config_pilot.py 的 Bridge）====================
/** Cookie 文本解析结果。`value` 只在"导入"这一次流程里用，**界面只显示 describe**（名字 + 长度）。 */
export interface CookieParse {
  ok: boolean
  value: string
  count: number
  length: number
  describe: string
  warnings: string[]
  envName: string
  envReady: boolean
  envText: string
  placeholder: string
  /** portpanel.ui.image_source 的懒加载耗时（首次调用才有值） */
  importMs: number
}

export interface EnvStatusResult {
  ok: boolean
  ready: boolean
  persisted: boolean
  text: string
}

export interface EnvApplyResult {
  ok: boolean
  copied?: boolean
  length?: number
  too_long?: boolean
  persisted?: boolean
  text: string
}

export interface SetxCommandResult {
  ok: boolean
  cmd: string
  too_long: boolean
}

export interface EnvReloadResult {
  ok: boolean
  updated: string[]
  text: string
}

// ==================== A3-2：入口导入 / 分享（.apientry.json）====================
/** 入口文件里的一个入口（**没有 id** —— id 是本前端给列表用的，文件里不带）。 */
export interface EntryPayload {
  name: string
  base_url: string
  endpoints: ApiEndpoint[]
}

export interface EntryParse {
  ok: boolean
  entries: EntryPayload[]
  /** 重名自动改名记录（原文名 → 新名） */
  renames: Array<{ from: string; to: string }>
  error: string
  importMs?: number
}

export interface EntryBuild {
  ok: boolean
  payload?: unknown
  filename?: string
  text?: string
  error?: string
}

export interface EntryWriteResult {
  ok: boolean
  path?: string
  bytes?: number
  /** saveEntryAs 被用户取消（不算错） */
  canceled?: boolean
  error?: string
}

// ==================== A3-3：子端口调试 ====================
/** 调试请求（前端只给"打哪个子端口"，**真实请求头由 Python 侧从配置里取**）。 */
export interface DebugStartRequest {
  method: string
  url: string
  sourceName?: string
  path?: string
  params?: Record<string, string>
  body?: string
}

export interface DebugStart {
  ok: boolean
  jobId?: string
  method?: string
  url?: string
  /** 配置里没找到请求头之类的提示（不算错） */
  hint?: string
  error?: string
}

export interface DebugResult {
  ok: boolean
  state: 'running' | 'done' | 'missing'
  statusCode?: number
  error?: string
  body?: string
  /** 已经渲染好的「请求头 / 响应头 / 耗时 / 诊断」文本（敏感头已打码） */
  info?: string
  hint?: string
  via?: string
  elapsedMs?: number
  attempts?: Array<{ via: string; code: number; error: string }>
  method?: string
  url?: string
  started?: string
}

// ==================== A3-4：云服务（阿里云 / OSS）====================
/** 云服务配置现状（只读；密钥只报"有没有设置"）。 */
export interface CloudState {
  ok: boolean
  found: boolean
  configured: boolean
  /** 只回前 6 位 + 掩码 */
  keyId: string
  hasSecret: boolean
  endpoint: string
  bucket: string
  region: string
  /** 阿里云客户端模块（portpanel.integration.aliyun_client / oss2 + SDK）是否可用 */
  clientAvailable: boolean
  importMs?: number
  error?: string
}

export interface AliyunTestStart {
  ok: boolean
  jobId?: string
  endpoint?: string
  bucket?: string
  error?: string
}

export interface AliyunTestResult {
  ok: boolean
  state: 'running' | 'done' | 'missing'
  success: boolean
  message: string
  error?: string
}




