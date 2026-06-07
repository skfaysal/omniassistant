from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from core.llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "knowledge_base.txt"


def build_knowledge_base_chain() -> Runnable:
    system_prompt = _PROMPT_PATH.read_text()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("placeholder", "{messages}"),
    ])
    return prompt | get_llm()
