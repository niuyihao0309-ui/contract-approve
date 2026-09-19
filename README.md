# 合同审批智能审查系统

面向企业合同审批场景的智能审查系统：把合同附件（Word / PDF / 扫描件）解析成结构化条款，用「规则引擎 + 大模型」识别法律与商业风险，在前端做**原文双向高亮定位**，输出修改建议与审查报告，并把意见**自动回写审批系统评论区**。

## 核心能力

| 模块 | 说明 |
|------|------|
| 文档接入与解析 | DOCX / PDF / 扫描件 OCR 三种格式，归一为统一 `blocks` 中间表示（`block_id + char 区间` 全文唯一可溯源） |
| 智能合规审查 | 规则引擎先行（14 条可配置规则）+ LLM 语义研判补齐，输出风险等级 + 法律依据 + 证据链 |
| 交互研判与建议 | 原文与风险卡片**双向高亮跳转**；风险条款生成修改示范文本，支持法务采纳/调整/批注 |
| 审批集成与回写 | 意见文本幂等回写审批系统评论区（mock），导出 Markdown / PDF 审查报告 |
| 规则与模板管理 | 审查规则 + 标准示范条款库，JSON 配置、启动同步入库、支持检索 |

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
│   ├── api/v1/              # 路由（tasks/reviews/documents/risks/rules/reports/writeback/approvals）
│   ├── engines/
│   │   ├── parsing/         # DOCX/PDF/OCR 解析 + 完整性校验 + 元数据抽取 + 条款切分
│   │   ├── review/          # 规则引擎 + checker + LLM 研判 + 风险聚合
│   │   └── recommend/       # 条款推荐（模板检索 + LLM 改写）
│   ├── llm/                 # LLM 客户端 + prompt 模板
│   ├── services/            # 流水线 / 持久化 / 回写 / 报告 / 文件 / 规则
│   ├── models/              # ORM 表与枚举
│   ├── rules/               # review_rules.json + clause_templates.json
│   └── schemas/             # Pydantic 模型
├── frontend/                # Vue3 前端（大盘 / 工作台 / 引擎状态 / 规则库）
├── samples/                 # 8 个样例合同（采购/销售 标准与风险版 + 加密/空正文/模糊/扫描件）
├── scripts/                 # demo_e2e.py（端到端演示）/ check_parsing.py / init_db.py / make_samples.py
├── tests/                   # 回归测试
└── storage/                 # 运行时产物（日志 / 报告 / 上传文件，git 忽略）
```

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

## 验证与演示

```bash
# 端到端演示（5 场景：采购DOCX / 销售PDF / 异常阻塞重试 / 对照组不误报 / 指纹去重）
.venv\Scripts\python.exe scripts/demo_e2e.py --fresh

# 回归测试
.venv\Scripts\python.exe -m pytest tests/test_regressions.py -v

# 解析引擎单独验证（肉眼核对定位与切分）
.venv\Scripts\python.exe scripts/check_parsing.py
```

## 验收标准对照（原需求 2.4.10）

1. ✅ 接入并解析 DOCX、PDF、图片扫描件
2. ✅ 提取核心元数据与条款，记录原文页码与位置（`block_id + char 区间`）
3. ✅ 必备条款缺失 / 权责不对等 / 高危表述的合规审查，输出等级与依据
4. ✅ 工作台原文与风险卡片双向高亮定位与跳转
5. ✅ 生成可落地修改示范条款，支持法务编辑
6. ✅ 生成格式化审查意见并写回审批系统评论区（mock，幂等）
7. ✅ 解析失败 / 接口异常进入 `blocked` 并支持重试
8. ✅ 典型合同「拉取 → 解析 → 审查 → 复核 → 回写 → 导报」全流程演示
