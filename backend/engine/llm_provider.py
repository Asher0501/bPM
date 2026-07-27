# -*- coding: utf-8 -*-
"""LLM Provider — 上下文与执行解耦，支持可插拔 SDK + CLI 双模式

SDK 模式通过 LLM_SDK_TYPE 环境变量选择后端，默认 "anthropic"。
切换 SDK 只需设置环境变量，一行代码都不用改。

要添加新 SDK 后端，只需在本文件内：
  1. 继承 LlmSdkProvider，实现 execute() 方法
  2. 在 _SDK_PROVIDER_REGISTRY 中注册一行

所有 SDK 的 import 都延迟执行（在 execute() 内部 import），
不会因为缺少某个 SDK 包而影响其他后端的使用。

参考 Nexus ProviderDef{Llm, LlmSdk} + NodeExecutor 模式：
  LlmContext（纯数据）→ LlmExecutor.execute() → LlmResult（纯数据）
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 熔断器 (Circuit Breaker)
# ═══════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """LLM 调用熔断器：连续失败 N 次后熔断，冷却后自动恢复"""

    def __init__(self, failure_threshold: int = 3, timeout_seconds: float = 30.0):
        self._threshold = failure_threshold
        self._timeout = timeout_seconds
        self._failures = 0
        self._last_failure_time: float = 0.0
        self._state = "closed"  # closed | open | half-open

    @property
    def is_open(self) -> bool:
        if self._state == "closed":
            return False
        if self._state == "open":
            if time.time() - self._last_failure_time > self._timeout:
                self._state = "half-open"
                logger.info("Circuit breaker: open → half-open")
                return False
            return True
        # half-open: allow one probe call
        self._state = "open"
        return False

    def record_success(self):
        if self._state != "closed":
            logger.info("Circuit breaker: → closed (recovered)")
        self._failures = 0
        self._state = "closed"

    def record_failure(self):
        self._failures += 1
        self._last_failure_time = time.time()
        if self._failures >= self._threshold:
            self._state = "open"
            logger.warning("Circuit breaker: closed → open (%d failures)", self._failures)


# 全局熔断器实例
_llm_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _llm_circuit_breaker
    if _llm_circuit_breaker is None:
        from config import get_config
        llm_cfg = get_config().llm
        _llm_circuit_breaker = CircuitBreaker(
            failure_threshold=llm_cfg.circuit_breaker_failures,
            timeout_seconds=llm_cfg.circuit_breaker_timeout_seconds,
        )
    return _llm_circuit_breaker


# ═══════════════════════════════════════════════════════════════════════
# 纯数据结构
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class LlmContext:
    """一次 LLM 调用的完整上下文——不绑定任何执行方式"""

    system_prompt: str
    messages: list[dict]  # [{"role": "user", "content": "..."}]
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 32768
    api_key: str | None = None
    base_url: str | None = None


@dataclass
class LlmResult:
    """LLM 调用结果——纯文本"""

    content: str


# ═══════════════════════════════════════════════════════════════════════
# SDK Provider 抽象层（可插拔）
# ═══════════════════════════════════════════════════════════════════════


class LlmSdkProvider:
    """SDK Provider 基类——所有 SDK 后端实现此接口

    子类只需要实现 execute(ctx) 一个方法。
    SDK 的 import 在 execute() 内部延迟执行，避免缺少包时影响其他后端。
    """

    def execute(self, ctx: LlmContext) -> LlmResult:
        raise NotImplementedError


# ── Anthropic SDK ───────────────────────────────────────────────────


class AnthropicProvider(LlmSdkProvider):
    """Anthropic Python SDK 后端

    依赖: pip install anthropic
    兼容: api.anthropic.com 及任何 Anthropic 兼容端点 (DeepSeek, etc.)

    DeepSeek 兼容: 当 API 返回 "Streaming is required" 时自动切换为流式模式。
    """

    def execute(self, ctx: LlmContext) -> LlmResult:
        from anthropic import Anthropic

        kwargs = {"api_key": ctx.api_key}
        if ctx.base_url:
            kwargs["base_url"] = ctx.base_url
        client = Anthropic(**kwargs)

        # 优先尝试非流式（更简单可靠）
        try:
            response = client.messages.create(
                model=ctx.model,
                max_tokens=ctx.max_tokens,
                system=ctx.system_prompt,
                messages=ctx.messages,
            )
            return LlmResult(content=_extract_anthropic_content(response))
        except Exception as e:
            err_msg = str(e)
            # DeepSeek 要求流式：自动切换
            if "streaming" in err_msg.lower() or "stream" in err_msg.lower():
                logger.info("Provider 检测到流式要求，自动切换为 stream 模式")
                return self._execute_streaming(client, ctx)
            raise

    def _execute_streaming(self, client, ctx: LlmContext) -> LlmResult:
        """流式调用 LLM，收集所有 text delta 拼接为完整响应。"""
        text_parts: list[str] = []

        with client.messages.stream(
            model=ctx.model,
            max_tokens=ctx.max_tokens,
            system=ctx.system_prompt,
            messages=ctx.messages,
        ) as stream:
            for text in stream.text_stream:
                text_parts.append(text)

        return LlmResult(content="".join(text_parts))


# ── OpenAI SDK ─────────────────────────────────────────────────────


class OpenAIProvider(LlmSdkProvider):
    """OpenAI Python SDK 后端

    依赖: pip install openai
    兼容: OpenAI / DeepSeek / 通义千问 / Moonshot / Ollama / vLLM 等
          所有提供 OpenAI 兼容端点的 LLM 服务
    """

    def execute(self, ctx: LlmContext) -> LlmResult:
        from openai import OpenAI

        kwargs = {"api_key": ctx.api_key}
        if ctx.base_url:
            kwargs["base_url"] = ctx.base_url
        client = OpenAI(**kwargs)

        # OpenAI 用 messages 传 system prompt，不是独立参数
        messages: list[dict] = []
        if ctx.system_prompt:
            messages.append({"role": "system", "content": ctx.system_prompt})
        messages.extend(ctx.messages)

        response = client.chat.completions.create(
            model=ctx.model,
            max_tokens=ctx.max_tokens,
            messages=messages,
        )

        content = response.choices[0].message.content or ""
        return LlmResult(content=content)


# ── Provider 注册表 ────────────────────────────────────────────────

_SDK_PROVIDER_REGISTRY: dict[str, type[LlmSdkProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    # 要添加新 SDK 后端，在这里注册一行即可，例如:
    # "azure": AzureProvider,
    # "bedrock": BedrockProvider,
}


def _create_sdk_provider(sdk_type: str) -> LlmSdkProvider:
    """工厂方法：根据 sdk_type 创建对应的 Provider 实例"""
    provider_cls = _SDK_PROVIDER_REGISTRY.get(sdk_type)
    if provider_cls is None:
        available = ", ".join(_SDK_PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"不支持的 LLM_SDK_TYPE={sdk_type}，可用的后端: {available}"
        )
    return provider_cls()


# ═══════════════════════════════════════════════════════════════════════
# 执行器
# ═══════════════════════════════════════════════════════════════════════


class LlmExecutor:
    """执行 LlmContext，按 provider 类型分发到对应后端

    provider: "sdk" → 使用 SDK（由 sdk_type 选择具体后端）
              "cli" → 使用命令行工具（不限工具，由 LLM_CLI_COMMAND 控制）
    """

    def __init__(
        self,
        provider: Literal["sdk", "cli"] = "sdk",
        sdk_type: str = "anthropic",
    ):
        self.provider = provider
        self.sdk_type = sdk_type

    def execute(self, ctx: LlmContext) -> LlmResult:
        cb = get_circuit_breaker()
        if cb.is_open:
            raise RuntimeError("LLM 熔断器已打开，所有 LLM 调用暂时被拒绝。请等待冷却后重试。")

        try:
            if self.provider == "sdk":
                result = self._execute_sdk(ctx)
            elif self.provider == "cli":
                result = self._execute_cli(ctx)
            else:
                raise ValueError(f"Unknown provider: {self.provider}")
            cb.record_success()
            return result
        except Exception:
            cb.record_failure()
            raise

    # ── SDK provider ───────────────────────────────────────────────

    def _execute_sdk(self, ctx: LlmContext) -> LlmResult:
        """通过可插拔 SDK Provider 调用"""
        sdk_provider = _create_sdk_provider(self.sdk_type)
        logger.info("SDK call: backend=%s model=%s", self.sdk_type, ctx.model)
        return sdk_provider.execute(ctx)

    # ── CLI provider ───────────────────────────────────────────────

    def _execute_cli(self, ctx: LlmContext) -> LlmResult:
        """通过命令行工具调用——不限工具，由 LLM_CLI_COMMAND 模板控制

        LLM_CLI_COMMAND 示例:
          claude -p "{prompt}" --output-format json --max-tokens {max_tokens}
          claude --file "{prompt_file}" --output-format json

        占位符:
          {prompt}      — prompt 文本内联（短 prompt 用，Windows 有长度限制）
          {prompt_file} — prompt 写入临时文件，传文件路径（无长度限制，推荐）
          {model} {max_tokens} {system_prompt}

        ⚠️ 安全说明: shell=True 用于支持命令行模板中的管道/重定向等 shell 特性。
        当前命令来自环境变量 LLM_CLI_COMMAND，由管理员配置，不是用户输入。
        如果后续改为接受用户输入，必须添加命令白名单校验或改用 shell=False + shlex.split()。
        """
        from config import get_env

        command_template = get_env(
            "LLM_CLI_COMMAND",
            'claude -p "{prompt}" --output-format json --max-tokens {max_tokens}',
        )

        full_prompt = _build_cli_prompt(ctx)

        # 如果有 {prompt_file} 占位符，写入临时文件
        prompt_file = None
        if "{prompt_file}" in command_template:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            )
            tmp.write(full_prompt)
            tmp.close()
            prompt_file = tmp.name

        # 模板替换
        cmd_str = (
            command_template.replace("{prompt_file}", prompt_file or "")
            .replace("{prompt}", _shell_escape(full_prompt))
            .replace("{model}", ctx.model)
            .replace("{max_tokens}", str(ctx.max_tokens))
            .replace("{system_prompt}", _shell_escape(ctx.system_prompt))
        )

        logger.info("CLI execute: %.300s...", cmd_str)

        try:
            # shell=True 用于支持模板中的 shell 特性（管道、重定向等）
            # 命令来源是管理员配置的环境变量，不是用户输入，风险可控
            result = subprocess.run(
                cmd_str,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            if prompt_file:
                try:
                    os.unlink(prompt_file)
                except OSError:
                    pass
            raise RuntimeError("CLI 调用超时（300s）")
        finally:
            if prompt_file:
                try:
                    os.unlink(prompt_file)
                except OSError:
                    pass

        if result.returncode != 0:
            stderr_preview = result.stderr[:200] if result.stderr else "(empty)"
            raise RuntimeError(
                f"CLI 返回非零退出码 {result.returncode}: {stderr_preview}"
            )

        raw = result.stdout.strip() or result.stderr.strip()
        return LlmResult(content=raw)


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════


def _extract_anthropic_content(response) -> str:
    """从 Anthropic SDK 响应中提取纯文本（兼容多种 block 类型）"""
    text_parts = []
    for block in response.content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
        elif hasattr(block, "input") and hasattr(block, "name"):
            try:
                if isinstance(block.input, dict):
                    text_parts.append(json.dumps(block.input, ensure_ascii=False))
                else:
                    text_parts.append(str(block.input))
            except Exception:
                pass
        elif hasattr(block, "thinking") and block.thinking:
            text_parts.append(block.thinking)
    return "".join(text_parts)


def _build_cli_prompt(ctx: LlmContext) -> str:
    """将 LlmContext 的多条 messages 合并为单一 prompt 文本，附带 system prompt"""
    parts = []
    if ctx.system_prompt:
        parts.append(f"## System\n{ctx.system_prompt}")
    for m in ctx.messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        parts.append(f"## {role}\n{content}")
    return "\n\n".join(parts)


def _shell_escape(text: str) -> str:
    """转义 shell 双引号敏感字符（简单实现，够用即可）"""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
