"""LLM 工厂：按配置创建 DeepSeek 客户端。

主 Agent 用思考模式；需要强制结构化输出的内部评审器用非思考模式，
避免 DeepSeek 拒绝 ``tool_choice`` 参数。
"""
from langchain_deepseek import ChatDeepSeek

from .settings import get_settings


def get_llm(
    *,
    thinking: bool = True,
    timeout: float | None = None,
    max_retries: int = 2,
    max_tokens: int | None = None,
) -> ChatDeepSeek:
    """获取 DeepSeek 模型实例。"""
    llm = get_settings().llm
    common_kwargs = {
        "model": llm.model,
        "base_url": llm.base_url,
        "api_key": llm.api_key,
        "max_tokens": max_tokens if max_tokens is not None else llm.max_tokens,
        "max_retries": max_retries,
    }
    if timeout is not None:
        common_kwargs["timeout"] = timeout

    if thinking:
        return ChatDeepSeek(
            **common_kwargs,
            reasoning_effort=llm.reasoning_effort,
            extra_body={"thinking": {"type": "enabled"}},
        )

    model = ChatDeepSeek(
        **common_kwargs,
        temperature=llm.temperature,
        extra_body={"thinking": {"type": "disabled"}},
    )
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        profile["structured_output"] = False
    return model
