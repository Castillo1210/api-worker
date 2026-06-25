import cv2
import numpy as np
import base64
from pdf2image import convert_from_bytes
from PIL import Image
import io
from app.config import get_settings
from app.models.deposit import QualityResult
import structlog

logger = structlog.get_logger()

class QualityValidator:
    def __init__(self):
        self.settings = get_settings()

    def validate(self, file_bytes: bytes, file_type: str) -> QualityResult:
        """
        Valida calidad de imagen o PDF
        - Imagen: OpenCV directo
        - PDF: Renderiza 1ra página a DPI configurado -> valida como imagen
        Retorna QualityResult con processed_bytes (imagen mejorada) si válido.
        """
        try:
            if file_type == "pdf":
                return self._validate_pdf(file_bytes)
            else:
                return self._validate_image(file_bytes)
        except Exception as e:
            logger.error("Error en validación calidad", error=str(e), file_type=file_type)
            return QualityResult(
                is_valid=False,
                issues=["VALIDATION_ERROR"],
                metrics={"error": str(e)},
                file_type=file_type
            )
        
    def _validate_pdf(self, pdf_bytes: bytes) -> QualityResult:
        """Renderiza primera página del PDF y valida como imagen"""
        try:
            # Convertir primera página
            images = convert_from_bytes(
                pdf_bytes,
                dpi=self.settings.QUALITY_PDF_DPI,
                first_page=1,
                last_page=1,
                fmt="RGB"
            )

            if not images:
                return QualityResult(
                    is_valid=False,
                    issues=["PDF_EMPTY"],
                    metrics={},
                    file_type="pdf"
                )
            
            # PIL a OpenCV
            pil_image = images[0]
            cv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)

            # Validar commo imagen
            result = self._validate_image_cv(cv_image)
            result.file_type = "pdf"

            # Si válido, convertir imagen procesada a bytes (PNG)
            if result.is_valid and result.processed_bytes is not None:
                _, buffer = cv2.imencode('.png', result.processed_bytes)
                result.processed_bytes = buffer.tobytes()

            return result
        except Exception as e:
            logger.error("Error procesando PDF", error=str(e))
            return QualityResult(
                is_valid=False,
                issues=["PDF_PROCESSING_ERROR"],
                metrics={"error": str(e)},
                file_type="pdf"
            )
        
    def _validate_image(self, image_bytes: bytes) -> QualityResult:
        """Valida imagen directa"""
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return QualityResult(
                is_valid=False,
                issues=["INVALID_IMAGE_FORMAT"],
                metrics={},
                file_type="image"
            )
        
        return self._validate_image_cv(img)
    
    def _validate_image_cv(self, img: np.ndarray) -> QualityResult:
        """Validación core con OpenCV"""
        issues = []
        metrics = {}
        h, w = img.shape[:2]

        metrics["resolution"] = f"{w}x{h}"
        metrics["width"] = w
        metrics["height"] = h

        # 1. Resolución mínima
        if w < self.settings.QUALITY_MIN_WIDTH or h < self.settings.QUALITY_MIN_HEIGHT:
            issues.append("RESOLUTION_TOO_LOW")

        # 2. Blur - Laplacian variance
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
        metrics["blur_score"] = float(blur_score)

        if blur_score < self.settings.QUALITY_MIN_BLUR_SCORE:
            issues.append("BLURRY")

        # 3. Glare - píxeles saturados
        glare_ratio = np.sum(gray > 240)
        metrics["glare_ratio"] = float(glare_ratio)

        if glare_ratio > self.settings.QUALITY_MAX_GLARE_RATIO:
            issues.append("GLARE_DETECTED")

        # 4. Contraste
        contrast = gray.std()
        metrics["contrast"] = float(contrast)

        if contrast < self.settings.QUALITY_MIN_CONTRAST:
            issues.append("LOW_CONTRAST")

        # 5. Detección documento (contorno rectangular grande)
        doc_confidence = self.__detect_document(gray, w, h)
        metrics["document_confidence"] = doc_confidence

        if doc_confidence < self.settings.QUALITY_MIN_DOC_CONFIDENCE:
            issues.append("NO_DOCUMENT_DETECTED")

        is_valid = len(issues) == 0

        # Si válido, procesar imagen (crop, enhance)
        processed_bytes = None
        if is_valid:
            processed_img = self._enhance_image(img, gray)
            _, buffer = cv2.imencode('.png', processed_img)
            processed_bytes = buffer.tobytes()
            metrics["processed_size"] = len(processed_bytes)

        logger.info(
            "Validación calidad completada",
            is_valid=is_valid,
            issues=issues,
            file_type="image" if "pdf" not in str(type(img)) else "pdf"
        )

        return QualityResult(
            is_valid=is_valid,
            issues=issues,
            metrics=metrics,
            processed_bytes=processed_bytes,
            file_type="image"
        )
    
    def _detect_document(self, gray: np.ndarray, w: int, h: int) -> float:
        """Detecta contorno rectangular de documento"""
        try:
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            if not contours:
                return 0.0
            
            max_area = 0
            best_score = 0

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < (w * h * 0.1):
                    continue

                peri = cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)

                if len(approx) == 4:
                    rect_area = cv2.contourArea(approx)
                    if rect_area > max_area:
                        max_area = rect_area
                        best_score = min(1.0, rect_area / (w * h))

            return best_score
        except Exception:
            return 0.0
        
    def _enhance_image(self, img: np.ndarray, gray: np.ndarray) -> np.ndarray:
        """Mejora imagen: crop documento + CLAHE contraste"""
        try:
            # Detectar contorno documento para crop
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            best_cnt = None
            max_area = 0
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area > max_area and area > (img.shape[0] * img.shape[1] * 0.1):
                    peri = cv2.arcLength(cnt, True)
                    approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
                    if len(approx) == 4:
                        max_area = area
                        best_cnt = approx

            # Si hay contorno rectangular, hacer perspective transform
            if best_cnt is not None:
                img = self._four_point_transform(img, best_cnt.reshape(4, 2))
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # CLAHE para contraste
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced_gray = clahe.apply(gray)

            # Convertir de vuelta a BGR
            enhanced = cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)

            return enhanced
        except Exception:
            return img
        
    def _four_point_transform(self, image: np.ndarray, pts: np.ndarray) -> np.ndarray:
        """Perspective transform a 4 puntos"""
        rect = self._order_points(pts)
        (tl, tr, br, bl) = rect

        width_a = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        width_b = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        max_width = max(int(width_a), int(width_b))
        
        height_a = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        height_b = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        max_height = max(int(height_a), int(height_b))

        dst = np.array([
            [0, 0],
            [max_width - 1, 0],
            [max_width -1, max_height - 1],
            [0, max_height - 1]
        ], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(image, M, (max_width, max_height))

        return warped
    
    def _order_points(self, pts: np.ndarray) -> np.ndarray:
        """Ordena puntos: top-left, top-right, bottom-right, bottom-left"""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect