from pydantic import BaseModel


class UserQuery(BaseModel):
    question: str


class AgentResponse(BaseModel):
    answer: str
    routed_to: str  # which sub-agent handled the question
