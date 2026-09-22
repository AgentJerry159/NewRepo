"""Resumable Py-Feat v2 batch debugger for five-minute fatigue-induction clips.

The program deliberately stores frame-level model output only.  It does not
calculate window means, a fatigue score, MAD scores, clusters, or a three-level
classification.  Those are separate, explicitly chosen downstream analyses.

Expected input names: participant-period-segment.ext, e.g. 27-1-4.mp4.
`period` is 1/2/3 and `segment` is 1..6.  Six contiguous five-minute clips are
placed on a common session axis: (segment - 1) * 300 + clip_time_sec.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterable

import cv2
import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm"}
NAME_PATTERN = re.compile(
    r"^(?P<participant>\d+)-(?P<period>[123])-(?P<segment>[1-6])$"
)
FRAME_PATTERN = re.compile(r"frame_(?P<frame>\d+)\.png$")
SEGMENT_SECONDS = 300.0
# Leave some headroom below Excel's 1,048,576-row worksheet limit.  A single
# five-minute clip at 12 fps is far below this; the combined workbook may not
# be, so it is split into consecutive raw-data sheets when necessary.
EXCEL_DATA_ROWS_PER_SHEET = 1_000_000

# The compact profile intentionally keeps only interpretable, frame-level
# measures relevant to the present fatigue protocol.  It is 35 columns when
# all fields are available, versus about 1,679 columns in full Py-Feat output.
COMPACT_OUTPUT_COLUMNS = (
    "participant_id", "period_code", "segment_index", "source_frame_index",
    "clip_time_sec", "session_time_sec", "target_analysis_fps", "source_fps",
    "FaceScore", "face_detected",
    "AU01", "AU02", "AU04", "AU05", "AU06", "AU07", "AU09", "AU10",
    "AU11", "AU12", "AU14", "AU15", "AU17", "AU20", "AU23", "AU24",
    "AU25", "AU26", "AU28", "AU43",
    "Pitch", "Roll", "Yaw", "eyeBlinkLeft", "eyeBlinkRight",
)
OUTPUT_PROFILE_DESCRIPTIONS = {
    "compact": "疲劳精简：时间轴、FaceScore/人脸检测、20 个 AU、三轴头姿及左右眨眼。",
    "full": "完整 Py-Feat：保存全部非身份向量输出，含 3D 网格、关键点和 Blendshape。",
}


@dataclass(frozen=True)
class ClipSpec:
    path: Path
    participant_id: str
    period_code: str
    segment_index: int

    @property
    def session_key(self) -> str:
        return f"{self.participant_id}-{self.period_code}"

    @property
    def session_offset_sec(self) -> float:
        return (self.segment_index - 1) * SEGMENT_SECONDS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_clip(path: Path) -> ClipSpec | None:
    match = NAME_PATTERN.fullmatch(path.stem)
    if match is None:
        return None
    return ClipSpec(
        path=path,
        participant_id=match.group("participant"),
        period_code=match.group("period"),
        segment_index=int(match.group("segment")),
    )


def discover_clips(input_root: Path) -> tuple[list[ClipSpec], list[dict[str, str]]]:
    clips: list[ClipSpec] = []
    rejected: list[dict[str, str]] = []
    for path in sorted(input_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        parsed = parse_clip(path)
        if parsed is None:
            rejected.append({
                "path": str(path),
                "reason": "文件名不符合 受试者-时段-片段.ext，例如 27-1-4.mp4",
            })
        else:
            clips.append(parsed)
    return clips, rejected


def inspect_session_integrity(clips: list[ClipSpec]) -> tuple[list[dict[str, Any]], set[str]]:
    """Describe whether each participant-period has exactly six unique clips.

    A duplicate segment would otherwise map to the same output folder and could
    silently mix data.  Missing segments do not prevent individual-clip
    debugging. A timeline is still exported for the files that exist, with
    missing segments explicitly recorded and no invented rows for the gap.
    """
    grouped: dict[str, list[ClipSpec]] = {}
    for spec in clips:
        grouped.setdefault(spec.session_key, []).append(spec)

    rows: list[dict[str, Any]] = []
    available_sessions: set[str] = set()
    expected = set(range(1, 7))
    for session_key, specs in sorted(grouped.items()):
        counts: dict[int, int] = {}
        for spec in specs:
            counts[spec.segment_index] = counts.get(spec.segment_index, 0) + 1
        present = set(counts)
        duplicates = sorted(segment for segment, count in counts.items() if count > 1)
        missing = sorted(expected - present)
        is_complete = not duplicates and not missing
        available_sessions.add(session_key)
        rows.append({
            "session_key": session_key,
            "present_segments": ",".join(map(str, sorted(present))),
            "missing_segments": ",".join(map(str, missing)),
            "duplicate_segments": ",".join(map(str, duplicates)),
            "session_timeline_export": "yes_available_files_only",
            "status": "complete" if is_complete else ("duplicate_name_error" if duplicates else "incomplete"),
        })
    return rows, available_sessions


def format_eta(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "计算中"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def expected_sample_count(frame_count: int, source_fps: float, sample_fps: float) -> int:
    """Return the number of fixed-time-grid samples expected from a CBR clip."""
    if frame_count <= 0 or source_fps <= 0:
        return 0
    last_time = (frame_count - 1) / source_fps
    return math.floor(last_time * sample_fps + 1e-9) + 1


def clip_output_dir(output_root: Path, spec: ClipSpec) -> Path:
    return output_root / "clips" / spec.session_key / f"segment_{spec.segment_index}_{spec.path.stem}"


def ffprobe_metadata(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"available": False, "reason": "ffprobe 未在 PATH 中找到"}
    command = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,codec_long_name,avg_frame_rate,r_frame_rate,time_base,nb_frames,width,height,duration",
        "-show_entries", "format=duration,size,format_name",
        "-of", "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        return {"available": True, "ok": False, "stderr": completed.stderr.strip()}
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"available": True, "ok": False, "stderr": "ffprobe 输出不是 JSON"}
    return {"available": True, "ok": True, "data": data}


def open_capture(path: Path) -> tuple[cv2.VideoCapture, str]:
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("OpenCV 无法打开视频；请检查编码，或让 ffmpeg 先标准化该文件。")
    try:
        backend = capture.getBackendName()
    except cv2.error:
        backend = "unknown"
    return capture, backend


def capture_metadata(path: Path) -> dict[str, Any]:
    capture, backend = open_capture(path)
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        capture.release()
    duration = frames / fps if fps > 0 else None
    return {
        "opencv_backend": backend,
        "opencv_fps": fps,
        "opencv_frame_count": frames,
        "opencv_width": width,
        "opencv_height": height,
        "opencv_duration_sec": duration,
    }


def environment_metadata(requested_device: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "python": sys.version,
        "opencv": cv2.__version__,
        "requested_device": requested_device,
        "ffmpeg_on_path": shutil.which("ffmpeg") is not None,
        "ffprobe_on_path": shutil.which("ffprobe") is not None,
    }
    try:
        import torch

        data.update({
            "torch": torch.__version__,
            "torch_cuda_build": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "gpu_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        })
        if torch.cuda.is_available():
            data["gpu_name"] = torch.cuda.get_device_name(0)
    except Exception as exc:  # preflight should still be useful without torch
        data["torch_error"] = f"{type(exc).__name__}: {exc}"
    try:
        data["py_feat"] = importlib_metadata.version("py-feat")
    except importlib_metadata.PackageNotFoundError:
        data["py_feat"] = None
    return data


def resolve_device(requested: str) -> str:
    import torch

    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("已明确请求 GPU，但当前 PyTorch 未检测到 CUDA。请先运行 --dry-run 检查环境。")
        return "cuda"
    return "cuda" if torch.cuda.is_available() else "cpu"


def install_image_only_torchcodec_compatibility() -> None:
    """Allow Py-Feat's image API to import when Windows TorchCodec DLLs fail.

    Py-Feat imports ``VideoDecoder`` at module import time even though this
    program only supplies PNG image paths to ``Detectorv2.detect``.  Replacing
    that one unused symbol is deliberately narrower than a MagicMock.  Any
    accidental attempt to use Py-Feat/TorchCodec video decoding fails loudly,
    while OpenCV remains the sole decoder in this program.
    """
    class ImageOnlyVideoDecoder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(
                "此程序使用 OpenCV 解码并仅向 Py-Feat 传入 PNG 图片；"
                "TorchCodec 视频解码在当前 Windows 环境不可用。"
            )

    torchcodec_module = types.ModuleType("torchcodec")
    decoders_module = types.ModuleType("torchcodec.decoders")
    decoders_module.VideoDecoder = ImageOnlyVideoDecoder
    torchcodec_module.decoders = decoders_module
    sys.modules["torchcodec"] = torchcodec_module
    sys.modules["torchcodec.decoders"] = decoders_module


def import_detector_v2() -> tuple[Any, bool]:
    # Do this *before* importing Py-Feat. On Windows, attempting the native
    # TorchCodec import can show a system DLL dialog before Python can catch
    # the OSError. This program never uses Py-Feat's video API: OpenCV reads
    # the video and Detectorv2 receives PNG paths.
    install_image_only_torchcodec_compatibility()
    for module_name in list(sys.modules):
        if module_name == "feat" or module_name.startswith("feat."):
            sys.modules.pop(module_name, None)
    from feat import Detectorv2
    return Detectorv2, True


def load_detector(requested_device: str) -> tuple[Any, str, bool]:
    device = resolve_device(requested_device)
    try:
        Detectorv2, torchcodec_compatibility_used = import_detector_v2()
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError(
            "无法导入 Py-Feat v2。请在新建的 GPU 环境中安装 py-feat、torch、opencv-python；"
            "本程序只支持 OpenCV 解码后传入图片路径；原始错误："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    # Identity embeddings are neither retained nor required by this study.
    # Passing None avoids running the separate ArcFace/FaceNet identity model.
    detector = Detectorv2(device=device, identity_model=None)
    return detector, device, torchcodec_compatibility_used


def write_png_unicode(path: Path, frame) -> None:
    ok, buffer = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError(f"无法编码临时帧：{path.name}")
    buffer.tofile(str(path))


def load_processed_frame_ids(parts_dir: Path) -> set[int]:
    processed: set[int] = set()
    for part in sorted(parts_dir.glob("part_*.csv")):
        try:
            values = pd.read_csv(part, usecols=["source_frame_index"])["source_frame_index"]
            processed.update(int(value) for value in values.dropna().tolist())
        except (ValueError, OSError, pd.errors.EmptyDataError) as exc:
            logging.warning("跳过无法读取的检查点分块 %s：%s", part.name, exc)
    return processed


def rectangle_from_row(row: pd.Series) -> tuple[float, float, float, float] | None:
    candidate_columns = [
        ("FaceRectX", "FaceRectY", "FaceRectWidth", "FaceRectHeight"),
        ("face_x", "face_y", "face_width", "face_height"),
    ]
    for x_col, y_col, w_col, h_col in candidate_columns:
        if all(column in row.index and pd.notna(row[column]) for column in (x_col, y_col, w_col, h_col)):
            return (float(row[x_col]), float(row[y_col]), float(row[w_col]), float(row[h_col]))
    return None


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + bw * bh - intersection
    return 0.0 if union <= 0 else intersection / union


def choose_primary_face(candidates: pd.DataFrame, previous_box: tuple[float, float, float, float] | None) -> tuple[pd.Series, tuple[float, float, float, float] | None]:
    scored: list[tuple[float, int, tuple[float, float, float, float] | None]] = []
    for position, (_, row) in enumerate(candidates.iterrows()):
        box = rectangle_from_row(row)
        area = 0.0 if box is None else max(0.0, box[2]) * max(0.0, box[3])
        continuity = 0.0 if box is None or previous_box is None else iou(box, previous_box)
        # Largest face is the default; continuity only resolves close competitors.
        scored.append((area * (1.0 + 0.35 * continuity), position, box))
    _, position, box = max(scored, key=lambda value: value[0])
    return candidates.iloc[position], box


def result_to_dataframe(result: Any) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result.copy()
    return pd.DataFrame(result)


def filter_identity_columns(frame: pd.DataFrame) -> pd.DataFrame:
    identity_columns = [
        column for column in frame.columns
        if "identity" in str(column).lower() or "embedding" in str(column).lower()
    ]
    return frame.drop(columns=identity_columns, errors="ignore")


def apply_output_profile(frame: pd.DataFrame, output_profile: str) -> pd.DataFrame:
    """Keep the requested output fields without inventing any new feature."""
    if output_profile == "full":
        return filter_identity_columns(frame)
    if output_profile != "compact":
        raise ValueError(f"未知输出模式：{output_profile}")
    columns = [column for column in COMPACT_OUTPUT_COLUMNS if column in frame.columns]
    return frame.loc[:, columns].copy()


def atomic_csv_write(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(path)


def make_records(
    predictions: pd.DataFrame,
    requested: list[dict[str, Any]],
    previous_box: tuple[float, float, float, float] | None,
) -> tuple[pd.DataFrame, tuple[float, float, float, float] | None]:
    if not predictions.empty and "input" in predictions.columns:
        predictions = predictions.copy()
        predictions["_frame_id"] = predictions["input"].astype(str).map(
            lambda value: int(FRAME_PATTERN.search(Path(value).name).group("frame"))
            if FRAME_PATTERN.search(Path(value).name) else -1
        )
    else:
        predictions = pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for frame_meta in requested:
        source_frame = frame_meta["source_frame_index"]
        candidates = (
            predictions.loc[predictions["_frame_id"] == source_frame]
            if "_frame_id" in predictions.columns else predictions.iloc[0:0]
        )
        output = dict(frame_meta)
        if candidates.empty:
            output.update({"face_detected": False, "primary_face_rule": "largest_stable"})
        else:
            selected, selected_box = choose_primary_face(candidates, previous_box)
            previous_box = selected_box or previous_box
            for key, value in selected.to_dict().items():
                if key not in {"input", "frame", "_frame_id"}:
                    output[key] = value
            output.update({"face_detected": True, "primary_face_rule": "largest_stable"})
        rows.append(output)
    return filter_identity_columns(pd.DataFrame(rows)), previous_box


def create_figures(frame: pd.DataFrame, output_dir: Path) -> list[str]:
    images: list[str] = []
    if frame.empty or "clip_time_sec" not in frame.columns:
        return images
    x = frame["clip_time_sec"]
    face_score_column = next((column for column in ("FaceScore", "face_score") if column in frame.columns), None)
    if face_score_column:
        fig, ax = plt.subplots(figsize=(11, 3.5))
        ax.plot(x, frame[face_score_column], linewidth=0.7)
        ax.set(
            title="Face detection confidence (FaceScore; quality control only)",
            xlabel="Clip time (s)",
            ylabel=face_score_column,
        )
        ax.grid(alpha=0.25)
        fig.tight_layout()
        target = output_dir / "face_detection_quality.png"
        fig.savefig(target, dpi=180)
        plt.close(fig)
        images.append(target.name)

    emotion_columns = [column for column in ("anger", "disgust", "fear", "happiness", "sadness", "surprise", "neutral") if column in frame.columns]
    if emotion_columns:
        fig, ax = plt.subplots(figsize=(11, 5))
        for column in emotion_columns:
            ax.plot(x, frame[column], linewidth=0.7, label=column)
        ax.set(title="Raw Py-Feat emotion outputs", xlabel="Clip time (s)", ylabel="Model output")
        ax.grid(alpha=0.25)
        ax.legend(ncol=4, fontsize=8)
        fig.tight_layout()
        target = output_dir / "raw_emotion_timeseries.png"
        fig.savefig(target, dpi=180)
        plt.close(fig)
        images.append(target.name)
    return images


def export_clip_excel(parts_dir: Path, output_dir: Path, metadata: dict[str, Any]) -> pd.DataFrame:
    parts = sorted(parts_dir.glob("part_*.csv"))
    if not parts:
        raise RuntimeError("没有可导出的检查点分块。")
    frames = pd.concat([pd.read_csv(part) for part in parts], ignore_index=True, sort=False)
    frames = frames.drop_duplicates(subset=["source_frame_index"], keep="last").sort_values("source_frame_index")
    excel_path = output_dir / "frame_features.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame([metadata]).to_excel(writer, sheet_name="metadata", index=False)
        frames.to_excel(writer, sheet_name="frames_raw", index=False)
    return frames


def raw_sheet_names(excel_path: Path) -> list[str]:
    """Return raw-data sheets in their workbook order.

    ``frames_raw`` is used for a normal five-minute output.  The numbered
    variant also makes this reader forward-compatible with a large workbook
    that had to be split at Excel's row limit.
    """
    with pd.ExcelFile(excel_path, engine="openpyxl") as workbook:
        names = [
            name for name in workbook.sheet_names
            if name == "frames_raw" or name.startswith("frames_raw_")
        ]
    if not names:
        raise RuntimeError(f"结果 Excel 中没有 frames_raw 工作表：{excel_path}")
    return names


def read_exported_frames(excel_path: Path) -> pd.DataFrame:
    sheets = raw_sheet_names(excel_path)
    frames = [pd.read_excel(excel_path, sheet_name=sheet, engine="openpyxl") for sheet in sheets]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def existing_result_reason(
    spec: ClipSpec,
    metadata: dict[str, Any],
    sample_fps: float,
    output_profile: str,
) -> str | None:
    """Return why an existing output cannot safely be reused, if applicable."""
    recorded_source = metadata.get("source_file")
    if not recorded_source:
        return "缺少 source_file 元数据"
    try:
        if Path(str(recorded_source)).resolve() != spec.path.resolve():
            return "同名输出目录对应的是另一份源视频"
    except OSError:
        return "无法核对已有结果的源视频路径"
    if metadata.get("source_file_size_bytes") != spec.path.stat().st_size:
        return "源视频文件大小已经变化"
    recorded_mtime = metadata.get("source_file_mtime_ns")
    if recorded_mtime is not None and recorded_mtime != spec.path.stat().st_mtime_ns:
        return "源视频修改时间已经变化"
    try:
        if not math.isclose(float(metadata.get("target_analysis_fps")), sample_fps, rel_tol=0, abs_tol=1e-9):
            return "目标抽样率已经变化"
    except (TypeError, ValueError):
        return "缺少或无法读取目标抽样率"
    # Results written before this setting existed were full output, which is
    # the only safe backward-compatible interpretation.
    if metadata.get("output_profile", "full") != output_profile:
        return "输出字段模式已经变化"
    return None


def completed_clip_entries(output_root: Path) -> list[tuple[ClipSpec, Path, dict[str, Any], pd.DataFrame]]:
    """Read canonical, completed per-clip files from the shared staging root."""
    clips_root = output_root / "clips"
    if not clips_root.is_dir():
        return []
    entries: list[tuple[ClipSpec, Path, dict[str, Any], pd.DataFrame]] = []
    for status_path in sorted(clips_root.rglob("status.json")):
        clip_dir = status_path.parent
        excel_path = clip_dir / "frame_features.xlsx"
        if read_json(status_path).get("status") != "completed" or not excel_path.is_file():
            continue
        metadata = read_json(clip_dir / "metadata.json")
        try:
            spec = ClipSpec(
                path=Path(str(metadata.get("source_file", ""))),
                participant_id=str(metadata["participant_id"]),
                period_code=str(metadata["period_code"]),
                segment_index=int(metadata["segment_index"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logging.warning("跳过无法识别元数据的完成结果 %s：%s", clip_dir, exc)
            continue
        try:
            entries.append((spec, excel_path, metadata, read_exported_frames(excel_path)))
        except Exception as exc:
            logging.warning("跳过无法读取的完成结果 %s：%s", excel_path, exc)
    return entries


def rebuild_combined_workbook(output_root: Path) -> dict[str, Any]:
    """Rebuild combined raw results and session timelines without inference.

    This is intentionally based on the final per-clip Excel files, never on
    checkpoints.  It makes it safe to analyse different input batches on
    different days, as long as the same staging root is selected in the GUI.
    """
    entries = completed_clip_entries(output_root)
    if not entries:
        raise RuntimeError("暂存目录内没有可汇总的已完成 frame_features.xlsx 文件。")

    session_integrity, _ = inspect_session_integrity([entry[0] for entry in entries])
    session_timeline_export([(spec, frame) for spec, _excel, _meta, frame in entries], output_root, session_integrity)

    clip_rows: list[dict[str, Any]] = []
    raw_frames: list[pd.DataFrame] = []
    analysis_fps: set[str] = set()
    output_profiles: set[str] = set()
    for spec, excel_path, metadata, frame in entries:
        analysis_fps.add(str(metadata.get("target_analysis_fps", "unknown")))
        output_profiles.add(str(metadata.get("output_profile", "full")))
        clip_rows.append({
            "participant_id": spec.participant_id,
            "period_code": spec.period_code,
            "segment_index": spec.segment_index,
            "source_file": str(spec.path),
            "per_clip_excel": str(excel_path),
            "frame_rows": int(len(frame)),
            "source_fps": metadata.get("source_fps"),
            "target_analysis_fps": metadata.get("target_analysis_fps"),
            "output_profile": metadata.get("output_profile", "full"),
            "resolved_device": metadata.get("resolved_device"),
            "face_detected_fraction": metadata.get("face_detected_fraction"),
            "finished_at_utc": metadata.get("finished_at_utc"),
        })
        tagged = frame.copy()
        tagged["analysis_source_excel"] = str(excel_path)
        raw_frames.append(tagged)

    all_frames = pd.concat(raw_frames, ignore_index=True, sort=False)
    sort_columns = [column for column in ("participant_id", "period_code", "session_time_sec", "clip_time_sec") if column in all_frames.columns]
    if sort_columns:
        all_frames = all_frames.sort_values(sort_columns, kind="stable")
    combined_path = output_root / "all_analysis_raw.xlsx"
    combined_metadata = pd.DataFrame([{
        "generated_at_utc": utc_now(),
        "completed_clip_count": len(entries),
        "total_raw_rows": int(len(all_frames)),
        "target_analysis_fps_values": ",".join(sorted(analysis_fps)),
        "output_profile_values": ",".join(sorted(output_profiles)),
        "raw_data_layout": "frames_raw_001...; split only when a sheet approaches Excel row limit",
        "note": "仅合并已完成片段的原始 Py-Feat 输出；不计算均值、疲劳分数或分类。",
        "sampling_rate_warning": "" if len(analysis_fps) <= 1 else "暂存目录存在不同目标抽样率；不要把它们当作同一采样条件直接比较。",
        "output_profile_warning": "" if len(output_profiles) <= 1 else "暂存目录存在不同输出字段模式；总表保留各片段实际字段，不要将缺失列误解为零。",
    }])
    with pd.ExcelWriter(combined_path, engine="openpyxl") as writer:
        combined_metadata.to_excel(writer, sheet_name="summary_metadata", index=False)
        pd.DataFrame(clip_rows).to_excel(writer, sheet_name="clip_index", index=False)
        pd.DataFrame(session_integrity).to_excel(writer, sheet_name="session_integrity", index=False)
        for number, start in enumerate(range(0, len(all_frames), EXCEL_DATA_ROWS_PER_SHEET), start=1):
            all_frames.iloc[start:start + EXCEL_DATA_ROWS_PER_SHEET].to_excel(
                writer, sheet_name=f"frames_raw_{number:03d}", index=False
            )
    return {
        "combined_excel": combined_path,
        "clip_count": len(entries),
        "raw_rows": int(len(all_frames)),
        "session_count": len(session_integrity),
    }


def session_timeline_export(
    session_frames: Iterable[tuple[ClipSpec, pd.DataFrame]],
    root: Path,
    session_integrity: list[dict[str, Any]],
) -> None:
    grouped: dict[str, list[pd.DataFrame]] = {}
    for spec, frame in session_frames:
        grouped.setdefault(spec.session_key, []).append(frame)
    integrity_lookup = {row["session_key"]: row for row in session_integrity}
    for session_key, frames in grouped.items():
        all_frames = pd.concat(frames, ignore_index=True, sort=False).sort_values("session_time_sec")
        target_dir = root / "sessions" / session_key
        target_dir.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(target_dir / "session_timeline_raw.xlsx", engine="openpyxl") as writer:
            pd.DataFrame([integrity_lookup.get(session_key, {"session_key": session_key})]).to_excel(
                writer, sheet_name="session_metadata", index=False
            )
            all_frames.to_excel(writer, sheet_name="frames_raw", index=False)


def process_clip(spec: ClipSpec, args: argparse.Namespace, detector, device: str, runtime_info: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame | None]:
    clip_dir = clip_output_dir(args.output_root, spec)
    parts_dir = clip_dir / "checkpoint_parts"
    status_path = clip_dir / "status.json"
    clip_dir.mkdir(parents=True, exist_ok=True)
    existing_metadata = read_json(clip_dir / "metadata.json")
    existing_status = read_json(status_path)
    completed_excel = clip_dir / "frame_features.xlsx"
    if existing_status.get("status") == "completed" and completed_excel.is_file():
        reason = existing_result_reason(spec, existing_metadata, args.sample_fps, args.output_profile)
        if reason:
            raise RuntimeError(
                f"已有完成结果不能安全复用：{reason}。请改用新的中间暂存目录，避免混合分析条件。"
            )
        frames = read_exported_frames(completed_excel)
        face_count = int(frames["face_detected"].fillna(False).sum()) if "face_detected" in frames else 0
        logging.info("%s 已完成，直接复用已有 Excel，不重新推理。", spec.path.name)
        return {
            "status": "completed",
            "reused_existing_result": True,
            "participant_id": spec.participant_id,
            "period_code": spec.period_code,
            "segment_index": spec.segment_index,
            "source_file": str(spec.path),
            "output_dir": str(clip_dir),
            "source_fps": existing_metadata.get("source_fps"),
            "target_analysis_fps": args.sample_fps,
            "frame_rows": int(len(frames)),
            "estimated_total_samples": existing_metadata.get("estimated_total_samples"),
            "newly_analyzed_rows": 0,
            "analysis_elapsed_sec": 0,
            "face_detected_rows": face_count,
            "face_detected_fraction": existing_metadata.get("face_detected_fraction"),
            "resolved_device": existing_metadata.get("resolved_device", device),
        }, frames

    has_checkpoint_parts = parts_dir.is_dir() and any(parts_dir.glob("part_*.csv"))
    if has_checkpoint_parts and existing_metadata:
        reason = existing_result_reason(spec, existing_metadata, args.sample_fps, args.output_profile)
        if reason:
            raise RuntimeError(
                f"未完成检查点不能安全续跑：{reason}。请改用新的中间暂存目录，避免混合分析条件。"
            )
    parts_dir.mkdir(exist_ok=True)
    processed_ids = load_processed_frame_ids(parts_dir)

    metadata = {
        "source_file": str(spec.path),
        "source_file_size_bytes": spec.path.stat().st_size,
        "source_file_mtime_ns": spec.path.stat().st_mtime_ns,
        "participant_id": spec.participant_id,
        "period_code": spec.period_code,
        "segment_index": spec.segment_index,
        "segment_expected_start_sec": spec.session_offset_sec,
        "segment_expected_end_sec": spec.session_offset_sec + SEGMENT_SECONDS,
        "target_analysis_fps": args.sample_fps,
        "output_profile": args.output_profile,
        "output_profile_description": OUTPUT_PROFILE_DESCRIPTIONS[args.output_profile],
        "output_columns_requested": list(COMPACT_OUTPUT_COLUMNS) if args.output_profile == "compact" else "all_non_identity_pyfeat_columns",
        "requested_device": args.device,
        "resolved_device": device,
        "face_selection": "largest_stable",
        "identity_vectors": "not_computed_or_output",
        "aggregation": "none",
        "created_at_utc": utc_now(),
        "runtime": runtime_info,
        "ffprobe": ffprobe_metadata(spec.path),
        "opencv": capture_metadata(spec.path),
    }
    atomic_json_write(clip_dir / "metadata.json", metadata)
    atomic_json_write(status_path, {"status": "running", "updated_at_utc": utc_now(), "processed_frames": len(processed_ids)})

    capture, backend = open_capture(spec.path)
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0:
        capture.release()
        raise RuntimeError("OpenCV 未返回有效 FPS，无法建立可靠的帧级时间轴。")
    estimated_total_samples = expected_sample_count(source_frame_count, fps, args.sample_fps)

    batch_paths: list[Path] = []
    batch_meta: list[dict[str, Any]] = []
    frame_index = 0
    next_sample_sec = 0.0
    part_number = len(list(parts_dir.glob("part_*.csv"))) + 1
    temp_dir = Path(tempfile.mkdtemp(prefix="pyfeat_frames_"))
    previous_box: tuple[float, float, float, float] | None = None
    batch_count = 0
    samples_completed_this_run = 0
    errors: list[str] = []
    started = time.monotonic()

    def flush_batch() -> None:
        nonlocal batch_paths, batch_meta, part_number, previous_box, batch_count, samples_completed_this_run
        if not batch_paths:
            return
        try:
            result = detector.detect(
                [str(path) for path in batch_paths],
                data_type="image",
                batch_size=len(batch_paths),
                progress_bar=False,
            )
            records, previous_box = make_records(result_to_dataframe(result), batch_meta, previous_box)
            records = apply_output_profile(records, args.output_profile)
            atomic_csv_write(parts_dir / f"part_{part_number:06d}.csv", records)
            part_number += 1
            batch_count += 1
            samples_completed_this_run += len(records)
            elapsed = time.monotonic() - started
            rate = samples_completed_this_run / elapsed if elapsed > 0 else 0.0
            remaining = max(estimated_total_samples - len(processed_ids) - samples_completed_this_run, 0)
            eta_seconds = remaining / rate if rate > 0 else None
            atomic_json_write(status_path, {
                "status": "running",
                "updated_at_utc": utc_now(),
                "last_completed_source_frame": int(records["source_frame_index"].max()),
                "completed_batches_this_run": batch_count,
                "processed_frames_before_run": len(processed_ids),
                "estimated_total_samples": estimated_total_samples,
                "samples_completed_this_run": samples_completed_this_run,
                "estimated_remaining_seconds": eta_seconds,
            })
            logging.info(
                "%s | 当前片段进度 %s/%s（%.1f%%） | %.2f 帧/秒 | 预计剩余 %s",
                spec.path.name,
                len(processed_ids) + samples_completed_this_run,
                estimated_total_samples,
                100 * (len(processed_ids) + samples_completed_this_run) / estimated_total_samples if estimated_total_samples else 0,
                rate,
                format_eta(eta_seconds),
            )
        finally:
            for image_path in batch_paths:
                image_path.unlink(missing_ok=True)
            batch_paths, batch_meta = [], []

    try:
        while True:
            success, image = capture.read()
            if not success:
                break
            clip_time = frame_index / fps
            if clip_time + 1e-9 >= next_sample_sec:
                while next_sample_sec <= clip_time + 1e-9:
                    next_sample_sec += 1.0 / args.sample_fps
                if frame_index not in processed_ids:
                    temp_path = temp_dir / f"frame_{frame_index}.png"
                    write_png_unicode(temp_path, image)
                    batch_paths.append(temp_path)
                    batch_meta.append({
                        "participant_id": spec.participant_id,
                        "period_code": spec.period_code,
                        "segment_index": spec.segment_index,
                        "source_frame_index": frame_index,
                        "clip_time_sec": round(clip_time, 6),
                        "session_time_sec": round(spec.session_offset_sec + clip_time, 6),
                        "target_analysis_fps": args.sample_fps,
                        "source_fps": fps,
                        "opencv_backend": backend,
                    })
                    if len(batch_paths) >= args.batch_size:
                        flush_batch()
            frame_index += 1
        flush_batch()
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        atomic_json_write(status_path, {
            "status": "failed",
            "updated_at_utc": utc_now(),
            "error": errors[-1],
            "traceback": traceback.format_exc(),
        })
        raise
    finally:
        capture.release()
        shutil.rmtree(temp_dir, ignore_errors=True)

    metadata.update({
        "opencv_backend": backend,
        "source_fps": fps,
        "source_frames_read": frame_index,
        "estimated_total_samples": estimated_total_samples,
        "samples_completed_this_run": samples_completed_this_run,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "finished_at_utc": utc_now(),
    })
    frames = export_clip_excel(parts_dir, clip_dir, metadata)
    images = create_figures(frames, clip_dir)
    face_count = int(frames["face_detected"].fillna(False).sum()) if "face_detected" in frames else 0
    metadata.update({
        "exported_frame_rows": int(len(frames)),
        "face_detected_rows": face_count,
        "face_detected_fraction": round(face_count / len(frames), 6) if len(frames) else None,
        "figures": images,
        "checkpoint_parts_retained": False,
    })
    atomic_json_write(clip_dir / "metadata.json", metadata)
    atomic_json_write(status_path, {"status": "completed", "updated_at_utc": utc_now(), "frame_rows": int(len(frames))})
    try:
        # The final Excel is now the canonical result.  Checkpoints are only
        # useful during an interrupted run and otherwise create needless
        # duplicate data for every five-minute clip.
        shutil.rmtree(parts_dir)
    except OSError as exc:
        metadata["checkpoint_parts_retained"] = True
        metadata["checkpoint_cleanup_warning"] = f"{type(exc).__name__}: {exc}"
        atomic_json_write(clip_dir / "metadata.json", metadata)
        logging.warning("已完成但无法清理检查点目录 %s：%s", parts_dir, exc)
    summary = {
        "status": "completed",
        "participant_id": spec.participant_id,
        "period_code": spec.period_code,
        "segment_index": spec.segment_index,
        "source_file": str(spec.path),
        "output_dir": str(clip_dir),
        "source_fps": fps,
        "target_analysis_fps": args.sample_fps,
        "frame_rows": int(len(frames)),
        "estimated_total_samples": estimated_total_samples,
        "newly_analyzed_rows": samples_completed_this_run,
        "analysis_elapsed_sec": metadata["elapsed_sec"],
        "face_detected_rows": face_count,
        "face_detected_fraction": metadata["face_detected_fraction"],
        "resolved_device": device,
    }
    return summary, frames


def write_manifest(
    output_root: Path,
    rows: list[dict[str, Any]],
    rejected: list[dict[str, str]],
    session_integrity: list[dict[str, Any]],
) -> None:
    with pd.ExcelWriter(output_root / "batch_manifest.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="clip_status", index=False)
        pd.DataFrame(rejected).to_excel(writer, sheet_name="rejected_filenames", index=False)
        pd.DataFrame(session_integrity).to_excel(writer, sheet_name="session_integrity", index=False)


def run_dry_check(
    clips: list[ClipSpec],
    output_root: Path,
    requested_device: str,
    rejected: list[dict[str, str]],
    session_integrity: list[dict[str, Any]],
) -> int:
    rows: list[dict[str, Any]] = []
    for spec in clips:
        try:
            row = {
                "status": "ready",
                "source_file": str(spec.path),
                "participant_id": spec.participant_id,
                "period_code": spec.period_code,
                "segment_index": spec.segment_index,
                **capture_metadata(spec.path),
            }
        except Exception as exc:
            row = {"status": "cannot_open", "source_file": str(spec.path), "error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
    output_root.mkdir(parents=True, exist_ok=True)
    atomic_json_write(output_root / "preflight_environment.json", environment_metadata(requested_device))
    write_manifest(output_root, rows, rejected, session_integrity)
    print(f"预检完成：有效命名视频 {len(clips)} 个；不符合命名规则 {len(rejected)} 个。")
    print(f"预检结果：{output_root / 'batch_manifest.xlsx'}")
    return 0 if all(row["status"] == "ready" for row in rows) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, help="递归扫描的原始视频目录")
    parser.add_argument("--output-root", type=Path, required=True, help="共享的中间暂存/结果根目录；不会修改输入目录")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto", help="推理设备，默认 auto")
    parser.add_argument("--sample-fps", type=float, default=12.0, help="Py-Feat 原始输出的抽样率，默认 12 fps")
    parser.add_argument(
        "--output-profile",
        choices=("compact", "full"),
        default="compact",
        help="输出字段模式：compact 为疲劳精简约 35 列；full 保存高维完整输出",
    )
    parser.add_argument("--batch-size", type=int, default=8, help="单次送入模型的图片数；RTX 3060 建议从 8 开始")
    parser.add_argument("--dry-run", action="store_true", help="仅检查命名、视频可读性、帧率和环境，不加载模型")
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="只读取共享暂存目录内已完成的单片段 Excel，重建总表和统一时间轴；不重新推理",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.sample_fps <= 0 or args.batch_size <= 0:
        parser.error("--sample-fps 和 --batch-size 必须为正数")
    args.output_root = args.output_root.resolve()
    if args.summarize_only:
        if args.dry_run:
            parser.error("--summarize-only 不能与 --dry-run 同时使用")
        if args.input_root is not None:
            parser.error("汇总模式不需要 --input-root；请只指定 --output-root")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
        try:
            report = rebuild_combined_workbook(args.output_root)
        except Exception as exc:
            logging.exception("汇总失败")
            print(f"汇总失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(
            f"汇总完成：{report['clip_count']} 个已完成片段、{report['raw_rows']} 行原始数据、"
            f"{report['session_count']} 条受试者-时段时间轴。\n总表：{report['combined_excel']}"
        )
        return 0
    if args.input_root is None:
        parser.error("常规分析需要 --input-root")
    args.input_root = args.input_root.resolve()
    if not args.input_root.is_dir():
        parser.error(f"输入目录不存在：{args.input_root}")
    if args.output_root == args.input_root:
        parser.error("输出目录不能与输入目录相同，以免分析结果被重复扫描。")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    clips, rejected = discover_clips(args.input_root)
    if not clips:
        print("没有发现符合命名规则的视频。期望格式如 27-1-4.mp4。", file=sys.stderr)
        return 2
    session_integrity, _available_sessions = inspect_session_integrity(clips)
    if args.dry_run:
        return run_dry_check(clips, args.output_root, args.device, rejected, session_integrity)
    duplicate_sessions = [row["session_key"] for row in session_integrity if row["duplicate_segments"]]
    if duplicate_sessions:
        args.output_root.mkdir(parents=True, exist_ok=True)
        write_manifest(args.output_root, [], rejected, session_integrity)
        print(
            "发现同一受试者-时段存在重复片段编号，若继续会混入同一输出目录："
            + ", ".join(duplicate_sessions),
            file=sys.stderr,
        )
        return 2

    runtime_info = environment_metadata(args.device)
    detector, resolved_device, torchcodec_compatibility_used = load_detector(args.device)
    runtime_info["torchcodec_image_only_compatibility_used"] = torchcodec_compatibility_used
    estimated_new_rows: dict[Path, int] = {}
    for spec in clips:
        try:
            video = capture_metadata(spec.path)
            expected = expected_sample_count(int(video["opencv_frame_count"]), float(video["opencv_fps"]), args.sample_fps)
            clip_dir = clip_output_dir(args.output_root, spec)
            existing_status = read_json(clip_dir / "status.json")
            if existing_status.get("status") == "completed" and (clip_dir / "frame_features.xlsx").is_file():
                existing = expected
            else:
                existing = len(load_processed_frame_ids(clip_dir / "checkpoint_parts"))
            estimated_new_rows[spec.path] = max(expected - existing, 0)
        except Exception as exc:
            logging.warning("无法预估 %s 的工作量：%s", spec.path.name, exc)
            estimated_new_rows[spec.path] = 0
    estimated_batch_remaining = sum(estimated_new_rows.values())
    batch_new_rows = 0
    batch_started = time.monotonic()
    logging.info("批处理预计还需推理约 %s 帧；时间预估会在每个批次完成后更新。", estimated_batch_remaining)
    summaries: list[dict[str, Any]] = []
    session_frames: list[tuple[ClipSpec, pd.DataFrame]] = []
    for spec in clips:
        try:
            logging.info("分析 %s（受试者=%s，时段=%s，片段=%s）", spec.path.name, spec.participant_id, spec.period_code, spec.segment_index)
            summary, frames = process_clip(spec, args, detector, resolved_device, runtime_info)
            summaries.append(summary)
            session_frames.append((spec, frames))
            batch_new_rows += int(summary["newly_analyzed_rows"])
            elapsed = time.monotonic() - batch_started
            rate = batch_new_rows / elapsed if elapsed > 0 else 0.0
            remaining = max(estimated_batch_remaining - batch_new_rows, 0)
            eta_seconds = remaining / rate if rate > 0 else None
            logging.info(
                "批处理总体进度：新完成 %s/%s 帧 | %.2f 帧/秒 | 预计剩余 %s",
                batch_new_rows,
                estimated_batch_remaining,
                rate,
                format_eta(eta_seconds),
            )
        except Exception as exc:
            logging.exception("片段失败：%s", spec.path.name)
            summaries.append({
                "status": "failed",
                "participant_id": spec.participant_id,
                "period_code": spec.period_code,
                "segment_index": spec.segment_index,
                "source_file": str(spec.path),
                "error": f"{type(exc).__name__}: {exc}",
            })
        write_manifest(args.output_root, summaries, rejected, session_integrity)
    if session_frames:
        session_timeline_export(session_frames, args.output_root, session_integrity)
    try:
        combined_report = rebuild_combined_workbook(args.output_root)
        logging.info(
            "已更新总表：%s（%s 个完成片段，%s 行原始数据）",
            combined_report["combined_excel"],
            combined_report["clip_count"],
            combined_report["raw_rows"],
        )
    except Exception:
        # A batch's per-clip Excel files remain valid even if a very large
        # combined workbook cannot be written (for example due to disk space).
        logging.exception("批处理后的总表更新失败；各片段结果未受影响，可稍后点击“汇总”。")
    failures = sum(row["status"] == "failed" for row in summaries)
    print(f"批处理结束：完成 {len(summaries) - failures}，失败 {failures}。结果根目录：{args.output_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

