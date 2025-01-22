from typing import List, Any
from pydantic import BaseModel


class ResponseDetails(BaseModel):
    messages: List[dict]
    context_variables: dict


class AgentResponse(BaseModel):
    agent_name: str
    content: str | BaseModel
    details: Any


AgentInput = str | BaseModel | AgentResponse | List[str | BaseModel | dict]

