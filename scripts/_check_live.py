"""用真实浏览器（CDP 无头 Chrome）走一遍线上演示链接的登录流程。

用完即删。用法：
    .venv/Scripts/python.exe scripts/_check_live.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE = "https://contract-approve-demo.app.workbuddy.host"

sys.path.insert(0, r"C:\Users\NYH\.workbuddy\skills\frontend-page-verify-cdp\scripts")
from cdp import Chrome  # noqa: E402

SHOTS = Path(__file__).resolve().parents[1] / "docs" / "screenshots"

JS = """
(() => ({
  path: location.pathname,
  h2: (document.querySelector('h2') || {}).textContent?.trim() || '',
  rows: document.querySelectorAll('.el-table__row').length,
  menus: document.querySelectorAll('.el-menu-item').length,
  err: (document.querySelector('.el-message--error') || {}).textContent?.trim() || '',
}))()
"""


def main() -> int:
    with Chrome(headless=True) as ch:
        page = ch.new_page()
        page.set_viewport(1600, 1000)
        page.goto(BASE + "/login")
        page.wait_ready(timeout=40)
        time.sleep(1.5)
        print("登录页：", page.eval(JS))

        # 点演示账号填充（如果页面有）→ 直接填账号密码 → 提交
        ok = page.eval(
            """
            (() => {
              const u = document.querySelector('input[placeholder*="账号"], input[type=text]');
              const p = document.querySelector('input[type=password]');
              if (!u || !p) return 'no-input';
              const set = (el, v) => {
                const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                s.call(el, v);
                el.dispatchEvent(new Event('input', { bubbles: true }));
              };
              set(u, 'admin'); set(p, 'admin123');
              return 'filled';
            })()
            """
        )
        print("填表：", ok)
        clicked = page.eval(
            """
            (() => {
              const b = [...document.querySelectorAll('button')]
                .find(x => x.textContent.trim().includes('登') && !x.disabled);
              if (!b) return 'no-button';
              b.click();
              return 'ok';
            })()
            """
        )
        print("点登录：", clicked)
        time.sleep(4)
        st = page.eval(JS)
        print("登录后：", st)
        page.screenshot(str(SHOTS / "17_deploy_live_check.png"), full_page=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
