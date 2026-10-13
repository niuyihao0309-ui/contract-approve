---
name: contract-approve-ops
description: 合同审批智能审查系统（XM-contract-approve）的启动、复跑验收、排障与二次开发。当用户提到"合同审查/合同审批系统""XM-contract-approve""跑一下 demo/验收/回归测试""起后端起前端""MySQL 3307""解析/审查引擎报错"时使用。内含本机 Windows 环境的已知坑（bash shim 坏、PowerShell stdout 不回显、npm.cmd、uv 无 pip、编码），以及改代码必须遵守的 5 条硬约束。
description_zh: "合同审批审查系统：启动/验收复跑/排障/二次开发"
agent_created: true
---

# 合同审批智能审查系统 · 运维与二次开发

## 0. 项目坐标

| 项 | 值 |
|---|---|
| 项目根 | `D:\ke_cheng\XM\XM-contract-approve` |
| 需求原文 | `D:\ke_cheng\XM\2.4 合同审批审查系统.md` |
| 落地大纲 | `D:\ke_cheng\XM\大纲.md`（阶段 0~6 + 8 条验收标准） |
| 工作区记忆 | `D:\ke_cheng\XM\.workbuddy\memory\*.md` |
| git | 单仓，repo root = 项目根，首次提交 `7f64276` |

一句话：DOCX/PDF/扫描件 → 统一 `blocks` 中间表示 → 「规则引擎 + LLM」双引擎审查 → 前端原文双向高亮 → 意见幂等回写审批评论区 + 导出 MD/PDF 报告。

## 1. 环境（本机已就绪）

- **数据库**：Docker `xm-contract-mysql`，宿主端口 **3307**（避让 RAG 栈的 3306），库 `contract_review`，账号 `contract/contract123`。
- **Python**：`uv` 建的 `.venv`（Python 3.12）。**venv 里没有 pip**，装包只能：
  ```bash
  uv pip install --python ".venv/Scripts/python.exe" <包名>
  ```
- **已装的可选依赖**：`rapidocr-onnxruntime`（OCR）、`pytest` + `pytest-asyncio`。
- **前端**：`frontend/`，Vue3 + Vite5 + Element Plus + Pinia + Axios，已 `npm install` 过，`dist/` 已构建。

> ⚠️ **用户强烈拒绝镜像源**（科学上网已开）。`pyproject.toml` 里不许写 `[[tool.uv.index]]` 指向 tuna/npmmirror，恢复官方 PyPI 默认源。

## 2. 标准命令（照抄，全在项目根执行）

```bash
# 起库（3307）
docker compose up -d mysql

# 起后端
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 接口文档 http://127.0.0.1:8000/docs

# 起前端（Windows 必须用 npm.cmd，见第 4 节坑）
cd frontend && npm.cmd run dev     # http://localhost:5173，已代理 /api → 8000

# 端到端演示（5 场景，应 45/45 全过）
.venv/Scripts/python.exe scripts/demo_e2e.py --fresh

# 回归测试（应 9 passed）
.venv/Scripts/python.exe -m pytest tests/test_regressions.py -v

# 解析引擎单验（肉眼核对定位与切分）
.venv/Scripts/python.exe scripts/check_parsing.py

# 重建样例
.venv/Scripts/python.exe scripts/make_samples.py
```

## 3. 验收自检：8 条标准 → 对应证据

跑这一列就能证明项目没坏：

| # | 验收标准 | 证据/命令 |
|---|---|---|
| 1 | 解析 DOCX/PDF/扫描件 | `demo_e2e` 场景 A/B/C；`check_parsing.py` |
| 2 | 元数据 + 条款 + 原文位置 | `check_parsing.py` 打出的 `block_id + char 区间` |
| 3 | 缺失/不对等/高危审查 + 等级依据 | `demo_e2e` 场景 D（标准版不误报） |
| 4 | 原文 ↔ 卡片双向高亮 | 前端 `TaskDetail.vue` →「原文高亮」tab；组件 `HighlightDocument.vue` |
| 5 | 可落地示范条款 + 法务可编辑 | 前端「风险清单」tab 采纳/编辑；`app/rules/clause_templates.json` |
| 6 | 格式化意见写回评论区（幂等） | `demo_e2e` 的 B 回写 + 幂等重放用例 |
| 7 | 解析失败进入 blocked 并可重试 | `demo_e2e` 场景 C（加密/空正文/严重模糊） |
| 8 | 全流程演示 | `demo_e2e` 全绿 + `storage/reports/*.md/pdf` |

## 4. 本机环境坑（踩过，别再踩）

| 坑 | 现象 | 绕法 |
|---|---|---|
| **Bash 工具坏** | `dirname: command not found`、`cd: null directory`、`ls` command not found | 命令前加 `export PATH="/usr/bin:/bin:$PATH"` 即可恢复 |
| **PowerShell stdout 不回显** | 命令 exit 0 但拿不到任何输出 | 别依赖 PowerShell 取输出；改用 Bash（加 PATH）或让 Python 写文件再 Read |
| **PowerShell 禁跑 .ps1** | `npm` 报"禁止运行脚本" | 一律用 `npm.cmd` 全路径：`& "C:\Users\NYH\.workbuddy\binaries\node\versions\22.22.2-3\npm.cmd" install` |
| **cmd.exe 被禁** | `cmd /c` 失败 | 别用，走 Bash 或 PowerShell |
| **中文乱码** | 输出/提交信息乱码 | `[Console]::OutputEncoding=[Text.Encoding]::UTF8`、`$OutputEncoding`、`$env:PYTHONIOENCODING=utf-8` |
| **uv 装包报 numpy 元数据损坏** | `Failed to read numpy==2.5.3` | `uv pip install --reinstall numpy`（venv 里 dist-info 曾被装包中断弄残） |
| **OCR 不可用** | 场景 C 报 `reason=ocr_unavailable` | `uv pip install --python .venv/Scripts/python.exe rapidocr-onnxruntime` |
| **`.gitignore` 拼错** | 写成 `.gitgnore`，`.env` 被 git 跟踪 | 已修；动 ignore 前先核对文件名拼写 |

## 5. 改代码必须遵守的 5 条硬约束

1. **IR 结构是地基**：所有格式归一为 `blocks`（`block_id / type / text / page / para_index / char_start / char_end / bbox`）。`block_id` 是全文唯一地址，风险/元数据/批注**只引用 `block_id + char 区间`，绝不存裸文本** —— 否则高亮一定对不上。
2. **规则先行，LLM 只补语义**：确定性判定写进 `app/rules/review_rules.json`（现 13 条）；LLM 只处理"权责不对等/高危表述"，且必须 Pydantic 结构化输出。
3. **前端高亮只做叠加层**：`HighlightDocument.vue` 用 `<mark>` 叠加，**不改原文 DOM**（改了排版崩、分页错）。
4. **回写必须幂等**：幂等键用 `digest_basis`（**不含时间戳**）算；`build_writeback_markdown` 里的 `datetime.now()` 只做展示。回写状态机 `not_written→writing→success/failed` 与任务状态机分离。
5. **状态流转集中**：只在 `app/services/` 里改状态，禁止散落各地；枚举别裸写字符串。

> 历史真 bug 模式：查询/业务方法放错 Repository 类（如 Qdrant 仓库里写 MySQL 查询）→ `AttributeError`。排查先核对方法归属。

## 6. 已知未完成项（别当成"坏了"）

| 项 | 现状 | 性质 |
|---|---|---|
| **LLM 链路未实测** | `.env` 里 `APP_LLM_ENABLED=false`、`APP_LLM_BASE_URL` 还是 `api.openai.com/v1` 模板值、Key 空 → 验收 3/5 的"大模型"部分只跑规则引擎 + 模板兜底 | **需用户自己填 Key**，最高优先级 |
| 规则配置页无可视化编辑 | 后端只有 `PATCH /rules/{id}`（启停）与 `POST /rules/sync`；前端 `RulesView.vue` 纯只读 | 大纲标注"可后置" |
| 样例缺劳动/服务类合同 | `samples/` 只有采购 + 销售各 2 版 + 4 个异常件 | 非阻塞（验收只要 1 个典型合同） |
| 无异步化 | 无 Celery/BackgroundTasks，解析+审查全同步，长合同会阻塞请求 | 大纲阶段 0 提过，非阻塞 |
| 无 Alembic 迁移 | 靠 `APP_DB_AUTO_CREATE=true` + `metadata.create_all` | 模型改动不平滑，非阻塞 |
| README 数字不符 | README 写"14 条规则"，`review_rules.json` 实际 **13 条** | 文档小错，顺手改 |
| 前端首包 1.2MB | Element Plus 全量引入（gzip 388KB） | 性能优化项 |
| 无部署说明/演示话术 | 阶段 6.5 只完成 README | 交付补齐项 |

## 7. 报错排查顺序

1. `docker ps` 看 `xm-contract-mysql` 是否 healthy、3307 是否映射。
2. `.env` 的 `APP_DB_URL` / `APP_LLM_*` / `APP_OCR_ENABLED` 是否对上。
3. 跑 `pytest tests/test_regressions.py -v`，9 条回归覆盖了 5 个历史真 bug，先看它红在哪。
4. 跑 `demo_e2e.py --fresh`，45 条断言按 5 场景分组，直接定位是 A/B/C/D/E 哪条挂。
5. 看 `storage/logs/app_YYYY-MM-DD.log`（loguru），注意历史日志里的旧错误别误判为当前问题。

## 8. LLM 链路开启步骤（用户配 Key 后）

1. 编辑 `.env`：
   ```
   APP_LLM_ENABLED=true
   APP_LLM_BASE_URL=<OpenAI 兼容网关>/v1
   APP_LLM_API_KEY=<用户的 Key>
   APP_LLM_MODEL=<模型名>
   ```
2. 跑 `demo_e2e.py --fresh`，对比开/关 LLM 时场景 A/B 的"AI 推荐修改条款"是否从模板兜底变为语义改写。
3. 若 LLM 超时，调 `APP_LLM_TIMEOUT`。

## 9. 产出位置（别乱丢）

- 审查报告：`storage/reports/<合同名>_审查报告_YYYYMMDD-HHMMSS.md|pdf`
- 上传文件：`storage/uploads/`
- mock 审批单：`storage/mock_approval.json`
- 日志：`storage/logs/`
- 运行时目录全部 git 忽略，不要提交。

## 10. 临时文件纪律

本机的 skill 环境里，调试输出容易在 `D:\ke_cheng\XM\` 根目录留下 `_*.txt`（`_build.txt`/`_commit*.txt`/`_g*.txt`/`_tree.txt` 等）。收尾时清掉，工作区根只该留 `.workbuddy/`、`XM-contract-approve/`、`2.4 合同审批审查系统.md`、`大纲.md`。
