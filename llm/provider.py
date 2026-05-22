from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_message: str,
        json_mode: bool = False,
    ) -> str:
        """
        Send a chat completion request and return the response text.

        :param system_prompt: System / instruction message.
        :param user_message:  User turn content.
        :param json_mode:     When True, request JSON-formatted output.
        """


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    def __init__(self, model: str, api_key: str):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        json_mode: bool = False,
    ) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# Anthropic (optional)
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    def __init__(self, model: str, api_key: str):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        json_mode: bool = False,
    ) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return msg.content[0].text


# ---------------------------------------------------------------------------
# Gemini (optional)
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    def __init__(self, model: str, api_key: str):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._model_name = model

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        json_mode: bool = False,
    ) -> str:
        import google.generativeai as genai
        model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type=(
                    "application/json" if json_mode else "text/plain"
                )
            ),
        )
        return model.generate_content(user_message).text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> LLMProvider:
    """
    Instantiate the configured LLM provider based on LLM_PROVIDER in .env.
    Defaults to OpenAI (gpt-4o-mini).
    """
    from config import settings

    provider = settings.llm_provider.lower()

    if provider == "openai":
        return OpenAIProvider(
            model=settings.model_name or "gpt-4o-mini",
            api_key=settings.openai_api_key,
        )
    if provider == "anthropic":
        return AnthropicProvider(
            model=settings.model_name or "claude-3-haiku-20240307",
            api_key=settings.anthropic_api_key,
        )
    if provider == "gemini":
        return GeminiProvider(
            model=settings.model_name or "gemini-1.5-flash",
            api_key=settings.gemini_api_key,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. "
        "Set it to 'openai', 'anthropic', or 'gemini' in .env."
    )
