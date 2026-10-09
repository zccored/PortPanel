import type { PilotState } from './types'

// 纯伪造样本：站点名、路径、参数值全部是编造的，**不含任何真实凭据**。
// 用途：① 浏览器里 `npm run dev` 直接调 UI（mock 模式）；② 试点演示。
export const sampleState: PilotState = {
  origin: 'sample',
  config_path: '（伪造样本，未读取真实配置）',
  notice: '试点数据：全部为编造内容，不含任何真实站点或凭据。',
  sources: [
    {
      id: 'demo-gallery',
      name: '示例图站',
      base_url: 'https://example.invalid/api',
      endpoints: [
        {
          path: '/list',
          desc: '列表接口：按关键词分页取作品',
          params: [
            { name: 'keyword', kind: 'string', value: 'landscape', note: '搜索关键词，留空表示全部' },
            { name: 'page', kind: 'int', value: '1', note: '页码，从 1 开始' },
            { name: 'page_size', kind: 'int', value: '24', note: '每页条数，站点上限 60' },
          ],
        },
        {
          path: '/detail',
          desc: '详情接口：取单个作品的下载地址',
          params: [
            { name: 'id', kind: 'string', value: '{id}', note: '作品 ID，由上游列表接口喂入' },
            { name: 'quality', kind: 'string', value: 'original', note: '画质：original / preview' },
          ],
        },
      ],
    },
    {
      id: 'demo-forum',
      name: '示例论坛（分页 + 作者）',
      base_url: 'https://forum.example.invalid',
      endpoints: [
        {
          path: '/thread/{tid}',
          desc: '帖子接口：按作者与页码翻页',
          params: [
            { name: 'tid', kind: 'string', value: '{tid}', note: '帖子 ID' },
            { name: 'author', kind: 'string', value: '{author}', note: '作者名，由数据库框逐行提供' },
            { name: 'p', kind: 'int', value: '{page}', note: '页码，由步进器当内层循环' },
          ],
        },
      ],
    },
  ],
  headers: [
    { name: 'User-Agent', value: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) …', sensitive: false },
    { name: 'Accept', value: 'application/json, text/plain, */*', sensitive: false },
    { name: 'Referer', value: 'https://example.invalid/', sensitive: false },
    {
      name: 'Cookie',
      value: 'session=••••••••',
      sensitive: true,
      env: 'DEMO_COOKIE',
      env_set: false,
    },
  ],
}
