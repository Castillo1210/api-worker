import unittest
import numpy as np
import cv2
from app.services.quality_validator import QualityValidator

class TestQualityValidator(unittest.TestCase):
    def setUp(self):
        self.validator = QualityValidator()

    def generate_synthetic_screenshot(self):
        # 1080x2400 portrait digital image
        img = np.ones((2400, 1080, 3), dtype=np.uint8) * 245
        # Draw some boxes and sharp text (simulating a digital voucher)
        cv2.rectangle(img, (100, 100), (980, 2300), (220, 220, 220), 4)
        cv2.putText(img, "CONFIRMO VOUCHER", (150, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
        cv2.putText(img, "Monto: $150.00", (150, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 128, 0), 3)
        # Add some mock text details to keep edge density reasonable
        for i in range(10):
            cv2.putText(img, f"Detalle linea {i}: info relevante", (150, 600 + i*80), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (100, 100, 100), 2)
        # Add barcode-like vertical bars to get edge density
        for i in range(150, 930, 20):
            cv2.rectangle(img, (i, 1600), (i+10, 1800), (0, 0, 0), -1)
        return img

    def add_camera_noise_and_blur(self, img):
        # Add a bit of blur to simulate camera autofocus
        blurred = cv2.GaussianBlur(img, (5, 5), 0)
        # Add Gaussian noise (camera sensor noise)
        noise = np.random.normal(0, 3, img.shape).astype(np.float32)
        noisy = np.clip(blurred.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        # Add lighting gradient
        h, w = img.shape[:2]
        X, Y = np.meshgrid(np.arange(w), np.arange(h))
        gradient = (1.0 - 0.15 * (X / w + Y / h)).reshape(h, w, 1)
        lit = np.clip(noisy.astype(np.float32) * gradient, 0, 255).astype(np.uint8)
        return lit

    def test_classify_screenshot(self):
        # Generate a clean digital screenshot
        img = self.generate_synthetic_screenshot()
        _, img_bytes = cv2.imencode(".png", img)
        
        result = self.validator.validate(img_bytes.tobytes(), "image", capture_mode="auto")
        
        # Verify classification
        self.assertEqual(result.capture_type, "screenshot")
        self.assertLess(result.metrics.get("fft_moire_score", 0.0), 0.20)
        self.assertTrue(result.is_valid)

    def test_classify_photo_of_paper(self):
        # Generate a camera photo of paper
        base_img = self.generate_synthetic_screenshot()
        img = self.add_camera_noise_and_blur(base_img)
        _, img_bytes = cv2.imencode(".png", img)
        
        result = self.validator.validate(img_bytes.tobytes(), "image", capture_mode="auto")
        
        # Verify classification
        self.assertEqual(result.capture_type, "photo")
        self.assertLess(result.metrics.get("fft_moire_score", 0.0), 0.20)

    def test_classify_photo_of_screen(self):
        # Generate a camera photo of screen with high-frequency grid lines (Moiré)
        base_img = self.generate_synthetic_screenshot()
        h, w, c = base_img.shape
        y, x = np.mgrid[0:h, 0:w]
        
        # Simulate grid lines with 8 pixel period
        grid_y = np.sin(y * (2 * np.pi / 8.0))
        grid_x = np.sin(x * (2 * np.pi / 8.0))
        grid = ((grid_y + 1.0) * (grid_x + 1.0) / 4.0) * 0.20 + 0.80
        grid = np.repeat(grid[:, :, np.newaxis], 3, axis=2)
        
        screen_img = np.clip(base_img.astype(np.float32) * grid, 0, 255).astype(np.uint8)
        img = self.add_camera_noise_and_blur(screen_img)
        
        # Add a bright glare reflection spot
        glare_center = (w // 2, h // 3)
        dist_sq = (x - glare_center[0])**2 + (y - glare_center[1])**2
        glare = np.exp(-dist_sq / (2 * (150**2))) * 60
        glare = np.repeat(glare[:, :, np.newaxis], 3, axis=2)
        img = np.clip(img.astype(np.float32) + glare, 0, 255).astype(np.uint8)
        
        _, img_bytes = cv2.imencode(".png", img)
        result = self.validator.validate(img_bytes.tobytes(), "image", capture_mode="auto")
        
        # Verify classification
        self.assertEqual(result.capture_type, "photo_of_screen")
        self.assertGreaterEqual(result.metrics.get("fft_moire_score", 0.0), 0.50)
