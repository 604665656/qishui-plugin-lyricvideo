# -*- coding: utf-8 -*-
"""revenue_model 桩（原主程序模块）。

歌词视频插件不依赖「按收益挑歌」的能力，但为了兼容旧 CLI 辅助函数
（cmd_list / cmd_top / bmg_video_v2 --top）的符号引用，这里保留同名桩。
插件渲染路径（engine.render）完全不调用它。

若将来需要「0 投稿且播放最高」的选歌，应改用 ctx.api / ctx.accounts 实现，
而非直接 import 主程序模块。
"""


def find_uid():
    """桩：原实现从账号注册表找当前 uid；这里返回空（无可用账号）。"""
    return ""


class AccountData(object):
    """桩：原实现读取某账号的曲库/收益数据。插件用不到，返回空。"""

    def __init__(self, uid=""):
        self.uid = uid or ""
        self.titles = {}

    def pick_candidates(self, only_zero_pub=False):
        return []
