import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.models.deposit import DepositUpdateData
from app.services.cloudsql_client import CloudSQLClient


class _AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AcquireContext(self.connection)


@pytest.mark.asyncio
async def test_update_deposit_persiste_datos_ocr_como_json():
    connection = AsyncMock()
    connection.execute.return_value = "UPDATE 1"

    client = object.__new__(CloudSQLClient)
    client.pool = _FakePool(connection)

    datos_ocr = {
        "verificacion": {
            "monto": {
                "accion": "revision_manual",
                "motivo": "Llama y OCR discrepan.",
            }
        },
        "ocr_candidatos": {"monto": [10.0]},
    }
    data = DepositUpdateData(
        monto=Decimal("39.82"),
        moneda="USD",
        fecha_deposito=date(2026, 8, 21),
        numero_operacion="9984434",
        datos_ocr=datos_ocr,
        estado="procesado",
    )

    assert await client.update_deposit("deposit-id", data)

    query, *values = connection.execute.await_args.args
    assert '"DatosOcr"' in query
    assert json.loads(values[5]) == datos_ocr
