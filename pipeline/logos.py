"""Team logos for the site. ESPN logo-pack images are public; custom uploads need a logged-in
session, so those are downloaded once (with the league cookies) and published with the site."""
from espn_client import ESPNClient, ROOT

LOGO_DIR = ROOT / "site" / "img" / "logos"
PRIVATE_HOST = "mystique-api.fantasy.espn.com"
EXT = {"image/jpg": "jpg", "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg"}

_client = None


def localize(url):
    """Public logo -> unchanged URL. Custom upload -> site-relative path of a local copy (None if it can't be fetched)."""
    global _client
    if not url or PRIVATE_HOST not in url:
        return url
    key = url.rstrip("/").rsplit("/", 1)[-1]
    existing = next(LOGO_DIR.glob(f"{key}.*"), None) if LOGO_DIR.exists() else None
    if existing:
        return f"img/logos/{existing.name}"
    try:
        _client = _client or ESPNClient()
        r = _client.session.get(url, timeout=30)
        ext = EXT.get((r.headers.get("content-type") or "").split(";")[0].strip())
        if r.status_code != 200 or not ext:
            return None
        LOGO_DIR.mkdir(parents=True, exist_ok=True)
        (LOGO_DIR / f"{key}.{ext}").write_bytes(r.content)
        return f"img/logos/{key}.{ext}"
    except Exception:
        return None
