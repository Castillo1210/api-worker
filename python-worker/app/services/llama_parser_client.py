from pydantic import BaseModel
import time
from llama_cloud import LlamaCloud
from llama_cloud.types import ExtractConfiguration, FileCreateResponse, ExtractV2Job
from io import BytesIO
from typing import Optional, Dict, Any, Type
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import get_settings
from app.models.llama_parser import LLamaParserRequest, LlamaParserResponse
from app.services.schema_registry import SchemaRegistry
from app.services.cloudsql_client import CloudSQLClient
from app.utils.schema_builder import build_pydantic_model, build_response_model
import structlog

logger = structlog.get_logger()

class LlamaParserError(Exception):
    def __init__(self, message: str, error_code: str = "IA_ERROR_GENERICO", original_error: Exception = None):
        super().__init__(message)
        self.error_code = error_code
        self.original_error = original_error

class LlamaParserClient:
    def __init__(self, schema_registry: SchemaRegistry, cloud_sql_client: CloudSQLClient):
        """
        Cliente para LlamaParse usando su API REST.
        :param api_key: API Key obtenida en cloud.llamaindex.ai
        :param timeout: Tiempo máximo total para el proceso
        """
        self.settings = get_settings()
        self.client = LlamaCloud(api_key=self.settings.LLAMA_PARSER_API_KEY)
        self.schema_registry = schema_registry
        self.db = cloud_sql_client
        self.timeout = self.settings.LLAMA_PARSER_TIMEOUT
        self.max_retries = self.settings.LLAMA_PARSER_MAX_RETRIES

        # Cache de modelos (hash simple de campos)
        self._schema_cache: Dict[str, tuple[Type[BaseModel], Type[BaseModel]]] = {}

    async def extract(self, file_bytes: bytes, file_type: str) -> LlamaParserResponse:
        """
        Llama a LlamaCloud usando SDK oficial
        Retorna LlamaParserResponse parseado
        """

        # 1. Obtener o construir modelos dinámicos
        schema_class, response_class = await self._get_or_build_models()

        # 2. Guardar archivo temporal
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=f".{file_type}", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            return await self._extract_with_retry(tmp_path, schema_class, response_class)
        finally:
            # Limpiar archivo temporal
            try:
                os.unlink(tmp_path)
            except:
                pass

    async def _get_or_build_models(self) -> tuple[Type[BaseModel], Type[BaseModel]]:
        """Obtiene modelos cacheados o los construye desde BD"""
        fields = await self.db.get_active_schema_fields()

        # Hash simple para cache invalidation
        fields_hash = str(hash(tuple((f["field_name"], f["field_type"], f["description"], f["is_required"]) for f in fields)))

        if fields_hash in self._schema_cache:
            return self._schema_cache[fields_hash]
        
        # Construir modelos
        schema_class = build_pydantic_model("VoucherSchema", fields)
        response_class = build_response_model(schema_class, "LlamaParserResponse")

        self._schema_cache[fields_hash] = (schema_class, response_class)
        logger.info("Modelos Pydantic construidos dinámicamente", fields_count=len(fields))

        return schema_class, response_class

    async def _extract_with_retry(self, file_path: str, schema_class, response_class):
        last_error = None

        for attempt in range(self.max_retries):
            try:
                logger.info("Llamando LlamaCloud", attempt=attempt + 1)

                # 1. Subir archivo
                file_obj = self.client.files.create(file=file_path, purpose="extract")

                # 2. Crear job con schema dinámico
                job = self.client.extract.create(
                    file_input=file_obj.id,
                    configuration={
                        "data_schema": schema_class.model_json_schema(),
                        "extraction_target": "per_doc",
                        "tier": "agentic",
                    },
                )

                # 3. Poll con timeout
                start_time = time.time()
                while job.status not in ("COMPLETED", "FAILED", "CANCELLED"):
                    if time.time() - start_time > self.timeout:
                        raise LlamaParserError(
                            f"Timeout después de {self.timeout}s esperando respuesta de LlamaCloud",
                            error_code="IA_TIMEOUT"
                        )
                    time.sleep(2)
                    job = self.client.extract.get(job.id)

                # 4. Manejar respuesta
                if job.status == "FAILED":
                    error_msg = getattr(job, 'error', 'Unknown error')
                    # Determinar código de error según el mensaje
                    if "500" in str(error_msg) or "Internal Server Error" in str(error_msg):
                        raise LlamaParserError(
                            f"LlamaCloud error 500: {error_msg}",
                            error_code="IA_ERROR_500"
                        )
                    raise LlamaParserError(f"LlamaCloud FAILED: {error_msg}", error_code="IA_CANCELLED")
                
                if job.status == "CANCELLED":
                    raise LlamaParserError("LlamaCloud job CANCELLED", error_code="IA_CANCELLED")
                
                # Éxito - parsear respuesta
                data = job.extract_result
                if isinstance(data, BaseModel):
                    data = data.model_dump()

                logger.info("LlamaCloud extracción exitosa", fiels=list(data.keys()))
                return data
            except LlamaParserError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(
                    "Error en llamada LlamaCloud, reintentando",
                    attemp=attempt + 1,
                    error=str(e)
                )
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
        raise LlamaParserError(f"Falló tras {self.max_retries} intentos: {last_error}", error_code="IA_MAX_RETRIES", original_error=last_error)