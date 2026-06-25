import cv2
import numpy as np
import tensorflow as tf
from dataclasses import dataclass
from typing import List

@dataclass
class QualityResult:
    is_valid: bool
    issues: List[str]
    metrics: dict

class ImageQualityValidator:
    def __init__(self, model_path: str = "models/document_quality.tflite"):
        self.interpreter = tf.lite.Interpreter(model_path="model_path=model_path")
        self.interpreter.allocate_tensors()
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

    def validate(self, image_bytes: bytes) -> QualityResult:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)

        issues = []
        h, w = img.shape[:2]
        if w < 800 or h < 600:
            issues.append("RESOLUTION_TOO_LOW")
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur_score < 100:
            issues.append("BLURRY")

        glare_ratio = np.sum(gray > 240)
        if glare_ratio > 0.15:
            issues.append("GLARE_DETECTED")

        input_tensor = self._preprocess_for_model(img)
        self.interpreter.set_tensor(self.input_details[0]['index'], input_tensor)
        self.interpreter.invoke()
        doc_condifence = self.interpreter.get_tensor(self.output_details[0]['index'])[0][0]

        if doc_condifence < 0.7:
            issues.append("NO_DOCUMENT_DETECTED")
        
        is_valid = len(issues) == 0

        return QualityResult(
            is_valid=is_valid,
            issues=issues,
            metrics={
                "blur_score": float(blur_score),
                "glare_ratio": float(glare_ratio),
                "resolution": f"{w}x{h}",
                "document_confidence": float(doc_condifence)
            }
        )
