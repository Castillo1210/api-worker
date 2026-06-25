# app/utils/image_utils.py
import cv2
import numpy as np
import base64
import structlog

logger = structlog.get_logger()


def bytes_to_cv2(image_bytes: bytes) -> np.ndarray:
    """Convierte bytes a imagen OpenCV"""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("No se pudo decodificar imagen")
    return img


def cv2_to_base64(img: np.ndarray, format: str = ".png") -> str:
    """Convierte imagen OpenCV a base64 string"""
    _, buffer = cv2.imencode(format, img)
    return base64.b64encode(buffer).decode()


def base64_to_cv2(base64_str: str) -> np.ndarray:
    """Convierte base64 a imagen OpenCV"""
    img_bytes = base64.b64decode(base64_str)
    return bytes_to_cv2(img_bytes)


def enhance_image_clahe(img: np.ndarray) -> np.ndarray:
    """Mejora contraste con CLAHE"""
    try:
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        
        if len(img.shape) == 3:
            enhanced = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        
        return enhanced
    except Exception:
        return img


def auto_crop_document(img: np.ndarray) -> np.ndarray:
    """Auto-crop a contorno de documento detectado"""
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        best_cnt = None
        max_area = 0
        h, w = img.shape[:2]
        
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < (w * h * 0.1):
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) == 4 and area > max_area:
                max_area = area
                best_cnt = approx.reshape(4, 2)
        
        if best_cnt is not None:
            return four_point_transform(img, best_cnt)
        
        return img
    except Exception:
        return img


def four_point_transform(image: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Perspective transform a 4 puntos ordenados"""
    rect = order_points(pts)
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
        [max_width - 1, max_height - 1],
        [0, max_height - 1]
    ], dtype="float32")
    
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (max_width, max_height))
    
    return warped


def order_points(pts: np.ndarray) -> np.ndarray:
    """Ordena puntos: top-left, top-right, bottom-right, bottom-left"""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def resize_max_dim(img: np.ndarray, max_dim: int = 2000) -> np.ndarray:
    """Redimensiona si excede dimensión máxima manteniendo aspect ratio"""
    h, w = img.shape[:2]
    if max(h, w) <= max_dim:
        return img
    
    scale = max_dim / max(h, w)
    new_w, new_h = int(w * scale), int(h * scale)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)