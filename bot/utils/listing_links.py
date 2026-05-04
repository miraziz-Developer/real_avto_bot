"""Kanaldagi e'lon postiga havola (ochiq @username yoki t.me/c/...)."""


def channel_post_url(*, channel_id: str, message_id: int, public_username: str | None) -> str | None:
    un = (public_username or "").strip().lstrip("@")
    if un:
        return f"https://t.me/{un}/{message_id}"
    raw = str(channel_id).strip()
    try:
        cid = int(raw)
    except ValueError:
        return None
    s = str(cid)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{message_id}"
    return None
