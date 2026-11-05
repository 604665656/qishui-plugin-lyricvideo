# 汽水音乐创作者工具 · 歌词视频插件

把歌词自动渲染成竖屏视频（ffmpeg 合成），用于抖音/汽水分发。

## 这是什么

主程序「汽水音乐发布工作台」把功能拆成了独立插件，本仓库是其中之一。
它既能被 [qishui-host](https://github.com/604665656/qishui-host) 加载成一个页签，
也能单独打成 EXE 独立运行——独立运行时自带内核，不需要宿主。

## 能力依赖

宿主/内核需要提供的核心能力：`api、auth、store`（`api_version` 2）。

## 用法

```bash
# 作为插件：放进 qishui-host 的 plugins/ 目录即可被加载
# 独立运行界面：
python -m lyricvideo
# 无界面跑一次（计划任务用）：
python -m lyricvideo --task
```

## 打包成独立 EXE

```bash
pip install pyinstaller
python build_exe.py --onedir
```

## 更新

`plugin.json` 的 `update.feed` 留空时，宿主只在手动点「检查更新」时读
`update.json`。把 `update.json` 放到一个能被 `raw.githubusercontent.com`
访问的地址，再把地址填进 `update.feed` 即可开启热更新。

## 开源协议

MIT —— 与整套工具一致，每个插件独立开源、独立发版。
