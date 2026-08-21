from google.cloud import vision
from app.config import get_settings

def _build_client() -> vision.ImageAnnotatorClient:
    settings = get_settings()
    if settings.VISION_CREDENTIALS_PATH:
        return vision.ImageAnnotatorClient.from_service_account_file(settings.VISION_CREDENTIALS_PATH)
    return vision.ImageAnnotatorClient()

_client: vision.ImageAnnotatorClient = None

def get_vision_client() -> vision.ImageAnnotatorClient:
    global _client
    if _client is None:
        _client = _build_client()
    return _client

def obtener_texto_ocr(content: bytes, es_pdf: bool) -> str:
    client = get_vision_client()
    if es_pdf:
        input_config = vision.InputConfig(content=content, mime_type="application/pdf")
        feature = vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)
        request = vision.AnnotateFileRequest(input_config=input_config, features=[feature])
        response = client.batch_annotate_files(requests=[request])
        textos = []
        for page_response in response.responses[0].responses:
            if page_response.error.message:
                raise RuntimeError(f"Vision API error (PDF): {page_response.error.message}")
            if page_response.full_text_annotation:
                textos.append(page_response.full_text_annotation.text)
        return "\n".join(textos)

    image = vision.Image(content=content)
    response = client.text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")
    return response.full_text_annotation.text if response.full_text_annotation else ""