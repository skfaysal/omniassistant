from pathlib import Path
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from core.llm import get_llm
from states import AgentState

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "orchestrator.txt"


class RoutingDecision(BaseModel):
    next: Literal["text2sql", "knowledge_base"]


def orchestrator_node(state: AgentState) -> dict:
    system_prompt = _PROMPT_PATH.read_text()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("placeholder", "{messages}"),
    ])
    llm = get_llm().with_structured_output(RoutingDecision)
    chain = prompt | llm

    decision: RoutingDecision = chain.invoke({"messages": state["messages"]})
    return {"next": decision.next}
