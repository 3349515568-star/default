"""Official publisher PDF routing knowledge.

The module is deliberately side-effect free: it only identifies publishers and
builds candidate URLs.  Network requests and cookie handling stay in
``fulltext.py`` / ``session_store.py`` so routes are easy to test and extend.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import quote, urljoin, urlparse


@dataclass(frozen=True)
class PublisherRoute:
    key: str
    aliases: tuple[str, ...]
    doi_prefixes: tuple[str, ...]
    domains: tuple[str, ...]
    pdf_templates: tuple[str, ...] = ()


# Routes are ordered from specific DOI namespaces to broader publisher families.
# A URL may stop working when a publisher redesigns its site; HTML discovery and
# source-URL transforms below then provide a second route without changing the
# downloader architecture.
PUBLISHER_ROUTES: tuple[PublisherRoute, ...] = (
    # sciencedirectassets.com 是 Elsevier 正文 PDF 的实际下发 CDN，缺它浏览器捕获会全丢
    PublisherRoute("elsevier", ("elsevier", "sciencedirect"), ("10.1016/",),
                   ("sciencedirect.com", "elsevier.com", "sciencedirectassets.com")),
    PublisherRoute("ieee", ("ieee", "institute of electrical"), ("10.1109/",),
                   ("ieeexplore.ieee.org", "ieee.org")),
    PublisherRoute("acs", ("american chemical society", "acs"), ("10.1021/",),
                   ("pubs.acs.org",), ("https://pubs.acs.org/doi/pdf/{doi}",)),
    PublisherRoute("aps", ("american physical society", "aps"), ("10.1103/",),
                   ("journals.aps.org",), ("https://journals.aps.org/doi/pdf/{doi}",)),
    PublisherRoute("aip", ("american institute of physics", "aip"), ("10.1063/",),
                   ("pubs.aip.org",), ("https://pubs.aip.org/doi/pdf/{doi}",)),
    PublisherRoute("acm", ("association for computing machinery", "acm"), ("10.1145/",),
                   ("dl.acm.org",), ("https://dl.acm.org/doi/pdf/{doi}",)),
    PublisherRoute("wiley", ("wiley", "blackwell"), ("10.1002/", "10.1111/"),
                   ("onlinelibrary.wiley.com", "wiley.com"),
                   ("https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}",)),
    PublisherRoute("springer", ("springer", "biomed central", "bmc"), ("10.1007/",),
                   ("link.springer.com", "springer.com"),
                   ("https://link.springer.com/content/pdf/{doi}.pdf",)),
    PublisherRoute("nature", ("nature portfolio", "nature publishing", "nature"),
                   ("10.1038/",), ("nature.com",),
                   ("https://www.nature.com/articles/{suffix}.pdf",)),
    PublisherRoute("taylor-francis", ("taylor & francis", "taylor and francis"),
                   ("10.1080/",), ("tandfonline.com",),
                   ("https://www.tandfonline.com/doi/pdf/{doi}",)),
    PublisherRoute("sage", ("sage",), ("10.1177/",), ("journals.sagepub.com",),
                   ("https://journals.sagepub.com/doi/pdf/{doi}",)),
    PublisherRoute("cambridge", ("cambridge university press", "cambridge core"),
                   ("10.1017/",), ("cambridge.org",)),
    PublisherRoute("oxford", ("oxford university press", "oxford academic"),
                   ("10.1093/",), ("academic.oup.com", "oup.com"),
                   ("https://academic.oup.com/doi/pdf/{doi}",)),
    PublisherRoute("rsc", ("royal society of chemistry", "rsc"), ("10.1039/",),
                   ("pubs.rsc.org",)),
    PublisherRoute("iop", ("iop publishing", "institute of physics"), ("10.1088/",),
                   ("iopscience.iop.org",),
                   ("https://iopscience.iop.org/article/{doi}/pdf",)),
    PublisherRoute("science", ("american association for the advancement of science", "aaas"),
                   ("10.1126/",), ("science.org",),
                   ("https://www.science.org/doi/pdf/{doi}",)),
    PublisherRoute("pnas", ("national academy of sciences", "pnas"), ("10.1073/",),
                   ("pnas.org",), ("https://www.pnas.org/doi/pdf/{doi}",)),
    PublisherRoute("annual-reviews", ("annual reviews",), ("10.1146/",),
                   ("annualreviews.org",), ("https://www.annualreviews.org/doi/pdf/{doi}",)),
    PublisherRoute("royal-society", ("royal society publishing",), ("10.1098/",),
                   ("royalsocietypublishing.org",),
                   ("https://royalsocietypublishing.org/doi/pdf/{doi}",)),
    PublisherRoute("plos", ("public library of science", "plos"), ("10.1371/",),
                   ("journals.plos.org",)),
    PublisherRoute("frontiers", ("frontiers",), ("10.3389/",),
                   ("frontiersin.org",),
                   ("https://www.frontiersin.org/articles/{doi}/pdf",)),
    # MDPI's PDF path contains journal ISSN/volume/issue/article and cannot be
    # derived reliably from the DOI alone; the browser reads citation_pdf_url.
    PublisherRoute("mdpi", ("mdpi",), ("10.3390/",), ("mdpi.com",)),
    PublisherRoute("copernicus", ("copernicus",), ("10.5194/",),
                   ("copernicus.org",)),
    PublisherRoute("elife", ("elife",), ("10.7554/",), ("elifesciences.org",),
                   ("https://elifesciences.org/articles/{suffix}.pdf",)),
    PublisherRoute("bmj", ("bmj", "british medical journal"), ("10.1136/",),
                   ("bmj.com",)),
    PublisherRoute("world-scientific", ("world scientific",), ("10.1142/",),
                   ("worldscientific.com",),
                   ("https://www.worldscientific.com/doi/pdf/{doi}",)),
)

ROUTES_BY_KEY = {route.key: route for route in PUBLISHER_ROUTES}

_PLOS_JOURNALS = {
    "pone": "plosone", "pbio": "plosbiology", "pmed": "plosmedicine",
    "pgen": "plosgenetics", "pcbi": "ploscompbiol", "ppat": "plospathogens",
    "pntd": "plosntds",
}

# Supplementary files must never win merely because they are PDFs.
SUPPLEMENT_RE = re.compile(r"(?:supp(?:lement|info)?|supporting|mmc\d*|esm)(?:[._/-]|$)", re.I)


def _host_matches(host: str, domain: str) -> bool:
    host = host.split(":", 1)[0].lower().strip(".")
    domain = domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def route_for_record(rec: dict, source_url: str = "") -> PublisherRoute | None:
    """Infer a publisher route from DOI, publisher label, or official URL."""
    doi = str(rec.get("doi") or "").strip().lower()
    publisher = " ".join(str(rec.get(k) or "") for k in ("publisher", "journal")).casefold()
    host = (urlparse(source_url).hostname or "").lower()
    for route in PUBLISHER_ROUTES:
        if doi and any(doi.startswith(prefix) for prefix in route.doi_prefixes):
            return route
    for route in PUBLISHER_ROUTES:
        if host and any(_host_matches(host, domain) for domain in route.domains):
            return route
    for route in PUBLISHER_ROUTES:
        if publisher and any(alias.casefold() in publisher for alias in route.aliases):
            return route
    return None


class _PDFLinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.urls: list[str] = []

    def _add(self, value: str | None):
        if not value:
            return
        url = urljoin(self.base_url, value.strip())
        if url.startswith(("http://", "https://")) and url not in self.urls:
            self.urls.append(url)

    def handle_starttag(self, tag: str, attrs):
        values = {str(k).lower(): str(v) for k, v in attrs if v is not None}
        if tag.lower() == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name in {
                "citation_pdf_url", "wkhealth_pdf_url", "eprints.document_url",
                "dc.identifier", "og:pdf",
            }:
                self._add(values.get("content"))
        elif tag.lower() in {"a", "link", "iframe", "embed"}:
            target = values.get("href") or values.get("src")
            hint = " ".join((target or "", values.get("type", ""),
                             values.get("title", ""), values.get("class", ""))).lower()
            if any(marker in hint for marker in ("pdf", "download", "article-file")):
                self._add(target)


def discover_pdf_links(html_text: str, base_url: str) -> list[str]:
    """Extract publisher-declared PDF links from a landing page."""
    if not html_text:
        return []
    parser = _PDFLinkParser(base_url)
    try:
        parser.feed(html_text)
    except Exception:
        return parser.urls
    return parser.urls


def _source_url_candidates(route: PublisherRoute, source_url: str) -> list[str]:
    if not source_url:
        return []
    parsed = urlparse(source_url)
    path = parsed.path
    candidates: list[str] = []

    if route.key == "elsevier":
        match = re.search(r"/pii/([A-Z0-9]+)", path, re.I)
        if match:
            candidates.append(f"https://www.sciencedirect.com/science/article/pii/{match.group(1)}/pdfft")
    elif route.key == "ieee":
        match = re.search(r"/document/(\d+)", path)
        if match:
            candidates.append(
                f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={match.group(1)}")
    elif route.key == "aps" and "/abstract/" in path:
        candidates.append(source_url.replace("/abstract/", "/pdf/", 1))
    elif route.key == "wiley" and "/doi/" in path:
        candidates.append(re.sub(r"/doi/(?:abs|full|epdf)/", "/doi/pdfdirect/", source_url))
    elif route.key == "nature" and "/articles/" in path and not path.endswith(".pdf"):
        candidates.append(source_url.rstrip("/") + ".pdf")
    elif route.key == "springer" and "/article/" in path:
        candidates.append("https://link.springer.com/content/pdf/" + path.split("/article/", 1)[1] + ".pdf")
    elif route.key in {"acs", "aip", "acm", "sage", "taylor-francis", "science",
                       "pnas", "annual-reviews", "royal-society", "oxford",
                       "world-scientific"} and "/doi/" in path:
        candidates.append(re.sub(r"/doi/(?:abs|full|epdf)/", "/doi/pdf/", source_url))
    return candidates


def build_pdf_candidates(rec: dict, source_url: str = "", html_text: str = "") -> list[str]:
    """Build deduplicated official PDF candidates for one article."""
    route = route_for_record(rec, source_url)
    if route is None:
        return discover_pdf_links(html_text, source_url) if source_url else []
    doi = str(rec.get("doi") or "").strip().lower()
    suffix = doi.split("/", 1)[1] if "/" in doi else doi
    values = {"doi": doi, "doi_q": quote(doi, safe=""), "suffix": suffix}
    candidates: list[str] = []

    if doi:
        for template in route.pdf_templates:
            candidates.append(template.format(**values))
        if route.key == "plos":
            match = re.search(r"journal\.([a-z]+)\.", doi)
            if match and match.group(1) in _PLOS_JOURNALS:
                journal = _PLOS_JOURNALS[match.group(1)]
                candidates.append(
                    f"https://journals.plos.org/{journal}/article/file?id={quote(doi, safe='')}&type=printable")
        elif route.key == "copernicus":
            match = re.match(r"10\.5194/([a-z0-9-]+)-(\d+)-(\d+)-(\d{4})", doi)
            if match:
                journal, volume, first_page, year = match.groups()
                candidates.append(
                    f"https://{journal}.copernicus.org/articles/{volume}/{first_page}/{year}/{journal}-{volume}-{first_page}-{year}.pdf")

    candidates.extend(_source_url_candidates(route, source_url))
    candidates.extend(discover_pdf_links(html_text, source_url))
    result: list[str] = []
    for url in candidates:
        if not url or SUPPLEMENT_RE.search(url) or url in result:
            continue
        result.append(url)
    return result


def official_domains(route: PublisherRoute | None) -> tuple[str, ...]:
    return route.domains if route else ()
