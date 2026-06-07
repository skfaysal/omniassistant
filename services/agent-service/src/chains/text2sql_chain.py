from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from core.llm import get_llm

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "text2sql.txt"


def build_text2sql_chain() -> Runnable:
    system_prompt = _PROMPT_PATH.read_text()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("placeholder", "{messages}"),
    ])
    return prompt | get_llm()
