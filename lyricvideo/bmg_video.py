#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已有歌曲做成「可直接投稿抖音」的竖屏歌词视频。

为什么需要它：
  实测数据表明，抖音投稿量是汽水收益的总开关——0 投稿的歌单价 0.24 元/千次，
  有投稿的 0.76 元/千次，差 3 倍多。但「让歌被投稿」缺的是现成视频素材。
  这个脚本把「选中歌曲 → 可上传 mp4」之间那段手工活干掉。

产出规格：1080×1920 竖屏 / H.264 / 30fps / 前 N 秒（默认 60 秒，抖音最优）
画面：渐变底 + 歌名 + 逐句歌词（按 LRC 时间轴切换）
不依赖封面图，因此不涉及任何第三方图片版权。

插件化改写说明（相对原始 bmg_video.py）：
  * 去掉对主程序 datapath 的依赖；资产目录由 engine 在渲染时按
    ctx.account_dir(uid)/歌词视频 重设本模块的 OUT_DIR / WORK_DIR。
  * revenue_model 改为包内桩（from . import revenue_model），仅旧 CLI 辅助函数用，
    插件渲染不走它。
  * 模块级 OUT_DIR / WORK_DIR 默认落在系统临时目录，绝不落仓库或账号目录之外。

用法（原 CLI，插件形态不调用）：
  python bmg_video.py --list 20
  python bmg_video.py --name 一饭一抬眼
  python bmg_video.py --top 3
"""
import os
import re
import sys
import glob
import hashlib
import subprocess
import tempfile

BASE = (os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# 插件形态：资产目录由 engine 在渲染时按 ctx.account_dir 重设，这里只给一个
# 不会落到仓库/账号目录之外的兜底（系统临时目录）。
_DATA = BASE
_WORK = os.path.join(tempfile.gettempdir(), "qishui_lyricvideo")
MUSIC_DIR = os.path.join(_WORK, "音乐")
LRC_DIR = os.path.join(_WORK, "歌词")
OUT_DIR = os.path.join(_WORK, "out")

W, H = 1080, 1920
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"

# 固定的渲染工作目录：drawtext 的 fontfile/textfile 必须用**相对路径**，
# 因为滤镜语法里 ':' 是选项分隔符，绝对路径的盘符 'C:' 会被截断解析失败。
WORK_DIR = os.path.join(_WORK, "_render")

_SKIP = re.compile(r"^\s*(作词|作曲|编曲|制作|混音|母带|和声|出品|监制|统筹|发行|录音|吉他|贝斯|鼓|"
                   r"键盘|弦乐|封面|策划|企划|OP|SP|未经许可)")


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def ffprobe_dur(path):
    """用 ffmpeg -i 的 stderr 解析时长，避免依赖 ffprobe。"""
    exe = ffmpeg_exe()
    try:
        p = subprocess.run([exe, "-i", path], capture_output=True, text=True,
                           errors="replace", timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr or "")
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------- 资产定位
def index_assets():
    """{歌名: (音频路径, 歌词路径)}，文件名形如 0001-歌名.m4a。"""
    out = {}
    for p in glob.glob(os.path.join(MUSIC_DIR, "*.m4a")):
        base = os.path.splitext(os.path.basename(p))[0]
        name = base.split("-", 1)[1] if "-" in base else base
        lrc = os.path.join(LRC_DIR, base + ".lrc")
        out[name] = (p, lrc if os.path.exists(lrc) else None)
    return out


def parse_lrc(path):
    """[(秒, 歌词)]，跳过版权/制作人员行和空行。"""
    rows = []
    if not path or not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8-sig", errors="replace"):
        m = re.match(r"\[(\d+):(\d+)(?:[.:](\d+))?\](.*)", line.strip())
        if not m:
            continue
        t = int(m.group(1)) * 60 + int(m.group(2))
        if m.group(3):
            frac = m.group(3)
            t += int(frac) / (1000.0 if len(frac) == 3 else 100.0)
        txt = m.group(4).strip()
        if not txt or _SKIP.match(txt):
            continue
        if txt.startswith("["):
            continue
        rows.append((t, txt))
    rows.sort()
    # 同时间戳去重
    dedup, last = [], None
    for t, txt in rows:
        if last is not None and abs(t - last) < 0.05:
            continue
        dedup.append((t, txt))
        last = t
    return dedup


# ---------------------------------------------------------------- 画面
def wrap(text, per_line=13):
    """中英文混排的粗折行：按字宽估算，中文算 1 个字位。"""
    lines, cur, w = [], "", 0.0
    for ch in text:
        cw = 1.0 if ord(ch) > 127 else 0.55
        if w + cw > per_line and cur:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines or [text]


def make_background(seed_text, path, top=(24, 28, 44), bottom=(58, 34, 58)):
    from PIL import Image, ImageDraw, ImageFilter
    h = hashlib.md5(seed_text.encode("utf-8")).hexdigest()
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    # 由歌名派生配色：同一首每次一致，不同歌之间能区分；整体偏暗以突出白字
    c1 = (28 + r // 4, 26 + g // 4, 52 + b // 5)
    c2 = (78 + r // 3, 52 + g // 3, 96 + b // 3)
    img = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / (H - 1.0)
        d.line([(0, y), (W, y)], fill=(
            int(c1[0] + (c2[0] - c1[0]) * k),
            int(c1[1] + (c2[1] - c1[1]) * k),
            int(c1[2] + (c2[2] - c1[2]) * k)))
    # 柔光斑 + 中心提亮，避免整块死板
    ov = Image.new("RGB", (W, H), (255, 255, 255))
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-260, -380, 820, 620], fill=34)
    md.ellipse([W - 560, H - 780, W + 240, H + 120], fill=26)
    md.ellipse([W // 2 - 420, H // 2 - 420, W // 2 + 420, H // 2 + 420], fill=16)
    mask = mask.filter(ImageFilter.GaussianBlur(140))
    img = Image.composite(ov, img, mask)
    img.save(path, "PNG")
    return path


def esc_filter_text(s):
    """drawtext 的 text 参数转义（换行已在 textfile 里，这里处理特殊符号）。"""
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\u2019").replace("%", "\\%"))


def prepare_workdir():
    """渲染目录 + 字体就位（相对路径引用，避开滤镜里 ':' 的坑）。"""
    os.makedirs(WORK_DIR, exist_ok=True)
    for src, dst in ((r"C:\Windows\Fonts\msyh.ttc", "msyh.ttc"),
                     (r"C:\Windows\Fonts\msyhbd.ttc", "msyhbd.ttc")):
        d = os.path.join(WORK_DIR, dst)
        if not os.path.exists(d) and os.path.exists(src):
            try:
                import shutil
                shutil.copy(src, d)
            except Exception:
                pass
    return WORK_DIR


def build_filters(seq, title_file, bottom_file=None):
    """滤镜链：底图 → 暗角 → 歌名 → 逐句歌词。

    seq: [(t0, t1, [第1行, 第2行, ...])]
    每行单独一条 drawtext 并各自居中——drawtext 的多行文本只有「整体块」居中，
    行内是左对齐的，直接塞多行会出现第二行贴左边的问题。
    """
    parts = ["[0:v]scale=%d:%d,setsar=1[v0]" % (W, H),
             "[v0]vignette=PI/5[v1]"]
    parts.append(
        "[v1]drawtext=fontfile=msyhbd.ttc:textfile=%s:fontcolor=white:fontsize=72:"
        "x=(w-text_w)/2:y=210:alpha=0.96:shadowcolor=black@0.55:shadowx=0:shadowy=3[t1]"
        % title_file)
    parts.append("[t1]drawbox=x=(iw-300)/2:y=320:w=300:h=3:color=white@0.4:t=fill[t2]")
    cur = "t2"
    if bottom_file:
        parts.append(
            "[%s]drawtext=fontfile=msyh.ttc:textfile=%s:fontcolor=white:fontsize=42:"
            "x=(w-text_w)/2:y=h-250:alpha=0.78:shadowcolor=black@0.5:shadowx=0:shadowy=2[t3]"
            % (cur, bottom_file))
        cur = "t3"
    n_idx = 0
    for t0, t1, blocks in seq:
        n = len(blocks)
        for k in range(n):
            # 垂直居中：整块居中后按行数上下摊开
            off = 40 + int((k - (n - 1) / 2.0) * 86)
            label = "l%d" % n_idx
            parts.append(
                "[%s]drawtext=fontfile=msyh.ttc:textfile=line%03d.txt:fontcolor=white:fontsize=54:"
                "x=(w-text_w)/2:y=(h-text_h)/2%+d:"
                "shadowcolor=black@0.6:shadowx=0:shadowy=4:"
                "alpha='if(lt(t,%0.2f),0,if(lt(t,%0.2f),(t-%0.2f)/0.25,1))':"
                "enable='between(t,%0.2f,%0.2f)'[%s]"
                % (cur, n_idx, off, t0, t0 + 0.25, t0, t0, t1, label))
            cur = label
            n_idx += 1
    parts.append("[%s]format=yuv420p[vout]" % cur)
    return ";".join(parts)


def make_video(name, audio, lrc, seconds=60, out_path=None):
    lines = parse_lrc(lrc)
    if not lines:
        return None, "没有可用歌词（%s）" % (lrc or "缺 lrc")
    dur = ffprobe_dur(audio)
    if not dur:
        return None, "读不出音频时长：%s" % audio
    total = min(seconds, dur) if dur > 0 else seconds

    # 只保留落在 total 之内的句子，并补上结束时间
    seq = []
    for i, (t, txt) in enumerate(lines):
        if t >= total - 1:
            break
        end = lines[i + 1][0] if i + 1 < len(lines) else total
        end = min(end, total)
        if end - t < 0.6:      # 太短的过渡句直接并到下一句
            continue
        seq.append((t, end, txt))
    if not seq:
        return None, "歌词时间轴与音频不匹配"

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = out_path or os.path.join(OUT_DIR, re.sub(r'[\\/:*?"<>|]', "_", name) + ".mp4")
    wd = prepare_workdir()
    bg = make_background(name, os.path.join(wd, "bg.png"))

    def wf(fn, text):
        p = os.path.join(wd, fn)
        open(p, "w", encoding="utf-8").write(text)
        return fn          # 相对路径，滤镜里用

    title_file = wf("title.txt", name)
    # 每句歌词折行后，每「行」写一个独立文本文件（各自居中绘制）
    seq_blocks = []
    n_idx = 0
    for (t0, t1, txt) in seq:
        blocks = []
        for seg in wrap(txt):
            wf("line%03d.txt" % n_idx, seg)
            n_idx += 1
            blocks.append(seg)
        seq_blocks.append((t0, t1, blocks))
    bottom_file = wf("bottom.txt", "完整版在汽水音乐搜：%s" % name)
    flt = build_filters(seq_blocks, title_file, bottom_file)

    cmd = [ffmpeg_exe(), "-y",
           "-loop", "1", "-t", "%.2f" % total, "-i", bg,
           "-i", audio,
           "-filter_complex", flt,
           "-map", "[vout]", "-map", "1:a",
           "-t", "%.2f" % total,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-r", "30", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
           "-movflags", "+faststart",
           out_path]
    # cwd 必须在渲染目录，滤镜里的 fontfile/textfile 才会被正确解析
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=wd)
    if p.returncode != 0 or not os.path.exists(out_path):
        tail = (p.stderr or "")[-900:]
        return None, "ffmpeg 失败：\n" + tail
    return out_path, "%.1fs / %.1f MB" % (total, os.path.getsize(out_path) / 1e6)


# ---------------------------------------------------------------- CLI
def cmd_list(top=20):
    from . import revenue_model as RM
    uid = RM.find_uid()
    data = RM.AccountData(uid)
    idx = index_assets()
    cands = data.pick_candidates(only_zero_pub=True)
    n = 0
    print("%-4s %-26s %9s %8s %s" % ("#", "歌名", "累计播放", "已赚", "素材"))
    print("-" * 74)
    for tid, cum, pub, rv in cands:
        title = data.titles.get(tid, "")
        a, l = idx.get(title, (None, None))
        if not a:
            continue
        n += 1
        print("%-4d %-26s %9d %8.2f %s" % (n, title[:24], cum, rv,
                                           "音频+歌词" if l else "只有音频"))
        if n >= top:
            break
    print("\n共 %d 首可做（抖音投稿=0 且播放最高前 %d）" % (n, top))


def cmd_one(name, seconds=60):
    idx = index_assets()
    if name not in idx:
        # 支持模糊匹配
        hit = [k for k in idx if name in k]
        if len(hit) == 1:
            name = hit[0]
        elif hit:
            print("匹配到多首，请写全名："); [print("  ", h) for h in hit[:10]]
            return
        else:
            print("找不到歌曲：%s" % name)
            return
    audio, lrc = idx[name]
    print("生成中：%s …" % name)
    path, msg = make_video(name, audio, lrc, seconds=seconds)
    if path:
        print("✅ %s\n   %s" % (path, msg))
    else:
        print("❌ %s" % msg)


def cmd_top(n=3, seconds=60):
    from . import revenue_model as RM
    uid = RM.find_uid()
    data = RM.AccountData(uid)
    idx = index_assets()
    done = 0
    for tid, cum, pub, rv in data.pick_candidates(only_zero_pub=True):
        title = data.titles.get(tid, "")
        if title not in idx:
            continue
        audio, lrc = idx[title]
        print("\n[%d/%d] %s（播放 %d，已赚 %.2f）" % (done + 1, n, title, cum, rv))
        path, msg = make_video(title, audio, lrc, seconds=seconds)
        print(("  ✅ " + path + "\n     " + msg) if path else ("  ❌ " + msg))
        done += 1
        if done >= n:
            break
    print("\n完成 %d 首，输出目录：%s" % (done, OUT_DIR))


def main():
    a = sys.argv[1:]
    seconds = 60
    if "--seconds" in a:
        seconds = int(a[a.index("--seconds") + 1])
    if "--list" in a:
        cmd_list(int(a[a.index("--list") + 1]) if len(a) > a.index("--list") + 1 else 20)
    elif "--name" in a:
        cmd_one(a[a.index("--name") + 1], seconds)
    elif "--top" in a:
        cmd_top(int(a[a.index("--top") + 1]) if len(a) > a.index("--top") + 1 else 3, seconds)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
