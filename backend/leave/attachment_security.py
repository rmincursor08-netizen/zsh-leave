from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import os
import signal
import subprocess
import tempfile
import unicodedata
import warnings

from django.conf import settings
from django.utils.module_loading import import_string
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import EmptyFileError, PdfReadError, PdfStreamError


MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_TYPES = {
    "pdf": ("application/pdf", "application/pdf"),
    "png": ("image/png", "image/png"),
    "jpg": ("image/jpeg", "image/jpeg"),
    "jpeg": ("image/jpeg", "image/jpeg"),
}


class InvalidAttachment(ValueError):
    pass


class AttachmentScannerUnavailable(RuntimeError):
    pass


class AttachmentScanFailed(RuntimeError):
    CODES = {
        "scanner_timeout",
        "scanner_process_failed",
        "scanner_invalid_response",
        "stored_file_invalid",
    }

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "scanner_process_failed"
        super().__init__(self.code)


@dataclass(frozen=True)
class ValidatedAttachment:
    filename: str
    content_type: str
    content: bytes


def normalize_attachment_filename(filename: str) -> str:
    if not filename or len(filename) > 1024 or "/" in filename or "\\" in filename:
        raise InvalidAttachment("Invalid attachment filename.")

    normalized = unicodedata.normalize("NFKC", filename).strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or len(normalized) > 255
        or any(unicodedata.category(char).startswith("C") for char in normalized)
        or ":" in normalized
    ):
        raise InvalidAttachment("Invalid attachment filename.")

    return normalized


def _validate_pdf(content: bytes) -> bool:
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        return False
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        return not reader.is_encrypted and len(reader.pages) > 0
    except (EmptyFileError, PdfReadError, PdfStreamError, ValueError, KeyError, IndexError):
        return False


def _validate_image(content: bytes, expected_format: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format != expected_format:
                    return False
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    return False
                image.verify()
        return True
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return False


def _scan_with_command(content: bytes, command: list[str]) -> str:
    resource_limiter = getattr(settings, "ZSH_ATTACHMENT_PRLIMIT", "")
    if not resource_limiter:
        raise AttachmentScannerUnavailable("resource_limiter_unavailable")
    timeout = settings.ZSH_ATTACHMENT_SCANNER_TIMEOUT_SECONDS
    limited_command = [
        resource_limiter,
        f"--cpu={timeout}:{timeout + 1}",
        "--fsize=65536:65536",
        "--nofile=64:64",
        "--core=0:0",
        "--",
        *command,
    ]
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                limited_command,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env={"PATH": os.defpath, "LANG": "C", "HOME": "/nonexistent"},
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
            try:
                process.communicate(content, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise AttachmentScanFailed("scanner_timeout") from exc
            output.seek(0)
            result = output.read(64 * 1024 + 1)
    except FileNotFoundError as exc:
        raise AttachmentScannerUnavailable("scanner_executable_missing") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise AttachmentScanFailed("scanner_process_failed") from exc

    if process.returncode != 0 or len(result) > 64 * 1024:
        raise AttachmentScanFailed("scanner_process_failed")
    try:
        def reject_duplicate_keys(pairs):
            response = {}
            for key, value in pairs:
                if key in response:
                    raise ValueError("duplicate key")
                response[key] = value
            return response

        response = json.loads(result.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise AttachmentScanFailed("scanner_invalid_response") from exc
    if response == {"status": "clean"}:
        return "clean"
    if response == {"status": "rejected"}:
        return "rejected"
    raise AttachmentScanFailed("scanner_invalid_response")


def scan_attachment(content: bytes) -> str:
    command = getattr(settings, "ZSH_ATTACHMENT_SCANNER_COMMAND", [])
    if command:
        return _scan_with_command(content, command)

    scanner_path = getattr(settings, "ZSH_ATTACHMENT_SCANNER", "")
    if not scanner_path:
        raise AttachmentScannerUnavailable("scanner_not_configured")
    try:
        scanner = import_string(scanner_path)
    except (ImportError, AttributeError, ValueError) as exc:
        raise AttachmentScannerUnavailable("scanner_unavailable") from exc
    try:
        clean = scanner(content)
    except Exception as exc:
        raise AttachmentScanFailed("scanner_process_failed") from exc
    if not isinstance(clean, bool):
        raise AttachmentScanFailed("scanner_invalid_response")
    return "clean" if clean else "rejected"


def validate_attachment(upload) -> ValidatedAttachment:
    if upload.size <= 0 or upload.size > MAX_ATTACHMENT_SIZE:
        raise InvalidAttachment("Attachment size is outside the allowed range.")

    filename = normalize_attachment_filename(upload.name or "")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    expected = ALLOWED_TYPES.get(extension)
    if expected is None:
        raise InvalidAttachment("Attachment extension is not allowed.")

    content = upload.read(MAX_ATTACHMENT_SIZE + 1)
    if len(content) != upload.size or len(content) > MAX_ATTACHMENT_SIZE:
        raise InvalidAttachment("Attachment size does not match the uploaded content.")
    upload.seek(0)

    detected_type, declared_type = expected
    if upload.content_type != declared_type:
        raise InvalidAttachment("Attachment MIME type does not match its allowed type.")

    if detected_type == "application/pdf":
        valid = _validate_pdf(content)
    elif detected_type == "image/png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n") and _validate_image(
            content, "PNG",
        )
    else:
        valid = content.startswith(b"\xff\xd8\xff") and _validate_image(
            content, "JPEG",
        )
    if not valid:
        raise InvalidAttachment("Attachment content is malformed or has an invalid signature.")

    return ValidatedAttachment(filename=filename, content_type=detected_type, content=content)
