from __future__ import annotations

import ipaddress
import re
from urllib.parse import SplitResult, urlsplit


_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_TRACKING_PARAMETERS = frozenset(
    {
        "gclid",
        "dclid",
        "fbclid",
        "msclkid",
        "irclickid",
        "irgwc",
        "irpid",
        "sharedid",
        "afsrc",
    }
)
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".home", ".lan")
_STORE_RULES = (
    (
        "steam",
        frozenset({"store.steampowered.com"}),
        re.compile(r"/app/(\d+)(?:/[A-Za-z0-9_.%~-]+)?/?"),
    ),
    (
        "gog",
        frozenset({"gog.com", "www.gog.com"}),
        re.compile(r"/(?:[a-z]{2}/)?game/([a-z0-9_]+)/?"),
    ),
    (
        "humble",
        frozenset({"humblebundle.com", "www.humblebundle.com"}),
        re.compile(r"/store/([a-z0-9-]+)/?"),
    ),
    (
        "battlenet",
        frozenset({"shop.battle.net", "us.shop.battle.net", "eu.shop.battle.net"}),
        re.compile(r"/[a-z]{2}-[a-z]{2}/product/([a-z0-9]+(?:-[a-z0-9]+)*)/?"),
    ),
    (
        "psn",
        frozenset({"store.playstation.com"}),
        re.compile(
            r"/(?:[a-z]{2}-[a-z]{2}/)?(?:product/([A-Za-z0-9._-]+)|concept/(\d+))/?",
            re.IGNORECASE,
        ),
    ),
    (
        "xbox",
        frozenset({"xbox.com", "www.xbox.com"}),
        re.compile(
            r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:games/store/([a-z0-9-]+)(?:/([A-Za-z0-9]{12}))?|play/games/[a-z0-9-]+/([A-Za-z0-9]{12})|games/([a-z0-9-]+))/?",
            re.IGNORECASE,
        ),
    ),
    (
        "switch",
        frozenset({"nintendo.com", "www.nintendo.com"}),
        re.compile(
            r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?store/products/([a-z0-9-]+)/?",
            re.IGNORECASE,
        ),
    ),
    (
        "appstore",
        frozenset({"apps.apple.com"}),
        re.compile(
            r"/[a-z]{2}/app/(?:[a-z0-9._%~-]+/)?(id\d+)/?",
            re.IGNORECASE,
        ),
    ),
    (
        "googleplay",
        frozenset({"play.google.com"}),
        re.compile(r"/store/apps/details/?"),
    ),
)
# Browsing category for each store. "pc", "console", and "mobile" say which
# site page a listing belongs on. They do not describe operating systems:
# Steam sells Windows, Mac, and Linux builds and is still "pc"; an App Store
# listing means the iOS build. The site imports this table so both
# repositories agree on it.
STORE_PLATFORM = {
    "steam": "pc",
    "gog": "pc",
    "humble": "pc",
    "battlenet": "pc",
    "psn": "console",
    "xbox": "console",
    "switch": "console",
    "appstore": "mobile",
    "googleplay": "mobile",
}
_PLAY_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


def platform_for_store(store: str) -> str:
    """Return the browsing category for a supported store id."""
    return STORE_PLATFORM[store]


def _google_play_package(query: str) -> str | None:
    """The package name when the query is exactly one ``id`` parameter."""
    name, separator, value = query.partition("=")
    if separator != "=" or name != "id" or "&" in value:
        return None
    return value if _PLAY_PACKAGE.fullmatch(value) else None


_HTML = re.compile(r"(?:<\s*/?\s*[A-Za-z][^>]*>|<!--[\s\S]*?-->)")
_EXECUTABLE_EXPRESSION = re.compile(
    r"(?:\$\{\{[\s\S]*?\}\}|\{\{[\s\S]*?\}\}|<%[\s\S]*?%>|\$\{[^}]*\}|\$\([^)]*\))"
)
_GITHUB_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?")
# GitHub allows a repository name to end in a hyphen, underscore, or dot,
# unlike an owner name; only "." and ".." are reserved.
_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+")
_NEXUS_MOD_PAGE = re.compile(r"/[A-Za-z0-9_-]+/mods/[0-9]+/?")
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_IPV4_NUMBER_COMPONENT = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)")


def _split_https_url(url: str) -> tuple[SplitResult | None, str | None]:
    if not isinstance(url, str) or not url or url != url.strip():
        return None, "URL must be a non-empty string without surrounding whitespace"
    if len(url) > 2048:
        return None, "URL must not exceed 2048 characters"
    if "\\" in url or _ENCODED_SEPARATOR.search(url):
        return None, "URL must not contain raw or encoded path separators"
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in url
    ):
        return None, "URL must not contain whitespace or control characters"
    if _BAD_PERCENT_ESCAPE.search(url):
        return None, "URL contains an invalid percent escape"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return None, "URL authority is malformed"
    if parsed.scheme != "https" or not parsed.netloc or host is None:
        return None, "URL must be absolute HTTPS"
    if parsed.username is not None or parsed.password is not None:
        return None, "URL credentials are forbidden"
    if "#" in url:
        return None, "URL fragments are forbidden"
    if port not in (None, 443):
        return None, "URL non-default ports are forbidden"
    if host.endswith("."):
        return None, "URL host must not have a trailing dot"
    host = host.casefold()
    if "." not in host:
        return None, "URL host must contain more than one label"
    if host == "localhost" or host.endswith(".localhost") or host.endswith(_PRIVATE_HOST_SUFFIXES):
        return None, "URL private-style hosts are forbidden"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None, "URL IP-literal hosts are forbidden"
    if all(_IPV4_NUMBER_COMPONENT.fullmatch(label) for label in host.split(".")):
        return None, "URL IP-literal hosts are forbidden"
    if len(host) > 253 or any(_DNS_LABEL.fullmatch(label) is None for label in host.split(".")):
        return None, "URL host is not a canonical DNS name"
    return parsed, None


def url_policy_error(url: str, *, allow_nexus_description: bool = False) -> str | None:
    """Return a stable reason when an external URL violates registry policy."""
    parsed, error = _split_https_url(url)
    if error is not None:
        return error
    assert parsed is not None
    if "?" not in url:
        return None
    if not parsed.query:
        return "URL query parameters are not allowed for this field"
    query_names = [part.partition("=")[0].casefold() for part in parsed.query.split("&")]
    if any(name.startswith("utm_") or name in _TRACKING_PARAMETERS for name in query_names):
        return "URL tracking and affiliate parameters are forbidden"
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        allow_nexus_description
        and host in {"nexusmods.com", "www.nexusmods.com"}
        and _NEXUS_MOD_PAGE.fullmatch(parsed.path)
        and parsed.query == "tab=description"
    ):
        return None
    if host == "play.google.com" and parsed.path.rstrip("/") == "/store/apps/details":
        if _google_play_package(parsed.query) is not None:
            return None
    return "URL query parameters are not allowed for this field"


def classify_store_url(url: str) -> tuple[str, str] | None:
    """Return the exact supported store identity for one canonical HTTPS URL."""
    parsed, error = _split_https_url(url)
    if error is not None or parsed is None:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if "?" in url and host != "play.google.com":
        return None
    for store, hosts, pattern in _STORE_RULES:
        if host not in hosts:
            continue
        match = pattern.fullmatch(parsed.path)
        if match is None:
            return None
        if store == "psn":
            return store, match.group(1) or f"concept/{match.group(2)}"
        if store == "xbox":
            key = match.group(2) or match.group(3) or match.group(1) or match.group(4)
            return None if key.casefold() in {"games", "store"} else (store, key)
        if store == "googleplay":
            package = _google_play_package(parsed.query)
            return None if package is None else (store, package)
        if store == "appstore":
            return store, match.group(1).lower()
        return store, match.group(1)
    return None


def wiki_rating_url_error(url: str) -> str | None:
    error = url_policy_error(url)
    if error is not None:
        return error
    parsed = urlsplit(url)
    if (
        parsed.hostname != "accessiblegaming.wiki"
        or not parsed.path.startswith("/AccessibilityRating/")
    ):
        return "wiki rating URL must use https://accessiblegaming.wiki/AccessibilityRating/"
    return None


def github_repository_url_error(url: str) -> str | None:
    error = url_policy_error(url)
    if error is not None:
        return error
    parsed = urlsplit(url)
    segments = parsed.path.split("/")
    if (
        parsed.hostname != "github.com"
        or len(segments) != 3
        or not _GITHUB_OWNER.fullmatch(segments[1])
        or not _GITHUB_REPOSITORY.fullmatch(segments[2])
        or segments[2] in {".", ".."}
        or segments[2].casefold().endswith(".git")
    ):
        return "release source must be a canonical GitHub repository URL"
    return None


_NEXUS_MOD_PATH = re.compile(r"/[a-z0-9]+/mods/[0-9]+")


def nexus_mod_url_error(url: str) -> str | None:
    error = url_policy_error(url, allow_nexus_description=True)
    if error is not None:
        return error
    parsed = urlsplit(url)
    if (
        parsed.hostname not in {"nexusmods.com", "www.nexusmods.com"}
        or parsed.query
        or parsed.fragment
        or not _NEXUS_MOD_PATH.fullmatch(parsed.path)
    ):
        return "release source must be a bare Nexus Mods mod page URL"
    return None


def release_source_url_error(kind: str, url: str) -> str | None:
    """Policy error for a release source URL, given its declared kind."""
    if kind == "nexus":
        return nexus_mod_url_error(url)
    return github_repository_url_error(url)


def text_policy_error(value: str) -> str | None:
    if _HTML.search(value):
        return "display text must be plain text without raw HTML"
    if _EXECUTABLE_EXPRESSION.search(value):
        return "display text must not contain executable expressions"
    return None
