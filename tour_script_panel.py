# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· 新手引导脚本
# Copyright (C) 2026 zccored  ·  AGPL-3.0-only（见 LICENSE）
#
# 这就是"低代码"那一层：**只写一个 TourStep 列表**，引擎负责遮罩、打洞、定位、
# 推进、清理。要改引导内容 / 加一步 / 调顺序，改这个文件就够了，不用碰引擎。
#
# 本文件刻意**不 import img_server**（避免循环依赖），只按属性名 duck-type 那个窗口。
# 少数需要用到节点类的地方在函数内部延迟 import。
#
# 目标读者：**第一次打开端口画板、画布还空着的人**。所以顺序是按"搭出一条最小流水线"
# 来的：先有图纸 → 认识 API 入口 → 放一个容器框 → 看看画布怎么连 → 然后才是各个按钮。
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import List

from tour_layer import TourStep

# 首次运行自动弹的标记键；改这里等于换一套"看过没看过"的记录
TOUR_KEY = 'port_panel_v1'


# ==================== 小工具（给 done / prepare 用） ====================
def _ensure_left_open(dlg) -> bool:
    """左侧栏收着就先展开。返回 True 表示刚改了布局（引擎会下一 tick 重新取几何）。"""
    tri = getattr(dlg, '_left_tri', None)
    if tri is None:
        return False
    try:
        if getattr(tri, '_collapsed', False):
            tri.set_collapsed(False)
            return True
    except Exception:
        pass
    return False


def _tab_count(dlg) -> int:
    try:
        return int(dlg.tab_bar.count())
    except Exception:
        return -1


def _container_count(dlg) -> int:
    """当前图纸画布上有几个「容器框」。"""
    try:
        from img_server import ContainerNode
        scene = dlg._tabs[dlg._active_tab].get('scene')
        if scene is None:
            return -1
        return sum(1 for it in scene.items() if isinstance(it, ContainerNode))
    except Exception:
        return -1


# ==================== 引导正文（要改文案就改这里） ====================
def panel_tour_steps(dlg) -> List[TourStep]:
    """端口画板的新手引导步骤表。`dlg` 是 FlowEditorDialog 实例。"""
    n_tabs0 = _tab_count(dlg)
    n_cont0 = _container_count(dlg)

    return [
        # ---------- 0. 开场 ----------
        TourStep(
            key='welcome',
            target=lambda: getattr(dlg, 'tab_bar', None),
            title='欢迎使用端口画板',
            body=(
                '这是一个十分方便的下载器流水线工具，'
                '您可以拖拽连线把各个节点拼成一条完整的下载流程。<br><br>'
                '如果您对每个节点所产生的数据有疑问，可以随时将连线切断，通过右边的“数据处理”以及再次点击该标签来切换富文本模式，查看每个节点的输出数据。<br><br>'
                '按「▶ 执行」即可跑起来。<br><br>'
                '下面请跟随带你走一遍，<b>该点的地方会留一个亮框，您直接点就行</b>，'
                '做完会自动继续。<br>'
                '随时可以按 <b>Esc</b> 退出；以后想重看，从工具栏的 '
                '<b>🛠️ 工具栏 → 🎓 新手引导</b> 进来。'
            ),
            anchor='below',
        ),

        # ---------- 1. 新建图纸（交互） ----------
        TourStep(
            key='new_drawing',
            target=lambda: getattr(dlg, 'btn_new_drawing', None),
            title='第一步：先建一张图纸',
            body=(
                '每张图纸 = 一条独立的流水线，上面可以切换图纸标签页，并且<b>图纸之间互不干扰</b>，'
                '下载参数、日志、输出都各算各的。<br><br>'
                '点一下高亮里的 <b>＋ 新建图纸</b> —— 会弹出命名框，'
                '<b>名字已经预填好</b>（带时间的那种），不想取就直接回车。<br><br>'
                '建好之后想改名，去左下<b>「已保存流程图」里右键 → ✏️ 编辑名称</b>。'
            ),
            anchor='right',
            hint='点「＋ 新建图纸」并起个名字（回车即用预填名），建好会自动继续',
            done=lambda: _tab_count(dlg) > n_tabs0,
            prepare=lambda: _ensure_left_open(dlg),
        ),

        # ---------- 2. API 入口（讲解） ----------
        TourStep(
            key='api_tree',
            target=lambda: getattr(dlg, 'api_tree', None),
            title='这里是「API 入口」',
            body=(
                '一个入口 = 一个站点（含它的一堆子端口）。它们来自'
                '<b>「📷 图源配置」</b>，改完那边这里会自动刷新。<br><br>'
                '用法：把入口<b>拖到右边画布上</b>，就成了一个 API 节点。'
            ),
            anchor='right',
            prepare=lambda: _ensure_left_open(dlg),
        ),

        # ---------- 3. 放一个容器框（交互） ----------
        TourStep(
            key='add_container',
            target=lambda: getattr(dlg, 'btn_add_container', None),
            title='给它配一个「容器框」',
            body=(
                'API 节点只负责<b>取数据</b>，真正<b>存到硬盘</b>的是容器框：'
                '在里面设存储路径、文件夹命名规则，它来统计下了多少字节。<br><br>'
                '点一下高亮里的 <b>📦 容器框</b> ，画布上会多出一个框。'
            ),
            anchor='below',
            hint='点「📦 容器框」，画布上出现新框就会自动继续',
            done=lambda: _container_count(dlg) > n_cont0,
        ),

        # ---------- 4. 画布（讲解） ----------
        TourStep(
            key='canvas',
            target=lambda: getattr(dlg, 'view', None),
            title='画布：连线就是数据流',
            body=(
                '把框从<b>输出端口拖到另一个框的输入端口</b>就连起来了。<br><br>'
                '· 连线拖到视口边缘会<b>自动滚屏</b><br>'
                '· 鼠标<b>悬停</b>任一框，会弹出它的端口清单和每个参数是干什么的<br>'
                '· <b>Ctrl+Z</b> 撤销（20 步）'
            ),
            anchor='center',
        ),

        # ---------- 5. 仪表盘（讲解） ----------
        TourStep(
            key='dashboard',
            target=lambda: dlg._active_dash(),
            title='数据传递参数仪表盘',
            body=(
                '并发几个文件、重试几次、超时多久、多大的文件走流式写盘……'
                '<b>14 项速率参数都在这一条里</b>。<br><br>'
                '关键是它<b>随图纸保存</b>：换个图纸就是另一套参数，互不影响。'
            ),
            anchor='below',
        ),

        # ---------- 6. 图源配置（讲解） ----------
        TourStep(
            key='image_sources',
            target=lambda: getattr(dlg, 'btn_image_sources', None),
            title='接口在「图源配置」里配',
            body=(
                '站点地址、子端口路径、参数、请求头、Cookie 都在这里面管，'
                '<b>不用先开主程序</b>。<br><br>'
                '还能 <b>📥 导入入口 / 📤 分享入口</b>：一个入口就是一个 '
                '<code>.apientry.json</code>，别人发你一个文件，导进来就能用。'
            ),
            anchor='below',
        ),

        # ---------- 7. 保存 / 加载（讲解） ----------
        TourStep(
            key='save',
            target=lambda: getattr(dlg, 'btn_save', None),
            title='搭好了就存成图纸',
            body=(
                '<b>💾 保存流程</b>把整张图纸（节点、连线、参数）写成一个 '
                '<code>.wbt</code> 文件；<b>📂 加载流程</b>再读回来。<br><br>'
                '左栏「已保存流程图」里能随时右键打开、在资源管理器里定位。'
            ),
            anchor='below',
        ),

        # ---------- 8. 执行（讲解） ----------
        TourStep(
            key='run',
            target=lambda: getattr(dlg, 'btn_run', None),
            title='按这里开始跑',
            body=(
                '执行顺序是<b>按连线拓扑排的</b>，不是按你摆放的位置。<br><br>'
                '跑起来之后这个按钮会禁用 —— 想停下，用左下角的取消。'
                '<b>执行中不要改图源配置</b>，改完要重新加载图纸。'
            ),
            anchor='below',
        ),

        # ---------- 9. 交接给图库检索（讲解） ----------
        TourStep(
            key='handoff',
            target=lambda: getattr(dlg, 'btn_handoff', None),
            title='下完了？一键交给「图库检索」',
            body=(
                '点这个按钮：写交接文件 → 拉起图库检索管理器 → '
                '<b>本程序自动退出把内存让给它</b>，由它增量建库。<br><br>'
                '没装图库检索也没关系，这按钮会提示你找不到启动器。'
            ),
            anchor='below',
        ),

        # ---------- 10. 收尾 ----------
        TourStep(
            key='tools',
            target=lambda: getattr(dlg, 'btn_tools', None),
            title='就这些，开画吧',
            body=(
                '工具栏这个 <b>🛠️</b> 里还有步进器、站点解析等节点，'
                '以及<b>🎓 新手引导</b> —— 想再看一遍随时点。<br><br>'
                '遇到问题先看每个按钮的<b>悬停提示</b>，写得很细。'
            ),
            anchor='below',
        ),
    ]
