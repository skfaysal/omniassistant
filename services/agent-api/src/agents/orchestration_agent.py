from langgraph_supervisor import create_supervisor

from agents.math_agent import math_agent
from agents.prompts.orchestration_prompt import ORCHESTRATION_SYSTEM_PROMPT
from agents.research_agent import research_agent
from core import get_model, settings

_model = get_model(settings.DEFAULT_MODEL)

_workflow = create_supervisor(
    [research_agent, math_agent],
    model=_model,
    prompt=ORCHESTRATION_SYSTEM_PROMPT,
    add_handoff_back_messages=True,
    output_mode="full_history",
)

orchestration_agent = _workflow.compile()
