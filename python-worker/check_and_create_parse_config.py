# check_and_create_parse_config.py
import os
import builtins
from pydantic import BaseModel

# Shim necesario (igual que en llama_parser_client.py) antes de importar llama_cloud
if not hasattr(builtins, 'MetadataFilters'):
    class MetadataFilters(BaseModel):
        pass
    builtins.MetadataFilters = MetadataFilters

from llama_cloud import LlamaCloud

client = LlamaCloud(api_key=os.environ["LLAMA_CLOUD_API_KEY"])

# 1. Ver qué configuraciones parse_v2 existen bajo esta key/proyecto
existing = client.get(
    "/api/v1/beta/configurations",
    cast_to=dict,
    options={"params": {"product_type": "parse_v2"}},
)
print("Parse configs (parse_v2) visibles con esta key:")
for cfg in existing.get("data", []):
    print(f"  {cfg['name']} -> {cfg['id']}")

# 2. Crear una nueva, tier=fast
parse_config = client.post(
    "/api/v1/beta/configurations",
    body={
        "name": "Voucher Parse Fast",
        "parameters": {
            "product_type": "parse_v2",
            "version": "latest",
            "tier": "fast",
        },
    },
    cast_to=dict,
)
print(f"\nNuevo Parse config ID (usar este en LLAMA_PARSE_CONFIG_ID): {parse_config['id']}")