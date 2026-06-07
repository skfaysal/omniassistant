from typing import Literal

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    # Routing decision set by the orchestrator node
    next: Literal["text2sql", "knowledge_base"]
