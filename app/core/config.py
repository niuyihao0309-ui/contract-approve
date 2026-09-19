"""全局配置。

所有可调参数集中在这里，通过环境变量（前缀 ``APP_``）或项目根目录的 ``.env`` 覆盖。
代码里禁止出现裸字符串配置 —— 这是大纲「地基」清单里的要求。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：app/core/config.py -> app/core -> app -> 项目根
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置对象。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "合同审批智能审查系统"
    app_version: str = "0.1.0"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    # ---------------- 数据库 ----------------
    # 默认走 Docker MySQL（见 docker-compose.yml，端口 3307 避让你既有的 RAG 栈）。
    # 起库：docker compose up -d mysql  →  docker compose logs -f mysql
    #
    # 想复用既有 MySQL（3306 那套）时，只改这一行即可，代码是方言无关的：
    #   APP_DB_URL=mysql+asyncmy://root:你的密码@127.0.0.1:3306/contract_review?charset=utf8mb4
    # 临时想用 SQLite 快速验逻辑（不需要 Docker）：
    #   APP_DB_URL=sqlite+aiosqlite:///./storage/contract_review.db
    db_url: str = (
        "mysql+asyncmy://contract:contract123@127.0.0.1:3307/contract_review?charset=utf8mb4"
    )
    # 打印所有 SQL，排查数据库问题时打开
    db_echo: bool = False
    # 启动时自动建表（开发期方便；生产应交给 Alembic）
    db_auto_create: bool = True
    # 数据库连接超时（秒）。Docker 刚起来时 MySQL 可能要几十秒才 ready
    db_connect_timeout: int = 10

    # ---------------- 文件存储 ----------------
    storage_dir: Path = BASE_DIR / "storage"
    upload_dir_name: str = "uploads"
    report_dir_name: str = "reports"

    # ---------------- LLM（OpenAI 兼容）----------------
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_timeout: float = 120.0
    llm_max_retries: int = 2

    # ---------------- OCR ----------------
    # 只在需要时才 import rapidocr，避免未安装时整体起不来
    ocr_enabled: bool = True
    # PDF 单页可抽取文本少于此值，视为扫描页，走 OCR
    pdf_page_text_threshold: int = 30

    # ---------------- 解析完整性校验阈值 ----------------
    # 正文总字数低于此值 → blocked（验收标准第 7 条）
    min_body_chars: int = 50
    # 单 block 平均字数低于此值且 block 数少 → 疑似严重模糊
    blurry_avg_chars: float = 4.0
    # 重复字符占比超过此值 → 疑似乱码/水印
    gibberish_ratio: float = 0.6

    # ---------------- 审查引擎 ----------------
    rules_file: Path = BASE_DIR / "app" / "rules" / "review_rules.json"
    templates_file: Path = BASE_DIR / "app" / "rules" / "clause_templates.json"
    # LLM 语义研判单次送审的最大字符数（超出按条款分片）
    llm_chunk_chars: int = 6000

    #: 我方在合同中的身份，决定「管辖地是否有利于我方」「知识产权是否归我方」等判断。
    #: 支持 party_a / party_b。默认 party_a（多数企业合同里我方排在甲方）。
    our_side: str = "party_a"
    #: 我方在 prompt 里的称呼
    our_side_label: str = "甲方"

    # ---------------- 数据校验 ----------------
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".docx", ".doc", ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"]
    )
    max_upload_mb: int = 50

    @field_validator("storage_dir", "rules_file", "templates_file", mode="after")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        """相对路径统一按项目根解析，避免受启动时 CWD 影响。"""
        return v if v.is_absolute() else (BASE_DIR / v).resolve()

    # ---------------- 派生路径 ----------------
    @property
    def upload_dir(self) -> Path:
        return self.storage_dir / self.upload_dir_name

    @property
    def report_dir(self) -> Path:
        return self.storage_dir / self.report_dir_name

    def ensure_dirs(self) -> None:
        """确保运行期需要的目录存在（parent=True + exist_ok=True 惯用法）。"""
        for d in (self.storage_dir, self.upload_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def llm_ready(self) -> bool:
        """LLM 是否真正可用：开关打开 + 有 key。"""
        return bool(self.llm_enabled and self.llm_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()


settings = get_settings()
