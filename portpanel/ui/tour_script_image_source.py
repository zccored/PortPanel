# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# 端口画板（PortPanel）· 「图源配置」新手引导脚本
# Copyright (C) 2026 zccored  ·  AGPL-3.0-only（见 LICENSE）
#
# 和 `tour_script_panel.py` 一样：这里只有**一个 TourStep 列表**，机制全在
# `tour_layer.py` 里。改文案、加步骤、调顺序都只动这个文件。
#
# 目标窗口是 `APIConfigDialog.create_image_source_tab()` 搭出来的那一页 ——
# 它同时存在于：
#   · 独立的「图源配置」窗口（ImageSourceConfigDialog）
#   · 「API 和云服务配置」里的图源配置选项卡
# 所以两边共用同一份脚本，不用写两遍。本文件同样**不 import** 任何 UI 模块，
# 只按属性名 duck-type（少数要类对象的地方在函数内部延迟 import）。
#
# 目标读者：第一次打开图源配置的人。所以顺序是"从哪来 → 怎么建 → 每个字段是干嘛的
# → 怎么验证 → 怎么分享"，而不是照着按钮从左到右念一遍。
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import List, Optional

from portpanel.ui.tour_layer import TourStep

# 首次运行自动弹的标记键（与端口画板那份互相独立）
TOUR_KEY = 'image_source_v1'


# ==================== 小工具（给 done / prepare 用） ====================
def _source_count(dlg) -> int:
    try:
        return int(len((dlg.config or {}).get('image_sources') or []))
    except Exception:
        return -1


def _current_source_index(dlg) -> int:
    try:
        return int(dlg.image_source_tabs.currentIndex())
    except Exception:
        return -1


def _endpoint_list(dlg):
    """当前入口页的子端口列表控件（没有就返回 None）。"""
    try:
        return dlg.endpoint_list_widgets.get(_current_source_index(dlg))
    except Exception:
        return None


def _ensure_endpoint_selected(dlg) -> bool:
    """详情区那堆 `_ep_*` 控件**只有选中子端口时才有内容**。

    所以讲它们之前先把列表的第一行选中。返回 True 表示"刚改了状态、布局要下一 tick
    再量"。没有入口 / 没有子端口时返回 False，由 `optional` 决定跳过。
    """
    lst = _endpoint_list(dlg)
    if lst is None or lst.count() == 0:
        return False
    try:
        if lst.currentRow() < 0:
            lst.setCurrentRow(0)
            return True
    except Exception:
        pass
    return False


def _ep_ready(dlg) -> bool:
    """详情区是不是真的有东西可讲（选中了子端口）。"""
    lst = _endpoint_list(dlg)
    try:
        return lst is not None and lst.count() > 0 and lst.currentRow() >= 0
    except Exception:
        return False


# ==================== 引导正文（要改文案就改这里） ====================
def image_source_tour_steps(dlg) -> List[TourStep]:
    """「图源配置」的新手引导步骤表。`dlg` 是 APIConfigDialog / ImageSourceConfigDialog。"""
    n_src0 = _source_count(dlg)

    return [
        # ---------- 0. 开场 ----------
        TourStep(
            key='welcome',
            target=lambda: getattr(dlg, 'image_source_tabs', None),
            title='这里是「图源配置」',
            body=(
                '端口画板里的 API 节点，数据都来自这里。<br><br>'
                '一个 <b>API 入口</b> = 一个站点；入口下面挂若干 <b>子端口</b>（就是一条条路径）；'
                '子端口再带参数、请求头、Cookie。<br><br>'
                '改完记得点最下面的 <b>保存图源配置</b> —— 不保存的话端口画板那边看不到。'
            ),
            anchor='below',
        ),

        # ---------- 1. 新建入口（交互） ----------
        TourStep(
            key='add_source',
            target=lambda: getattr(dlg, 'add_source_btn', None),
            title='先建一个 API 入口',
            body=(
                '点一下高亮里的 <b>➕ 新建API入口</b>，填两样东西：<br>'
                '· <b>名称</b>：随便起，只给你自己看<br>'
                '· <b>基础 URL</b>：这个站点接口的根地址，'
                '比如 <code>https://example.com/api/v1</code><br><br>'
                '建好之后它会出现在上面的选项卡里。'
            ),
            anchor='below',
            hint='点「➕ 新建API入口」并填好名称与基础 URL，建好会自动继续',
            done=lambda: _source_count(dlg) > n_src0,
        ),

        # ---------- 2. 入口选项卡（讲解） ----------
        TourStep(
            key='source_tabs',
            target=lambda: getattr(dlg, 'image_source_tabs', None),
            title='每个入口一页',
            body=(
                '上面的选项卡就是你的所有入口，点哪个改哪个。<br><br>'
                '入口多了也别慌，右侧的 <b>✏️ 编辑入口 / 🗑️ 删除入口</b> 都是对'
                '<b>当前这一页</b>生效的。'
            ),
            anchor='below',
        ),

        # ---------- 3. 子端口列表 + 添加（讲解 + 指路） ----------
        TourStep(
            key='endpoint_list',
            target=lambda: _endpoint_list(dlg),
            title='一个入口下面挂若干「子端口」',
            body=(
                '每条子端口 = 这个站点的一个具体接口路径（比如 <code>/creators</code>、'
                '<code>/posts/{id}</code>）。<br><br>'
                '想加就点右上角的 <b>➕ 添加子端口</b>；'
                '右键这条列表还能改路径、复制、删除。'
            ),
            anchor='right',
        ),

        # ---------- 4. 参数表（讲解；文字要落到端口画板的悬停提示上） ----------
        TourStep(
            key='param_table',
            target=lambda: getattr(dlg, '_ep_param_table', None),
            title='参数表 —— 请把「注释」写满',
            body=(
                '名 / 类型 / 值 / <b>注释</b> 四列。值是接口要的参数，'
                '<code>{name}</code> 这种占位符会在执行时被替换。<br><br>'
                '<b>注释不是写给自己看的</b>：端口画板里鼠标悬停在 API 节点上时，'
                '这一列会直接显示成提示（<code>—— 作者唯一标识符ID</code>）。'
                '多写一句，三个月后的你会感谢现在的你。'
            ),
            anchor='right',
            prepare=lambda: _ensure_endpoint_selected(dlg),
            optional=True,
        ),

        # ---------- 5. 请求头 / Cookie（讲解） ----------
        TourStep(
            key='headers_cookie',
            target=lambda: getattr(dlg, '_ep_cookie_verify_btn', None),
            title='请求头与 Cookie',
            body=(
                '这一块显示这条子端口的请求头与 Cookie 状态。<br><br>'
                '· 密钥类的东西写成 <code>${ENV:名字}</code>，'
                '真值放你自己机器的环境变量里 —— <b>别硬写进配置</b>，'
                '将来分享图纸时会把请求头一起带出去<br>'
                '· 点 <b>🔎 验证 Cookie</b> 能当场试出来它到底生效没有'
            ),
            anchor='right',
            prepare=lambda: _ensure_endpoint_selected(dlg),
            optional=True,
        ),

        # ---------- 6. 调试（讲解） ----------
        TourStep(
            key='debug',
            target=lambda: getattr(dlg, '_ep_debug_btn', None),
            title='不确定？直接「🚀 调试」',
            body=(
                '它会<b>真的发一次请求</b>，然后把响应摊在下面给你看：'
                '是不是风控页、返回的是不是你以为的那个 JSON、哪个头没生效。<br><br>'
                '配接口时先在这里试通，再去画板里搭流程，能省很多来回。'
            ),
            anchor='right',
            prepare=lambda: _ensure_endpoint_selected(dlg),
            optional=True,
        ),

        # ---------- 7. 编辑子端口（讲解） ----------
        TourStep(
            key='edit_endpoint',
            target=lambda: getattr(dlg, '_ep_edit_btn', None),
            title='改路径 / 参数 / 请求头',
            body=(
                '路径、方法、参数、请求头都在「✏️ 编辑子端口」里改。<br><br>'
                '旁边的 <b>📤 导出JSON</b> 是把这条子端口的响应存下来，'
                '方便你对着真实返回调参数。'
            ),
            anchor='right',
            prepare=lambda: _ensure_endpoint_selected(dlg),
            optional=True,
        ),

        # ---------- 8. 导入 / 分享入口（讲解） ----------
        TourStep(
            key='import_share',
            target=lambda: getattr(dlg, 'share_source_btn', None),
            title='别人的入口，一个文件就能给你',
            body=(
                '<b>📤 分享入口</b>把<b>当前这一页</b>的整个入口（含全部子端口、参数、注释）'
                '导成一个 <code>.apientry.json</code>；<br>'
                '<b>📥 导入入口</b>把它读回来 —— 可以一次多选，重名会自动加 <code>(2)</code>。<br><br>'
                '<b>注意</b>：导出的文件里带着请求头，发人之前先确认里面没有你的 Cookie。'
            ),
            anchor='below',
        ),

        # ---------- 9. 保存（讲解） ----------
        TourStep(
            key='save',
            target=lambda: getattr(dlg, 'save_btn', None),
            title='最后一步：保存',
            body=(
                '点 <b>保存图源配置</b> 才会真正写进配置文件。'
                '存好之后端口画板那边的「📡 API 入口」列表会自己刷新。<br><br>'
                '配置是<b>加密落盘</b>的；直接关窗口 = 这次的改动不保存。'
            ),
            anchor='above',
        ),
    ]
