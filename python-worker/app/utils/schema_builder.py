from datetime import date, datetime
from decimal import Decimal
from typing import List, Dict, Type, Optional, Any
from pydantic import BaseModel, Field, create_model

TYPE_MAP = {
    "float": float,
    "int": int,
    "str": str,
    "bool": bool,
    "date": date,
    "datetime": datetime,
    "decimal": Decimal,
    "optional[str]": Optional[str],
    "optional[float]": Optional[float],
    "optional[int]": Optional[int],
    "optional[date]": Optional[date],
    "list[str]": List[str],
}

def build_pydantic_model(name: str, fields: List[Dict[str, Any]]) -> Type[BaseModel]:
    """
    Construye un modelo Pydantic dinámico desde lista de campos de BD.

    fields: [
        {"field_name": "monto", "field_type": "float", "description": "...", "is_required": true, "field_order": 1},
        ...
    ]
    """
    field_defs = {}

    for f in fields:
        fname = f["field_name"]
        ftype_str = f["field_type"].lower().strip()
        ftype = TYPE_MAP.get(ftype_str, str)
        required = f.get("is_required", True)
        desc = f.get("description", "")
        default = ftype = Optional[ftype]

        if required:
            field_defs[fname] = (ftype, Field(..., description=desc))
        else:
            field_defs[fname] = (ftype, Field(default=None, description=desc))

    return create_model(name, **field_defs)

def build_response_model(base_model: Type[BaseModel], name: str = "LlamaParserResponse") -> Type[BaseModel]:
    """Añade campos de respuesta estándar al modelo base"""
    return create_model(
        name,
        __base__=base_model,
        confidence=(Optional[float], Field(default=None,ge=0.0, le=1.0)),
        field_confidences=(Dict[str, float], Field(default_factory=dict, description="Confianza por campo")),
        raw_response=(Optional[Dict[str, Any]], Field(default=None, description="Respuesta cruda de LlamaCloud")),
    )