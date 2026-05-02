from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agents.prompts.research_agent_prompt import RESEARCH_AGENT_SYSTEM_PROMPT
from core import get_model, settings


@tool
def web_search(query: str) -> str:
    """Search the web for up-to-date information on any topic."""
    # TODO: replace with a real search integration (e.g. Tavily, SerpAPI)
    return (
        "Here are the headcounts for each of the FAANG companies in 2024:\n"
        "1. **Facebook (Meta)**: 67,317 employees.\n"
        "2. **Apple**: 164,000 employees.\n"
        "3. **Amazon**: 1,551,000 employees.\n"
        "4. **Netflix**: 14,000 employees.\n"
        "5. **Google (Alphabet)**: 181,269 employees."
    )


_tools = [web_search]
_model = get_model(settings.DEFAULT_MODEL).bind_tools(_tools)


def call_model(state: MessagesState) -> dict:
    messages = [SystemMessage(content=RESEARCH_AGENT_SYSTEM_PROMPT)] + state["messages"]
    response = _model.invoke(messages)
    return {"messages": [response]}


_graph = StateGraph(MessagesState)
_graph.add_node("agent", call_model)
_graph.add_node("tools", ToolNode(_tools))
_graph.set_entry_point("agent")
_graph.add_conditional_edges("agent", tools_condition)
_graph.add_edge("tools", "agent")

research_agent = _graph.compile()
research_agent.name = "sub-agent-research_expert"
