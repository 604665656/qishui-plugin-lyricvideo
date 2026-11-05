#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""歌词视频 v2：动态频谱背景 + 描边发光歌词 + 按音乐能量换配色。

为什么要 v2（v1 只有纯渐变底 + 白字，太素）：
  参考市面做法，好看的关键不是堆特效，而是这三件事——
  1. **背景在动**：模糊频谱条 / 缓慢流动的色相，画面不死；
  2. **文字有厚度**：8px 黑描边 + 外发光，压在亮背景上也读得清；
  3. **配色跟着音乐走**：安静段冷青、推进段粉紫、高潮段暖金，
     观众能"看见"情绪变化，而不是几十秒一个颜色。

实现：PIL 预渲染每句歌词为带发光/描边的 PNG，ffmpeg 负责动态背景、
      频谱叠加、按时间轴硬切（卡点感，比堆转场干净）。

插件化改写说明（相对原始 bmg_video_v2.py）：
  * `import bmg_video as V1` → `from . import bmg_video as V1`（同包内）。
  * 去掉对主程序 datapath 的依赖；OUT_DIR 默认走系统临时目录，
    由 engine 在渲染时按 ctx.account_dir(uid)/歌词视频 重设。
  * revenue_model 改为包内桩（仅 --top 旧 CLI 用，插件渲染不走）。

用法（原 CLI，插件形态不调用）：
  python bmg_video_v2.py --name 一饭一抬眼
  python bmg_video_v2.py --name X --seconds 45 --style neon|soft|cinema
  python bmg_video_v2.py --top 2
"""
import os
import re
import sys
import math
import subprocess
import tempfile

BASE = (os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from . import bmg_video as V1

W, H = 1080, 1920
WORK = V1.WORK_DIR
OUT_DIR = os.path.join(tempfile.gettempdir(), "qishui_lyricvideo", "out")

# 三套配色：安静 → 推进 → 高潮
# glow1/glow2 是背景光斑的颜色，必须给足饱和度 —— 用近灰色会糊成一片脏雾。
# spec 用 showspectrum 自带的调色板名，比事后 hue 旋转可靠（hue 转出来会串色）。
STYLES = {
    "neon": {   # 电音/说唱，高对比
        "name": "霓虹",
        "bg": [(5, 7, 18), (14, 7, 22)],
        "weak": (127, 216, 255), "mid": (255, 143, 208), "strong": (255, 209, 102),
        "glow1": (0, 168, 255), "glow2": (255, 46, 184),
        "glow": 26, "stroke": 9,
        "spec": "nebulae", "tint": "colorbalance=rs=0.00:gs=0.02:bs=0.06",
    },
    "soft": {   # 流行/抒情，柔和
        "name": "柔光",
        "bg": [(9, 9, 17), (20, 12, 22)],
        "weak": (176, 214, 255), "mid": (255, 196, 214), "strong": (255, 232, 176),
        "glow1": (86, 132, 255), "glow2": (255, 118, 186),
        "glow": 34, "stroke": 8,
        "spec": "magma", "tint": "colorbalance=rs=0.06:gs=0.00:bs=0.04",
    },
    "cinema": {  # 叙事/国风，橙青对比的电影感
        "name": "电影",
        "bg": [(8, 8, 11), (17, 13, 11)],
        "weak": (206, 218, 228), "mid": (238, 214, 176), "strong": (255, 240, 206),
        "glow1": (255, 138, 40), "glow2": (36, 96, 168),
        "glow": 22, "stroke": 7,
        "spec": "fire", "tint": "colorbalance=rs=0.10:gs=0.01:bs=-0.12",
    },
}


# ---------------------------------------------------------------- 音频能量
def decode_pcm(audio, sr=8000):
    """把音频解码成 8k 单声道 s16le，返回 numpy 数组（归一化到 -1~1）。"""
    import numpy as np
    exe = V1.ffmpeg_exe()
    cmd = [exe, "-v", "error", "-i", audio, "-ac", "1", "-ar", str(sr),
           "-f", "s16le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        return None, sr
    a = np.frombuffer(p.stdout, dtype="<i2").astype("float32") / 32768.0
    return a, sr


def energy_curve(audio, sr=8000, win=0.10):
    """按 0.1 秒窗口算 RMS 能量，归一化到 0~1。"""
    import numpy as np
    a, sr = decode_pcm(audio, sr)
    if a is None or len(a) == 0:
        return None, win
    n = max(1, int(sr * win))
    k = len(a) // n
    if k < 1:
        return None, win
    frames = a[:k * n].reshape(k, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    # 用 95 分位做归一化，避免个别爆音把整体压暗
    hi = float(np.percentile(rms, 95)) or 1.0
    rms = np.clip(rms / hi, 0, 1)
    # 平滑一下，避免每句颜色跳变太碎
    if len(rms) >= 5:
        ker = np.array([1, 2, 3, 2, 1], dtype="float32")
        ker /= ker.sum()
        rms = np.convolve(rms, ker, mode="same")
    return rms, win


def level_at(curve, win, t0, t1):
    """取某个时间段内的平均能量（0~1）。"""
    if curve is None:
        return 0.5
    i0 = max(0, int(t0 / win))
    i1 = min(len(curve), max(i0 + 1, int(t1 / win)))
    seg = curve[i0:i1]
    return float(seg.mean()) if len(seg) else 0.5


# ---------------------------------------------------------------- 歌词渲染
def pick_accent(style, lv):
    if lv < 0.34:
        return style["weak"], 0.85
    if lv < 0.68:
        return style["mid"], 1.0
    return style["strong"], 1.18


def wrap_cn(text, per_line=10, min_tail=3):
    """按显示宽度折行，尽量不在词中间断，且避免末行只剩一两个字（孤字最丑）。

    规则：优先在空格处断；没有空格时按字宽断；若断完末行不足 min_tail 字，
    把上一行末尾的字挪下来平衡。
    """
    def width(s):
        return sum(1.0 if ord(c) > 127 else 0.55 for c in s)

    if width(text) <= per_line:
        return [text]

    # 先在空格处尝试均匀分段
    words = text.split(" ")
    if len(words) > 1:
        rows, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if width(trial) <= per_line or not cur:
                cur = trial
            else:
                rows.append(cur)
                cur = w
        if cur:
            rows.append(cur)
        # 末行太短就把上一行拆一点过来
        if len(rows) >= 2 and width(rows[-1]) < min_tail:
            prev = rows[-2]
            move = prev[-1]
            rows[-2] = prev[:-1]
            rows[-1] = move + rows[-1]
        if all(width(r) <= per_line + 1 for r in rows):
            return [r for r in rows if r]

    # 兜底：纯按宽度切
    rows, cur, w = [], "", 0.0
    for ch in text:
        cw = 1.0 if ord(ch) > 127 else 0.55
        if w + cw > per_line and cur:
            rows.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        rows.append(cur)
    if len(rows) >= 2 and width(rows[-1]) < min_tail and len(rows[-2]) > min_tail + 1:
        move = rows[-2][-2:]
        rows[-2] = rows[-2][:-2]
        rows[-1] = move + rows[-1]
    return rows


def render_line(text, accent, strength, style, path):
    """把一句歌词渲染成透明 PNG：外发光 + 黑描边 + 白字。

    strength>1 表示重句，字号更大、发光更浓。
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    segs = wrap_cn(text, per_line=10)
    base_size = int(64 * min(1.25, strength))
    font_path = "C:/Windows/Fonts/msyhbd.ttc"
    try:
        font = ImageFont.truetype(font_path, base_size)
    except Exception:
        font = ImageFont.load_default()

    pad = 60
    tmp = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    widths = []
    for s in segs:
        bb = d.textbbox((0, 0), s, font=font)
        widths.append(bb[2] - bb[0])
    line_h = int(base_size * 1.42)
    box_h = line_h * len(segs) + pad * 2
    layer = Image.new("RGBA", (W, box_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    for i, s in enumerate(segs):
        bw = widths[i]
        x = (W - bw) // 2
        y = pad + i * line_h
        d.text((x, y), s, font=font, fill=(255, 255, 255, 255))

    # 外发光：整体取 alpha 做彩色膨胀
    glow_a = layer.split()[3].filter(ImageFilter.GaussianBlur(style["glow"]))
    glow = Image.new("RGBA", layer.size, accent + (0,))
    glow.putalpha(glow_a.point(lambda v: int(v * 0.85)))
    glow_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    glow_layer.paste(glow, (0, 0), glow)

    # 描边：把文字向右下平移几个像素铺黑底（比逐像素描边快）
    stroke = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(stroke)
    o = style["stroke"]
    for i, s in enumerate(segs):
        x = (W - widths[i]) // 2
        y = pad + i * line_h
        for dx, dy in ((-o, 0), (o, 0), (0, -o), (0, o), (-o, -o), (o, o), (-o, o), (o, -o)):
            sd.text((x + dx, y + dy), s, font=font, fill=(0, 0, 0, 220))

    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out = Image.alpha_composite(out, glow_layer)
    out = Image.alpha_composite(out, stroke)
    out = Image.alpha_composite(out, layer)
    out.save(path, "PNG")
    return path


def render_title(name, style, path):
    """歌名：粗体 + 描边，固定位置。"""
    from PIL import Image, ImageDraw, ImageFont
    size = 76
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", size)
    except Exception:
        font = ImageFont.load_default()
    layer = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bb = d.textbbox((0, 0), name, font=font)
    w = bb[2] - bb[0]
    x, y = (W - w) // 2, 40
    for dx, dy in ((-5, 0), (5, 0), (0, -5), (0, 5)):
        d.text((x + dx, y + dy), name, font=font, fill=(0, 0, 0, 210))
    d.text((x, y), name, font=font, fill=(255, 255, 255, 255))
    layer.save(path, "PNG")
    return path


def render_bottom(text, path):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 42)
    except Exception:
        font = ImageFont.load_default()
    layer = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bb = d.textbbox((0, 0), text, font=font)
    w = bb[2] - bb[0]
    x, y = (W - w) // 2, 30
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        d.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 180))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 215))
    layer.save(path, "PNG")
    return path


def render_bg(seed, style, path):
    """纯深色对角渐变打底。不加光斑——光斑单独做成可漂移的透明层。"""
    import hashlib
    from PIL import Image, ImageDraw
    h = hashlib.md5(seed.encode("utf-8")).hexdigest()
    jitter = int(h[0:2], 16) % 24
    c1, c2 = style["bg"]
    c1 = (max(6, c1[0] // 2 + jitter // 4), max(6, c1[1] // 2), max(12, c1[2] // 2 + jitter // 3))
    c2 = (max(10, c2[0] // 2), max(8, c2[1] // 2 + jitter // 5), max(14, c2[2] // 2))
    img = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / (H - 1.0)
        d.line([(0, y), (W, y)], fill=(
            int(c1[0] + (c2[0] - c1[0]) * k),
            int(c1[1] + (c2[1] - c1[1]) * k),
            int(c1[2] + (c2[2] - c1[2]) * k)))
    img.save(path, "PNG")
    return path


def render_shade(path):
    """字幕区渐变压暗层：中间略暗、上下透明，避免 drawbox 那种硬边。"""
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    h = 900
    y0 = (H - h) // 2
    d.rectangle([0, y0, W, y0 + h], fill=(0, 0, 0, 30))
    img = img.filter(ImageFilter.GaussianBlur(110))
    img.save(path, "PNG")
    return path


def render_glow(color, path, radius=780, peak=170, power=2.4, cx=0.5, cy=0.34):
    """一团柔光（透明 PNG）。

    早前用「硬边椭圆 + 高斯模糊」，模糊把 alpha 摊平了，落地后是一大片灰雾，
    看着很廉价。这里改用径向渐变：alpha = (1 - d/radius)^power * peak，
    中心亮、边缘平顺归零，才像真正的光源。cx/cy 用 0~1 的相对坐标。
    """
    import numpy as np
    from PIL import Image
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt((xx - W * cx) ** 2 + (yy - H * cy) ** 2) / float(radius)
    a = np.clip(1.0 - d, 0.0, 1.0) ** power * float(peak)
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[..., 0] = color[0]
    rgba[..., 1] = color[1]
    rgba[..., 2] = color[2]
    rgba[..., 3] = a.astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(path, "PNG")
    return path


def render_spec_fade(path, ramp=210):
    """频谱上沿的羽化遮罩：把频谱条顶端压暗，让它从背景里淡出，而不是一刀切断。

    只覆盖频谱最上面 ramp 像素，alpha 从 205 递减到 0，下面完全不遮。
    """
    import numpy as np
    from PIL import Image
    img = np.zeros((ramp, W, 4), dtype=np.uint8)
    for y in range(ramp):
        img[y, :, 3] = int(205 * max(0.0, 1.0 - y / float(ramp)) ** 1.5)
    Image.fromarray(img, "RGBA").save(path, "PNG")
    return path


# ---------------------------------------------------------------- 组装
def make_video_v2(name, audio, lrc, seconds=60, style_key="neon", out_path=None):
    style = STYLES.get(style_key) or STYLES["neon"]
    lines = V1.parse_lrc(lrc)
    if not lines:
        return None, "没有可用歌词"
    dur = V1.ffprobe_dur(audio)
    if not dur:
        return None, "读不出音频时长"
    total = min(seconds, dur)

    # 只保留落在时长内、且不太短的句子
    seq = []
    for i, (t, txt) in enumerate(lines):
        if t >= total - 1:
            break
        end = lines[i + 1][0] if i + 1 < len(lines) else total
        end = min(end, total)
        if end - t < 0.8:
            continue
        seq.append((t, end, txt))
    if not seq:
        return None, "歌词时间轴与音频不匹配"

    V1.prepare_workdir()
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = out_path or os.path.join(
        OUT_DIR, re.sub(r'[\\/:*?"<>|]', "_", name) + "_%s.mp4" % style_key)

    curve, win = energy_curve(audio)
    bg = render_bg(name, style, os.path.join(WORK, "bg2.png"))
    # 三团光：上（主色）、中（副色）、下（主色压暗）——只给上两团会留下一大片死黑。
    blob1 = render_glow(style["glow1"], os.path.join(WORK, "blob1.png"),
                        860, 168, 2.4, cx=0.42, cy=0.30)
    blob2 = render_glow(style["glow2"], os.path.join(WORK, "blob2.png"),
                        640, 126, 2.2, cx=0.68, cy=0.44)
    blob3 = render_glow(style["glow1"], os.path.join(WORK, "blob3.png"),
                        700, 104, 2.8, cx=0.34, cy=0.76)
    shade = render_shade(os.path.join(WORK, "shade.png"))
    specfade = render_spec_fade(os.path.join(WORK, "specfade.png"))
    title = render_title(name, style, os.path.join(WORK, "title2.png"))
    bottom = render_bottom("完整版在汽水音乐搜：%s" % name,
                           os.path.join(WORK, "bottom2.png"))

    pngs = []
    for i, (t0, t1, txt) in enumerate(seq):
        lv = level_at(curve, win, t0, t1)
        accent, strength = pick_accent(style, lv)
        p = os.path.join(WORK, "line2_%03d.png" % i)
        render_line(txt, accent, strength, style, p)
        pngs.append(p)

    # ---- 滤镜链
    # 输入下标集中在这一处：0=底 1=音频 2=歌名 3=底部引导 4/5/6=三团光 7=压暗层
    # 8=频谱上沿羽化，9 起是歌词
    I_BG, I_AUD, I_TITLE, I_BOTTOM = 0, 1, 2, 3
    I_G1, I_G2, I_G3, I_SHADE, I_SPECFADE = 4, 5, 6, 7, 8
    I_LYRIC0 = 9

    parts = []
    # 音频分两路：一路输出，一路给频谱
    parts.append("[%d:a]asplit=2[am][as]" % I_AUD)
    # 底：深色渐变，色相只做极小幅度流动，避免整幅变色
    parts.append("[0:v]scale=%d:%d,setsar=1,hue=h='t*0.4':s=1.0,format=rgb24[v0]" % (W, H))
    # 三团柔光按不同周期缓慢漂移 → 背景在动，但色调稳定
    parts.append("[v0][%d:v]overlay=x='-46+46*sin(t/11)':y='-34+34*cos(t/13)':"
                 "format=auto,format=rgb24[v1]" % I_G1)
    parts.append("[v1][%d:v]overlay=x='46-46*sin(t/8)':y='18+28*sin(t/16)':"
                 "format=auto,format=rgb24[v2]" % I_G2)
    parts.append("[v2][%d:v]overlay=x='-30+30*sin(t/15)':y='-20+24*cos(t/9)':"
                 "format=auto,format=rgb24[v2b]" % I_G3)
    # 字幕区渐变压暗（用羽化遮罩，避免硬边）
    parts.append("[v2b][%d:v]overlay=0:0,format=rgb24[v3]" % I_SHADE)
    # 频谱：只占底部一条。showspectrum 自带黑底，pad 到全屏后抠黑叠加 → 只有底部被点亮。
    # 配色直接用 showspectrum 的调色板（14 种），比事后 hue 旋转稳得多：
    # 之前 hue=h=40 想让 cinema 出暖金，实际出来是洋红，整条频谱串色。
    spec_h = 340
    parts.append("[as]showspectrum=s=%dx%d:mode=combined:slide=scroll:color=%s:"
                 "scale=log:fps=30,%s,eq=saturation=1.14:brightness=0.06,format=rgba,"
                 "pad=%d:%d:0:%d:black,colorkey=black:0.09:0.03[sgr]"
                 % (W, spec_h, style["spec"], style["tint"], W, H, H - spec_h))
    parts.append("[v3][sgr]overlay=0:0:format=auto,format=rgb24[v4]")
    # 频谱上沿淡出，避免一条硬直线
    parts.append("[v4][%d:v]overlay=0:%d,format=rgb24[v4b]" % (I_SPECFADE, H - spec_h))
    # 歌名
    parts.append("[v4b][%d:v]overlay=0:150[v5]" % I_TITLE)
    # 底部引导：放到频谱上方，别压在频谱里（压在频谱上又花又难读）
    parts.append("[v5][%d:v]overlay=0:%d[v6]" % (I_BOTTOM, H - spec_h - 104))
    cur = "v6"
    # 歌词逐句（互不重叠），入场做 0.2s 淡入 + 从下方 26px 上浮。
    # 纯硬切看着像 PPT，加了入场才有"成片"的感觉。
    y0 = (H - 460) // 2
    for i, (t0, t1, _) in enumerate(seq):
        idx = I_LYRIC0 + i
        label = "s%d" % i
        parts.append("[%d:v]format=rgba,fade=t=in:st=%0.2f:d=0.20:alpha=1[ln%d]"
                     % (idx, t0, i))
        parts.append("[%s][ln%d]overlay=0:'%d+26*(1-min(1,(t-%0.2f)/0.22))':"
                     "enable='between(t,%0.2f,%0.2f)'[%s]"
                     % (cur, i, y0, t0, t0, t1, label))
        cur = label
    # 暗角压边 + 极轻颗粒，去数码味
    parts.append("[%s]vignette=PI/5.6,noise=alls=4:allf=t+u,format=yuv420p[vout]" % cur)
    flt = ";".join(parts)

    cmd = [V1.ffmpeg_exe(), "-y",
           "-loop", "1", "-t", "%.2f" % total, "-i", bg,
           "-i", audio,
           "-loop", "1", "-t", "%.2f" % total, "-i", title,
           "-loop", "1", "-t", "%.2f" % total, "-i", bottom,
           "-loop", "1", "-t", "%.2f" % total, "-i", blob1,
           "-loop", "1", "-t", "%.2f" % total, "-i", blob2,
           "-loop", "1", "-t", "%.2f" % total, "-i", blob3,
           "-loop", "1", "-t", "%.2f" % total, "-i", shade,
           "-loop", "1", "-t", "%.2f" % total, "-i", specfade]
    for p in pngs:
        cmd += ["-loop", "1", "-t", "%.2f" % total, "-i", p]
    cmd += ["-filter_complex", flt,
            "-map", "[vout]", "-map", "[am]",
            "-t", "%.2f" % total,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-r", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart", out_path]
    pr = subprocess.run(cmd, capture_output=True, text=True,
                        errors="replace", cwd=WORK)
    if pr.returncode != 0 or not os.path.exists(out_path):
        return None, "ffmpeg 失败：\n" + (pr.stderr or "")[-900:]
    return out_path, "%.1fs / %.1f MB / %s / %d 句" % (
        total, os.path.getsize(out_path) / 1e6, style["name"], len(seq))


# ---------------------------------------------------------------- CLI
def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    seconds, style = 60, "neon"
    if "--seconds" in a:
        seconds = int(a[a.index("--seconds") + 1])
    if "--style" in a:
        style = a[a.index("--style") + 1]
    idx = V1.index_assets()

    def one(name):
        if name not in idx:
            cand = [k for k in idx if name in k]
            if len(cand) != 1:
                print("找不到或匹配到多首：%s" % (cand[:5] or name))
                return
            name = cand[0]
        audio, lrc = idx[name]
        print("生成中（%s）：%s …" % (STYLES.get(style, STYLES["neon"])["name"], name))
        p, msg = make_video_v2(name, audio, lrc, seconds, style)
        print(("✅ %s\n   %s" % (p, msg)) if p else ("❌ " + msg))

    if "--name" in a:
        one(a[a.index("--name") + 1])
    elif "--top" in a:
        from . import revenue_model as RM
        data = RM.AccountData(RM.find_uid())
        n = int(a[a.index("--top") + 1]) if len(a) > a.index("--top") + 1 else 2
        done = 0
        for tid, cum, pub, rv in data.pick_candidates(only_zero_pub=True):
            t = data.titles.get(tid, "")
            if t not in idx:
                continue
            one(t)
            done += 1
            if done >= n:
                break
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
