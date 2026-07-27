# -*- coding: utf-8 -*-
"""bePm 配置管理 — 参考 Nexus 模式

配置加载优先级（从高到低）：
1. os.environ 中的环境变量
2. ~/.claude/settings.json 的 env 段
3. 项目 backend/config.json
4. 代码默认值

用法:
  from config import get_config
  cfg = get_config()
  port = cfg.server.port
  model = cfg.llm.model
"""

import json
import os
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════
# 配置数据类
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 48090


@dataclass
class CorsConfig:
    origins: list[str] = field(default_factory=lambda: [
        "http://127.0.0.1:48090",
        "http://localhost:48090",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ])


@dataclass
class TestConfig:
    base_url: str = "http://127.0.0.1:48090"
    timeout_seconds: int = 120


@dataclass
class LlmConfig:
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 32768
    max_tokens_intent: int = 1024
    max_tokens_translation: int = 2048
    retry_count: int = 3
    timeout_seconds: int = 300
    circuit_breaker_failures: int = 3
    circuit_breaker_timeout_seconds: int = 30


@dataclass
class SchedulerConfig:
    buffer_ratio: float = 0.5


@dataclass
class RiskScanConfig:
    merge_threshold_indegree: int = 3
    chain_depth_warning: int = 5
    confidence_warning: float = 0.6
    near_critical_float_days: float = 2.0
    near_critical_min_count: int = 3


@dataclass
class MessagesConfig:
    max_count: int = 200
    trim_to: int = 150


@dataclass
class RateLimitConfig:
    projects_per_minute: int = 5
    commands_per_minute: int = 20
    progress_per_minute: int = 10


@dataclass
class LoggingConfig:
    dir: str = "logs"
    max_bytes: int = 10_485_760
    backup_count: int = 5
    level: str = "INFO"


@dataclass
class ProjectConfig:
    """bePm 全部可配置项"""
    production: bool = False
    server: ServerConfig = field(default_factory=ServerConfig)
    cors: CorsConfig = field(default_factory=CorsConfig)
    test: TestConfig = field(default_factory=TestConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    risk_scan: RiskScanConfig = field(default_factory=RiskScanConfig)
    messages: MessagesConfig = field(default_factory=MessagesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ═══════════════════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════════════════

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PROJECT_CONFIG_PATH = Path(__file__).parent / "config.json"
PROJECT_SETTINGS_PATH = Path(__file__).parent.parent / ".claude" / "settings.local.json"


# ═══════════════════════════════════════════════════════════════════════
# Claude Code settings 加载
# ═══════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def load_claude_settings() -> dict:
    """从 ~/.claude/settings.json 加载配置"""
    try:
        with open(CLAUDE_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@lru_cache(maxsize=1)
def load_project_settings() -> dict:
    """从项目 .claude/settings.local.json 加载配置"""
    try:
        with open(PROJECT_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════
# 环境变量注入（保持兼容）
# ═══════════════════════════════════════════════════════════════════════

def get_env(key: str, default: str = "") -> str:
    """获取环境变量，按优先级查询：os.environ > claude settings > default"""
    if key in os.environ:
        return os.environ[key]

    claude = load_claude_settings()
    env_vars = claude.get("env", {})
    if key in env_vars and env_vars[key]:
        os.environ[key] = env_vars[key]
        return env_vars[key]

    project = load_project_settings()
    env_vars = project.get("env", {})
    if key in env_vars and env_vars[key]:
        os.environ[key] = env_vars[key]
        return env_vars[key]

    return default


def init_env():
    """初始化环境：将 claude settings 中的 env vars 注入 os.environ"""
    claude = load_claude_settings()
    for k, v in claude.get("env", {}).items():
        if k not in os.environ and v:
            os.environ[k] = v

    project = load_project_settings()
    for k, v in project.get("env", {}).items():
        if k not in os.environ and v:
            os.environ[k] = v


# ═══════════════════════════════════════════════════════════════════════
# 项目配置 JSON 加载
# ═══════════════════════════════════════════════════════════════════════

def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个 dict，override 的值覆盖 base"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


@lru_cache(maxsize=1)
def _load_config_json() -> dict:
    """从 config.json 加载原始配置 dict"""
    try:
        with open(PROJECT_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 去掉注释字段
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def _env_override(config_dict: dict, prefix: str, key: str, converter=None):
    """用环境变量覆盖配置值。环境变量名: BEPM_{PREFIX}_{KEY} (大写)

    例: BEPM_SERVER_PORT=9090 → config_dict["server"]["port"] = 9090
    """
    env_key = f"BEPM_{prefix}_{key}".upper()
    val = os.environ.get(env_key)
    if val is not None:
        if converter:
            try:
                config_dict[key] = converter(val)
            except (ValueError, TypeError):
                pass
        else:
            config_dict[key] = val


def _apply_env_overrides(config_dict: dict):
    """将所有环境变量覆盖应用到配置 dict"""
    # Production toggle
    prod_env = os.environ.get("BEPM_PRODUCTION", "").lower()
    if prod_env in ("true", "1", "yes"):
        config_dict["production"] = True

    # Server
    if "server" in config_dict:
        srv = config_dict["server"]
        _env_override(srv, "SERVER", "host")
        _env_override(srv, "SERVER", "port", converter=int)

    # Test
    if "test" in config_dict:
        tst = config_dict["test"]
        _env_override(tst, "TEST", "base_url")
        _env_override(tst, "TEST", "timeout_seconds", converter=int)

    # LLM
    if "llm" in config_dict:
        llm = config_dict["llm"]
        _env_override(llm, "LLM", "model")
        _env_override(llm, "LLM", "max_tokens", converter=int)
        _env_override(llm, "LLM", "max_tokens_intent", converter=int)
        _env_override(llm, "LLM", "max_tokens_translation", converter=int)
        _env_override(llm, "LLM", "retry_count", converter=int)

    # Scheduler
    if "scheduler" in config_dict:
        _env_override(config_dict["scheduler"], "SCHEDULER", "buffer_ratio", converter=float)

    # Messages
    if "messages" in config_dict:
        msg = config_dict["messages"]
        _env_override(msg, "MESSAGES", "max_count", converter=int)
        _env_override(msg, "MESSAGES", "trim_to", converter=int)


@lru_cache(maxsize=1)
def get_config() -> ProjectConfig:
    """获取完整项目配置（单次加载，缓存结果）

    优先级: 环境变量 > config.json > 代码默认值

    Returns:
        ProjectConfig 数据类实例，支持 cfg.server.port 点号访问
    """
    # 1. 从 config.json 加载
    raw = _load_config_json()

    # 2. 应用环境变量覆盖
    _apply_env_overrides(raw)

    # 3. 构建配置对象（config.json 的值覆盖 dataclass 默认值）
    return ProjectConfig(
        production=raw.get("production", False),
        server=ServerConfig(**raw.get("server", {})),
        cors=CorsConfig(**raw.get("cors", {})),
        test=TestConfig(**raw.get("test", {})),
        llm=LlmConfig(**raw.get("llm", {})),
        rate_limit=RateLimitConfig(**raw.get("rate_limit", {})),
        scheduler=SchedulerConfig(**raw.get("scheduler", {})),
        risk_scan=RiskScanConfig(**raw.get("risk_scan", {})),
        messages=MessagesConfig(**raw.get("messages", {})),
        logging=LoggingConfig(**raw.get("logging", {})),
    )


# ═══════════════════════════════════════════════════════════════════════
# Anthropic 配置（保持向后兼容）
# ═══════════════════════════════════════════════════════════════════════

def get_anthropic_config() -> dict:
    """获取 Anthropic SDK 所需的完整配置

    返回:
      {
        "api_key": "...",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-20250514",
      }
    """
    init_env()

    # API key: ANTHROPIC_API_KEY > ANTHROPIC_AUTH_TOKEN
    api_key = get_env("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = get_env("ANTHROPIC_AUTH_TOKEN")

    # Base URL
    base_url = get_env("ANTHROPIC_BASE_URL")

    # Model: ANTHROPIC_MODEL 环境变量 > config.json > 默认
    model = get_env("ANTHROPIC_MODEL")
    if not model:
        model = get_config().llm.model

    return {
        "api_key": api_key,
        "base_url": base_url or None,
        "model": model,
    }
