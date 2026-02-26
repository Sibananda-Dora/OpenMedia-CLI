from pydantic import BaseModel, Field
from typing import List, Optional

class AgentState(BaseModel):
    user_input: str
    target_file: str
    media_context: Optional[str] = None
    ultra_safe: bool = False
    encoding_preference: Optional[str] = None
    llm_runtime_mode: Optional[str] = None
    llm_effective_mode: Optional[str] = None
    effective_encoder_family: Optional[str] = None
    preferred_video_encoder: Optional[str] = None
    nvenc_available: Optional[bool] = None
    generated_command: Optional[str] = None
    is_valid: bool = False
    error_message: Optional[str] = None
    iteration_count: int = 0
    history: List[str] = Field(default_factory=list)
