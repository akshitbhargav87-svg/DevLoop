from langchain_ollama import ChatOllama

from backend.config import settings


def get_llm() -> ChatOllama:
    """Create the configured local Ollama LLM client."""
    return ChatOllama(
        model=settings.ollama_model,
        temperature=0,
    )