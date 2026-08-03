import hashlib
import json
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from django.conf import settings
from pypdf import PdfReader

HTML_CONTENT_SELECTORS = (
    ".betterdocs-content-wrapper",
    "article .entry-content",
    ".entry-content",
    ".post-content",
    ".elementor-widget-theme-post-content",
    "main",
    "article",
    "[role='main']",
    "body",
)
HTML_BLOCK_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "blockquote",
    "pre",
    "table",
}


class ExtractionError(RuntimeError):
    pass


class UnsupportedArtifactError(ExtractionError):
    pass


@dataclass(frozen=True)
class ExtractedBlockData:
    block_type: str
    text: str
    heading: str = ""
    heading_level: int | None = None
    page_number: int | None = None
    source_locator: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedDocumentData:
    title: str
    language_hint: str
    blocks: list[ExtractedBlockData]
    metadata: dict
    page_count: int | None = None
    requires_ocr: bool = False

    @property
    def plain_text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


class Extractor(Protocol):
    name: str
    version: str

    @property
    def configuration(self) -> dict: ...

    def extract(self, content: bytes, *, source_url: str = "") -> ExtractedDocumentData: ...


def normalize_inline_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def normalize_multiline_text(value: str) -> str:
    lines = [normalize_inline_text(line) for line in value.splitlines()]
    output: list[str] = []
    previous_blank = False
    for line in lines:
        if line:
            output.append(line)
            previous_blank = False
        elif output and not previous_blank:
            output.append("")
            previous_blank = True
    return "\n".join(output).strip()


def configuration_hash(configuration: dict) -> str:
    serialized = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def html_path(element: Tag, root: Tag) -> str:
    parts: list[str] = []
    current: Tag | None = element
    while current is not None:
        if current.get("id"):
            parts.append(f"{current.name}#{current['id']}")
            break
        siblings = (
            [
                sibling
                for sibling in current.parent.find_all(current.name, recursive=False)
                if isinstance(sibling, Tag)
            ]
            if isinstance(current.parent, Tag)
            else []
        )
        position = siblings.index(current) + 1 if current in siblings else 1
        parts.append(f"{current.name}:nth-of-type({position})")
        if current is root:
            break
        current = current.parent if isinstance(current.parent, Tag) else None
    return " > ".join(reversed(parts))


def table_text(element: Tag) -> str:
    rows: list[str] = []
    for row in element.find_all("tr"):
        cells = [
            normalize_inline_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(("th", "td"), recursive=False)
        ]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


class HTMLExtractor:
    name = "html"
    version = "1"

    @property
    def configuration(self) -> dict:
        return {
            "content_selectors": list(HTML_CONTENT_SELECTORS),
            "removed_tags": [
                "script",
                "style",
                "noscript",
                "svg",
                "nav",
                "footer",
                "header",
                "form",
                "aside",
            ],
        }

    def extract(self, content: bytes, *, source_url: str = "") -> ExtractedDocumentData:
        soup = BeautifulSoup(content, "html.parser")
        for unwanted in soup.find_all(self.configuration["removed_tags"]):
            unwanted.decompose()

        root: Tag | None = None
        selected_with = ""
        for selector in HTML_CONTENT_SELECTORS:
            candidates = [
                candidate
                for candidate in soup.select(selector)
                if isinstance(candidate, Tag)
                and len(normalize_inline_text(candidate.get_text(" ", strip=True))) >= 40
            ]
            if candidates:
                root = max(
                    candidates,
                    key=lambda candidate: len(candidate.get_text(" ", strip=True)),
                )
                selected_with = selector
                break
        if root is None:
            raise ExtractionError("HTML artifact has no qualifying content root.")

        title = ""
        heading = root.find(("h1", "h2"))
        if heading:
            title = normalize_inline_text(heading.get_text(" ", strip=True))
        if not title:
            og_title = soup.select_one("meta[property='og:title']")
            if og_title and og_title.get("content"):
                title = normalize_inline_text(str(og_title["content"]))
        if not title and soup.title:
            title = normalize_inline_text(soup.title.get_text(" ", strip=True))
            title = re.split(r"\s+[•|–—-]\s+", title, maxsplit=1)[0]

        blocks: list[ExtractedBlockData] = []
        current_heading = ""
        for element in root.find_all(HTML_BLOCK_TAGS):
            if any(
                isinstance(parent, Tag) and parent.name in HTML_BLOCK_TAGS
                for parent in element.parents
                if parent is not root
            ):
                continue
            if element.name == "table":
                text = table_text(element)
                block_type = "table"
            else:
                text = normalize_multiline_text(element.get_text("\n", strip=True))
                block_type = {
                    "li": "list_item",
                    "blockquote": "quote",
                    "pre": "code",
                }.get(element.name, "paragraph")
            if not text:
                continue
            heading_level = None
            if element.name and re.fullmatch(r"h[1-6]", element.name):
                block_type = "heading"
                heading_level = int(element.name[1])
                current_heading = text
            blocks.append(
                ExtractedBlockData(
                    block_type=block_type,
                    text=text,
                    heading=current_heading if block_type != "heading" else text,
                    heading_level=heading_level,
                    source_locator={"html_path": html_path(element, root)},
                )
            )
        if not blocks:
            fallback_text = normalize_multiline_text(root.get_text("\n", strip=True))
            if not fallback_text:
                raise ExtractionError("HTML content root produced no text blocks.")
            blocks.append(
                ExtractedBlockData(
                    block_type="paragraph",
                    text=fallback_text,
                    source_locator={"html_path": html_path(root, root)},
                )
            )

        links: list[dict[str, str]] = []
        seen_links: set[str] = set()
        for link in root.select("a[href]"):
            absolute = urljoin(source_url, str(link.get("href", "")))
            if not absolute or absolute in seen_links:
                continue
            seen_links.add(absolute)
            links.append(
                {
                    "url": absolute,
                    "text": normalize_inline_text(link.get_text(" ", strip=True)),
                }
            )

        html = soup.html
        language_hint = normalize_inline_text(str(html.get("lang", ""))).lower() if html else ""
        return ExtractedDocumentData(
            title=title,
            language_hint=language_hint,
            blocks=blocks,
            metadata={
                "content_selector": selected_with,
                "links": links,
                "extractor": f"{self.name}:{self.version}",
            },
        )


class PDFExtractor:
    name = "pdf"
    version = "pypdf-6-v1"

    @property
    def configuration(self) -> dict:
        return {
            "extraction_mode": "layout",
            "ocr_min_characters_per_page": settings.PDF_OCR_MIN_CHARACTERS_PER_PAGE,
        }

    def extract(self, content: bytes, *, source_url: str = "") -> ExtractedDocumentData:
        try:
            reader = PdfReader(BytesIO(content), strict=False)
        except Exception as error:
            raise ExtractionError("PDF artifact could not be parsed.") from error
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as error:
                raise ExtractionError("PDF artifact is encrypted and cannot be opened.") from error
            if not unlocked:
                raise ExtractionError("PDF artifact is encrypted and cannot be opened.")

        blocks: list[ExtractedBlockData] = []
        total_characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = (
                    page.extract_text(extraction_mode="layout") or ""
                    if page.get("/Contents") is not None
                    else ""
                )
            except Exception as error:
                raise ExtractionError(f"PDF page {page_number} could not be extracted.") from error
            text = normalize_multiline_text(text)
            total_characters += len(re.sub(r"\s+", "", text))
            if text:
                blocks.append(
                    ExtractedBlockData(
                        block_type="page",
                        text=text,
                        page_number=page_number,
                        source_locator={"page": page_number},
                    )
                )

        page_count = len(reader.pages)
        threshold = page_count * settings.PDF_OCR_MIN_CHARACTERS_PER_PAGE
        requires_ocr = total_characters < threshold
        metadata = {
            str(key).lstrip("/"): str(value)
            for key, value in (reader.metadata or {}).items()
            if value is not None
        }
        return ExtractedDocumentData(
            title=normalize_inline_text(metadata.get("Title", "")),
            language_hint="",
            blocks=blocks,
            metadata={
                "pdf_metadata": metadata,
                "source_url": source_url,
                "extractor": f"{self.name}:{self.version}",
                "non_whitespace_characters": total_characters,
            },
            page_count=page_count,
            requires_ocr=requires_ocr,
        )


def get_extractor(content_type: str) -> Extractor:
    if content_type == "text/html":
        return HTMLExtractor()
    if content_type == "application/pdf":
        return PDFExtractor()
    raise UnsupportedArtifactError(f"No extractor supports content type '{content_type}'.")
