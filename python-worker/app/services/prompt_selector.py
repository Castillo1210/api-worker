import json
from pathlib import Path
import time
from typing import Dict, Any
from app.services.cloudsql_client import CloudSQLClient
import structlog

logger = structlog.get_logger()

class PromptSelector:
    def __init__(self, db: CloudSQLClient):
        self.db = db
        self._cache = {} # Cache en memoria: banco_id -> prompt
        self._cache_ttl = 300 # 5 min

    async def get_prompt(self, banco_id: str) -> str:
        # 1. Check cache
        if banco_id in self._cache:
            cached, timestamp = self._cache[banco_id]
            if time.time() - timestamp < self._cache_ttl:
                return cached
            
        # 2. Query BD
        prompt_data = await self.db.get_bank_prompt(banco_id)
        if not prompt_data:
            # Fallback a prompt genérico
            return self._default_base_template()
        
        # 3. Construir prompt combinado
        prompt = self._build_prompt(prompt_data)

        # 4. Cachear
        self._cache[banco_id] = (prompt, time.time())
        return prompt
    
    def _build_prompt(self, prompt_data: str) -> str:
        prompt = self._default_base_template()
        prompt += f"\n\n--- PROMPT ESPECÍFICO BANCO ---\n{prompt_data}"
        return prompt
    
    def _default_base_template(self) -> str:
        return """Eres un experto en extracción de datos de vouchers bancarios peruanos.
        Analiza el documento (imagen o PDF) y extrae EXACTAMENTE los siguientes campos en formato JSON:
        
        {
            "monto": float,
            "moneda": "PEN" | "USD",
            "beneficiario": "string",
            "fecha_operacion" "YYYY-MM-DD",
            "numero_operacion": "string",
            "cliente": "string|null",
            "confidence": float,
            "field_confidence": {"campo": float}
        }
        
        REGLAS OBLIGATORIAS:
        - monto: número decimal, usar PUNTO como separador decimal (ej: 1500.50)
        - moneda: SOLO "PEN" o "USD"
        - beneficiario: nombre de la empresa que recibe el deposito
        - fecha_operacion: formato ISO YYYY-MM-DD
        - numero_operacion: tal cual aparece (alfanumérico, sin espacios)
        - cliente: nombre del cliente/pagador si es visible, sino null
        - confidence: confianza global 0.0-1.0
        - field_confidences: confianza por campo individual 0.0-1.0

        Si un campo no es legible: pon null y 0.0 en field_confidences.
        NO INVENTES DATOS. Solo extrae lo visible.
        """
