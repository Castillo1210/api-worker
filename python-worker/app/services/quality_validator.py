import cv2
import numpy as np
import structlog
from pdf2image import convert_from_bytes

from app.config import get_settings
from app.models.deposit import QualityResult

logger = structlog.get_logger()


class QualityValidator:
    def __init__(self):
        self.settings = get_settings()

    def validate(self, file_bytes: bytes, file_type: str, capture_mode: str = "auto") -> QualityResult:
        """
        Validate image or PDF quality.
        Capture types:
        - photo: photo of paper/document
        - photo_of_screen: phone/monitor photographed by camera
        - screenshot: digital screenshot
        """
        try:
            if file_type == "pdf":
                return self._validate_pdf(file_bytes)
            return self._validate_image(file_bytes, capture_mode=capture_mode)
        except Exception as e:
            logger.error("Error en validacion calidad", error=str(e), file_type=file_type)
            return QualityResult(
                is_valid=False,
                issues=["VALIDATION_ERROR"],
                metrics={"error": str(e)},
                file_type=file_type,
                capture_type=None,
                capture_scores={},
            )

    def _validate_pdf(self, pdf_bytes: bytes) -> QualityResult:
        try:
            images = convert_from_bytes(
                pdf_bytes,
                dpi=self.settings.QUALITY_PDF_DPI,
                first_page=1,
                last_page=1,
                fmt="RGB",
            )

            if not images:
                return QualityResult(
                    is_valid=False,
                    issues=["PDF_EMPTY"],
                    metrics={},
                    file_type="pdf",
                    capture_type="photo",
                    capture_scores={},
                )

            cv_image = cv2.cvtColor(np.array(images[0]), cv2.COLOR_RGB2BGR)
            result = self._validate_image_cv(cv_image, capture_mode="photo")
            result.file_type = "pdf"
            return result
        except Exception as e:
            logger.error("Error procesando PDF", error=str(e))
            return QualityResult(
                is_valid=False,
                issues=["PDF_PROCESSING_ERROR"],
                metrics={"error": str(e)},
                file_type="pdf",
                capture_type="photo",
                capture_scores={},
            )

    def _validate_image(self, image_bytes: bytes, capture_mode: str = "auto") -> QualityResult:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return QualityResult(
                is_valid=False,
                issues=["INVALID_IMAGE_FORMAT"],
                metrics={},
                file_type="image",
                capture_type=None,
                capture_scores={},
            )

        return self._validate_image_cv(img, capture_mode=capture_mode)

    def _validate_image_cv(self, img: np.ndarray, capture_mode: str = "auto") -> QualityResult:
        issues = []
        metrics = {}
        h, w = img.shape[:2]
        short_edge = min(w, h)
        long_edge = max(w, h)
        aspect_ratio = long_edge / short_edge if short_edge else 0.0

        capture_type, scores = self._classify_capture_type(img, capture_mode=capture_mode)
        likely_screenshot = capture_type == "screenshot"
        is_photo_of_screen = capture_type == "photo_of_screen"

        metrics["resolution"] = f"{w}x{h}"
        metrics["width"] = w
        metrics["height"] = h
        metrics["short_edge"] = short_edge
        metrics["long_edge"] = long_edge
        metrics["aspect_ratio"] = float(aspect_ratio)
        metrics["capture_type"] = capture_type
        metrics.update(scores)

        if likely_screenshot:
            min_short_edge = self.settings.QUALITY_MIN_SCREENSHOT_WIDTH
            min_long_edge = self.settings.QUALITY_MIN_SCREENSHOT_HEIGHT
        elif is_photo_of_screen:
            min_short_edge = 320
            min_long_edge = 500
        else:
            min_short_edge = 320
            min_long_edge = 500

        if short_edge < min_short_edge or long_edge < min_long_edge:
            issues.append("RESOLUTION_TOO_LOW")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur_score, blur_details = self._compute_blur_score(gray, is_photo_of_screen=is_photo_of_screen)
        contrast = gray.std()
        glare_ratio = float(np.sum(gray > 240) / gray.size)

        metrics["blur_score"] = float(blur_score)
        metrics["blur_score_raw"] = float(blur_details["raw"])
        metrics["blur_score_normalized"] = float(blur_details["normalized"])
        metrics["blur_score_local"] = float(blur_details["local"])
        metrics["blur_scale"] = float(blur_details["scale"])
        metrics["contrast"] = float(contrast)
        metrics["glare_ratio"] = glare_ratio

        validation_trace = {
            "input": {
                "capture_mode": capture_mode,
                "width": w,
                "height": h,
                "short_edge": short_edge,
                "long_edge": long_edge,
                "aspect_ratio": float(aspect_ratio),
            },
            "classification": {
                "capture_type": capture_type,
                "capture_scores": scores,
            },
            "metrics": {
                "blur_score": float(blur_score),
                "blur_score_raw": float(blur_details["raw"]),
                "blur_score_normalized": float(blur_details["normalized"]),
                "blur_score_local": float(blur_details["local"]),
                "blur_scale": float(blur_details["scale"]),
                "contrast": float(contrast),
                "glare_ratio": glare_ratio,
            },
            "thresholds": {},
            "issues": [],
        }

        if likely_screenshot:
            # Digital screenshots should be sharp and consistent, but we keep the checks light.
            validation_trace["thresholds"] = {
                "min_short_edge": self.settings.QUALITY_MIN_SCREENSHOT_WIDTH,
                "min_long_edge": self.settings.QUALITY_MIN_SCREENSHOT_HEIGHT,
                "min_blur_score": self.settings.QUALITY_MIN_SCREENSHOT_BLUR_SCORE,
            }
            if blur_score < self.settings.QUALITY_MIN_SCREENSHOT_BLUR_SCORE:
                issues.append("BLURRY")
        elif is_photo_of_screen:
            # Photo of a screen: camera artifacts are expected, but glare/blur still matter.
            validation_trace["thresholds"] = {
                "min_short_edge": 320,
                "min_long_edge": 500,
                "min_blur_score": self.settings.QUALITY_MIN_SCREEN_PHOTO_BLUR_SCORE,
                "max_glare_ratio": self.settings.QUALITY_MAX_GLARE_RATIO * 1.5,
                "min_contrast": self.settings.QUALITY_MIN_CONTRAST * 0.8,
            }
            if blur_score < self.settings.QUALITY_MIN_SCREEN_PHOTO_BLUR_SCORE:
                issues.append("BLURRY")
            if glare_ratio > self.settings.QUALITY_MAX_GLARE_RATIO * 1.5:
                issues.append("GLARE_DETECTED")
            if contrast < self.settings.QUALITY_MIN_CONTRAST * 0.8:
                issues.append("LOW_CONTRAST")
        else:
            validation_trace["thresholds"] = {
                "min_short_edge": 320,
                "min_long_edge": 500,
                "min_blur_score": self.settings.QUALITY_MIN_BLUR_SCORE,
                "max_glare_ratio": self.settings.QUALITY_MAX_GLARE_RATIO,
                "min_contrast": self.settings.QUALITY_MIN_CONTRAST,
                "min_document_confidence": self.settings.QUALITY_MIN_DOC_CONFIDENCE,
            }
            if blur_score < self.settings.QUALITY_MIN_BLUR_SCORE:
                issues.append("BLURRY")
            if glare_ratio > self.settings.QUALITY_MAX_GLARE_RATIO:
                issues.append("GLARE_DETECTED")
            if contrast < self.settings.QUALITY_MIN_CONTRAST:
                issues.append("LOW_CONTRAST")

            doc_confidence = self._detect_document(gray, w, h)
            metrics["document_confidence"] = doc_confidence
            if doc_confidence < self.settings.QUALITY_MIN_DOC_CONFIDENCE:
                issues.append("NO_DOCUMENT_DETECTED")

        # Compute validation outcome.
        # We make NO_DOCUMENT_DETECTED non-blocking so that close-ups and light background photos
        # are not rejected if the text is sharp and has contrast.
        blocking_issues = [issue for issue in issues if issue != "NO_DOCUMENT_DETECTED"]
        is_valid = len(blocking_issues) == 0

        processed_bytes = None
        if is_valid:
            processed_img = self._enhance_image(img, gray)
            _, buffer = cv2.imencode(".png", processed_img)
            processed_bytes = buffer.tobytes()
            metrics["processed_size"] = len(processed_bytes)

        logger.info(
            "Validacion calidad completada",
            is_valid=is_valid,
            issues=issues,
            capture_type=capture_type,
            blur_score=float(blur_score),
            blur_score_raw=float(blur_details["raw"]),
            blur_score_normalized=float(blur_details["normalized"]),
            blur_score_local=float(blur_details["local"]),
            blur_scale=float(blur_details["scale"]),
            contrast=float(contrast),
            glare_ratio=float(glare_ratio),
        )
        logger.info(
            "Validacion calidad detalle",
            trace=validation_trace,
        )

        return QualityResult(
            is_valid=is_valid,
            issues=issues,
            metrics=metrics,
            processed_bytes=processed_bytes,
            file_type="image",
            capture_type=capture_type,
            capture_scores=scores,
        )

    def _classify_capture_type(self, img: np.ndarray, capture_mode: str = "auto") -> tuple[str, dict]:
        if capture_mode in ("photo", "photo_of_screen", "screenshot"):
            scores = {
                "capture_mode_forced": 1.0,
                "photo_score": 1.0 if capture_mode == "photo" else 0.0,
                "photo_of_screen_score": 1.0 if capture_mode == "photo_of_screen" else 0.0,
                "screenshot_score": 1.0 if capture_mode == "screenshot" else 0.0,
            }
            logger.info(
                "Clasificacion forzada",
                capture_type=capture_mode,
                capture_mode=capture_mode,
                scores=scores,
            )
            return capture_mode, scores

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        short_edge = min(w, h)
        long_edge = max(w, h)
        border = max(8, min(h, w) // 25)

        border_pixels = np.concatenate(
            [
                gray[:border, :].ravel(),
                gray[-border:, :].ravel(),
                gray[:, :border].ravel(),
                gray[:, -border:].ravel(),
            ]
        )
        corner_pixels = np.concatenate(
            [
                gray[:border, :border].ravel(),
                gray[:border, -border:].ravel(),
                gray[-border:, :border].ravel(),
                gray[-border:, -border:].ravel(),
            ]
        )

        border_dark_ratio = float(np.mean(border_pixels < 40))
        corner_dark_ratio = float(np.mean(corner_pixels < 50))
        edge_density = float(np.mean(cv2.Canny(gray, 50, 150) > 0))
        contrast = float(gray.std())
        blur_score, blur_details = self._compute_blur_score(gray)
        aspect_ratio = long_edge / short_edge if short_edge else 0.0
        resolution_signature_score = self._score_resolution_signature(short_edge, long_edge, aspect_ratio)

        # Compute FFT-based Moiré metric
        fft_moire_score, fft_peak_count, fft_max_peak_val = self._compute_fft_moire_score(gray)

        photo_of_screen_score = self._score_photo_of_screen(
            border_dark_ratio,
            corner_dark_ratio,
            edge_density,
            blur_score,
            aspect_ratio,
            resolution_signature_score,
            fft_moire_score,
        )
        screenshot_score = self._score_screenshot(
            border_dark_ratio,
            corner_dark_ratio,
            edge_density,
            blur_score,
            contrast,
            aspect_ratio,
            resolution_signature_score,
        )
        photo_score = self._score_photo(
            border_dark_ratio,
            corner_dark_ratio,
            edge_density,
            blur_score,
            contrast,
            resolution_signature_score,
        )

        scores = {
            "photo_score": float(photo_score),
            "photo_of_screen_score": float(photo_of_screen_score),
            "screenshot_score": float(screenshot_score),
            "resolution_signature_score": float(resolution_signature_score),
            "border_dark_ratio": float(border_dark_ratio),
            "corner_dark_ratio": float(corner_dark_ratio),
            "edge_density": float(edge_density),
            "blur_score_raw": float(blur_details["raw"]),
            "blur_score_normalized": float(blur_details["normalized"]),
            "short_edge": float(short_edge),
            "long_edge": float(long_edge),
            "aspect_ratio": float(aspect_ratio),
            "fft_moire_score": float(fft_moire_score),
            "fft_peak_count": int(fft_peak_count),
            "fft_max_peak_val": float(fft_max_peak_val),
        }

        best = max(
            [("photo", photo_score), ("photo_of_screen", photo_of_screen_score), ("screenshot", screenshot_score)],
            key=lambda item: item[1],
        )

        screenshot_evidence = (
            (resolution_signature_score >= 0.70 or blur_score >= 150.0)
            and edge_density >= 0.005
            and contrast >= 20.0
            and blur_score >= self.settings.QUALITY_MIN_SCREENSHOT_BLUR_SCORE
            and fft_moire_score < 0.20
        )
        photo_of_screen_evidence = (
            fft_moire_score >= 0.50
            or (border_dark_ratio >= 0.12 and corner_dark_ratio >= 0.08 and aspect_ratio >= 1.25)
        )
        if photo_of_screen_evidence and (fft_moire_score >= 0.50 or photo_of_screen_score >= (photo_score * 0.80)):
            best = ("photo_of_screen", photo_of_screen_score)
        if screenshot_evidence and screenshot_score >= (photo_score * 0.70):
            best = ("screenshot", screenshot_score)

        if best[0] == "screenshot" and screenshot_score < 0.55:
            best = ("photo_of_screen" if photo_of_screen_score >= photo_score else "photo", max(photo_of_screen_score, photo_score))

        if best[1] < 0.45:
            capture_type = "photo"
        else:
            capture_type = best[0]

        logger.info(
            "Clasificacion imagen",
            capture_type=capture_type,
            scores=scores,
            resolution_signature_score=float(resolution_signature_score),
            photo_score=float(photo_score),
            photo_of_screen_score=float(photo_of_screen_score),
            screenshot_score=float(screenshot_score),
            fft_moire_score=float(fft_moire_score),
        )

        return capture_type, scores

    def _compute_fft_moire_score(self, gray: np.ndarray) -> tuple[float, int, float]:
        """
        Estimate Moiré pattern presence using 2D FFT magnitude spectrum analysis on a native center crop.
        Screens emit light through a physical pixel grid which shows up as symmetric
        high-intensity spikes in the frequency domain.
        """
        h, w = gray.shape[:2]
        size = 512

        # Take a native crop from the center
        cy, cx = h // 2, w // 2
        y1 = max(0, cy - size // 2)
        y2 = min(h, cy + size // 2)
        x1 = max(0, cx - size // 2)
        x2 = min(w, cx + size // 2)

        crop = gray[y1:y2, x1:x2]
        if crop.shape[0] < size or crop.shape[1] < size:
            crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR)
        else:
            ch, cw = crop.shape[:2]
            if ch != size or cw != size:
                crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR)

        # Compute 2D Fast Fourier Transform
        f = np.fft.fft2(crop)
        fshift = np.fft.fftshift(f)
        magnitude = np.abs(fshift)
        log_mag = np.log(magnitude + 1.0)

        # Apply high-pass filter by subtracting a heavily blurred version
        log_mag_blurred = cv2.GaussianBlur(log_mag, (31, 31), 0)
        log_mag_highpass = log_mag - log_mag_blurred

        # Find local peaks on the highpass spectrum
        kernel = np.ones((5, 5), dtype=np.uint8)
        local_max = cv2.dilate(log_mag_highpass, kernel)

        # Peaks are local maxima that are significantly above the background
        # Exclude central low-frequency region (radius of 40) and outer high-frequency region (radius of 240)
        Y, X = np.mgrid[0:size, 0:size]
        center = size // 2
        dist_from_center = np.sqrt((X - center) ** 2 + (Y - center) ** 2)
        valid_freq_mask = (dist_from_center >= 40) & (dist_from_center <= 240)

        # Exclude exact axis lines where text rows and borders manifest
        off_axis_mask = (np.abs(X - center) > 0) & (np.abs(Y - center) > 0)

        peaks = (log_mag_highpass == local_max) & (log_mag_highpass > 2.8) & (log_mag > 5.0) & valid_freq_mask & off_axis_mask

        peak_count = np.sum(peaks)
        max_peak_val = np.max(log_mag_highpass[valid_freq_mask & off_axis_mask]) if np.any(valid_freq_mask & off_axis_mask) else 0.0

        # Normalize score between 0.0 and 1.0
        score = 0.0
        if peak_count >= 2:
            score += 0.5
        if peak_count >= 4:
            score += 0.3
        score += min(0.2, max(0.0, (max_peak_val - 2.8) / 4.0))

        return float(min(1.0, score)), int(peak_count), float(max_peak_val)

    def _score_photo_of_screen(
        self,
        border_dark_ratio: float,
        corner_dark_ratio: float,
        edge_density: float,
        blur_score: float,
        aspect_ratio: float,
        resolution_signature_score: float,
        fft_moire_score: float,
    ) -> float:
        return float(
            max(
                0.0,
                min(
                    1.0,
                    (fft_moire_score * 0.60)
                    + (border_dark_ratio * 0.30)
                    + (corner_dark_ratio * 0.15)
                    + (edge_density * 0.50)
                    + min(1.0, blur_score / 150.0) * 0.10
                    + (0.10 if 1.2 <= aspect_ratio <= 4.5 else 0.0)
                    + (resolution_signature_score * 0.10),
                ),
            )
        )

    def _score_screenshot(
        self,
        border_dark_ratio: float,
        corner_dark_ratio: float,
        edge_density: float,
        blur_score: float,
        contrast: float,
        aspect_ratio: float,
        resolution_signature_score: float,
    ) -> float:
        # Boost signature score for high-res digital images that are extremely sharp
        res_sig_weight = resolution_signature_score
        if resolution_signature_score < 0.40 and blur_score >= 150.0:
            res_sig_weight = 0.80

        return float(
            max(
                0.0,
                min(
                    1.0,
                    (edge_density * 2.8)
                    + (min(1.0, blur_score / 120.0) * 0.2)
                    + (min(1.0, contrast / 80.0) * 0.15)
                    + (0.10 if 1.2 <= aspect_ratio <= 4.0 else 0.0)
                    + (res_sig_weight * 0.40)
                    - (border_dark_ratio * 0.4)
                    - (corner_dark_ratio * 0.3)
                    - (0.15 if border_dark_ratio >= 0.12 and corner_dark_ratio >= 0.08 else 0.0),
                ),
            )
        )

    def _score_photo(
        self,
        border_dark_ratio: float,
        corner_dark_ratio: float,
        edge_density: float,
        blur_score: float,
        contrast: float,
        resolution_signature_score: float,
    ) -> float:
        documentish = self._doc_like_score(border_dark_ratio, corner_dark_ratio, edge_density)
        return float(
            max(
                0.0,
                min(
                    1.0,
                    (documentish * 0.55)
                    + (min(1.0, blur_score / 120.0) * 0.15)
                    + (min(1.0, contrast / 80.0) * 0.10)
                    + (0.20 if edge_density < 0.18 else 0.0)
                    - (resolution_signature_score * 0.25)
                    - (0.10 if border_dark_ratio >= 0.12 and corner_dark_ratio >= 0.08 else 0.0),
                ),
            )
        )

    def _doc_like_score(self, border_dark_ratio: float, corner_dark_ratio: float, edge_density: float) -> float:
        return max(0.0, min(1.0, 1.2 * edge_density + 0.5 * (1.0 - border_dark_ratio) + 0.3 * (1.0 - corner_dark_ratio)))

    def _score_resolution_signature(self, short_edge: int, long_edge: int, aspect_ratio: float) -> float:
        """
        Screenshots on mobile usually have a portrait aspect ratio and live in a
        relatively tight resolution band. This score boosts that pattern.
        """
        if short_edge <= 0 or long_edge <= 0:
            return 0.0

        if not (1.2 <= aspect_ratio <= 5.0):
            return 0.0

        short_band = 1.0 - min(abs(short_edge - 480) / 420.0, 1.0)
        long_band = 1.0 - min(abs(long_edge - 960) / 900.0, 1.0)
        screenshot_window = 1.0 if (360 <= short_edge <= 720 and 600 <= long_edge <= 1800) else 0.0

        score = (short_band * 0.35) + (long_band * 0.25) + (screenshot_window * 0.40)
        return float(max(0.0, min(1.0, score)))

    def _compute_blur_score(self, gray: np.ndarray, is_photo_of_screen: bool = False) -> tuple[float, dict]:
        """
        Combine the raw Laplacian variance with normalized and local scores so
        that very large images with soft content do not falsely look sharp.
        """
        if is_photo_of_screen:
            gray = cv2.GaussianBlur(gray, (3, 3), 0)

        raw_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        h, w = gray.shape[:2]
        long_edge = max(h, w)

        target_long_edge = 1024.0
        scale = 1.0 if long_edge <= target_long_edge else target_long_edge / long_edge
        resized = cv2.resize(
            gray,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        ) if scale != 1.0 else gray

        normalized_score = float(cv2.Laplacian(resized, cv2.CV_64F).var())
        local_score = self._local_blur_score(resized)

        combined = min(raw_score, normalized_score * 1.10, local_score * 1.15)
        return float(combined), {
            "raw": raw_score,
            "normalized": normalized_score,
            "local": float(local_score),
            "scale": float(scale),
        }

    def _local_blur_score(self, gray: np.ndarray) -> float:
        """
        Estimate sharpness using local patches. Blurry images often keep a few
        strong edges but lose detail across most blocks.
        """
        if gray.size == 0:
            return 0.0

        h, w = gray.shape[:2]
        rows = 4
        cols = 4
        patch_scores = []

        for row in range(rows):
            y1 = int(row * h / rows)
            y2 = int((row + 1) * h / rows)
            for col in range(cols):
                x1 = int(col * w / cols)
                x2 = int((col + 1) * w / cols)
                patch = gray[y1:y2, x1:x2]
                if patch.size == 0 or patch.shape[0] < 8 or patch.shape[1] < 8:
                    continue
                patch_scores.append(float(cv2.Laplacian(patch, cv2.CV_64F).var()))

        if not patch_scores:
            return float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # Sort patch scores descending and take the top 50% (most textured/sharp patches).
        # This prevents clean, textureless backgrounds from dragging down the sharpness score of the text area.
        patch_scores.sort(reverse=True)
        top_half = patch_scores[:max(1, len(patch_scores) // 2)]
        return float(np.median(top_half))

    def _detect_document(self, gray: np.ndarray, w: int, h: int) -> float:
        try:
            # Pad the image by 10 pixels to close contours touching the border
            padded = cv2.copyMakeBorder(gray, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)

            edges = cv2.Canny(padded, 40, 120)
            kernel = np.ones((3, 3), np.uint8)
            edges = cv2.dilate(edges, kernel, iterations=1)

            # Use RETR_LIST to catch nested contours and receipts connected to borders
            contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

            if not contours:
                return 0.0

            image_area = float(w * h)
            best_score = 0.0

            for cnt in contours:
                area = cv2.contourArea(cnt)
                # Ignore contours representing the full padded frame or background box
                if area < (image_area * 0.04) or area > (image_area * 0.95):
                    continue

                hull = cv2.convexHull(cnt)
                hull_area = cv2.contourArea(hull)
                hull_area = max(hull_area, 1.0)

                rect = cv2.minAreaRect(cnt)
                box = cv2.boxPoints(rect)
                box_area = cv2.contourArea(box)
                box_area = max(box_area, 1.0)

                perimeter = cv2.arcLength(cnt, True)
                approx = cv2.approxPolyDP(cnt, 0.03 * perimeter, True)
                approx_vertices = len(approx)

                extent = float(area / box_area)
                rectangularity = float(area / hull_area)
                fill_ratio = float(area / image_area)
                aspect_ratio = rect[1][0] / rect[1][1] if rect[1][1] else 0.0
                aspect_ratio = aspect_ratio if aspect_ratio >= 1 else (1 / aspect_ratio if aspect_ratio else 0.0)

                # Edge evidence around the candidate region
                x, y, bw, bh = cv2.boundingRect(cnt)
                roi = edges[max(0, y):min(edges.shape[0], y + bh), max(0, x):min(edges.shape[1], x + bw)]
                roi_edge_density = float(np.mean(roi > 0)) if roi.size else 0.0
                roi_line_score = self._line_presence_score(roi)
                solidity = float(area / hull_area)
                perimeter_fill = float(area / max(1.0, perimeter * perimeter))

                # Score tolerates torn / rounded / wrinkled vouchers:
                # it prefers a strong contour with a large edge-supported region,
                # but does not require perfect corners.
                score = (
                    min(1.0, fill_ratio * 3.0)
                    + min(1.0, extent)
                    + min(1.0, rectangularity)
                    + min(1.0, solidity)
                    + min(1.0, roi_edge_density * 4.0)
                    + min(1.0, roi_line_score)
                    + min(1.0, perimeter_fill * 800.0)
                ) / 6.0

                if 1.1 <= aspect_ratio <= 6.0:
                    score += 0.08

                if approx_vertices >= 4:
                    score += 0.05
                elif approx_vertices == 3:
                    score += 0.02

                best_score = max(best_score, min(1.0, score))

            return best_score
        except Exception:
            return 0.0

    def _line_presence_score(self, roi: np.ndarray) -> float:
        """Score based on long line segments inside a candidate region."""
        if roi.size == 0:
            return 0.0

        lines = cv2.HoughLinesP(
            roi,
            1,
            np.pi / 180,
            threshold=18,
            minLineLength=max(20, min(roi.shape[:2]) // 4),
            maxLineGap=12,
        )

        if lines is None:
            return 0.0

        total_length = 0.0
        for line in lines[:12]:
            x1, y1, x2, y2 = line[0]
            total_length += float(np.hypot(x2 - x1, y2 - y1))

        roi_area = float(roi.shape[0] * roi.shape[1]) if roi.shape[0] and roi.shape[1] else 1.0
        return max(0.0, min(1.0, (total_length / roi_area) * 8.0))

    def _enhance_image(self, img: np.ndarray, gray: np.ndarray) -> np.ndarray:
        try:
            edges = cv2.Canny(gray, 40, 120)
            edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            best_cnt = None
            max_area = 0
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area > max_area and area > (img.shape[0] * img.shape[1] * 0.08):
                    max_area = area
                    best_cnt = cnt

            if best_cnt is not None:
                rect = cv2.minAreaRect(best_cnt)
                box = cv2.boxPoints(rect)
                img = self._four_point_transform(img, box.reshape(4, 2))
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced_gray = clahe.apply(gray)
            return cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)
        except Exception:
            return img

    def _four_point_transform(self, image: np.ndarray, pts: np.ndarray) -> np.ndarray:
        rect = self._order_points(pts)
        (tl, tr, br, bl) = rect

        width_a = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        width_b = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        max_width = max(int(width_a), int(width_b))

        height_a = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        height_b = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        max_height = max(int(height_a), int(height_b))

        dst = np.array(
            [
                [0, 0],
                [max_width - 1, 0],
                [max_width - 1, max_height - 1],
                [0, max_height - 1],
            ],
            dtype="float32",
        )

        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, M, (max_width, max_height))

    def _order_points(self, pts: np.ndarray) -> np.ndarray:
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect
