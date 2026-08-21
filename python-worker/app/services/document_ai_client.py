import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

import structlog
from google.api_core.client_options import ClientOptions
from google.cloud import documentai_v1 as documentai

from app.config import get_settings

logger = structlog.get_logger()

class DocumentAiError(Exception):
    def __init__(self, message: str, error_code: str = "DOCAI_ERROR_GENERICO", original_error: Exception = None):
        super().__init__(message)
        self.error_code = error_code
        self.original_error = original_error

class DocumentAiClient:
    """
    Cliente para el Custom Extractor de Google Document AI (modo plantilla)
    
    Pensado como capa de COMPARACION en paralelo a LlamaParserClient.
    No reemplaza a LlamaCloud como fuente de verdad todavía - solo
    registra lectura para medir precisión real antes de decidir
    si pasa a ser el extractor principal.
    """

    def __init__(self):
        self.settings = get_settings()
        if not self.settings.DOCUMENT_AI_PROCESSOR_ID or not self.settings.DOCUMENT_AI_PROJECT_ID:
            raise ValueError("Falta configurar DOCUMENT_AI_PROJECT_ID / DOCUMENT_AI_PROCESSOR_ID")

        opts = ClientOptions(
            api_endpoint=f"{self.settings.DOCUMENT_AI_LOCATION}-documentai.googleapis.com"
        )

        if self.settings.DOCUMENT_AI_CREDENTIALS_PATH:
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(
                self.settings.DOCUMENT_AI_CREDENTIALS_PATH
            )
            self._client = documentai.DocumentProcessorServiceClient(
                client_options=opts, credentials=credentials
            )
        else:
            self._client = documentai.DocumentProcessorServiceClient(client_options=opts)

        self._processor_name = self._client.processor_path(
            self.settings.DOCUMENT_AI_PROJECT_ID,
            self.settings.DOCUMENT_AI_LOCATION,
            self.settings.DOCUMENT_AI_PROCESSOR_ID,
        )

    async def extract(self, file_bytes: bytes, file_type: str) -> Dict[str, Any]:
        """
        Devuelve un dict con las mismas claves que usa LlamaParserResponse
        (monto, moneda, fecha_operacion, numero_operacion, field_confidence)
        para poder comparar campo a campo con LlamaCloud.
        """

        media_type = "application/pdf" if file_type == "pdf" else f"image/{file_type}"
        raw_document = documentai.RawDocument(content=file_bytes, mime_type=media_type)
        request = documentai.ProcessRequest(name=self._processor_name, raw_document=raw_document)

        try:
            result = await asyncio.to_thread(self._client.process_document, request)
        except Exception as e:
            raise DocumentAiError(f"Error llamando a Document AI: {e}", original_error=e)

        document = result.document
        fields: Dict[str, str] = {}
        confidences: Dict[str, float] = {}

        for entity in document.entities:
            field_name = entity.type_
            value = entity.normalized_value.text if entity.normalized_value.text else entity.mention_text
            fields[field_name] = value
            confidences[field_name] = entity.confidence

        return {
            "monto": self._parse_monto(fields.get("monto")),
            "moneda": fields.get("moneda", "PEN"),
            "fecha_operacion": self._normalize_fecha(fields.get("fecha")),
            "numero__operacion": fields.get("numero_operacion"),
            "field_confidence": confidences,
        }

    @staticmethod
    def _parse_monto(raw: Optional[str]) -> Optional[float]:
        if not raw:
            return None
        try:
            cleaned = raw.replace("S/", "").replace("$", "").replace(",", "").strip()
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _normalize_fecha(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        if len(raw) == 10 and raw[4] == "-":
            return raw
        for fmt in ("%d/%m/%Y", "%d de %B de %Y", "%d %b. %Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue

        return