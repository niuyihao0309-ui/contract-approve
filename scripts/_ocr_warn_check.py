# -*- coding: utf-8 -*-
"""验证：OCR 关掉时，上传页在**选文件前**就出现「未启用 OCR」提示。用完即删。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
sys.path.insert(0, r"C:\Users\NYH\.workbuddy\skills\frontend-page-verify-cdp\scripts")
from cdp import Chrome  # noqa: E402

BASE = "http://127.0.0.1:8099"

ok = True


def check(name, cond, extra=""):
    global ok
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  {extra}"))
    ok = ok and cond


# 1. 后端 /health 必须下发 ocr_enabled=false
with urllib.request.urlopen(BASE + "/api/v1/health", timeout=15) as r:
    h = json.loads(r.read().decode())
print(f"/health：ocr_enabled={h.get('ocr_enabled')!r}  llm_ready={h.get('llm_ready')!r}")
check("/health 下发 ocr_enabled 且为 false", h.get("ocr_enabled") is False, f"got {h.get('ocr_enabled')!r}")

# 2. 拿令牌
req = urllib.request.Request(
    BASE + "/api/v1/auth/login",
    data=json.dumps({"username": "zhang.wei", "password": "operator123"}).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=15) as r:
    body = json.loads(r.read().decode())
token = body["token"]

# 3. 浏览器：注入登录态 → 进上传页 → 断言提示文字
with Chrome() as ch:
    page = ch.new_page()
    page.set_viewport(1600, 1000)
    page.goto(BASE + "/")
    page.seed_storage("ca.auth", json.dumps({"token": token, "user": body.get("user")}), BASE)
    page.goto(BASE + "/upload", wait_ms=7000)

    txt = page.eval("document.body.innerText")
    path = page.eval("location.pathname")
    print(f"\n路由：{path}")
    check("进入上传页", "/upload" in str(path), f"pathname={path}")
    check(
        "选文件前就出现「未启用 OCR」提示",
        "未启用 OCR" in str(txt) and "图片 / 扫描件无法解析" in str(txt),
        f"文本前 300 字：{str(txt)[:300]}",
    )
    page.screenshot(r"D:\ke_cheng\XM\deploy-demo\_ocr_warn.png")
    print("\n截图：D:\\ke_cheng\\XM\\deploy-demo\\_ocr_warn.png")

print("\n结果：" + ("全通过" if ok else "有失败"))
raise SystemExit(0 if ok else 1)
