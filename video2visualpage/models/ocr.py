from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import types
from pathlib import Path
from typing import Any, Callable

from ..paths import repo_root, resolve_artifact_path

CropBoxProvider = Callable[[Path], list[tuple[int, int, int, int]]]


def _dedupe_texts(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item)).strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _parse_json_value(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            return _parse_json_value(fence.group(1))
        for start_char, end_char in (("[", "]"), ("{", "}")):
            start = stripped.find(start_char)
            end = stripped.rfind(end_char)
            if start >= 0 and end > start:
                return json.loads(stripped[start : end + 1])
    raise ValueError("Model response must be valid JSON")


def extract_ocr_texts(raw_text: str) -> list[str]:
    try:
        value = _parse_json_value(raw_text)
    except Exception:
        lines = [line.strip("- \t\r") for line in raw_text.splitlines()]
        return _dedupe_texts([line for line in lines if line and not line.startswith("```")])

    texts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
            for key in ("words_info", "ocr_result", "kv_result", "data", "items", "result"):
                if key in node:
                    visit(node[key])
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    if not texts and isinstance(value, str):
        texts.append(value)
    return _dedupe_texts(texts)


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_repo_path(value: Any, default: Path) -> Path:
    if value in (None, ""):
        path = default
    else:
        path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (repo_root() / path).resolve()


def paddleocr_model_name(config: dict[str, Any], model: str | None = None) -> str:
    return str(model or config.get("ocr_model") or "PP-OCRv6_medium").strip()


def _paddleocr_result_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result.get("res", result)
    if hasattr(result, "json"):
        raw_json = result.json
        try:
            value = raw_json() if callable(raw_json) else raw_json
        except TypeError:
            value = raw_json
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value
    if hasattr(result, "to_dict"):
        to_dict = result.to_dict
        return to_dict() if callable(to_dict) else to_dict
    return result


def _extract_paddleocr_texts(result: Any, *, min_score: float = 0.0) -> list[str]:
    texts: list[str] = []

    def add_text(value: Any) -> None:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if text:
            texts.append(text)

    def visit(node: Any) -> None:
        node = _paddleocr_result_payload(node)
        if isinstance(node, dict):
            rec_texts = node.get("rec_texts")
            rec_scores = node.get("rec_scores")
            if isinstance(rec_texts, list):
                for index, text in enumerate(rec_texts):
                    if min_score > 0 and isinstance(rec_scores, list) and index < len(rec_scores):
                        try:
                            if float(rec_scores[index]) < min_score:
                                continue
                        except (TypeError, ValueError):
                            pass
                    add_text(text)
            rec_text = node.get("rec_text")
            if isinstance(rec_text, str):
                if min_score > 0 and "rec_score" in node:
                    try:
                        if float(node.get("rec_score")) < min_score:
                            rec_text = ""
                    except (TypeError, ValueError):
                        pass
                add_text(rec_text)
            for key in ("text", "transcription", "label"):
                if key in node:
                    add_text(node[key])
            for key in ("res", "ocr_result", "overall_ocr_res", "result", "data", "items"):
                if key in node:
                    visit(node[key])
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, str):
            add_text(node)

    visit(result)
    return _dedupe_texts(texts)


def _iter_paddleocr_payloads(result: Any) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        node = _paddleocr_result_payload(node)
        if isinstance(node, dict):
            payloads.append(node)
            for key in ("res", "ocr_result", "overall_ocr_res", "result", "data", "items"):
                if key in node:
                    visit(node[key])
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(result)
    return payloads


def _bounds_from_ocr_box(box: Any) -> tuple[float, float, float, float] | None:
    if hasattr(box, "tolist"):
        box = box.tolist()
    if not isinstance(box, (list, tuple)):
        return None
    if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
        x0, y0, x1, y1 = [float(value) for value in box]
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    points: list[tuple[float, float]] = []
    for point in box:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _paddleocr_result_needs_crop_fallback(result: Any, image_size: tuple[int, int]) -> bool:
    width, height = image_size
    if width <= 0 or height <= 0:
        return False
    image_area = float(width * height)
    for payload in _iter_paddleocr_payloads(result):
        for key in ("rec_boxes", "rec_polys", "dt_polys"):
            boxes = payload.get(key)
            if hasattr(boxes, "tolist"):
                boxes = boxes.tolist()
            if not isinstance(boxes, list) or len(boxes) != 1:
                continue
            bounds = _bounds_from_ocr_box(boxes[0])
            if bounds is None:
                continue
            x0, y0, x1, y1 = bounds
            box_width = max(0.0, x1 - x0)
            box_height = max(0.0, y1 - y0)
            if box_width * box_height >= image_area * 0.75 and box_width >= width * 0.85 and box_height >= height * 0.55:
                return True
    return False


def _clamp_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = box
    x0 = max(0, min(width, x0))
    x1 = max(0, min(width, x1))
    y0 = max(0, min(height, y0))
    y1 = max(0, min(height, y1))
    if x1 - x0 < 24 or y1 - y0 < 12:
        return None
    return x0, y0, x1, y1


def _dedupe_boxes(boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    unique: list[tuple[int, int, int, int]] = []
    for box in boxes:
        x0, y0, x1, y1 = box
        area = max(1, (x1 - x0) * (y1 - y0))
        duplicate = False
        for existing in unique:
            ex0, ey0, ex1, ey1 = existing
            ix0, iy0 = max(x0, ex0), max(y0, ey0)
            ix1, iy1 = min(x1, ex1), min(y1, ey1)
            intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
            if intersection / float(area) > 0.9:
                duplicate = True
                break
        if not duplicate:
            unique.append(box)
    return unique


def _candidate_text_crop_boxes(image_path: Path) -> list[tuple[int, int, int, int]]:
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size

    boxes: list[tuple[int, int, int, int]] = []
    try:
        import cv2

        image = cv2.imread(str(image_path))
        if image is not None:
            image_height, image_width = image.shape[:2]
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            _, dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            mask = cv2.bitwise_or(edges, dark)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 7))
            closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            closed = cv2.dilate(closed, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)), iterations=1)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                area = box_width * box_height
                if box_width < 40 or box_height < 18 or area < 1500:
                    continue
                if box_width > image_width * 0.95 and box_height > image_height * 0.7:
                    continue
                if box_height > image_height * 0.45:
                    continue
                pad_x = max(8, int(box_width * 0.08))
                pad_y = max(6, int(box_height * 0.2))
                clamped = _clamp_box(
                    (x - pad_x, y - pad_y, x + box_width + pad_x, y + box_height + pad_y),
                    image_width,
                    image_height,
                )
                if clamped:
                    boxes.append(clamped)
    except Exception:
        boxes = []

    fixed_boxes = [
        (0, 0, width, int(height * 0.35)),
        (0, int(height * 0.25), width, int(height * 0.65)),
        (0, int(height * 0.55), width, height),
        (int(width * 0.15), int(height * 0.20), int(width * 0.60), int(height * 0.65)),
        (int(width * 0.28), int(height * 0.45), int(width * 0.72), int(height * 0.78)),
        (int(width * 0.15), int(height * 0.78), int(width * 0.85), height),
        (int(width * 0.05), int(height * 0.10), int(width * 0.75), int(height * 0.82)),
    ]
    for box in fixed_boxes:
        clamped = _clamp_box(box, width, height)
        if clamped:
            boxes.append(clamped)

    boxes.sort(key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
    boxes = _dedupe_boxes(boxes)
    boxes.sort(key=lambda item: (item[1] // 40, item[0], -((item[2] - item[0]) * (item[3] - item[1]))))
    return boxes[:24]


def _install_modelscope_stub() -> None:
    if "modelscope" in sys.modules:
        return

    modelscope = types.ModuleType("modelscope")
    hub = types.ModuleType("modelscope.hub")
    errors = types.ModuleType("modelscope.hub.errors")

    class NotExistError(Exception):
        pass

    class HTTPError(Exception):
        pass

    def snapshot_download(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("ModelScope download is disabled; configure local PaddleOCR model directories.")

    errors.NotExistError = NotExistError
    errors.HTTPError = HTTPError
    hub.errors = errors
    modelscope.hub = hub
    modelscope.snapshot_download = snapshot_download
    modelscope.__path__ = []

    sys.modules["modelscope"] = modelscope
    sys.modules["modelscope.hub"] = hub
    sys.modules["modelscope.hub.errors"] = errors


class PaddleOCRFrameRecognizer:
    def __init__(
        self,
        project_dir: str | Path,
        config: dict[str, Any],
        *,
        model: str | None = None,
        crop_box_provider: CropBoxProvider | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.ocr_model = paddleocr_model_name(config, model)
        self.ocr_root = _resolve_repo_path(
            config.get("ocr_root"),
            repo_root() / "utils" / "PaddleOCR-main",
        )
        self.ocr_det_model_dir = _resolve_repo_path(
            config.get("ocr_det_model_dir"),
            self.ocr_root / "models" / f"{self.ocr_model}_det",
        )
        self.ocr_rec_model_dir = _resolve_repo_path(
            config.get("ocr_rec_model_dir"),
            self.ocr_root / "models" / f"{self.ocr_model}_rec",
        )
        self.ocr_device = str(config.get("ocr_device") or config.get("device") or "gpu").strip()
        self.ocr_engine = str(config.get("ocr_engine") or "paddle").strip()
        self.ocr_min_score = _float_value(config.get("ocr_min_score"), 0.0)
        self.ocr_crop_min_score = max(self.ocr_min_score, _float_value(config.get("ocr_crop_min_score"), 0.6))
        self.ocr_use_tensorrt = _bool_value(config.get("ocr_use_tensorrt"), False)
        self.ocr_precision = str(config.get("ocr_precision") or "fp32").strip()
        self.ocr_allow_cpu_fallback = _bool_value(config.get("ocr_allow_cpu_fallback"), True)
        self.ocr_crop_fallback = _bool_value(config.get("ocr_crop_fallback"), True)
        self.runtime_warnings: list[str] = []
        self._engine: Any | None = None
        self._recognizer: Any | None = None
        self._recognition_device: str | None = None
        self._crop_box_provider = crop_box_provider or _candidate_text_crop_boxes
        self._engine_lock = threading.Lock()
        self._recognizer_lock = threading.Lock()
        self._pipeline_predict_lock = threading.Lock()
        self._recognizer_predict_lock = threading.Lock()
        self._warnings_lock = threading.Lock()

    def warnings_since(self, start: int) -> list[str]:
        with self._warnings_lock:
            return self.runtime_warnings[start:]

    def _append_warning(self, warning: str) -> None:
        with self._warnings_lock:
            self.runtime_warnings.append(warning)

    def _ensure_import_path(self) -> None:
        root_text = str(self.ocr_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        _install_modelscope_stub()

    def _load_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            missing_paths = [
                str(path)
                for path in (self.ocr_root, self.ocr_det_model_dir, self.ocr_rec_model_dir)
                if not path.exists()
            ]
            if missing_paths:
                raise RuntimeError("PaddleOCR 本地目录或模型目录不存在: " + ", ".join(missing_paths))

            self._ensure_import_path()
            try:
                from paddleocr import PaddleOCR
            except ModuleNotFoundError as exc:
                missing = exc.name or "paddleocr dependency"
                raise RuntimeError(
                    "PaddleOCR 运行依赖未安装。请安装 PaddlePaddle GPU 运行时，并执行 "
                    "`python -m pip install -e utils/PaddleOCR-main`；缺失模块: "
                    f"{missing}"
                ) from exc

            kwargs: dict[str, Any] = {
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
                "text_detection_model_name": f"{self.ocr_model}_det",
                "text_recognition_model_name": f"{self.ocr_model}_rec",
                "text_detection_model_dir": str(self.ocr_det_model_dir),
                "text_recognition_model_dir": str(self.ocr_rec_model_dir),
                "device": self.ocr_device,
                "engine": self.ocr_engine,
            }
            if self.ocr_min_score > 0:
                kwargs["text_rec_score_thresh"] = self.ocr_min_score
            if self.ocr_use_tensorrt:
                kwargs["use_tensorrt"] = True
                kwargs["precision"] = self.ocr_precision

            try:
                self._engine = PaddleOCR(**kwargs)
            except Exception as exc:  # noqa: BLE001 - keep the provider error actionable.
                raise RuntimeError(
                    "PaddleOCR 初始化失败: "
                    f"device={self.ocr_device}, det={self.ocr_det_model_dir}, rec={self.ocr_rec_model_dir}: {exc}"
                ) from exc
        return self._engine

    def _recognizer_passes_sanity_check(self, recognizer: Any) -> bool:
        from PIL import Image, ImageDraw, ImageFont

        sample_text = "\u6d4b\u8bd5OCR"
        font = None
        for font_path in (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
        ):
            if font_path.exists():
                try:
                    font = ImageFont.truetype(str(font_path), 42)
                    break
                except OSError:
                    pass
        if font is None:
            font = ImageFont.load_default()
            sample_text = "OCR2026"

        with tempfile.TemporaryDirectory() as temp_dir:
            sample_path = Path(temp_dir) / "ocr_sanity.jpg"
            image = Image.new("RGB", (360, 90), "white")
            draw = ImageDraw.Draw(image)
            draw.text((18, 18), sample_text, fill="black", font=font)
            image.save(sample_path, quality=95)
            result = recognizer.predict(str(sample_path))
        texts = _extract_paddleocr_texts(result, min_score=0.5)
        joined = "".join(texts)
        return "OCR" in joined.upper() or "\u6d4b\u8bd5" in joined

    def _load_recognizer(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        with self._recognizer_lock:
            if self._recognizer is not None:
                return self._recognizer
            missing_paths = [str(path) for path in (self.ocr_root, self.ocr_rec_model_dir) if not path.exists()]
            if missing_paths:
                raise RuntimeError("PaddleOCR 本地目录或识别模型目录不存在: " + ", ".join(missing_paths))

            self._ensure_import_path()
            try:
                from paddleocr import TextRecognition
            except ModuleNotFoundError as exc:
                missing = exc.name or "paddleocr dependency"
                raise RuntimeError(
                    "PaddleOCR TextRecognition 运行依赖未安装。请安装 PaddlePaddle 运行时，并执行 "
                    "`python -m pip install -e utils/PaddleOCR-main`；缺失模块: "
                    f"{missing}"
                ) from exc

            requested_device = self.ocr_device or "gpu"
            candidate_devices = [requested_device]
            if self.ocr_allow_cpu_fallback and requested_device.lower().startswith("gpu"):
                candidate_devices.append("cpu")
            errors: list[str] = []
            for device in _dedupe_texts(candidate_devices):
                try:
                    recognizer = TextRecognition(
                        model_name=f"{self.ocr_model}_rec",
                        model_dir=str(self.ocr_rec_model_dir),
                        device=device,
                    )
                    if not self._recognizer_passes_sanity_check(recognizer):
                        raise RuntimeError("OCR sanity check failed")
                    self._recognizer = recognizer
                    self._recognition_device = device
                    if device != requested_device:
                        self._append_warning(
                            f"ocr_device_fallback:{requested_device}->cpu: GPU OCR sanity check failed; using CPU recognition."
                        )
                    return recognizer
                except Exception as exc:  # noqa: BLE001 - try the configured fallback device before failing the frame.
                    errors.append(f"{device}: {exc}")
            raise RuntimeError("PaddleOCR TextRecognition 初始化/自检失败: " + " | ".join(errors))

    def _crop_frame_texts(self, image_path: Path) -> list[str]:
        from PIL import Image

        recognizer = self._load_recognizer()
        boxes = self._crop_box_provider(image_path)
        if not boxes:
            return []
        with Image.open(image_path) as image, tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            crop_paths: list[str] = []
            for index, box in enumerate(boxes):
                crop_path = temp_path / f"crop_{index:02d}.jpg"
                image.crop(box).save(crop_path, quality=95)
                crop_paths.append(str(crop_path))
            with self._recognizer_predict_lock:
                result = recognizer.predict(crop_paths)
        texts = _extract_paddleocr_texts(result, min_score=self.ocr_crop_min_score)
        return _dedupe_texts(texts)

    def frame_texts(self, frame: str) -> list[str]:
        image_path = resolve_artifact_path(self.project_dir, frame)
        if image_path is None or not image_path.exists():
            raise FileNotFoundError(f"frame not found: {frame}")
        result: Any | None = None
        pipeline_error: Exception | None = None
        try:
            engine = self._load_engine()
            predict_kwargs: dict[str, Any] = {}
            if self.ocr_min_score > 0:
                predict_kwargs["text_rec_score_thresh"] = self.ocr_min_score
            with self._pipeline_predict_lock:
                result = engine.predict(str(image_path), **predict_kwargs)
            texts = _extract_paddleocr_texts(result, min_score=self.ocr_min_score)
        except Exception as exc:  # noqa: BLE001 - crop recognition can still recover useful text.
            pipeline_error = exc
            texts = []

        needs_crop = False
        if self.ocr_crop_fallback:
            if result is None:
                needs_crop = True
            else:
                try:
                    from PIL import Image

                    with Image.open(image_path) as image:
                        needs_crop = _paddleocr_result_needs_crop_fallback(result, image.size) or not texts
                except Exception:
                    needs_crop = not texts
        if needs_crop:
            crop_texts = self._crop_frame_texts(image_path)
            if crop_texts:
                if pipeline_error:
                    self._append_warning(
                        f"ocr_pipeline_fallback:{Path(frame).name}:{pipeline_error}"
                    )
                else:
                    self._append_warning(
                        f"ocr_crop_fallback:{Path(frame).name}: pipeline detection returned whole-frame/empty OCR."
                    )
                return crop_texts
        if pipeline_error:
            raise pipeline_error
        return texts


__all__ = [
    "PaddleOCRFrameRecognizer",
    "_candidate_text_crop_boxes",
    "extract_ocr_texts",
    "paddleocr_model_name",
]
