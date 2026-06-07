from langchain_core.language_models import BaseChatModel

from core.settings import settings


def get_llm() -> BaseChatModel:
    match settings.LLM_PROVIDER:
        case "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=settings.MODEL_ID or "gpt-4o-mini", api_key=settings.OPENAI_API_KEY)
        case "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=settings.MODEL_ID or "claude-sonnet-4-6", api_key=settings.ANTHROPIC_API_KEY)
        case "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=settings.MODEL_ID or "gemini-2.0-flash", google_api_key=settings.GOOGLE_API_KEY)
        case _:
            raise ValueError(f"Unsupported LLM_PROVIDER: '{settings.LLM_PROVIDER}'. Choose openai | anthropic | gemini")
