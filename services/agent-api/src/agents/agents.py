from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph
from langgraph.pregel import Pregel

from agents.orchestration_agent import orchestration_agent
from schema import AgentInfo

DEFAULT_AGENT = "orchestration-agent"

AgentGraph = CompiledStateGraph | Pregel


@dataclass
class Agent:
    description: str
    graph_like: AgentGraph


agents: dict[str, Agent] = {
    "orchestration-agent": Agent(
        description="An orchestration agent that routes tasks to specialist sub-agents (math and research).",
        graph_like=orchestration_agent,
    )
}


def get_agent(agent_id: str) -> AgentGraph:
    return agents[agent_id].graph_like


def get_all_agent_info() -> list[AgentInfo]:
    return [
        AgentInfo(key=agent_id, description=agent.description) for agent_id, agent in agents.items()
    ]
