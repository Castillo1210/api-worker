import asyncio
import base64
import json
from io import BytesIO
from typing import Optional

import httpx
from pydantic import BaseModel

class AIExtractedData(BaseModel):
    monto: float
    fecha: str
    numero_operacion: str
    moneda: str
    banco: Optional[str] = None
    cuenta_ultimos4: Optional[str] = None
    cliente: Optional[str] = None
    codigo_validacion: Optional[str] = None
    confidence: float

class ThirdPartyAIError(Exception):
    pass

class ThirdPartyAIClient:
    def __init__(self, api_key: str, timeout: float = 60.0):
        """
        Cliente para LlamaParse usando su API REST.
        :param api_key: API Key obtenida en cloud.llamaindex.ai
        :param timeout: Tiempo máximo total para el proceso
        """

        self.api_key = api_key
        self.base_url = "https://api.cloud.llamaindex.ai/api/parsing"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout
        self.poll_interval = 1.0
        self.max_attempts = 3

    async def extract(self, image_base64: str, prompt: str) -> AIExtractedData:
        """
        Procesa una imagen/PDF de voucher y extrae los datos estructurados.
        :param image_base64: Imagen en base64 (string)
        :param prompt: Instrucciones específicas para el parser (por banco, etc.)
        :return: AIExtractedData con los campos extraídos
        """

        # 1. Decodificar base64 a bytes
        try:
            file_bytes = base64.b64decode(image_base64)
        except Exception as e:
            raise ThirdPartyAIError(f"Error decodificando base64: {e}")
        
        # 2. Determinar tipo MIME (puedes mejorarlo con la extensión)
        # Asumimos JPEG por defecto, pero puedes detectar según el contexto
        mime_type = "image/jpeg"

        job_id = await self._upload_file(file_bytes, prompt, mime_type)

        result = await self._poll_job(job_id)

        # 5. Mapear el resultado al modelo AIExtractedData
        try:
            # Si usamos result_type="json", result es un dict con los datos
            if isinstance(result, dict):
                data = result
            else:
                data = json.loads(result)

            # Construir el objeto con los campos extraídos
            # Nota: algunos campos pueden no existir, usar .get()
            return AIExtractedData(
                monto=float(data.get("monto", 0.0)),
                fecha=data.get("fecha", ""),
                numero_operacion=data.get("numero_operacion", ""),
                moneda=data.get("moneda", "PEN"),
                banco=data.get("banco"),
                cuenta_ultimos4=data.get("cuenta_ultimos4"),
                cliente=data.get("cliente"),
                codigo_validacion=data.get("codigo_validacion"),
                confidence=float(data.get("confidence", 0.0))
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ThirdPartyAIError(f"Error parseando la respuesta de LlamaParse: {e}")
        
    # ---------------------- Métodos internos -----------------

    async def _upload_file(self, file_bytes: bytes, prompt: str, mime_type: str) -> str:
        """
        Sube el archivo a LlamaParse y devuelve el job_id.
        """
        files = {
            "file": ("voucher.jpg", BytesIO(file_bytes), mime_type)
        }
        data = {
            "parsing_instruction": prompt,
            "result_type": "json" # Para obtener el resultado en formato JSON
        }

        for attempt in range(self.max_attempts):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/upload",
                        files=files,
                        data=data,
                        headers=self.headers
                    )
                    resp.raise_for_status()
                    job_data = resp.json()
                    job_id = job_data.get("job_id")
                    if not job_id:
                        raise ThirdPartyAIError("No se recibió job_id en la respuesta")
                    return job_id
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt == self.max_attempts - 1:
                    raise ThirdPartyAIError(f"Fallo al subir archivo tras {self.max_attempts} intentos: {e}")
                await asyncio.sleep(2 ** attempt) # 1, 2, 4 segundos
        raise ThirdPartyAIError("No se pudo obtener job.id")
    
    async def _poll_job(self, job_id: str) -> dict:
        """
        Consulta el estado del trabajo hasta que esté completado o falle
        """

        start_time = asyncio.get_event_loop().time()
        interval = self.poll_interval

        while True:
            # Verificar timeout global
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > self.timeout:
                raise ThirdPartyAIError(f"Timeout de {self.timeout}s excedido para el trabajo {job_id}")
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    resp = await client.get(
                        f"{self.base_url}/job/{job_id}",
                        headers=self.headers
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                    await asyncio.sleep(interval)
                    continue

                status = data.get("status")
                if status == "SUCCESS":
                    # Resultado en 'result' si result_type="json"
                    result = data.get("result")
                    if not result:
                        raise ThirdPartyAIError("El job finalizó sin resultado")
                    return result
                elif status == "ERROR":
                    error_msg = data.get("error", "Error desconocido")
                    raise ThirdPartyAIError(f"El job {job_id} falló: {error_msg}")
                elif status in ("PENDING", "PROCESSING"):
                    # Aún procesando, esperar un poco más
                    await asyncio.sleep(interval)
                    # Aumentar el intervalo gradualmente (hasta un máximo)
                    interval = min(interval * 1.5, 10.0)
                else:
                    raise ThirdPartyAIError(f"Estado desconocido: {status}")