# -*- coding: utf-8 -*-
"""歌词视频 插件主类。

宿主契约：
  * setup(self, ctx)  —— 拿到 ctx。
  * build(self, ctx)  —— 返回 QWidget（页签内容）；PySide6 只在函数内 import，
                        任何异常都被捕获并回退成 QLabel，绝不带崩宿主。
  * run_task(...)     —— 无界面入口：渲染一首歌词视频，返回 dict，绝不抛异常。
  * on_unload()       —— 可选清理。

插件不 import 任何主程序模块，只用 ctx 与 qishui_core（经 ctx）。
"""
from __future__ import annotations

import os

from . import engine


class LyricvideoPlugin(object):
    def __init__(self):
        self.ctx = None
        self._lrc = None
        self._sid = None
        self._sec = None
        self._style = None
        self._audio = ""
        self._log = None

    # ---------------------------------------------------- 宿主契约
    def setup(self, ctx):
        self.ctx = ctx

    def build(self, ctx):
        """构建页签界面。防御式：失败显示 QLabel，不崩宿主。"""
        self.ctx = ctx
        try:
            return self._build_ui()
        except Exception as e:
            try:
                from PySide6.QtWidgets import QLabel
            except Exception:
                return None
            return QLabel("歌词视频界面加载失败：%s" % e)

    def run_task(self, song_id=None, lrc_text=None, seconds=60, style="neon"):
        """无界面入口：渲染一首歌词视频。返回 dict，绝不抛异常。"""
        try:
            if self.ctx is None:
                return {"ok": False, "msg": "未初始化 ctx"}
            # 允许从设置里取上次输入
            if not song_id:
                song_id = (self.ctx.settings.get("last_song_id") or "") or None
            if not lrc_text:
                lrc_text = self.ctx.settings.get("last_lrc") or ""
            if not song_id and not lrc_text:
                return {"ok": True, "msg": "未配置 song_id / 歌词，跳过渲染"}
            path, msg = engine.render(
                self.ctx, song_id=song_id, lrc_text=lrc_text,
                seconds=seconds, style=style)
            return {"ok": bool(path), "path": path, "msg": msg}
        except Exception as e:
            import traceback
            return {"ok": False, "msg": "渲染异常：%s\n%s" % (e, traceback.format_exc()[-600:])}

    def on_unload(self):
        """可选：插件卸载时清理。这里无需释放资源。"""
        pass

    # ---------------------------------------------------- 界面
    def _build_ui(self):
        from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                                       QTextEdit, QLineEdit, QPushButton,
                                       QLabel, QComboBox, QFileDialog)
        from PySide6.QtCore import Qt

        w = QWidget()
        lay = QVBoxLayout(w)

        lay.addWidget(QLabel("歌词（LRC 格式，每行 [分:秒] 歌词）："))
        self._lrc = QTextEdit()
        self._lrc.setAcceptRichText(False)
        self._lrc.setPlaceholderText("[00:00.00] 第一句\n[00:03.50] 第二句")
        lay.addWidget(self._lrc, 3)

        row = QHBoxLayout()
        row.addWidget(QLabel("歌曲ID："))
        self._sid = QLineEdit()
        self._sid.setPlaceholderText("可选；留空则需提供本地音频")
        row.addWidget(self._sid, 1)
        row.addWidget(QLabel("时长(s)："))
        self._sec = QLineEdit("60")
        row.addWidget(self._sec, 0)
        row.addWidget(QLabel("风格："))
        self._style = QComboBox()
        self._style.addItems(["neon", "soft", "cinema"])
        row.addWidget(self._style, 0)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self._audio_btn = QPushButton("选择音频…")
        self._audio_btn.clicked.connect(self._pick_audio)
        row2.addWidget(self._audio_btn)
        self._gen_btn = QPushButton("生成视频")
        self._gen_btn.clicked.connect(self._on_generate)
        row2.addWidget(self._gen_btn)
        row2.addStretch(1)
        lay.addLayout(row2)

        self._log = QLabel("等待生成…")
        self._log.setWordWrap(True)
        self._log.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._log, 1)

        # 还原上次输入
        if self.ctx is not None:
            self._lrc.setPlainText(self.ctx.settings.get("last_lrc") or "")
            self._sid.setText(self.ctx.settings.get("last_song_id") or "")
        return w

    def _pick_audio(self):
        from PySide6.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            None, "选择音频", "", "音频 (*.m4a *.mp3 *.wav *.aac)")
        if p:
            self._audio = p
            self._audio_btn.setText("音频：" + os.path.basename(p))

    def _on_generate(self):
        try:
            song_id = (self._sid.text().strip() or None) if self._sid else None
            lrc = self._lrc.toPlainText() if self._lrc else ""
            try:
                seconds = int((self._sec.text().strip() or "60"))
            except Exception:
                seconds = 60
            style = self._style.currentText() if self._style else "neon"
            if self.ctx is not None:
                self.ctx.settings.set("last_song_id", song_id or "")
                self.ctx.settings.set("last_lrc", lrc)
            if self._log is not None:
                self._log.setText("生成中…")
            path, msg = engine.render(
                self.ctx, song_id=song_id, audio_path=self._audio or None,
                lrc_text=lrc, seconds=seconds, style=style)
            if path:
                if self._log is not None:
                    self._log.setText("✅ %s\n   %s" % (path, msg))
                if self.ctx is not None:
                    try:
                        self.ctx.open_path(path)
                    except Exception:
                        pass
            else:
                if self._log is not None:
                    self._log.setText("❌ %s" % msg)
        except Exception as e:
            import traceback
            if self._log is not None:
                self._log.setText("异常：%s\n%s" % (e, traceback.format_exc()[-600:]))
