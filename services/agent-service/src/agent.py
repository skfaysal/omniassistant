from langgraph.graph import END, START, StateGraph

from nodes.knowledge_base_node import knowledge_base_node
from nodes.orchestrator_node import orchestrator_node
from nodes.text2sql_node import text2sql_node
from states import AgentState


def build_graph():
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("text2sql", text2sql_node)
    graph.add_node("knowledge_base", knowledge_base_node)

    # Entry point
    graph.add_edge(START, "orchestrator")

    # Orchestrator decides which sub-agent to call
    graph.add_conditional_edges(
        "orchestrator",
        lambda state: state["next"],
        {
            "text2sql": "text2sql",
            "knowledge_base": "knowledge_base",
        },
    )

    # Both sub-agents terminate after responding
    graph.add_edge("text2sql", END)
    graph.add_edge("knowledge_base", END)

    return graph.compile()


agent = build_graph()
