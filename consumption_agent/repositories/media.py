from __future__ import annotations

import os


def save_media_asset(conn, data: bytes, *, mime: str = "image/jpeg", base_dir: str | None = None) -> int | None:
    import memory_lane

    return memory_lane.save_media(conn, data, mime=mime, base_dir=base_dir)


def link_item_photo(conn, *, item_id: int, media_asset_id: int, is_primary: bool = True) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO item_photos (item_id, media_asset_id, is_primary) VALUES (?, ?, ?)",
        (item_id, media_asset_id, 1 if is_primary else 0),
    )


def get_item_photo_path(conn, item_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT ma.file_path
        FROM item_photos ip
        JOIN media_assets ma ON ip.media_asset_id = ma.id
        WHERE ip.item_id = ? LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    return row[0] if row else None


def unlink_item_photos(conn, item_id: int) -> None:
    conn.execute("DELETE FROM item_photos WHERE item_id = ?", (item_id,))


def delete_media_asset(conn, media_asset_id: int) -> None:
    row = conn.execute("SELECT file_path FROM media_assets WHERE id = ?", (media_asset_id,)).fetchone()
    conn.execute("DELETE FROM media_assets WHERE id = ?", (media_asset_id,))
    if row and os.path.exists(row[0]):
        try:
            os.remove(row[0])
        except Exception:
            pass
