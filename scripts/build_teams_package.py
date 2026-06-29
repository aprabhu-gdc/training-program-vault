"""Build an installable Microsoft Teams app package for the Graydaze bot.

Substitutes the manifest placeholders (`__TEAMS_APP_ID__`, `__BOT_APP_ID__`,
`__BOT_HOSTNAME__`) with real values and zips the manifest together with the icon
assets into `dist/teams_app.zip`, ready to sideload via Teams "Upload a custom
app" or the Developer Portal.

Placeholder icons are generated automatically if missing (no third-party
dependency required); replace them with real brand art before a wider rollout.

Usage:
    python -m scripts.build_teams_package \
        --bot-app-id <ENTRA_APP_CLIENT_ID> \
        --host <app>.azurewebsites.net \
        [--teams-app-id <GUID>] [--out dist/teams_app.zip]

`--teams-app-id` defaults to a deterministic GUID derived from the bot id so
repeat builds are stable; pass your own to override. Values may also be supplied
via the env vars TEAMS_APP_ID / BOT_APP_ID / BOT_HOSTNAME.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import uuid
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEAMS_APP_DIR = REPO_ROOT / "teams_app"
MANIFEST_PATH = TEAMS_APP_DIR / "manifest.json"
COLOR_ICON = TEAMS_APP_DIR / "color.png"
OUTLINE_ICON = TEAMS_APP_DIR / "outline.png"

ACCENT = (244, 180, 0, 255)        # Graydaze #F4B400
ACCENT_BORDER = (180, 120, 0, 255)
WHITE = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)


def _write_png(path: Path, width: int, height: int, pixel) -> None:
    """Write an 8-bit RGBA PNG using only the stdlib (struct + zlib)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (none) per scanline
        for x in range(width):
            raw += bytes(pixel(x, y))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def ensure_placeholder_icons() -> None:
    """Create placeholder icons if they are missing. Replaceable with brand art."""

    if not COLOR_ICON.exists():
        def color_pixel(x: int, y: int):
            border = 12
            edge = x < border or y < border or x >= 192 - border or y >= 192 - border
            return ACCENT_BORDER if edge else ACCENT
        _write_png(COLOR_ICON, 192, 192, color_pixel)

    if not OUTLINE_ICON.exists():
        # Teams outline icons are a transparent background with a single-color mark.
        def outline_pixel(x: int, y: int):
            border = 3
            edge = x < border or y < border or x >= 32 - border or y >= 32 - border
            return WHITE if edge else TRANSPARENT
        _write_png(OUTLINE_ICON, 32, 32, outline_pixel)


def _default_teams_app_id(bot_app_id: str) -> str:
    # Deterministic GUID so repeat builds produce the same package id.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"graydaze-pm-training-vault:{bot_app_id}"))


def build_package(*, bot_app_id: str, host: str, teams_app_id: str, out_path: Path) -> Path:
    host = host.strip().replace("https://", "").replace("http://", "").strip("/")
    if not bot_app_id:
        raise SystemExit("error: --bot-app-id (or BOT_APP_ID) is required.")
    if not host:
        raise SystemExit("error: --host (or BOT_HOSTNAME) is required.")

    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    manifest_text = (
        manifest_text.replace("__TEAMS_APP_ID__", teams_app_id)
        .replace("__BOT_APP_ID__", bot_app_id)
        .replace("__BOT_HOSTNAME__", host)
    )

    if "__" in manifest_text:
        leftovers = sorted({tok for tok in manifest_text.split('"') if tok.startswith("__") and tok.endswith("__")})
        raise SystemExit(f"error: unresolved manifest placeholders remain: {leftovers}")

    # Validate the result is well-formed JSON before packaging.
    manifest = json.loads(manifest_text)

    ensure_placeholder_icons()
    for icon in (COLOR_ICON, OUTLINE_ICON):
        if not icon.exists():
            raise SystemExit(f"error: missing icon {icon}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.write(COLOR_ICON, "color.png")
        archive.write(OUTLINE_ICON, "outline.png")

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Teams app package (.zip).")
    parser.add_argument("--bot-app-id", default=os.getenv("BOT_APP_ID", ""), help="Entra app (client) ID used by the Azure Bot.")
    parser.add_argument("--host", default=os.getenv("BOT_HOSTNAME", ""), help="Public bot hostname, e.g. myapp.azurewebsites.net.")
    parser.add_argument("--teams-app-id", default=os.getenv("TEAMS_APP_ID", ""), help="Teams app package GUID (defaults to a stable derived GUID).")
    parser.add_argument("--out", default=str(REPO_ROOT / "dist" / "teams_app.zip"), help="Output zip path.")
    args = parser.parse_args()

    teams_app_id = args.teams_app_id or _default_teams_app_id(args.bot_app_id)
    out = build_package(
        bot_app_id=args.bot_app_id,
        host=args.host,
        teams_app_id=teams_app_id,
        out_path=Path(args.out),
    )
    print(f"Built Teams package: {out}")
    print(f"  teams_app_id = {teams_app_id}")
    print(f"  bot_app_id   = {args.bot_app_id}")
    print(f"  host         = {args.host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
