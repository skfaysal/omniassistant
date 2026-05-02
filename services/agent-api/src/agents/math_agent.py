from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agents.prompts.math_agent_prompt import MATH_AGENT_SYSTEM_PROMPT
from core import get_model, settings


@tool
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


@tool
def subtract(a: float, b: float) -> float:
    """Subtract b from a."""
    return a - b


@tool
def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b


@tool
def divide(a: float, b: float) -> float:
    """Divide a by b. Raises an error if b is zero."""
    if b == 0:
        raise ValueError("Cannot divide by zero.")
    return a / b


_tools = [add, subtract, multiply, divide]
_model = get_model(settings.DEFAULT_MODEL).bind_tools(_tools)


def call_model(state: MessagesState) -> dict:
    messages = [SystemMessage(content=MATH_AGENT_SYSTEM_PROMPT)] + state["messages"]
    response = _model.invoke(messages)
    return {"messages": [response]}


_graph = StateGraph(MessagesState)
_graph.add_node("agent", call_model)
_graph.add_node("tools", ToolNode(_tools))
_graph.set_entry_point("agent")
_graph.add_conditional_edges("agent", tools_condition)
_graph.add_edge("tools", "agent")

math_agent = _graph.compile()
math_agent.name = "sub-agent-math_expert"
