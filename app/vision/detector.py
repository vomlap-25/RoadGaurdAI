"""OpenCV DNN adapter for local ONNX object-detection models.

No model is downloaded automatically. The adapter supports common YOLO-style
[1, attributes, candidates] / [candidates, attributes] outputs and post-NMS
Nx6 [x1,y1,x2,y2,confidence,class_id] outputs. Model-specific exports may need
an adapter implementing the Detector protocol instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class RawDetection:
    class_name: str
    bbox: tuple[int, int, int, int]
    confidence: float


class Detector(Protocol):
    def predict(self, image: np.ndarray) -> list[RawDetection]: ...


class ModelNotConfiguredError(RuntimeError):
    """Raised when video analysis is requested without a local ONNX model."""


class OpenCVDNNDetector:
    """Run an ONNX detector with OpenCV DNN and normalize results to xyxy boxes."""

    def __init__(
        self,
        model_path: str | Path,
        class_names: Sequence[str] = ("pothole", "crack"),
        input_size: int = 640,
        confidence_threshold: float = 0.25,
        nms_threshold: float = 0.45,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise ModelNotConfiguredError(
                f"ONNX model not found at {self.model_path}. Set ROADGUARD_ONNX_MODEL "
                "to a verified local model file; no model is downloaded automatically."
            )
        self.class_names = tuple(class_names)
        self.input_size = int(input_size)
        self.confidence_threshold = float(confidence_threshold)
        self.nms_threshold = float(nms_threshold)
        self.net = cv2.dnn.readNetFromONNX(str(self.model_path))

    def predict(self, image: np.ndarray) -> list[RawDetection]:
        if image is None or image.size == 0:
            return []
        height, width = image.shape[:2]
        blob = cv2.dnn.blobFromImage(
            image, scalefactor=1.0 / 255.0,
            size=(self.input_size, self.input_size), swapRB=True, crop=False,
        )
        self.net.setInput(blob)
        outputs = self.net.forward()
        rows = self._rows(outputs)
        boxes: list[list[int]] = []
        scores: list[float] = []
        class_ids: list[int] = []

        for row in rows:
            values = np.asarray(row, dtype=np.float32).reshape(-1)
            if values.size == 6:
                x1, y1, x2, y2, confidence, class_id = values.tolist()
                class_id = int(class_id)
                # Post-NMS output is conventionally xyxy; tolerate normalized coordinates.
                if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
                    x1, x2 = x1 * width, x2 * width
                    y1, y2 = y1 * height, y2 * height
            elif values.size >= 5:
                # YOLOv8-style: cx, cy, w, h, per-class confidence (no objectness).
                cx, cy, bw, bh = values[:4]
                class_scores = values[4:]
                class_id = int(np.argmax(class_scores))
                confidence = float(class_scores[class_id])
                # Scale model-space coordinates back to original image dimensions.
                sx, sy = width / self.input_size, height / self.input_size
                x1, y1 = (cx - bw / 2) * sx, (cy - bh / 2) * sy
                x2, y2 = (cx + bw / 2) * sx, (cy + bh / 2) * sy
            else:
                continue
            if confidence < self.confidence_threshold or not (0 <= class_id < len(self.class_names)):
                continue
            x1 = int(np.clip(round(x1), 0, width - 1))
            y1 = int(np.clip(round(y1), 0, height - 1))
            x2 = int(np.clip(round(x2), x1 + 1, width))
            y2 = int(np.clip(round(y2), y1 + 1, height))
            boxes.append([x1, y1, x2 - x1, y2 - y1])
            scores.append(float(confidence))
            class_ids.append(class_id)

        if not boxes:
            return []
        keep = cv2.dnn.NMSBoxes(boxes, scores, self.confidence_threshold, self.nms_threshold)
        indices = np.asarray(keep).reshape(-1).tolist() if len(keep) else []
        return [
            RawDetection(
                self.class_names[class_ids[i]],
                (boxes[i][0], boxes[i][1], boxes[i][0] + boxes[i][2], boxes[i][1] + boxes[i][3]),
                scores[i],
            )
            for i in indices
        ]

    @staticmethod
    def _rows(outputs: np.ndarray) -> np.ndarray:
        array = np.asarray(outputs)
        array = np.squeeze(array)
        if array.ndim == 1:
            return array.reshape(1, -1)
        if array.ndim > 2:
            array = array.reshape(-1, array.shape[-1])
        # YOLO exports commonly output [attributes, candidates].
        if array.ndim == 2 and array.shape[0] <= 128 and array.shape[1] > array.shape[0] and array.shape[0] != 6:
            array = array.T
        return array
