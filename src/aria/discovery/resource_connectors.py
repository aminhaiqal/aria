import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from aria.fetching.client import hostname_is_allowed


class ResourceStructureChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class ResourceLinkData:
    target_url: str
    fingerprint: str
    title: str = ""
    external_identifier: str = ""
    relation: str = "document"
    disposition: str = "accepted"
    quarantine_reason: str = ""
    metadata: dict = field(default_factory=dict)


def normalize_link_url(base_url: str, href: str) -> str:
    parsed = urlsplit(urljoin(base_url, href.strip()))
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    return urlunsplit((parsed.scheme.lower(), hostname, parsed.path or "/", parsed.query, ""))


def link_fingerprint(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def parse_detail_page(
    content: bytes,
    *,
    resource_url: str,
    allowed_domains: list[str],
    content_selector: str = ".betterdocs-entry-content",
    document_extensions: tuple[str, ...] = (".pdf", ".doc", ".docx", ".csv", ".json", ".xml"),
) -> tuple[ResourceLinkData, ...]:
    soup = BeautifulSoup(content, "html.parser")
    root = soup.select_one(content_selector)
    if root is None:
        raise ResourceStructureChanged(
            f"Detail selector '{content_selector}' was not found at {resource_url}."
        )

    allowed_extensions = {extension.lower() for extension in document_extensions}
    links: dict[str, ResourceLinkData] = {}
    for link in root.select("a[href]"):
        href = link.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        target_url = normalize_link_url(resource_url, href)
        parsed = urlsplit(target_url)
        suffix = PurePosixPath(parsed.path).suffix.lower()
        if suffix not in allowed_extensions:
            continue
        title = " ".join(link.get_text(" ", strip=True).split())[:1024]
        disposition = "accepted"
        quarantine_reason = ""
        if parsed.scheme != "https" or not parsed.hostname:
            disposition = "quarantined"
            quarantine_reason = "non_https_or_missing_hostname"
        elif not hostname_is_allowed(parsed.hostname, allowed_domains):
            disposition = "quarantined"
            quarantine_reason = "hostname_not_allowlisted"
        links.setdefault(
            target_url,
            ResourceLinkData(
                target_url=target_url,
                fingerprint=link_fingerprint(target_url),
                title=title,
                disposition=disposition,
                quarantine_reason=quarantine_reason,
                metadata={"source_detail_page": resource_url},
            ),
        )
    return tuple(sorted(links.values(), key=lambda item: item.target_url))
