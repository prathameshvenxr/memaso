from .llm_openai import OpenAILLM


class CompatLLM(OpenAILLM):
    """Any OpenAI-compatible server (vLLM, Ollama, LM Studio, gateways). base_url is required."""

    def __init__(self, model: str, base_url: str, api_key: str | None = None, timeout: float = 120):
        super().__init__(model, api_key=api_key or "", base_url=base_url, json_mode=True, timeout=timeout)
