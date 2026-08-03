import hashlib
import re
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pypdf import PdfReader


class OCRExecutionError(RuntimeError):
    pass


class RetryableOCRExecutionError(OCRExecutionError):
    pass


@dataclass(frozen=True)
class OCRResult:
    searchable_pdf: bytes
    text_sidecar: bytes
    page_count: int
    non_whitespace_characters: int
    toolchain: dict[str, str]


class OCRExecutor(Protocol):
    def execute(self, content: bytes, configuration: dict) -> OCRResult: ...


def _pdf_page_count(content: bytes) -> int:
    try:
        return len(PdfReader(BytesIO(content), strict=False).pages)
    except Exception as error:
        raise OCRExecutionError("OCR input or output is not a readable PDF.") from error


def _version_output(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OCRExecutionError(
            f"Unable to inspect OCR toolchain command '{command[0]}'."
        ) from error
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else "unknown"


def _tesseract_languages(binary: str) -> set[str]:
    try:
        result = subprocess.run(
            [binary, "--list-langs"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OCRExecutionError("Unable to inspect installed Tesseract languages.") from error
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.lower().startswith("list of available languages")
    }


class SubprocessOCRExecutor:
    def execute(self, content: bytes, configuration: dict) -> OCRResult:
        if len(content) > configuration["maximum_input_bytes"]:
            raise OCRExecutionError("OCR input exceeds the configured byte limit.")
        input_pages = _pdf_page_count(content)
        if input_pages < 1:
            raise OCRExecutionError("OCR input contains no pages.")

        required_languages = set(configuration["languages"].split("+"))
        installed_languages = _tesseract_languages(configuration["tesseract_binary"])
        missing_languages = required_languages - installed_languages
        if missing_languages:
            raise OCRExecutionError(
                "Required Tesseract languages are missing: " + ", ".join(sorted(missing_languages))
            )
        toolchain = {
            "ocrmypdf": _version_output([configuration["binary"], "--version"]),
            "tesseract": _version_output([configuration["tesseract_binary"], "--version"]),
            "languages": "+".join(sorted(required_languages)),
            "declared_toolchain": configuration["declared_toolchain"],
        }

        binary = configuration["binary"]
        with TemporaryDirectory(prefix="aria-ocr-") as temporary_directory:
            workdir = Path(temporary_directory)
            input_path = workdir / "input.pdf"
            output_path = workdir / "searchable.pdf"
            sidecar_path = workdir / "sidecar.txt"
            input_path.write_bytes(content)
            command = [
                binary,
                "--language",
                configuration["languages"],
                "--rotate-pages",
                "--deskew",
                "--skip-text",
                "--optimize",
                "0",
                "--jobs",
                "1",
                "--tesseract-timeout",
                str(configuration["tesseract_timeout_seconds"]),
                "--output-type",
                "pdf",
                "--sidecar",
                str(sidecar_path),
                "--quiet",
                str(input_path),
                str(output_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    timeout=configuration["process_timeout_seconds"],
                )
            except subprocess.TimeoutExpired as error:
                raise RetryableOCRExecutionError("OCR processing exceeded its timeout.") from error
            except OSError as error:
                raise OCRExecutionError("OCRmyPDF could not be started.") from error
            if completed.returncode != 0:
                error_text = (completed.stderr or completed.stdout).decode(
                    "utf-8", errors="replace"
                )
                error_text = " ".join(error_text.split())[:2000]
                raise OCRExecutionError(
                    f"OCRmyPDF exited with code {completed.returncode}: {error_text}"
                )
            if not output_path.exists() or not sidecar_path.exists():
                raise OCRExecutionError("OCRmyPDF did not produce both expected outputs.")

            searchable_pdf = output_path.read_bytes()
            text_sidecar = sidecar_path.read_bytes()

        output_pages = _pdf_page_count(searchable_pdf)
        if output_pages != input_pages:
            raise OCRExecutionError(
                f"OCR page-count mismatch: input={input_pages} output={output_pages}."
            )
        sidecar_text = text_sidecar.decode("utf-8", errors="replace")
        non_whitespace = len(re.sub(r"\s+", "", sidecar_text))
        if non_whitespace < 1:
            raise OCRExecutionError("OCR output contains no recognized text.")
        if hashlib.sha256(searchable_pdf).digest() == hashlib.sha256(content).digest():
            raise OCRExecutionError("OCR output is byte-identical to its source artifact.")

        return OCRResult(
            searchable_pdf=searchable_pdf,
            text_sidecar=text_sidecar,
            page_count=output_pages,
            non_whitespace_characters=non_whitespace,
            toolchain=toolchain,
        )
