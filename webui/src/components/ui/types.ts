// 组件库（src/components/ui）的公共类型。
// 只放「组件之间共享」的类型；业务数据类型仍归 src/types.ts。
/** 语义色调：对应 base.css 里的 .tag--* 系列 */
export type Tone = 'default' | 'accent' | 'warn' | 'ok' | 'danger'
