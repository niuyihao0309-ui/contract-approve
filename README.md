# 合同审批智能审查系统

面向企业合同审批场景的智能审查系统：把合同附件（Word / PDF / 扫描件）解析成结构化条款，用「规则引擎 + 大模型」识别法律与商业风险，在前端做**原文双向高亮定位**，输出修改建议与审查报告，并把意见**自动回写审批系统评论区**。

## 核心能力

| 模块 | 说明 |
|------|------|
| 文档接入与解析 | DOCX / PDF / 扫描件 OCR 三种格式，归一为统一 `blocks` 中间表示（`block_id + char 区间` 全文唯一可溯源） |
| 智能合规审查 | 规则引擎先行（13 条可配置规则）+ LLM 语义研判补齐，输出风险等级 + 法律依据 + 证据链 |
| 交互研判与建议 | 原文与风险卡片**双向高亮跳转**；条款级**差异对比**（原条款 vs 建议条款）；风险条款生成修改示范文本，支持法务采纳/调整/批注 |
| 审批集成与回写 | 意见文本幂等回写审批系统评论区（mock），回写状态**轮询**，导出 Markdown / PDF 审查报告 |
| 规则与模板管理 | 审查规则（树状 + 条件编辑表单）+ 标准示范条款库（可增改），JSON 配置、启动同步入库 |
| 登录与账号体系 | 数据库 `sys_user` 表 + 5 个演示账号（不同部门 / 职位 / 角色）；密码 PBKDF2-HMAC-SHA256，令牌 HMAC 自签名（零第三方依赖）；未登录访问任何业务页都会被送回 `/login` |
| 角色与权限 | 法务审查人 / 业务经办人 / 系统管理员三角色，21 个权限点；菜单级 + 页面级 + 按钮级收敛，**前端与后端各自执行同一份矩阵** |

## 技术栈

- **后端**：Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy(asyncmy) + loguru
- **文档解析**：python-docx（DOCX）、PyMuPDF（PDF）、RapidOCR/onnxruntime（扫描件）
- **LLM**：OpenAI 兼容协议（DeepSeek / 通义 / 智谱 / SiliconFlow 均可），Pydantic 结构化输出
- **前端**：Vue 3 + Vite 5 + Vue Router + Pinia + Element Plus + Axios
- **数据库**：MySQL 8（Docker 容器，宿主端口 3307）

## 目录结构

```
XM-contract-approve/
├── app/                     # 后端
│   ├── api/v1/              # 路由（auth/tasks/reviews/documents/risks/rules/reports/writeback/approvals/samples）
│   ├── core/                # config · exceptions · security（PBKDF2 密码 + HMAC 令牌）· permissions（权限矩阵）
│   ├── engines/
│   │   ├── parsing/         # DOCX/PDF/OCR 解析 + 完整性校验 + 元数据抽取 + 条款切分
│   │   ├── review/          # 规则引擎 + checker + LLM 研判 + 风险聚合
│   │   └── recommend/       # 条款推荐（模板检索 + LLM 改写）
│   ├── llm/                 # LLM 客户端 + prompt 模板
│   ├── services/            # 流水线 / 持久化 / 回写 / 报告 / 文件 / 规则 / 用户
│   ├── models/              # ORM 表与枚举（含 sys_user 账号表）
│   ├── rules/               # review_rules.json + clause_templates.json
│   └── schemas/             # Pydantic 模型
├── frontend/                # Vue3 前端
│   └── src/
│       ├── permissions.js   # 权限矩阵（与后端 app/core/permissions.py 同构，测试钉住一致性）
│       ├── stores/auth.js   # 登录态（token + user，localStorage[ca.auth]）+ 权限判定
│       ├── directives/perm.js  # v-perm：按身份收敛界面入口
│       ├── views/           # 登录 / 审查大盘 / 上传合同 / 工作台 / 规则与模板 / 引擎状态 / 角色与权限 / 403
│       └── components/      # 文本层高亮、原件预览、风险卡片、协同回写、用户菜单、示例选择
├── docs/                    # 2.4.2 角色与权限设计 · 前端模块说明 · 页面截图（22 张）
├── samples/                 # 8 个样例合同（采购/销售 标准与风险版 + 加密/空正文/模糊/扫描件）
├── scripts/                 # demo_e2e.py / check_parsing.py / init_db.py / verify_pages.spec.json
│                            # + verify_upload_e2e.py（上传→解析→高亮的浏览器端到端验收）
├── tests/                   # 回归测试（含接口级鉴权与权限边界）
└── storage/                 # 运行时产物（日志 / 报告 / 上传文件，git 忽略）
```

## 角色与权限（2.4.2）

三个角色：**法务审查人**（拍板）、**业务经办人**（送审）、**系统管理员**（修管道）。
21 个「资源.动作」权限点、页面级 403 拦截、按钮级禁用提示 —— 前端由 `frontend/src/permissions.js` 派生，
后端由 `app/core/permissions.py` 用**同一份矩阵**再执行一遍（`require_perm(...)` 依赖），
两份不一致会被 `tests/test_permissions_sync.py` 拦下。

登录账号（存在 `sys_user`，启动时幂等种入；登录页可一键填充）：

| 用户名 | 密码 | 姓名 / 职位 | 角色 | 权限数 |
|---|---|---|---|:--:|
| `li.jing` | `lawyer123` | 李静 / 法务审查专员 | 法务审查人 | 11 |
| `wang.fang` | `lawyer123` | 王芳 / 法务经理 | 法务审查人 | 11 |
| `zhang.wei` | `operator123` | 张伟 / 采购经办人 | 业务经办人 | 9 |
| `liu.yang` | `operator123` | 刘洋 / 销售经办人 | 业务经办人 | 9 |
| `admin` | `admin123` | — / 系统管理员 | 系统管理员 | 13 |

> 演示密码是刻意弱口令，**上线前必须改**并清掉 `is_demo` 账号。

- 设计文档：[`docs/2.4.2-用户角色与权限设计.md`](docs/2.4.2-用户角色与权限设计.md)
- 线上对照页：<http://localhost:5173/permissions>（登录后可见，页内含数据库真实账号表）
- 前端模块与需求 2.4.5 的映射：[`docs/前端模块说明.md`](docs/前端模块说明.md)

## 快速开始

### 1. 起数据库（Docker MySQL）

```bash
docker compose up -d mysql
```

> 端口用 **3307** 而非 3306，是为了避让本机既有 RAG 栈的 MySQL。若想复用已有库，改 `.env` 里的 `APP_DB_URL` 即可。

### 2. 配置环境变量

```bash
cp .env.example .env      # 按需改 APP_DB_URL / APP_LLM_* 
```

关键项：

| 变量 | 说明 |
|------|------|
| `APP_DB_URL` | 数据库连接串（默认 Docker MySQL 3307） |
| `APP_LLM_ENABLED` | `true` 才走 LLM 语义研判与条款改写；`false` 则纯规则引擎 |
| `APP_LLM_BASE_URL` / `APP_LLM_API_KEY` / `APP_LLM_MODEL` | OpenAI 兼容网关三件套 |
| `APP_OCR_ENABLED` | 扫描件 OCR 开关（依赖 `uv sync --extra ocr`） |
| `APP_AUTH_SECRET` | **令牌签名密钥**。缺省是开发值并在启动时告警，上线必须换成随机长串 |
| `APP_AUTH_TOKEN_TTL_MINUTES` | 令牌有效期（默认 720 分钟 = 12 小时） |
| `APP_AUTH_REQUIRED` | 默认 `true`。设 `false` 可整体关掉鉴权（仅调试用） |
| `APP_AUTH_SEED_USERS` | 默认 `true`。启动时幂等种入 5 个演示账号 |

### 3. 装依赖并起后端

```bash
uv sync                 # 需要 OCR 则 uv sync --extra ocr
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

接口文档：<http://127.0.0.1:8000/docs>

### 4. 起前端

```bash
cd frontend
npm install
npm run dev             # http://localhost:5173（已代理 /api → 8000）
```

> 若 5173 被上次没关干净的 dev server 占着，vite 会自动退到 **5174** —— 看终端里
> `Local:` 那一行的实际端口，别hardcode 5173。

页面：`/login` 登录 · `/` 审查大盘 · `/upload` 上传合同 · `/tasks/:id` 智能审查工作台 ·
`/rules` 规则与模板 · `/engine` 引擎状态 · `/permissions` 角色与权限

> **上传合同页支持一次选多份文件/图片**：**一份文件生成一条审查单**，同一批用批次号归组
> （不是「一张单挂多份文档」—— IR 的 `doc_id`、块唯一键、风险项字符区间都是单文档模型，
> 多文档会同时打断定位与高亮）。提交完成后就地出结果：左栏顶上一条**批次文件切换条**，
> 下面是**当前文件**的全文（风险处彩色高亮，可上/下一处跳转），右栏是**当前文件**的风险清单与
> 修改要求（等级、定位、原文片段、成因、法律依据、示范条款一键复制），两栏双向联动，
> **点哪份文件右侧就显示哪份的清单**；大盘上同批任务带「批次 n/N」标，
> 工作台左侧也有同一条切换条可以横跳。**不用先点进工作台**。
> 需要采纳/驳回、回写审批意见、导出报告时再点「进入审查工作台」。

> **未登录会被自动送到 `/login`**，用上表任意演示账号登入即可（登录页有卡片可一键填充）。
> 首次启动日志里应出现 `演示账号已就绪（5 个）`。

## 验证与演示

```bash
# 端到端演示（5 场景：采购DOCX / 销售PDF / 异常阻塞重试 / 对照组不误报 / 指纹去重）
.venv\Scripts\python.exe scripts/demo_e2e.py --fresh

# 回归测试：纯规则单测 + 接口级鉴权 / 权限边界 / 批量删除
.venv\Scripts\python.exe -m pytest tests -q

# 解析引擎单独验证（肉眼核对定位与切分）
.venv\Scripts\python.exe scripts/check_parsing.py
```

前端改动的验证**不能停在 `npm run build`**（build 只证明能编译）：未登录拦截、菜单收敛、403、
页内按钮禁用、换账号权限不残留，都只有真开浏览器才验得出来。用逐角色逐页断言跑一遍：

```bash
# 取一个真实任务 id（接口已鉴权，先登录）
.venv\Scripts\python.exe -c "import urllib.request,json;B='http://127.0.0.1:8000/api/v1';r=urllib.request.Request(B+'/auth/login',data=json.dumps({'username':'admin','password':'admin123'}).encode(),headers={'Content-Type':'application/json'});tok=json.load(urllib.request.urlopen(r))['token'];t=json.load(urllib.request.urlopen(urllib.request.Request(B+'/tasks?limit=50',headers={'Authorization':'Bearer '+tok})));[print(i['id'],i['status'],i['title']) for i in t['items']]"

python "C:/Users/NYH/.workbuddy/skills/frontend-page-verify-cdp/scripts/verify_pages.py" ^
  --spec scripts/verify_pages.spec.json --base http://localhost:5173 ^
  --task-id <上一步选一个 completed 的 id> --out docs/screenshots
```

期望 **88 条断言全通过 / 退出码 0**（未登录拦截 4 页 + 菜单收敛 + 403 拦截 + 页内按钮禁用 +
换账号不残留），同时把最新截图刷进 `docs/screenshots/`（号码即规格里的顺序）。

> ⚠️ 跑出 FAIL 但截图看着正常时，**先怀疑时序**（表格数据是异步拉的），别急着改业务代码 ——
> 详见 `docs/2.4.2-用户角色与权限设计.md` §6.3 末尾。

上面那套验的是「身份 × 页面」是否收敛，**它不会真的上传一份合同**。上传链路另有端到端验收
（真开浏览器把样例文件塞进文件框、点提交、等结果区渲染，再断言高亮与双向联动）：

```bash
# 默认用 DOCX 样例；换 --sample 可验 PDF（走文本层）/ 扫描件（走 OCR）
.venv\Scripts\python.exe scripts/verify_upload_e2e.py --base http://localhost:5173
.venv\Scripts\python.exe scripts/verify_upload_e2e.py --sample 大额销售合同_风险版.pdf --keep

# 多文件批次（一份文件一条任务 + 点哪份看哪份）：默认 2 份
.venv\Scripts\python.exe scripts/verify_upload_e2e.py --multi
# 也可自定义（支持混着传图片扫描件）
.venv\Scripts\python.exe scripts/verify_upload_e2e.py --multi-samples 软件采购合同_风险版.docx 大额销售合同_风险版.pdf 销售合同_扫描件.png
```

期望 **单文件 13 条 / 多文件 19 条断言全通过 / 退出码 0**：风险卡片数、原文块数、`mark.hl-*` 高亮数、
卡片 → 原文 / 原文 → 卡片 双向联动、修改示范条款字数；**提交进度条（跑完之前不许出现 100%）**；
多文件模式另验**文件切换条个数**、**点某份 → 右侧只显示该份的清单**、接口层「N 条任务同属一批」、
大盘批次标渲染、工作台切换条换单。截图落到 `docs/screenshots/16_upload_result_*.png`、
`16c_upload_batch_*.png`、`04b_dashboard_batch.png`、`11b_workbench_batch.png`。默认只清理**本轮新建**的任务
（跑前取 id 快照比对），`--keep` 则保留下来方便手动点进大盘查看。

## 验收标准对照（原需求 2.4.10）

1. ✅ 接入并解析 DOCX、PDF、图片扫描件
2. ✅ 提取核心元数据与条款，记录原文页码与位置（`block_id + char 区间`）
3. ✅ 必备条款缺失 / 权责不对等 / 高危表述的合规审查，输出等级与依据
4. ✅ 工作台原文与风险卡片双向高亮定位与跳转（另附 PDF / 扫描件原件预览）
5. ✅ 生成可落地修改示范条款，支持法务编辑（含条款差异对比与一键复制）
6. ✅ 生成格式化审查意见并写回审批系统评论区（mock，幂等，状态轮询）
7. ✅ 解析失败 / 接口异常进入 `blocked` 并支持重试（单条 + 批量）
8. ✅ 典型合同「拉取 → 解析 → 审查 → 复核 → 回写 → 导报」全流程演示

另附需求 2.4.2（角色与权限）：✅ 三角色 + 21 权限点 + 登录账号体系，
菜单 / 页面 / 按钮三级收敛，**前端与后端各自执行同一份矩阵**，
未登录访问任何业务页都会被送回登录页 —— 对照证据见
[`docs/2.4.2-用户角色与权限设计.md`](docs/2.4.2-用户角色与权限设计.md) 与 `docs/screenshots/`（22 张）。

## 已知边界（本期未做）

- **令牌会出现在 URL 里**：`?token=` 这一带法是为了让 `<img src>` / `window.open` 这类浏览器
  直连场景（原件预览、报告下载）也能认证，代价是令牌会进浏览器历史与反代日志。
  生产环境应改为 HttpOnly Cookie 或一次性下载票据。
- **没有刷新令牌 / 撤销列表**：改密码不会让已发出的令牌立刻失效（默认 12 小时自然过期）；
  停用账号（`enabled=false`）是每次都查库校验的，这条**立即生效**。
- **权限粒度到角色，不到个人**：同一角色下的不同职位（如法务经理 vs 法务专员）权限点相同，
  差异只体现在姓名 / 部门 / 职责说明。要做角色内再分权，需要另加一层「职位 → 额外权限」。
- **账号维护只有接口没有界面**：`POST/PATCH /api/v1/auth/users` 已就绪（需 `user.manage`），
  前端目前只做了列表展示，没有新增 / 停用 / 改密码的表单。
- 规则 / 模板的界面编辑落在 DB 运行期副本上，`POST /rules/sync` 会以 JSON 文件为准覆盖内容
  （仅 `enabled` 开关保留），页面上有醒目提示。
- 演示账号是弱口令，上线前必须改掉并清掉 `is_demo` 账号。
