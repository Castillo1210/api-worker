from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import date
from decimal import Decimal
from uuid import UUID
import uuid

class DepositRow(BaseModel):
    """Row from depositos table joined with profiles"""
    id: uuid.UUID
    numero_operacion: str
    cliente: Optional[str]
    monto: Decimal
    moneda: str
    fecha_registro: str
    imagen_voucher: Optional[str] # GCS object name
    anexo: Optional[str]
    numero_operacion_banco: Optional[str]
    fecha_deposito: Optional[date]
    estado: str
    observaciones: Optional[str]
    motivo_rechazo: Optional[str]
    fecha_validacion: Optional[str]
    empresa_id: uuid.UUID
    banco_id: Optional[uuid.UUID]
    sucursal_id: Optional[uuid.UUID]
    vendedor_id: uuid.UUID
    validado_por: Optional[uuid.UUID]
    trabajador_sucursal_id: Optional[int]
    referencia_cliente: Optional[str]
    datos_ocr: Optional[Dict[str, Any]]
    telefono_origen: Optional[str]
    ruc_cliente: Optional[str]
    es_antiguo: Optional[bool]
    fecha_solo_date: Optional[date]
    # From profiles join
    fcm_token: Optional[str]
    email: str

class QualityResult(BaseModel):
    is_valid: bool
    issues: List[str]
    metrics: Dict[str, Any]
    processed_bytes: Optional[bytes] = None
    file_type: str

class ProcessDepositRequest(BaseModel):
    deposit_id: str

class LlamaParserResponse(BaseModel):
    monto: float
    moneda: str # PEN | USD
    beneficiario: str
    fecha_operacion: str # YYYY-MM-DD
    numero_operacion: str
    cliente: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    field_confidence: Dict[str, float] = {}

class DepositUpdateData(BaseModel):
    monto: float
    moneda: str
    fecha_deposito: str
    numero_operacion: str
    numero_operacion_banco: Optional[str] = None
    empresa_id: Optional[uuid.UUID] = None
    cliente: Optional[str] = None
    datos_ocr: Dict[str, Any] = None
    estado: str
    motivo_rechazo: Optional[str] = None
    fecha_validacion: Optional[str] = None
    error_ids: List[UUID] = []
    warning_ids: List[UUID] = []

class ValidationResult(BaseModel):
    is_valid: bool
    errors: List[str]

class QualityValidateRequest(BaseModel):
    file: Any

class QualityValidateResponse(BaseModel):
    valid: bool
    issues: List[str] = []
    message: Optional[str] = None
    processed_file: Optional[str] = None
    file_type: Optional[str] = None