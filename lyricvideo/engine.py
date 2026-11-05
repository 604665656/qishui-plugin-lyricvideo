# -*- coding: utf-8 -*-
"""歌词视频渲染引擎。

把「歌词文本（LRC）+ 音频」合成竖屏 mp4。只依赖 ctx 与 qishui_core；
ffmpeg / PIL / numpy 全部函数内惰性 import，绝不 import 主程序模块。

输出约定（硬规则 3）：视频一律写到
    ctx.account_dir(uid) / "歌词视频" /
中间帧/音频临时文件写到同目录下的 "_render"，绝不写到其它任意路径。
"""
import os
import re

from . import bmg_video
from . import bmg_video_v2


def _safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s or "歌曲")


def require_ffmpeg():
    """惰性解析可用的 ffmpeg 可执行文件。

    优先 imageio-ffmpeg（函数内 import，不污染顶层）；否则回退系统 ffmpeg。
    两者都没有时抛出明确异常，错误信息提示安装 imageio-ffmpeg。
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return "ffmpeg"
    except Exception:
        raise RuntimeError(
            "需要 imageio-ffmpeg（请先执行 pip install imageio-ffmpeg，"
            "或安装系统 ffmpeg 并确保其在 PATH 中）")


def _download_audio(url, dest, ctx=None):
    """下载音频直链到 dest（dest 必须在 account_dir 下）。"""
    import urllib.request
    if ctx is not None:
        ctx.log("下载音频：%s" % url)
    d = os.path.dirname(dest)
    if d:
        os.makedirs(d, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def render(ctx, song_id=None, name=None, audio_path=None, lrc_text=None,
           seconds=60, style="neon", out_dir=None):
    """渲染一首歌词视频。

    参数：
      ctx        —— 宿主/内核下发的 ToolContext（必须）。
      song_id    —— 汽水音乐歌曲 ID；优先用来经 ctx.api.song_info 取音频直链下载。
      name       —— 歌名（用于文件名与画面标题），缺省取 song_id。
      audio_path —— 本地音频路径；给定则优先于 song_id。
      lrc_text   —— LRC 文本（每行 `[分:秒] 歌词`）。
      seconds    —— 截取秒数，默认 60。
      style      —— neon / soft / cinema。
      out_dir    —— 覆盖输出目录（默认 ctx.account_dir(uid)/歌词视频）。

    返回 (out_path, msg)：成功 out_path 为视频路径，失败为 (None, 错误说明)。
    """
    # 1) ffmpeg 可用性（惰性，没装直接给明确提示，不崩）
    try:
        require_ffmpeg()
    except Exception as e:
        return None, str(e)

    if ctx is None:
        return None, "缺少 ctx（宿主上下文）"

    uid = ctx.uid
    base = ctx.account_dir(uid)
    out_dir = out_dir or os.path.join(base, "歌词视频")
    work_dir = os.path.join(out_dir, "_render")
    os.makedirs(work_dir, exist_ok=True)

    # 2) 让底层模块写到我们的目录（它们读模块级全局，调用时取最新值）
    bmg_video.WORK_DIR = work_dir
    bmg_video_v2.OUT_DIR = out_dir
    bmg_video_v2.WORK = work_dir

    # 3) 音频：本地优先 → song_id 取直链下载
    if audio_path and os.path.exists(audio_path):
        audio = audio_path
    elif song_id:
        try:
            r = ctx.api.song_info(song_id)
            data = (r or {}).get("data") or {}
            clips = data.get("clips") or []
            url = (clips[0].get("clipUrl") if clips else "") or ""
            if not url:
                return None, "song_info 没有可用音频直链（song_id=%s）" % song_id
            audio = _download_audio(
                url, os.path.join(work_dir, _safe_name(name or song_id) + ".m4a"), ctx)
        except Exception as e:
            return None, "获取音频失败：%s" % e
    else:
        return None, "需要音频：请提供 song_id 或本地音频文件"

    # 4) 歌词
    if not lrc_text or not lrc_text.strip():
        return None, "需要歌词文本（LRC 格式）"
    lrc_path = os.path.join(work_dir, "input.lrc")
    with open(lrc_path, "w", encoding="utf-8") as f:
        f.write(lrc_text)

    name = name or (song_id or "歌曲")
    if ctx is not None:
        ctx.log("开始渲染：%s（%ss / %s）" % (name, seconds, style))

    # 5) 合成
    out_path, msg = bmg_video_v2.make_video_v2(
        name, audio, lrc_path, seconds, style,
        out_path=os.path.join(out_dir, _safe_name(name) + "_%s.mp4" % style))

    if out_path and ctx is not None:
        try:
            ctx.notify("歌词视频已生成", out_path)
        except Exception:
            pass
    return out_path, msg
