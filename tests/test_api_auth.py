"""接口级回归：登录鉴权 + 服务端权限边界 + **批量删除要真的删掉**。

第 1、2 组（鉴权 / 权限边界）是原 `_smoke_auth.py` 的正式化 —— 那个临时冒烟脚本跑完就删了，
但它验出来的行为不能跟着一起消失，所以落成永久测试。

第 3 组是 2026-09-19 跑前端回归时顺带抓到的**真 bug**：
``POST /tasks/batch`` 的 ``delete`` 分支在 ``session.delete()`` 之后直接 ``continue``，
**跳过了循环尾部的 ``await session.commit()``** —— 于是接口回报
「批量删除：成功 N 条」而数据一条没少（假成功，前端还弹成功提示）。
这个 bug 光跑前端回归看不出来（按钮可用、提示成功），只有接口级断言能钉住。

依赖 Docker MySQL（3307）。连不上就整体 skip，不影响那批纯单测。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.db.base import init_db, session_scope
from app.main import app
from app.services.user_service import ensure_seed_users

ADMIN = ("admin", "admin123")
LAWYER = ("li.jing", "lawyer123")
OPERATOR = ("zhang.wei", "operator123")
#: 测试建单用的标题前缀，跑完按这个前缀清理（避免留脏数据）
TMP_PREFIX = "_pytest_权限边界测试"


# ======================================================================
# 夹具
# ======================================================================
@pytest.fixture
async def client():
    """起一个直连 ASGI 的客户端（不依赖 uvicorn 有没有起）。"""
    try:
        await init_db()
        async with session_scope() as s:
            await ensure_seed_users(s)
    except Exception as exc:  # 没起 MySQL 就跳过，别让整批测试红掉
        pytest.skip(f"MySQL 不可达，跳过接口级回归：{type(exc).__name__}: {exc}")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60.0) as c:
        yield c


async def _login(c: httpx.AsyncClient, account: tuple[str, str]) -> dict:
    """登录并返回 ``{"token": ..., "user": ...}``。"""
    r = await c.post("/api/v1/auth/login", json={"username": account[0], "password": account[1]})
    assert r.status_code == 200, f"登录 {account[0]} 失败：HTTP {r.status_code} {r.text[:120]}"
    return r.json()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _purge_tmp_tasks(c: httpx.AsyncClient, token: str) -> None:
    """清掉本测试留下的任务（幂等，可重复调用）。"""
    r = await c.get("/api/v1/tasks", params={"limit": 100}, headers=_h(token))
    if r.status_code != 200:
        return
    ids = [
        it["id"]
        for it in (r.json().get("items") or [])
        if (it.get("title") or "").startswith(TMP_PREFIX)
    ]
    if ids:
        await c.post(
            "/api/v1/tasks/batch",
            json={"action": "delete", "task_ids": ids},
            headers=_h(token),
        )


# ======================================================================
# 1. 未登录拦截 / 令牌
# ======================================================================
async def test_anon_is_blocked(client):
    r = await client.get("/api/v1/tasks", params={"limit": 1})
    assert r.status_code == 401, f"未带令牌竟然放行了：HTTP {r.status_code}"
    assert r.json().get("code") in {"UNAUTHORIZED", "TOKEN_EXPIRED"}, r.text[:120]

    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401

    # 注：HTTP 头只能是 ASCII，所以这里用英文假令牌，别写中文
    r = await client.get("/api/v1/tasks", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401, "伪造令牌竟然放行了"


async def test_token_transports(client):
    """令牌三种带法都要能用（`?token=` 是给 `window.open` / `<img src>` 直连用的）。"""
    body = await _login(client, ADMIN)
    tok = body["token"]

    r = await client.get("/api/v1/tasks", params={"limit": 1}, headers=_h(tok))
    assert r.status_code == 200, "Authorization: Bearer 不认"
    r = await client.get("/api/v1/tasks", headers={"X-Auth-Token": tok})
    assert r.status_code == 200, "X-Auth-Token 不认"
    r = await client.get("/api/v1/tasks", params={"token": tok, "limit": 1})
    assert r.status_code == 200, "?token= 不认"


async def test_gateway_authorization_does_not_block_other_sources(client):
    """网关注入的 ``Authorization`` 不能把其余令牌来源堵死。

    背景（2026-09-19 线上踩过）：发布到云平台后，反向代理**哪怕客户端一个头都没带**，
    也会自己注入 ``Authorization: Bearer ey7DJIh9…``（平台边缘→沙箱的内部凭据）。
    它长得也是 Bearer，但**没有 ``.`` 分隔符**。旧逻辑见 ``Authorization`` 就认，
    于是 ``X-Auth-Token`` / ``?token=`` 永远轮不到 —— 本机一切正常、一上线就 401，
    三种带法全挂，非常像「令牌机制坏了」，其实是取值顺序的问题。

    修法分两步，缺一不可：① 按「长相」过滤（``base64url.base64url``，恰好一个点）；
    ② **把代理能改写的 ``Authorization`` 排到队尾**。只做①不做②，遇到单点形状的注入
    仍会被抢先命中 —— 参 `gateway_dotted` 那条。
    """
    body = await _login(client, ADMIN)
    tok = body["token"]
    # 三种网关注入形态：
    #   ① 像 Bearer 但没有点（线上真实踩到的）
    #   ② Basic 形态
    #   ③ **恰好一个点的形状** —— 这条专门钉「顺序」：光靠「长得像不像我们令牌」
    #      过滤挡不住它，只有把「网关唯一能改写的 Authorization」排到最后才安全。
    #      收紧过滤规则反而会让①之外的真实形态漏网，所以修的是顺序，不是正则。
    # HTTP 头只能是 ASCII，别写中文。
    gateway_bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3OTAwNjk2ODgsImlhdCI6MTc4OTgxMDQ4OCwiaXNzIjoiY2xvdWRzdHVkaW8ubmV0IiwianRpIjoiMGM4YjJhZTUtYzZiNy00NGU2LTgwY2ItMmUxMTIwZTNmMDU4IiwicmVnaW9uIjoic2g3Iiwic3BhY2UiOiJhNDMxZmJkNWJkYjc0YzkwOWZhYzJlMjA1OGNiMWRiMiIsInRva2VuIjoiIiwidXNlcl9pZCI6IjM2NzI5NCIsInVzZXJfbmlja25hbWUiOiLkvZPpqoznlKjmiLcifQ.vJcM40d3e9Ygj4v2wpIl-OM9g6PBLjEU14pxqUT0wLo"
    gateway_basic = "Basic Z2F0ZXdheTpzb21lLWludGVybmFsLWNyZWRlbnRpYWw="
    gateway_dotted = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.c2stcHJvZC1nYXRld2F5"

    for junk in (gateway_bearer, gateway_basic, gateway_dotted):
        # 网关的 Authorization + 正确的 X-Auth-Token → 必须放行
        r = await client.get(
            "/api/v1/tasks",
            params={"limit": 1},
            headers={"Authorization": junk, "X-Auth-Token": tok},
        )
        assert r.status_code == 200, (
            f"网关的 Authorization 堵死了 X-Auth-Token（{junk[:20]}…）：HTTP {r.status_code}"
        )

        # 网关的 Authorization + ?token= → 必须放行
        r = await client.get(
            "/api/v1/tasks", params={"token": tok, "limit": 1}, headers={"Authorization": junk}
        )
        assert r.status_code == 200, (
            f"网关的 Authorization 堵死了 ?token=（{junk[:20]}…）：HTTP {r.status_code}"
        )

        # 只有网关凭据、没有任何我们的令牌 → 仍然 401（不能因为跳过就放行匿名）
        r = await client.get("/api/v1/tasks", params={"limit": 1}, headers={"Authorization": junk})
        assert r.status_code == 401, "只有网关凭据竟然放行了"


async def test_health_exposes_capability_flags(client):
    """``/health`` 必须下发能力位：上传页靠它在**选文件时**提示「OCR 没开」。

    背景（2026-09-19 演示版踩到）：发布包剥掉了 rapidocr，图片提交后必然落到
    「解析受阻：OCR 引擎不可用」—— 行为是设计内降级，但提示出现在提交之后，
    用户以为系统坏了。前端现在读 ``/health`` 的 ``ocr_enabled`` 提前警告；
    这个键要是被删掉或改名，警告会**静默失效**，所以在这里钉住。
    """
    r = await client.get("/api/v1/health")
    assert r.status_code == 200, f"/health 挂了：HTTP {r.status_code}"
    body = r.json()
    assert isinstance(body.get("ocr_enabled"), bool), f"/health 缺 ocr_enabled 能力位：{body}"


# ======================================================================
# 2. 登录边界与权限边界
# ======================================================================
async def test_login_rejects_bad_credentials(client):
    r = await client.post("/api/v1/auth/login", json={"username": "li.jing", "password": "错的"})
    assert r.status_code == 401, f"错误密码竟然放行：HTTP {r.status_code}"

    r = await client.post("/api/v1/auth/login", json={"username": "查无此人", "password": "whatever1"})
    assert r.status_code == 401, "不存在的账号应与错误密码**同样** 401（否则可枚举账号）"


async def test_all_seed_accounts_can_login(client):
    for account in (LAWYER, OPERATOR, ADMIN):
        body = await _login(client, account)
        assert body.get("token"), f"{account[0]} 没拿到令牌"
        assert body["user"].get("perms"), f"{account[0]} 没带权限点清单"


async def test_me_and_no_password_leak(client):
    body = await _login(client, LAWYER)
    me = (await client.get("/api/v1/auth/me", headers=_h(body["token"]))).json()
    assert me.get("username") == LAWYER[0]
    assert me.get("role_name") == "法务审查人"
    assert "risk.decide" in (me.get("perms") or []), "法务缺少 risk.decide"
    assert "task.submit" not in (me.get("perms") or []), "法务不该有 task.submit"

    admin = await _login(client, ADMIN)
    for path in ("/api/v1/auth/me", "/api/v1/auth/users"):
        text = (await client.get(path, headers=_h(admin["token"]))).text
        assert "password_hash" not in text, f"{path} 泄露了密码摘要"


async def test_server_side_permission_boundary(client):
    """前端收敛是呈现层，服务端必须**独立**拦一遍。"""
    lawyer = await _login(client, LAWYER)
    admin = await _login(client, ADMIN)

    # 拿一条规则来试写权限（用当前值回写，不改动状态）
    tree = (await client.get("/api/v1/rules", headers=_h(lawyer["token"]))).json().get("tree") or {}
    rule = next((items[0] for items in tree.values() if items), None)
    if rule:
        rid, cur = rule["rule_id"], bool(rule.get("enabled"))
        r = await client.patch(f"/api/v1/rules/{rid}", json={"enabled": cur}, headers=_h(lawyer["token"]))
        assert r.status_code == 403, f"法务竟然能改规则：HTTP {r.status_code}"
        assert "rule.edit" in r.text, "403 里应点明缺哪个权限点"
        r = await client.patch(f"/api/v1/rules/{rid}", json={"enabled": cur}, headers=_h(admin["token"]))
        assert r.status_code == 200, f"管理员改规则失败：HTTP {r.status_code}"

    # 账号清单：只有 user.manage 能看
    r = await client.get("/api/v1/auth/users", headers=_h(lawyer["token"]))
    assert r.status_code == 403, "法务不该看到账号清单"
    r = await client.get("/api/v1/auth/users", headers=_h(admin["token"]))
    assert r.status_code == 200
    assert len(r.json()) >= 5, f"种子账号应至少 5 个，实得 {len(r.json())}"


async def test_engine_health_needs_admin(client):
    lawyer = await _login(client, LAWYER)
    admin = await _login(client, ADMIN)
    r = await client.get("/api/v1/review/health", headers=_h(admin["token"]))
    assert r.status_code == 200, f"管理员看引擎自检失败：HTTP {r.status_code}"
    r = await client.get("/api/v1/review/health", headers=_h(lawyer["token"]))
    assert r.status_code == 403, f"法务不该看引擎自检：HTTP {r.status_code}"


# ======================================================================
# 3. 批量删除必须真的删掉（本组就是为那个「假成功」bug 建的）
# ======================================================================
async def test_batch_delete_really_deletes(client):
    operator = await _login(client, OPERATOR)
    admin = await _login(client, ADMIN)

    try:
        # 经办人建单（申请人是登录人，不是前端传的）
        r = await client.post(
            "/api/v1/tasks",
            json={"title": TMP_PREFIX, "business_type": "purchase", "run_now": False},
            headers=_h(operator["token"]),
        )
        assert r.status_code == 200, f"经办人建单失败：HTTP {r.status_code} {r.text[:120]}"
        created = r.json()
        tid = created["id"]
        assert created.get("applicant") == "张伟", f"申请人应取自登录身份，实得 {created.get('applicant')}"

        # 法务不该能删（task.batch 是管理员的）
        lawyer = await _login(client, LAWYER)
        r = await client.post(
            "/api/v1/tasks/batch",
            json={"action": "delete", "task_ids": [tid]},
            headers=_h(lawyer["token"]),
        )
        assert r.status_code == 403, f"法务竟然能批量删除：HTTP {r.status_code}"

        # 管理员批量删 —— 响应报了成功还不够，必须**回查确认真的没了**
        r = await client.post(
            "/api/v1/tasks/batch",
            json={"action": "delete", "task_ids": [tid]},
            headers=_h(admin["token"]),
        )
        assert r.status_code == 200, f"批量删除接口失败：HTTP {r.status_code}"
        assert tid in (r.json().get("data") or {}).get("done", []), f"没被算作已删：{r.text[:160]}"

        r = await client.get("/api/v1/tasks", params={"limit": 100}, headers=_h(admin["token"]))
        still = [it["id"] for it in (r.json().get("items") or []) if it["id"] == tid]
        assert not still, "批量删除回报成功，但任务还在列表里 —— commit 又被漏掉了"

        r = await client.get(f"/api/v1/tasks/{tid}", headers=_h(admin["token"]))
        assert r.status_code == 404, f"单条回查应 404，实得 HTTP {r.status_code}"
    finally:
        await _purge_tmp_tasks(client, admin["token"])


# ======================================================================
# 4. 上传接口必须真的把附件解析出来（2026-09-19 的真 bug）
# ======================================================================
#: 样例目录（含 DOCX / PDF / 扫描件），用于端到端上传验证
SAMPLES = Path(__file__).resolve().parent.parent / "samples"

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_MIME_BY_EXT = {
    ".docx": _DOCX_MIME,
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def mime_of(filename: str) -> str:
    """按扩展名猜 MIME —— 多文件上传的用例要一份份把文件名/MIME 配齐。"""
    return _MIME_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("软件采购合同_风险版.docx", _DOCX_MIME),
        ("大额销售合同_风险版.pdf", "application/pdf"),
    ],
)
async def test_upload_really_parses_and_returns_findings(client, filename, mime):
    """上传 → 解析 → 审查 → 返回结果，整条链必须一次跑通。

    踩过的坑（2026-09-19）：``upload_task`` 里写成

        await PipelineRunner.run(task.id)
        session.expire_all()
        task = await TaskService.get(session, task.id)

    ``expire_all()`` 把 ORM 对象全部置为过期，而 **实参 ``task.id`` 的求值发生在
    ``await`` 之外**，于是触发同步惰性重载 → ``sqlalchemy.exc.MissingGreenlet``
    → ``POST /tasks/upload`` 整体 500。流水线本身其实跑完了、数据也落库了，
    但**响应体拿不到任务对象**，前端表现就是「上传了文件却解析不出来」——
    接口报错被前端吞成一句提示，只有接口级断言能钉住它。
    """
    sample = SAMPLES / filename
    if not sample.exists():  # pragma: no cover - 样例被清理时不该红
        pytest.skip(f"样例文件不存在：{sample}")

    operator = await _login(client, OPERATOR)
    admin = await _login(client, ADMIN)
    title = f"{TMP_PREFIX}_上传_{sample.stem}"

    try:
        r = await client.post(
            "/api/v1/tasks/upload",
            files={"file": (sample.name, sample.read_bytes(), mime)},
            data={"title": title, "business_type": "purchase", "run_now": "true"},
            headers=_h(operator["token"]),
        )
        assert r.status_code == 200, f"上传接口挂了：HTTP {r.status_code} {r.text[:240]}"

        created = r.json()
        assert created["status"] == "completed", f"上传后应审查完成，实得 {created['status']}"
        assert created.get("applicant") == "张伟", "申请人应取自登录身份"

        # 响应只是摘要，真正的解析产物在详情里 —— 前端高亮全靠它
        d = (await client.get(f"/api/v1/tasks/{created['id']}", headers=_h(operator["token"]))).json()
        assert d["doc"] and d["doc"]["blocks"], "解析后必须落库原文块，否则前端无从高亮"
        assert d["doc"]["full_text"].strip(), "全文不能为空"
        assert d["risk_items"], "风险版样例应命中风险项"

        for item in d["risk_items"]:
            # 定位必须齐全：前端就是拿这三个字段做叠层高亮的
            assert item["block_id"], f"风险 {item['name']} 缺少 block_id"
            assert item["char_end"] > item["char_start"], f"风险 {item['name']} 字符区间非法"
            assert item["block_id"] in {b["block_id"] for b in d["doc"]["blocks"]}, (
                f"风险 {item['name']} 的 block_id 不在文档里，高亮会落空"
            )

        assert any(it["suggestion"] for it in d["risk_items"]), "至少要有一条修改示范条款"
    finally:
        await _purge_tmp_tasks(client, admin["token"])


async def test_upload_batch_one_task_per_file_grouped(client):
    """多文件上传：**一份文件一条任务**，再用批次号归组。

    为什么必须是「一文件一任务」而不是「一任务挂多文档」
    ------------------------------------------------
    IR 的 ``doc_id`` 就等于任务 id，``doc_block`` 上有 ``(doc_id, block_id)`` 唯一键，
    风险项的 ``char_start~char_end`` 又是相对**本份文档全文**的偏移。
    一个任务塞多份文档会同时打断定位与高亮两条主线，所以多文件只能各建各的单。

    这里钉住四件事：
    1. N 份文件 → N 条任务，且都跑完了；
    2. 同一批次号，序号 1..N；
    3. 任务标题互不相同（否则文件切换条和审查大盘上分不出谁是谁）；
    4. **单文件上传不出现批次** —— 界面不该冒出一个只有一份文件的「批次」。
    """
    samples = [
        SAMPLES / "软件采购合同_风险版.docx",
        SAMPLES / "大额销售合同_风险版.pdf",
    ]
    missing = [s.name for s in samples if not s.exists()]
    if missing:  # pragma: no cover - 样例被清理时不该红
        pytest.skip(f"样例文件不存在：{missing}")

    operator = await _login(client, OPERATOR)
    admin = await _login(client, ADMIN)
    base_title = f"{TMP_PREFIX}_批次"

    try:
        r = await client.post(
            "/api/v1/tasks/upload-batch",
            files=[
                ("files", (s.name, s.read_bytes(), mime_of(s.name))) for s in samples
            ],
            data={"title": base_title, "business_type": "sales", "run_now": "true"},
            headers=_h(operator["token"]),
        )
        assert r.status_code == 200, f"批量上传挂了：HTTP {r.status_code} {r.text[:240]}"

        out = r.json()
        assert out["total"] == len(samples), f"应为每份文件建一条任务，实得 {out['total']} 条"
        assert len(out["tasks"]) == len(samples)

        titles = [t["title"] for t in out["tasks"]]
        assert len(set(titles)) == len(titles), f"同批次任务标题不该重复：{titles}"
        assert all(t.startswith(base_title) for t in titles), titles
        assert all(t["status"] == "completed" for t in out["tasks"]), [
            (t["title"], t["status"]) for t in out["tasks"]
        ]

        # 批次上下文：成员齐全、带序号与文件名、合计风险非零
        batch = out["batch"]
        assert batch and batch["total"] == len(samples), f"批次上下文不对：{batch}"
        assert batch["batch_id"] == out["batch_id"]
        assert [it["seq"] for it in batch["items"]] == list(range(1, len(samples) + 1))
        assert all(it["filename"] for it in batch["items"]), "文件切换条要有文件名"
        assert batch["summary"]["completed"] == len(samples)
        assert batch["summary"]["risk_total"] > 0, "风险版样例应命中风险"

        # 详情侧：任取一条任务，也能拿到同一批次（工作台的文件切换条靠它）
        first = out["tasks"][0]
        d = (await client.get(f"/api/v1/tasks/{first['id']}", headers=_h(operator["token"]))).json()
        assert d["batch"] and d["batch"]["total"] == len(samples), d.get("batch")
        assert d["batch"]["index"] == 1
        assert len([it for it in d["batch"]["items"] if it["active"]]) == 1, "只该有一份是当前文件"
        assert d["doc"] and d["doc"]["blocks"], "每份文件仍要各自落库原文块"

        # 每条任务各自有目标要求的 block_id（定位没被打断）
        block_ids = {b["block_id"] for b in d["doc"]["blocks"]}
        assert all(it["block_id"] in block_ids for it in d["risk_items"])

        # 单文件上传不该出现批次
        one = await client.post(
            "/api/v1/tasks/upload",
            files={"file": (samples[0].name, samples[0].read_bytes(), mime_of(samples[0].name))},
            data={"title": f"{base_title}_单份", "run_now": "true"},
            headers=_h(operator["token"]),
        )
        assert one.status_code == 200
        d1 = (
            await client.get(f"/api/v1/tasks/{one.json()['id']}", headers=_h(operator["token"]))
        ).json()
        assert d1["batch"] is None, f"单文件不该出现批次：{d1['batch']}"
    finally:
        await _purge_tmp_tasks(client, admin["token"])
