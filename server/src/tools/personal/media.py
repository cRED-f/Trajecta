"""Local/free media utilities using installed open-source binaries/libraries."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from server.src.tools.personal.documents import VirtualPathResolver


class MediaTools:
    def __init__(self, workspace_root: str, uploads_root: str) -> None:
        self._paths = VirtualPathResolver(workspace_root, uploads_root)
        self._whisper_model: Any | None = None

    async def metadata(self, virtual_path: str) -> dict[str, Any]:
        path = self._paths.resolve(virtual_path)
        if not shutil.which("ffprobe"):
            raise RuntimeError("ffprobe is required for audio/video metadata")
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace"))
        return json.loads(stdout.decode("utf-8"))

    async def convert(self, virtual_path: str, output_path: str) -> dict[str, Any]:
        source = self._paths.resolve(virtual_path)
        destination = self._paths.resolve(output_path, writable=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is required for media conversion")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(source), str(destination),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-20_000:])
        return {"input": virtual_path, "output": output_path, "bytes": destination.stat().st_size}

    async def video_frame(self, virtual_path: str, *, at_seconds: float = 0.0) -> dict[str, Any]:
        source = self._paths.resolve(virtual_path)
        virtual = f"/workspace/.trajecta/video-frames/{uuid.uuid4().hex}.jpg"
        destination = self._paths.resolve(virtual, writable=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is required for video frame extraction")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-ss", str(max(0.0, at_seconds)), "-i", str(source), "-frames:v", "1", str(destination),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-20_000:])
        return {
            "path": virtual,
            "instruction": "Use read_file on this path to inspect the frame multimodally.",
        }

    async def transcribe(self, virtual_path: str, *, model_size: str = "small") -> dict[str, Any]:
        path = self._paths.resolve(virtual_path)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install faster-whisper to use local speech-to-text") from exc
        if self._whisper_model is None:
            # CPU/int8 is the portable free default.  Users with CUDA can swap
            # this through a future model-provider setting without changing the tool API.
            self._whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = self._whisper_model.transcribe(str(path))
        rendered = []
        for segment in segments:
            rendered.append({"start": segment.start, "end": segment.end, "text": segment.text})
        return {"language": info.language, "duration": info.duration, "segments": rendered, "text": "".join(x["text"] for x in rendered)}

    def text_to_speech(self, text: str, output_path: str) -> dict[str, Any]:
        destination = self._paths.resolve(output_path, writable=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            import pyttsx3
        except ImportError as exc:
            raise RuntimeError("Install pyttsx3 to use local text-to-speech") from exc
        engine = pyttsx3.init()
        engine.save_to_file(text, str(destination))
        engine.runAndWait()
        return {"path": output_path, "bytes": destination.stat().st_size if destination.exists() else None}
