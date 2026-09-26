from typing import Any

from langchain_ollama import ChatOllama

from config import settings


def get_llm() -> Any:
    """Create the configured local Ollama or hosted OpenAI-compatible client."""
    if settings.llm_provider == "ollama":
        return ChatOllama(
            model=settings.ollama_model,
            temperature=0,
            format="json",
        )

    if settings.llm_provider == "openai_compatible":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for the hosted LLM provider.")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
