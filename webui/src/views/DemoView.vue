<script setup lang="ts">
import { ICONS } from '../assets/icons'
import { EmptyState, Field, Icon, Panel, Tag, Toolbar } from '../components/ui'

// 组件库画廊：地址栏带 `#demo` 时由 main.ts 挂这个视图（Qt 壳加载不带 hash → 仍是试点窗口）。
const iconNames = Object.keys(ICONS).sort()
const tones = ['default', 'accent', 'warn', 'ok', 'danger'] as const
</script>

<template>
  <div class="demo">
    <Toolbar>
      <template #lead>
        <strong>组件库</strong>
        <Tag tone="accent">雏形</Tag>
        <span class="muted">src/components/ui</span>
      </template>
      <button class="btn btn--ghost"><Icon name="refresh" /> 次要</button>
      <button class="btn btn--primary"><Icon name="check" /> 主要</button>
      <button class="btn btn--danger"><Icon name="trash" /> 危险</button>
      <button class="btn" disabled><Icon name="stop" /> 禁用</button>
    </Toolbar>

    <main class="demo__body">
      <Panel title="Panel 面板" meta="head / hint / body / footer">
        <template #actions><Tag>只读</Tag></template>
        <template #hint>
          面板统一提供表头、说明行、滚动区与页脚；body **不加内边距** —— 密度由内容自己决定。
        </template>
        <div class="demo__pad">
          <p class="demo__p muted">正文区域（默认插槽）。页脚是可选的 <code>#footer</code>。</p>
          <EmptyState icon="package" text="EmptyState：没有内容时统一用它" hint="图标 + 主文案 + 可选说明" />
        </div>
        <template #footer>页脚（#footer）：放统计 / 提示都行</template>
      </Panel>

      <Panel title="Tag 标签" meta="5 种语义色调">
        <div class="demo__pad demo__row">
          <Tag v-for="t in tones" :key="t" :tone="t">{{ t }}</Tag>
        </div>
      </Panel>

      <Panel title="Field 字段" meta="label + 控件 + 可选说明">
        <div class="demo__pad demo__fields">
          <Field label="名称" hint="默认不拉伸">
            <input class="input" value="图源 A" />
          </Field>
          <Field label="Base URL" grow hint="grow：占满剩余宽度">
            <input class="input input--mono" value="https://example.com/api" />
          </Field>
          <Field label="只读字段">
            <input class="input" value="不可改" disabled />
          </Field>
        </div>
      </Panel>

      <Panel title="EmptyState 空状态" meta="列表 / 表格 / 面板通用">
        <EmptyState text="默认只给文案" />
        <EmptyState icon="database" text="带图标" hint="以及一行说明" />
        <EmptyState icon="warning" text="警告场景" hint="图标名来自 design/icons.json" />
      </Panel>

      <Panel :title="`Icon 图标（${iconNames.length} 个）`" meta="design/icons.json → 生成物">
        <template #hint>
          图标名就是 icons.json 里的键；颜色随文字（currentColor），尺寸随字号（<code>size</code> 可放大）。
        </template>
        <div class="demo__icons">
          <div v-for="n in iconNames" :key="n" class="demo__icon">
            <Icon :name="n" size="22px" />
            <span class="muted">{{ n }}</span>
          </div>
        </div>
      </Panel>

      <Panel title="Toolbar 工具栏" meta="lead + 动作区">
        <template #hint>dense 变体用于面板内的小工具条。</template>
        <Toolbar dense>
          <template #lead>
            <strong>密集工具条</strong>
            <Tag>dense</Tag>
          </template>
          <button class="btn btn--ghost"><Icon name="plus" /> 新增</button>
        </Toolbar>
        <div class="demo__pad muted">工具栏默认通栏贴边；这里放进正文演示 dense。</div>
      </Panel>
    </main>

    <footer class="demo__foot muted">
      浏览器看这一页：<code>npm run dev</code> → http://localhost:5180/#demo
      （Qt 壳加载 <code>dist/index.html</code> 不带 hash → 仍是试点窗口）
    </footer>
  </div>
</template>

<style scoped>
.demo { display: flex; flex-direction: column; height: 100%; }
.demo__body {
  flex: 1; min-height: 0; overflow: auto; display: grid; gap: var(--sp-4);
  padding: var(--sp-4); grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  align-content: start;
}
.demo__pad { padding: var(--sp-3) var(--sp-4); }
.demo__p { margin: 0 0 var(--sp-2); font-size: var(--fs-2); }
.demo__row { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; }
.demo__fields { display: flex; flex-wrap: wrap; gap: var(--sp-4); }
.demo__icons { display: flex; flex-wrap: wrap; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); }
.demo__icon { display: flex; flex-direction: column; align-items: center; gap: var(--sp-1); font-size: var(--fs-1); width: 76px; }
.demo__foot {
  padding: var(--sp-2) var(--sp-4); background: var(--bg-1);
  border-top: 1px solid var(--line-soft); font-size: var(--fs-2);
}
</style>
