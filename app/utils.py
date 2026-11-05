import ipaddress
import re
import uuid
from urllib.parse import urlparse

from werkzeug.utils import secure_filename

SLUG_RE = re.compile(r"[^a-z0-9]+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_URL_LENGTH = 500
MAX_PAGE = 10_000
MAX_SEARCH_TERMS = 6
MAX_SEARCH_LENGTH = 100
IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
)


def new_id():
    return str(uuid.uuid4())


def slugify(value):
    value = (value or "").strip().lower()
    value = SLUG_RE.sub("-", value).strip("-")
    return value[:80] or "item"


def is_safe_http_url(value):
    """True only for public-looking http(s) URLs.

    Rejects other schemes (javascript:, data:, file:), embedded credentials,
    whitespace/control characters, over-long values, and localhost/private
    addresses (so a future server-side link checker cannot be pointed inward).
    """
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) > MAX_URL_LENGTH or CONTROL_RE.search(value) or " " in value:
        return False
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        parsed.port  # raises ValueError for a malformed port
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    if parsed.username or parsed.password:
        return False
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return "." in lowered  # a real hostname needs at least one dot
    return address.is_global


def normalise_url(value):
    """Canonical form used for duplicate detection."""
    value = (value or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}"


def allowed_image(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in {"png", "jpg", "jpeg", "webp", "gif"}


def looks_like_image(stream):
    """Check the first bytes of an upload, not only its file name."""
    head = stream.read(16)
    stream.seek(0)
    if head.startswith(IMAGE_SIGNATURES):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def safe_upload_name(filename):
    name = secure_filename(filename or "")
    if not name:
        return None
    return f"{new_id()}_{name}"


def split_lines(value):
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def join_lines(values):
    if not values:
        return ""
    if isinstance(values, str):
        return values
    return "\n".join(values)


def parse_page(value, default=1):
    """Turn a ?page= value into a safe positive integer. Never raises."""
    try:
        page = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return min(max(page, 1), MAX_PAGE)


def clean_text(value, limit=None):
    """Strip control characters and surrounding whitespace."""
    text = CONTROL_RE.sub(" ", value or "").strip()
    return text[:limit] if limit else text


def search_terms(query):
    """Split a search box value into at most a few plain terms."""
    text = clean_text(query, MAX_SEARCH_LENGTH).lower()
    return [term for term in text.split() if term][:MAX_SEARCH_TERMS]


def escape_like(term):
    """Escape SQL LIKE wildcards so '%' and '_' are matched literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def spreadsheet_safe(value):
    """Prevent CSV/Excel formula injection when exporting."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_bool_or_none(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "free")
