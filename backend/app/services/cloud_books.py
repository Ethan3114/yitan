from itertools import batched

from app.services.draft import reader_fact


def owned(repository, table: str, owner: str | None, identifier: str):
    if not owner:
        return None
    return repository.one(table, filters={"id": "eq." + identifier, "owner_id": "eq." + owner})


def chapter_views(repository, owner: str, call_id: str) -> list[dict]:
    chapters = list(repository.rows("chapters", filters={"owner_id": "eq." + owner, "call_id": "eq." + call_id}, order="created_at.asc,id.asc"))
    if not chapters:
        return []
    facts = list(repository.rows("fact_items", filters={"owner_id": "eq." + owner, "call_id": "eq." + call_id}, order="id.asc"))
    memories = list(repository.rows("memory_items", filters={"owner_id": "eq." + owner, "call_id": "eq." + call_id}, order="id.asc"))
    all_paragraphs = []
    for group in batched(chapters, 50):
        chapter_ids = ",".join(item["id"] for item in group)
        all_paragraphs.extend(repository.rows("paragraphs", filters={"owner_id": "eq." + owner, "chapter_id": "in.(" + chapter_ids + ")"}, order="chapter_id.asc,position.asc"))
    references = {}
    for group in batched(all_paragraphs, 50):
        paragraph_ids = ",".join(item["id"] for item in group)
        for reference in repository.rows("paragraph_evidence", filters={"owner_id": "eq." + owner, "paragraph_id": "in.(" + paragraph_ids + ")"}, order="source_line.asc,id.asc"):
            references.setdefault(reference["paragraph_id"], []).append({"line": reference["source_line"], "quote": reference["quote"]})
    paragraphs_by_chapter = {}
    for paragraph in all_paragraphs:
        paragraph["source_quotes"] = references.get(paragraph["id"], [])
        paragraphs_by_chapter.setdefault(paragraph["chapter_id"], []).append(paragraph)
    result = []
    for index, chapter in enumerate(chapters):
        paragraphs = paragraphs_by_chapter.get(chapter["id"], [])
        fact_views = []
        for fact in facts:
            topic, detail = reader_fact(fact["topic"], fact["detail"])
            fact_views.append({"id": fact["id"], "topic": topic, "detail": detail, "status": fact["status"]})
        result.append({"id": chapter["id"], "title": chapter["title"], "paragraphs": paragraphs,
                       "facts": fact_views if index == 0 else [],
                       "memories": [{"kind": item["kind"], "name": item["name"], "detail": item["detail"]} for item in memories] if index == 0 else []})
    return result


def call_view(repository, owner: str, call: dict) -> dict:
    chapters = chapter_views(repository, owner, call["id"])
    tasks = repository.request("GET", "yitan_draft_tasks", params={"owner_id": "eq." + owner, "call_id": "eq." + call["id"], "order": "created_at.desc,id.desc", "limit": "1"})
    task = tasks[0] if tasks else None
    kinds = [item["kind"] for item in repository.rows("audio_assets", filters={"owner_id": "eq." + owner, "call_id": "eq." + call["id"]}, columns="kind", order="kind.asc")]
    original = repository.one("private_files", filters={"owner_id": "eq." + owner, "call_id": "eq." + call["id"], "role": "eq.original_audio"}, columns="id")
    if original and "user" not in kinds:
        kinds.append("user")
    return {"id": call["id"], "status": call["status"], "failure_code": call["failure_code"],
            "texts": {item["track"]: item["text"] for item in repository.rows("transcripts", filters={"owner_id": "eq." + owner, "call_id": "eq." + call["id"]}, order="track.asc")},
            "chapter": chapters[0] if chapters else None, "chapters": chapters, "created_at": call["created_at"], "audio_kinds": kinds, "task": task}
