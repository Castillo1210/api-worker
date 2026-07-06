import asyncio
from pydantic import BaseModel
import time
from typing import Optional, Dict, Any, Type
from app.config import get_settings
import builtins
from app.services.schema_registry import SchemaRegistry
from app.services.cloudsql_client import CloudSQLClient
from app.utils.schema_builder import build_pydantic_model, build_response_model
import structlog
from pydantic import BaseModel

if not hasattr(builtins, 'MetadataFilters'):
    class MetadataFilters(BaseModel):
        pass
    builtins.MetadataFilters = MetadataFilters
# ==============================================

from llama_cloud import AsyncLlamaCloud

from app.config import get_settings
from app.services.cloudsql_client import CloudSQLClient
from app.services.schema_registry import SchemaRegistry
from app.utils.schema_builder import build_pydantic_model, build_response_model

logger = structlog.get_logger()


class LlamaParserError(Exception):
    def __init__(self, message: str, error_code: str = "IA_ERROR_GENERICO", original_error: Exception = None):
        super().__init__(message)
        self.error_code = error_code
        self.original_error = original_error


class LlamaParserClient:
    def __init__(
        self,
        schema_registry: Optional[SchemaRegistry] = None,
        cloud_sql_client: Optional[CloudSQLClient] = None,
    ):
        self.settings = get_settings()
        self.api_key = self.settings.LLAMA_CLOUD_API_KEY or self.settings.LLAMA_PARSER_API_KEY
        if not self.api_key:
            raise ValueError("Missing LLAMA_CLOUD_API_KEY")

        self.schema_registry = schema_registry
        self.db = cloud_sql_client
        self.timeout = self.settings.LLAMA_PARSER_TIMEOUT
        self.max_retries = self.settings.LLAMA_PARSER_MAX_RETRIES
        self._schema_cache: Dict[str, tuple[Type[BaseModel], Type[BaseModel]]] = {}
        self._client = None

    def _get_client(self):
        if self._client is None:

            self._client = AsyncLlamaCloud(api_key=self.api_key)
        return self._client

    async def extract(self, file_bytes: bytes, file_type: str) -> BaseModel:
        schema_class, response_class = await self._get_or_build_models()

        filename = f"voucher.{file_type}"
        media_type = "application/pdf" if file_type == "pdf" else f"image/{file_type}"
        client = self._get_client()

        last_error = None
        for attempt in range(self.max_retries):
            try:
                file_obj = await client.files.create(
                    file=(filename, file_bytes, media_type),
                    purpose="extract",
                )

                job = await client.extract.create(
                    file_input=file_obj.id,
                    configuration={
                        "data_schema": schema_class.model_json_schema(),
                        "extraction_target": "per_doc",
                        "tier": "agentic",
                    },
                )

                start_time = time.time()
                while job.status not in ("COMPLETED", "FAILED", "CANCELLED"):
                    if time.time() - start_time > self.timeout:
                        raise LlamaParserError(
                            f"Timeout después de {self.timeout}s esperando respuesta de LlamaCloud",
                            error_code="IA_TIMEOUT",
                        )
                    await asyncio.sleep(2)
                    job = await client.extract.get(job.id)

                if job.status == "FAILED":
                    error_msg = getattr(job, "error", None) or getattr(job, "error_message", None) or "Unknown error"
                    raise LlamaParserError(f"LlamaCloud FAILED: {error_msg}", error_code="IA_CANCELLED")

                if job.status == "CANCELLED":
                    raise LlamaParserError("LlamaCloud job CANCELLED", error_code="IA_CANCELLED")

                data = getattr(job, "extract_result", None)
                if hasattr(data, "model_dump"):
                    data = data.model_dump()
                if data is None:
                    raise LlamaParserError("LlamaCloud no devolvió extract_result", error_code="IA_NO_RESULT")

                logger.info("LlamaCloud extracción exitosa", fields=list(data.keys()) if isinstance(data, dict) else None)
                return response_class.model_validate(data)
            except LlamaParserError:
                raise
            except Exception as e:
                last_error = e
                logger.warning("Error en llamada LlamaCloud, reintentando", attempt=attempt + 1, error=str(e))
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)

        raise LlamaParserError(
            f"Falló tras {self.max_retries} intentos: {last_error}",
            error_code="IA_MAX_RETRIES",
            original_error=last_error,
        )

    async def _get_or_build_models(self) -> tuple[Type[BaseModel], Type[BaseModel]]:
        if self.schema_registry is not None:
            schema_class = await self.schema_registry.get_voucher_schema()
            response_class = await self.schema_registry.get_response_schema()
            return schema_class, response_class

        if self.db is None:
            self.db = CloudSQLClient()

        fields = await self.db.get_active_schema_fields()
        fields_hash = str(hash(tuple((f["field_name"], f["field_type"], f["description"], f["is_required"]) for f in fields)))

        if fields_hash in self._schema_cache:
            return self._schema_cache[fields_hash]

        schema_class = build_pydantic_model("VoucherSchema", fields)
        response_class = build_response_model(schema_class, "LlamaParserResponse")

        self._schema_cache[fields_hash] = (schema_class, response_class)
        logger.info("Modelos Pydantic construidos dinámicamente", fields_count=len(fields))
        return schema_class, response_class
