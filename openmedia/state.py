from pydantic import BaseModel, Field
from typing import List, Optional

class AgentState(BaseModel):
    user_input: str
    target_file: str
    generated_command: Optional[str] = None
    is_valid: bool = False
    error_message: Optional[str] = None
    iteration_count: int = 0
    history: List[str] = Field(default_factory=list)
    # We add this to track if the user actually clicked "Yes"
    execution_confirmed: bool = False