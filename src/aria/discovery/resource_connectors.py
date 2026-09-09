import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree.ElementTree import ParseError

import soupsieve
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from aria.fetching.client import hostname_is_allowed


class ResourceStructureChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class HtmlStructureFingerprint:
    sha256: str
    sample: tuple[str, ...]


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


@dataclass(frozen=True)
class ParsedFeed:
    feed_type: str
    title: str
    links: tuple[ResourceLinkData, ...]


def normalize_link_url(base_url: str, href: str) -> str:
    parsed = urlsplit(urljoin(base_url, href.strip()))
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    return urlunsplit((parsed.scheme.lower(), hostname, parsed.path or "/", parsed.query, ""))


def link_fingerprint(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def fingerprint_html_structure(
    content: bytes,
    *,
    maximum_nodes: int = 2000,
    maximum_sample: int = 80,
) -> HtmlStructureFingerprint:
    """Hash HTML shape without retaining page text or attribute values."""
    soup = BeautifulSoup(content, "html.parser")
    tokens: list[str] = []
    sample: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all(True, limit=maximum_nodes):
        classes = sorted(
            value[:80]
            for value in node.get("class", [])[:8]
            if isinstance(value, str) and value
        )
        token = node.name.lower()
        if classes:
            token += "." + ".".join(classes)
        tokens.append(token)
        if token not in seen and len(sample) < maximum_sample:
            sample.append(token)
            seen.add(token)
    canonical = "\n".join(tokens).encode("utf-8")
    return HtmlStructureFingerprint(
        sha256=hashlib.sha256(canonical).hexdigest(),
        sample=tuple(sample),
    )


def _detail_selectors(
    content_selector: str,
    content_selectors: tuple[str, ...] | None,
) -> tuple[str, ...]:
    configured = content_selectors or (content_selector,)
    selectors: list[str] = []
    for selector in configured:
        normalized = str(selector).strip()
        if normalized and normalized not in selectors:
            selectors.append(normalized)
    if not selectors:
        raise ResourceStructureChanged("No approved detail selector was configured.")
    return tuple(selectors)


def parse_detail_page(
    content: bytes,
    *,
    resource_url: str,
    allowed_domains: list[str],
    content_selector: str = ".betterdocs-entry-content",
    content_selectors: tuple[str, ...] | None = None,
    document_extensions: tuple[str, ...] = (".pdf", ".doc", ".docx", ".csv", ".json", ".xml"),
) -> tuple[ResourceLinkData, ...]:
    soup = BeautifulSoup(content, "html.parser")
    selectors = _detail_selectors(content_selector, content_selectors)
    root = None
    matched_selector = ""
    for selector in selectors:
        try:
            root = soup.select_one(selector)
        except soupsieve.SelectorSyntaxError as error:
            raise ResourceStructureChanged(
                f"Approved detail selector {selector!r} is invalid."
            ) from error
        if root is not None:
            matched_selector = selector
            break
    if root is None:
        raise ResourceStructureChanged(
            f"Approved detail selectors {list(selectors)!r} were not found at {resource_url}."
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
                metadata={
                    "source_detail_page": resource_url,
                    "matched_detail_selector": matched_selector,
                },
            ),
        )
    return tuple(sorted(links.values(), key=lambda item: item.target_url))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _direct_child(element, name: str):
    return next((child for child in element if _local_name(child.tag) == name), None)


def _child_text(element, name: str) -> str:
    child = _direct_child(element, name)
    return " ".join("".join(child.itertext()).split()) if child is not None else ""


def _iso_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
    return parsed.isoformat()


def _feed_link_data(
    *,
    feed_url: str,
    target: str,
    title: str,
    external_identifier: str,
    allowed_domains: list[str],
    metadata: dict,
) -> ResourceLinkData | None:
    if not target.strip():
        return None
    target_url = normalize_link_url(feed_url, target)
    parsed = urlsplit(target_url)
    disposition = "accepted"
    quarantine_reason = ""
    if parsed.scheme != "https" or not parsed.hostname:
        disposition = "quarantined"
        quarantine_reason = "non_https_or_missing_hostname"
    elif not hostname_is_allowed(parsed.hostname, allowed_domains):
        disposition = "quarantined"
        quarantine_reason = "hostname_not_allowlisted"
    return ResourceLinkData(
        target_url=target_url,
        fingerprint=link_fingerprint(target_url),
        title=title[:1024],
        external_identifier=external_identifier[:512],
        relation="entry",
        disposition=disposition,
        quarantine_reason=quarantine_reason,
        metadata=metadata,
    )


def parse_feed(
    content: bytes,
    *,
    feed_url: str,
    allowed_domains: list[str],
    max_entries: int = 50,
) -> ParsedFeed:
    if len(content) > 5 * 1024 * 1024:
        raise ResourceStructureChanged("Feed exceeds the 5 MiB parser limit.")
    try:
        root = ElementTree.fromstring(content)
    except (ParseError, DefusedXmlException) as error:
        raise ResourceStructureChanged("Feed is not well-formed XML.") from error

    root_name = _local_name(root.tag)
    links: dict[str, ResourceLinkData] = {}
    if root_name == "rss":
        channel = _direct_child(root, "channel")
        if channel is None:
            raise ResourceStructureChanged("RSS feed does not contain a channel.")
        feed_type = "rss"
        title = _child_text(channel, "title")[:512]
        entries = [child for child in channel if _local_name(child.tag) == "item"]
        for entry in entries[:max_entries]:
            entry_title = _child_text(entry, "title")
            target = _child_text(entry, "link")
            guid = _child_text(entry, "guid")
            link = _feed_link_data(
                feed_url=feed_url,
                target=target,
                title=entry_title,
                external_identifier=guid,
                allowed_domains=allowed_domains,
                metadata={
                    "feed_url": feed_url,
                    "published_at": _iso_date(_child_text(entry, "pubdate")),
                },
            )
            if link is not None:
                links.setdefault(link.target_url, link)
    elif root_name == "feed":
        feed_type = "atom"
        title = _child_text(root, "title")[:512]
        entries = [child for child in root if _local_name(child.tag) == "entry"]
        for entry in entries[:max_entries]:
            entry_title = _child_text(entry, "title")
            target = ""
            for child in entry:
                if _local_name(child.tag) != "link":
                    continue
                relation = child.attrib.get("rel", "alternate").lower()
                if relation == "alternate" and child.attrib.get("href"):
                    target = child.attrib["href"]
                    break
            link = _feed_link_data(
                feed_url=feed_url,
                target=target,
                title=entry_title,
                external_identifier=_child_text(entry, "id"),
                allowed_domains=allowed_domains,
                metadata={
                    "feed_url": feed_url,
                    "published_at": _iso_date(_child_text(entry, "published")),
                    "updated_at": _iso_date(_child_text(entry, "updated")),
                },
            )
            if link is not None:
                links.setdefault(link.target_url, link)
    else:
        raise ResourceStructureChanged(f"Unsupported XML feed root element: {root_name}.")

    if not links:
        raise ResourceStructureChanged("Feed contains no usable entries.")
    identifiers: dict[str, set[str]] = {}
    for link in links.values():
        if link.external_identifier:
            identifiers.setdefault(link.external_identifier, set()).add(link.target_url)
    ambiguous_identifiers = {
        identifier for identifier, targets in identifiers.items() if len(targets) > 1
    }
    if ambiguous_identifiers:
        links = {
            target: (
                replace(
                    link,
                    disposition="quarantined",
                    quarantine_reason="ambiguous_external_identifier",
                )
                if link.external_identifier in ambiguous_identifiers
                else link
            )
            for target, link in links.items()
        }
    return ParsedFeed(
        feed_type=feed_type,
        title=title,
        links=tuple(sorted(links.values(), key=lambda item: item.target_url)),
    )
