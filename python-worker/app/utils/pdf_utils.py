import fitz
import io
from PIL import Image
import structlog

logger = structlog.get_logger()

def validate_pdf_basic(pdf_bytes: bytes) -> dict:
    """
    Validación rápida de PDF sin renderizar páginas completas.
    Retorna info básica: páginas, tamaño, si tiene texto, etc.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        info = {
            "page_count": doc.page_count,
            "is_encrypted": doc.is_encrypted,
            "metadata": doc.metadata,
            "pages": []
        }

        # Solo primera página para validación
        if doc.page_count > 0:
            page = doc[0]
            info["pages"].append({
                "width": page.rect.width,
                "height": page.rect.height,
                "rotation": page.rotation,
                "has_text": bool(page.get_text().strip()),
                "has_images": len(page.get_images()) > 0
            })

        doc.close()
        return info
    except Exception as e:
        logger.error("Error validando PDF básico", error=str(e))
        return {"error": str(e), "valid": False}
    
def pdf_to_image(pdf_bytes: bytes, dpi: int = 200, page: int = 0) -> bytes:
    """Convierte página de PDF a imagen PNG bytes usando PyMuPDF (más rápido)"""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        if page >= doc.page_count:
            page = 0

        pg = doc[page]
        # Matrix para DPI deseado (72 DPI base)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        pix = pg.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")

        doc.close()
        return img_bytes
    
    except Exception as e:
        logger.error("Error convirtiendo PDF a imagen", error=str(e))
        raise

def pdf_to_image_pil(pdf_bytes: bytes, dpi: int = 200, page: int = 0) -> Image.Image:
    """Convierte página de PDF a PIL Image"""
    img_bytes = pdf_to_image(pdf_bytes, dpi, page)
    return Image.open(io.BytesIO(img_bytes))