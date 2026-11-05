# -*- coding: utf-8 -*-
"""compat.py —— 主程序旧模块名 → qishui_core 新接口的桥接。

规则 6 要求：插件若保留了 `import account_store as AS` /
`from datapath import base_dir` / `import api_client as A` /
`import cdp_lite as C` 这类旧写法，统一改到 qishui_core，并在这个文件里
把旧名重新导出，保证任何遗留代码用旧名也能跑。

映射：
  import account_store as AS  →  from qishui_core import store as AS
  from datapath import base_dir → from qishui_core.paths import data_root
  import api_client as A      →  from qishui_core import api as A
  import cdp_lite as C        →  from qishui_core import cdp_lite as C

注意：本插件已不再使用这些旧名（asset 路径改走 ctx，API 走 ctx.api，
认证走 ctx.auth，CDP 走 ctx.accounts），此文件主要为兼容与文档保留。
"""
from qishui_core import store as AS
from qishui_core import api as A
from qishui_core import cdp_lite as C
from qishui_core.paths import data_root

# 旧代码里 datapath.base_dir(BASE) 的等价写法
base_dir = data_root

# account_store 的旧接口：all() / entries() / get() 等已在 qishui_core.store 中，
# 额外补两个常用别名以便旧调用通过。
all = AS.all           # noqa: A001  旧名 all → store.all
entries = AS.entries   # 旧名 entries(True) 与 store.entries(True) 一致


__all__ = ["AS", "A", "C", "data_root", "base_dir", "all", "entries"]
