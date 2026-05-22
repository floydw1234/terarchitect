"""Notes domain helpers."""


def split_note_link_ids(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def join_note_link_ids(values):
    ids = split_note_link_ids(values)
    if not ids:
        return None
    # Preserve order while de-duplicating.
    return ",".join(dict.fromkeys(ids))


def note_to_json(n):
    return {
        "id": str(n.id),
        "project_id": str(n.project_id),
        "node_ids": split_note_link_ids(n.node_id),
        "edge_ids": split_note_link_ids(n.edge_id),
        "title": n.title,
        "content": n.content,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }
