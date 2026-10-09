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
