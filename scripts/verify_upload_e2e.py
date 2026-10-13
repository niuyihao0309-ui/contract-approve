"""上传 → 解析 → 高亮 的端到端验收（真浏览器 + 真后端）。

为什么单独有这么一个脚本
------------------------
`verify_pages.spec.json` 那套回归只验「身份 × 页面」是否收敛（菜单、按钮、路由守卫），
它**不会真的上传一份合同**。而上传这条链路恰恰出过一个只有真跑才能发现的 bug：

    2026-09-19：`POST /tasks/upload` 在流水线跑完后写
        session.expire_all()
        task = await TaskService.get(session, task.id)   # ← 实参 task.id 在 await 之外求值
    expire_all() 让 ORM 对象全部过期，读 `task.id` 触发**同步**惰性重载 →
    `sqlalchemy.exc.MissingGreenlet` → 接口 500。流水线与落库其实都成功了，
    但响应体拿不到任务对象，前端表现就是「传了文件却解析不出来」——
    点提交按钮、看提示、翻截图，全都像是「解析没做」而不是「接口挂了」。

所以这里验的是**用户视角的闭环**：文件进去 → 风险清单出来 → 原文高亮能对上。

两种模式
--------
1. **单文件**（默认）：走 `POST /tasks/upload` 的等价路径（上传页提交），
   验结果区、双向联动、示范条款。
2. **多文件**（`--multi`）：一次注入 N 份文件，走 `POST /tasks/upload-batch`。
   除上述断言外，还验「一份文件一条任务 + 同批次」「文件切换条个数」
   「点某一份 → 右侧风险清单归属跟着切」「大盘上能看到批次标」。
   ⚠️ 这一组必须真点，因为多文件的核心风险就是「右侧显示的是不是当前那份的清单」。

前置
----
1. 后端：`uvicorn app.main:app --port 8000`（Docker MySQL 3307 要通）
2. 前端：`cd frontend && npm run dev`，端口写进 `--base`（5173 常被旧进程占，vite 会退到 5174）

用法
----
    .venv/Scripts/python.exe scripts/verify_upload_e2e.py
    .venv/Scripts/python.exe scripts/verify_upload_e2e.py --sample 大额销售合同_风险版.pdf
    .venv/Scripts/python.exe scripts/verify_upload_e2e.py --multi
    .venv/Scripts/python.exe scripts/verify_upload_e2e.py --multi-samples 软件采购合同_风险版.docx 大额销售合同_风险版.pdf
    .venv/Scripts/python.exe scripts/verify_upload_e2e.py --keep     # 保留建出来的任务

退出码：0 = 全通过；1 = 有断言失败；2 = 环境/启动失败。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 复用技能里的零依赖 CDP 客户端（本机没有 playwright / agent-browser）
CDP_DIR = Path(r"C:\Users\NYH\.workbuddy\skills\frontend-page-verify-cdp\scripts")
sys.path.insert(0, str(CDP_DIR))
try:
    from cdp import Chrome  # noqa: E402
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"加载 CDP 客户端失败（{CDP_DIR}）：{exc}")

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
SHOTS = ROOT / "docs" / "screenshots"

MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

OPERATOR = ("zhang.wei", "operator123")
ADMIN = ("admin", "admin123")

#: `--multi` 默认用的两份样例（都在 samples/ 下；缺哪份就跳过哪份）
MULTI_SAMPLES = ["软件采购合同_风险版.docx", "大额销售合同_风险版.pdf"]


# ----------------------------------------------------------------------
# 断言收集
# ----------------------------------------------------------------------
class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, kind: str, ok: bool, detail: str = "") -> None:
        self.rows.append((kind, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  {kind:<28} {mark}  {detail}")

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.rows if not ok)


def wait_js(page, expr: str, timeout: float = 30.0, interval: float = 0.3):
    """轮询一段返回真值的 JS，直到为真或超时。返回最后一次的值。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = page.eval(expr)
        except Exception:
            last = None
        if last:
            return last
        time.sleep(interval)
    return last


# ----------------------------------------------------------------------
# 后端小工具（登录 / 清理）
# ----------------------------------------------------------------------
def api(api_base: str, path: str, *, token: str | None = None, payload: dict | None = None, method: str | None = None):
    req = urllib.request.Request(api_base + path, method=method or ("POST" if payload else "GET"))
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login(api_base: str, account: tuple[str, str]) -> dict:
    return api(api_base, "/auth/login", payload={"username": account[0], "password": account[1]})


def delete_task(api_base: str, token: str, task_id: str) -> None:
    try:
        api(api_base, f"/tasks/{task_id}", token=token, method="DELETE")
    except Exception as exc:  # 清理失败不该让整轮验收变红
        print(f"  （清理任务 {task_id[:8]} 失败：{type(exc).__name__}: {exc}）")


# ----------------------------------------------------------------------
# 注入文件：把样例内容塞进 <input type=file>（支持一次多份）
# ----------------------------------------------------------------------
JS_SET_FILES = """
(files) => {
  const dt = new DataTransfer();
  for (const f of files) {
    const bin = atob(f.b64);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    dt.items.add(new File([buf], f.name, { type: f.mime }));
  }
  const input = document.querySelector('input[type=file]');
  if (!input) return 'no-input';
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return 'ok';
}
"""

JS_CLICK_SUBMIT = """
(() => {
  const b = [...document.querySelectorAll('.el-button')]
    .find(x => x.textContent.trim().includes('提交审查申请'));
  if (!b) return 'no-button';
  if (b.classList.contains('perm-denied') || b.hasAttribute('disabled')) return 'disabled';
  b.click();
  return 'ok';
})()
"""

#: 上传页在「解析完成」后才渲染的结果区：风险卡片 / 原文高亮 / 结论
JS_RESULT_READY = "(() => document.querySelectorAll('.rk-card').length)()"
JS_COUNT_MARKS = """
(() => {
  const q = (s) => document.querySelectorAll(s).length;
  return {
    high: q('mark.hl-high'), medium: q('mark.hl-medium'), low: q('mark.hl-low'),
    blocks: q('.doc-block'), risks: q('.rk-card'), chips: q('.batch-chip'),
    activeChips: q('.batch-chip.active'),
  };
})()
"""

#: 当前右侧风险清单归属哪一份文件（批次模式下由 .rk-owner 标出）
JS_RISK_OWNER = "(() => { const e = document.querySelector('.rk-owner'); return e ? e.textContent.trim() : '' })()"

#: 提交进度条快照。钉的是「字节传完就顶到 100%，按钮却还在转圈」这个撒谎式进度：
#:   text          进度条右侧文案（完成态才是 100%）
#:   indeterminate 不确定态（内层整条左右扫描）
#:   striped       条纹（配合 striped-flow = 条纹滚动，也就是「流线」）
#:   loading       提交按钮是否还在 loading —— 只要它还在转，就说明活儿没干完
JS_PROGRESS_SNAPSHOT = """
(() => {
  const wrap = document.querySelector('.up-progress');
  if (!wrap) return { visible: false };
  const t = wrap.querySelector('.el-progress__text');
  const st = wrap.querySelector('.up-progress-text');
  return {
    visible: true,
    text: t ? t.textContent.trim() : '',
    indeterminate: !!wrap.querySelector('.el-progress-bar__inner--indeterminate'),
    striped: !!wrap.querySelector('.el-progress-bar__inner--striped'),
    stage: st ? st.textContent.trim() : '',
    loading: !!document.querySelector('.el-button.is-loading'),
  };
})()
"""


def js_click_chip(i: int) -> str:
    """点第 i 个文件切换条（批次模式）。"""
    return (
        f"(() => {{ const c = document.querySelectorAll('.batch-chip')[{i}];"
        f" if (!c) return 'no-chip'; c.click(); return 'ok'; }})()"
    )


def js_chip_status(i: int) -> str:
    """读第 i 份文件在切换条上的状态（上传页/TaskDetail 都挂了 data-batch-status）。"""
    return (
        f"(() => {{ const c = document.querySelectorAll('.batch-chip')[{i}];"
        f" return c ? (c.dataset.batchStatus || '') : ''; }})()"
    )


def run(args) -> int:
    api_base = args.api.rstrip("/")
    base = args.base.rstrip("/")

    names = args.multi_samples or (MULTI_SAMPLES if args.multi else [args.sample])
    files = [SAMPLES / n for n in names]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        print(f"样例文件不存在：{missing}")
        return 2
    multi = len(files) > 1
    stems = {f.stem for f in files}

    try:
        admin = login(api_base, ADMIN)["token"]
    except Exception as exc:
        print(f"后端不通（{api_base}）：{type(exc).__name__}: {exc}")
        return 2

    res = Result()
    short = ""
    payload = [
        {
            "b64": base64.b64encode(f.read_bytes()).decode(),
            "name": f.name,
            "mime": MIME.get(f.suffix.lower(), "application/octet-stream"),
        }
        for f in files
    ]

    # 跑之前先给已有任务存个快照：清理时**只删本轮新建的**。
    # 踩过：一开始按「标题 == 样例名」删，把用户之前跑出来的同名演示任务一起删掉了
    # —— 验收脚本绝不能删别人的数据。
    try:
        before_ids = {
            it["id"] for it in (api(api_base, "/tasks?limit=200", token=admin).get("items") or [])
        }
    except Exception:
        before_ids = set()

    with Chrome(headless=not args.headed) as ch:
        page = ch.new_page()
        page.set_viewport(1600, 1000)
        # 种登录态：必须是「页面脚本执行前」写入，否则 Pinia 初始化时读不到
        auth = login(api_base, OPERATOR)
        page.seed_storage("ca.auth", json.dumps(auth, ensure_ascii=False), base)

        page.goto(f"{base}/upload")
        if not page.wait_ready(timeout=args.timeout):
            print("上传页没渲染出来（h2 未出现）")
            return 2
        res.add("上传页可达", True, page.eval("location.pathname"))

        # ---- 1. 选文件（一次注入 N 份） ----
        got = page.eval(f"({JS_SET_FILES})({json.dumps(payload)})")
        res.add(
            f"注入 {len(files)} 份文件",
            got == "ok",
            f"{'、'.join(f.name for f in files)} / {got}",
        )
        if got != "ok":
            return 1
        picked = wait_js(page, "(() => document.querySelectorAll('.picked-name').length)()", 8)
        res.add("文件已选中", picked == len(files), f"已选 {picked} 份 / 期望 {len(files)} 份")
        if picked != len(files):
            return 1

        # ---- 2. 提交 ----
        got = page.eval(JS_CLICK_SUBMIT)
        res.add("点击提交审查申请", got == "ok", str(got))
        if got != "ok":
            return 1

        # ---- 3. 等结果区渲染（解析 + 审查 + 前端装配） ----
        # 顺便在这一段里采样提交进度条 —— 它只在「提交中」存在，跑完就收起，
        # 所以必须一边等结果一边抓，事后补是抓不到的。
        snap = {"bad100": False, "flow": False, "done": False, "n": 0, "seen": []}
        n = 0
        deadline = time.time() + args.wait_result
        while time.time() < deadline:
            try:
                s = page.eval(JS_PROGRESS_SNAPSHOT) or {}
            except Exception:
                s = {}
            rk = page.eval(JS_RESULT_READY) or 0
            if rk:
                n = rk
            if s.get("visible"):
                snap["n"] += 1
                snap["seen"].append(s.get("text") or "")
                if s.get("loading") and s.get("text") == "100%":
                    snap["bad100"] = True
                if s.get("indeterminate") or s.get("striped"):
                    snap["flow"] = True
                if s.get("text") == "100%":
                    snap["done"] = True
            # 结果区出来了、进度条也收起了 → 整段提交结束
            if rk and not s.get("visible"):
                break
            time.sleep(0.2)

        sample_txt = "/".join(t for t in snap["seen"][:8] if t) or "（没采到）"
        res.add(
            "提交中不显示 100%",
            not snap["bad100"],
            "按钮 loading 期间抓到 100%（撒谎式进度）"
            if snap["bad100"]
            else f"采样 {snap['n']} 次：{sample_txt}",
        )
        res.add(
            "提交中走流线加载",
            snap["flow"],
            "抓到条纹/扫描中间态"
            if snap["flow"]
            else f"没抓到流线中间态（{sample_txt}）",
        )
        res.add(
            "跑完才落到 100%",
            snap["done"],
            "抓到完成态 100%" if snap["done"] else f"没抓到完成态（{sample_txt}）",
        )

        if not n:
            n = wait_js(page, JS_RESULT_READY, 5)
        res.add("出风险卡片（解析+审查完成）", bool(n), f"{n} 张（等待上限 {args.wait_result:.0f}s）")
        if not n:
            page.screenshot(str(SHOTS / "_debug_upload_result.png"))
            return 1

        # 任务 id 从 URL 拿不到（提交后留在本页），从页面状态里取
        task_id = page.eval(
            "(() => { const t = document.querySelector('.task-id'); return t ? t.textContent.trim() : '' })()"
        ) or ""
        short = task_id or "(未取到短 id)"

        counts = page.eval(JS_COUNT_MARKS)
        res.add("原文块已渲染", counts["blocks"] > 0, f"{counts['blocks']} 个 .doc-block")
        res.add(
            "原文风险高亮存在",
            (counts["high"] + counts["medium"] + counts["low"]) > 0,
            f"高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}",
        )

        # ---- 4. 双向联动：点卡片 → 原文高亮被激活 ----
        page.eval("document.querySelector('.rk-card').click()")
        before = page.eval("(() => document.querySelectorAll('mark.hl-active').length)()")
        time.sleep(0.6)
        after = page.eval("(() => document.querySelectorAll('mark.hl-active').length)()")
        res.add("卡片 → 原文高亮联动", bool(after), f"活跃高亮 {before} → {after}")

        # ---- 5. 双向联动：点原文高亮 → 卡片被选中 ----
        clicked = page.eval(
            """(() => {
              const m = document.querySelector('mark.hl-high, mark.hl-medium, mark.hl-low');
              if (!m) return false;
              m.click();
              return true;
            })()"""
        )
        active_card = wait_js(
            page, "(() => document.querySelectorAll('.rk-card.active').length)()", 5
        )
        res.add("原文高亮 → 卡片联动", bool(clicked) and bool(active_card), f"选中 {active_card} 张卡片")

        # ---- 6. 修改建议文本 ----
        sug = page.eval(
            """(() => {
              const els = [...document.querySelectorAll('.rk-sug-text')];
              return {n: els.length, chars: els.reduce((a, e) => a + e.textContent.trim().length, 0)};
            })()"""
        )
        res.add(
            "修改示范条款可读",
            sug["n"] > 0 and sug["chars"] > 0,
            f"{sug['n']} 条建议 / 合计 {sug['chars']} 字",
        )

        # ---- 7. 多文件：一份文件一条任务 + 文件切换条 + 「点哪份就看哪份」 ----
        if multi:
            n_chip = wait_js(
                page, "(() => document.querySelectorAll('.batch-chip').length)()", 15
            )
            res.add(
                "批次文件切换条齐全（一份文件一条任务）",
                n_chip == len(files),
                f"{n_chip} 个 / 期望 {len(files)} 份",
            )

            ok_all = True
            detail: list[str] = []
            for i, f in enumerate(files):
                got = page.eval(js_click_chip(i))
                owner = wait_js(page, JS_RISK_OWNER, 12)
                c = page.eval(JS_COUNT_MARKS)
                st = page.eval(js_chip_status(i))
                # 归属必须对（这是本功能的核心）；已完成的那份还必须有原文块与风险项。
                # 扫描件/模糊图在没装 OCR 时会落到 blocked —— 那是既定行为，不算失败。
                good = (
                    got == "ok"
                    and owner == f.name
                    and c["activeChips"] == 1
                    and (st != "completed" or (c["blocks"] > 0 and c["risks"] > 0))
                )
                ok_all = ok_all and good
                detail.append(
                    f"#{i + 1}「{st or '?'}」归属"
                    f"{'对' if owner == f.name else f'错(实为「{owner}」)'}"
                    f"/原文{c['blocks']}块/风险{c['risks']}条"
                )
            # 这是多文件功能的核心：右侧清单必须跟着点的那份文件走
            res.add("点某份 → 右侧只显示该份的清单", ok_all, "；".join(detail))

            rows = api(api_base, "/tasks?limit=200", token=admin).get("items") or []
            mine = [
                r
                for r in rows
                if r["id"] not in before_ids and (r.get("title") or "") in stems
            ]
            res.add(
                "接口层：N 条任务同属一批",
                len(mine) == len(files)
                and all(r.get("batch_total") == len(files) for r in mine)
                and len({r.get("batch_seq") for r in mine}) == len(files),
                f"{len(mine)} 条 / 批次标 "
                + "、".join(f"{r.get('batch_seq')}/{r.get('batch_total')}" for r in mine),
            )

        SHOTS.mkdir(parents=True, exist_ok=True)
        shot = SHOTS / (
            f"16c_upload_batch_{len(files)}份.png"
            if multi
            else f"16_upload_result_{files[0].stem}.png"
        )
        page.screenshot(str(shot), full_page=True)
        print(f"  截图：{shot}")

        # ---- 8. 大盘：批次标要真的渲染出来（多文件时） ----
        if multi:
            page.goto(f"{base}/")
            n_tag = wait_js(
                page,
                "(() => document.querySelectorAll('.batch-tag').length)()",
                args.timeout,
            )
            res.add(
                "大盘渲染出批次标",
                n_tag and n_tag >= len(files),
                f"{n_tag} 个 .batch-tag",
            )
            page.screenshot(str(SHOTS / "04b_dashboard_batch.png"), full_page=True)

        # ---- 9. 工作台：批次切换条点一下就换单 ----
        # 这段最容易静默失效：批次切换是**同一个路由换参数**，组件不会重建，
        # 只改 URL 不重载的话页面会「点着没反应」，而截图看着完全正常。
        if multi and mine:
            start = f"/tasks/{mine[0]['id']}"
            page.goto(f"{base}{start}")
            n = wait_js(
                page,
                "(() => document.querySelectorAll('.batch-chip').length)()",
                args.timeout,
            )
            res.add("工作台批次切换条", n == len(files), f"{n} 个 / 期望 {len(files)} 份")

            got = page.eval(
                # 必须点**不是当前**的那一份：点当前项按设计就是空操作，
                # 拿它当断言目标会得到假的 FAIL
                "(() => { const c = [...document.querySelectorAll('.batch-chip')]"
                ".find(x => !x.classList.contains('active'));"
                " if (!c) return 'no-inactive-chip'; c.click(); return 'ok'; })()"
            )
            swapped = wait_js(
                page,
                f"(() => location.pathname !== {json.dumps(start)}"
                " && location.pathname.startsWith('/tasks/')"
                " && document.querySelectorAll('.doc-block').length > 0)()",
                args.timeout,
            )
            active_n = page.eval("(() => document.querySelectorAll('.batch-chip.active').length)()")
            res.add(
                "点切换条 → 换到那一份的详情",
                got == "ok" and bool(swapped) and active_n == 1,
                f"{start} → {page.eval('location.pathname')}（当前项标记 {active_n} 个）",
            )
            page.screenshot(str(SHOTS / "11b_workbench_batch.png"), full_page=True)

    print("-" * 66)
    print(f"合计 {len(res.rows)} 条断言：{len(res.rows) - res.failed} 通过 / {res.failed} 失败")

    if args.keep:
        print(f"任务短 id：{short}（--keep 已保留，可在审查大盘查看）")
    else:
        # 只清本轮新建的：任务 id 不在跑前快照里的，才是这次上传出来的；
        # 标题限定在本次用到的样例名上，避免误删历史数据
        try:
            rows = api(api_base, "/tasks?limit=200", token=admin).get("items") or []
            fresh = [
                r["id"]
                for r in rows
                if r["id"] not in before_ids and (r.get("title") or "") in stems
            ]
            for tid in fresh:
                delete_task(api_base, admin, tid)
            if fresh:
                print(f"已清理本轮新建的 {len(fresh)} 条任务（加 --keep 可保留）")
        except Exception as exc:
            print(f"（清理本轮任务失败：{type(exc).__name__}: {exc}）")

    return 1 if res.failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="上传 → 解析 → 高亮 端到端验收")
    ap.add_argument("--base", default="http://localhost:5173", help="前端 dev server 地址")
    ap.add_argument("--api", default="http://127.0.0.1:8000/api/v1", help="后端接口前缀")
    ap.add_argument("--sample", default="软件采购合同_风险版.docx", help="samples/ 下的样例文件名")
    ap.add_argument("--multi", action="store_true", help="多文件模式：一次注入 MULTI_SAMPLES 里的样例")
    ap.add_argument(
        "--multi-samples",
        nargs="+",
        default=None,
        dest="multi_samples",
        help="多文件模式自定义样例（2 份以上），给了它就等同于开了 --multi",
    )
    ap.add_argument("--timeout", type=float, default=25.0, help="首屏渲染等待秒数")
    ap.add_argument("--wait-result", type=float, default=180.0, dest="wait_result", help="等结果区出现的秒数")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头）")
    ap.add_argument("--keep", action="store_true", help="保留建出来的任务，不清理")
    return run(ap.parse_args())


if __name__ == "__main__":
    # 本机沙箱全量走代理，本地回环必须绕过，否则 urlopen 会拿到 502
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    sys.exit(main())
