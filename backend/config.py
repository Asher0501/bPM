# -*- coding: utf-8 -*-
"""bePm 配置管理 — 参考 Nexus 模式

环境变量加载优先级（从高到低）：
1. os.environ 中的环境变量
2. ~/.claude/settings.json 的 env 段
3. 项目 .claude/settings.local.json 的 env 段
4. 代码默认值
"""

import json
import os
from pathlib import Path
from functools import lru_cache


# ---- 常量 ----

# Claude Code 全局配置
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# 项目本地配置
PROJECT_SETTINGS_PATH = Path(__file__).parent.parent / ".claude" / "settings.local.json"


@lru_cache(maxsize=1)
def load_claude_settings() -> dict:
    """从 ~/.claude/settings.json 加载配置（单次加载，缓存结果）"""
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


def get_env(key: str, default: str = "") -> str:
    """获取环境变量，按优先级查询：os.environ > claude settings > project settings > default

    同时将 claude settings 中的值注入 os.environ（首次加载时），
    使得 Anthropic SDK 能自动拾取 ANTHROPIC_BASE_URL 等变量。
    """
    # 1. 先从 os.environ 查
    if key in os.environ:
        return os.environ[key]

    # 2. 从 ~/.claude/settings.json 的 env 段查，并注入 os.environ
    claude = load_claude_settings()
    env_vars = claude.get("env", {})
    if key in env_vars and env_vars[key]:
        os.environ[key] = env_vars[key]
        return env_vars[key]

    # 3. 从项目 .claude/settings.local.json 的 env 段查
    project = load_project_settings()
    env_vars = project.get("env", {})
    if key in env_vars and env_vars[key]:
        os.environ[key] = env_vars[key]
        return env_vars[key]

    # 4. 默认值
    return default


def init_env():
    """初始化环境：将 claude settings 中的 ALL env vars 注入 os.environ

    在应用启动时调用一次，确保 Anthropic SDK 等下游库能正常拾取配置。
    """
    # 从 claude settings 注入
    claude = load_claude_settings()
    for k, v in claude.get("env", {}).items():
        if k not in os.environ and v:
            os.environ[k] = v

    # 从项目 settings 注入（会覆盖同名的，但保留 os.environ 已有的优先级）
    project = load_project_settings()
    for k, v in project.get("env", {}).items():
        if k not in os.environ and v:
            os.environ[k] = v


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

    # API key: 尝试 ANTHROPIC_API_KEY > ANTHROPIC_AUTH_TOKEN
    api_key = get_env("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = get_env("ANTHROPIC_AUTH_TOKEN")

    # Base URL: 如果有自定义（如 DeepSeek proxy）
    base_url = get_env("ANTHROPIC_BASE_URL")

    # Model: 优先使用 ANTHROPIC_MODEL
    model = get_env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    return {
        "api_key": api_key,
        "base_url": base_url or None,  # None = Anthropic 默认
        "model": model,
    }
