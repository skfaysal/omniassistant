from langchain_core.messages import AIMessage

from chains.text2sql_chain import build_text2sql_chain
from states import AgentState

_chain = build_text2sql_chain()


def text2sql_node(state: AgentState) -> dict:
    result = _chain.invoke({"messages": state["messages"]})
    return {"messages": [AIMessage(content=result.content, name="text2sql_agent")]}
