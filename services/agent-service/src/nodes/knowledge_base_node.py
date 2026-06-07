from langchain_core.messages import AIMessage

from chains.knowledge_base_chain import build_knowledge_base_chain
from states import AgentState

_chain = build_knowledge_base_chain()


def knowledge_base_node(state: AgentState) -> dict:
    result = _chain.invoke({"messages": state["messages"]})
    return {"messages": [AIMessage(content=result.content, name="knowledge_base_agent")]}
