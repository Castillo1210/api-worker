from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class LLamaParserRequest(BaseModel):
    file_base64: str
    file_type: str
    prompt: str
    temperature: float = 0.0
    max_tokens: int = 2000

class LlamaParserResponse(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0, description="Confianza global 0-1")
    field_confidence: Dict[str, float] = Field(default_factory=dict, description="Confianza por campo")
    raw_response: Optional[Dict[str, Any]] = Field(default=None, description="Respuesta cruda")