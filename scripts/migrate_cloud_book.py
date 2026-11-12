import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloudbase import CloudRepository


def main():
    manifest_path = ROOT / "data/migration/li/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {}
    for item in manifest["files"]:
        path = (ROOT / item["path"]).resolve(strict=True)
        if not path.is_relative_to(ROOT / "data"):
            raise ValueError("资料必须来自忆谈 data 目录")
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("来源资料已发生变化，必须重新核实")
        files[item["role"]] = path
    book = json.loads(files["memoir"].read_text(encoding="utf-8"))
    transcript = files["transcript"].read_text(encoding="utf-8")
    owner = json.loads((ROOT / ".intermediate/private/cloud-admin.json").read_text())["owner_id"]
    source_hash = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
    import_id = "li-" + source_hash[:60]
    values = dotenv_values(ROOT / "backend/.env")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    try:
        account = repository.one("accounts", filters={"owner_id": "eq." + owner, "role": "eq.admin", "enabled": "eq.true"}, columns="id")
        if account is None:
            raise ValueError("迁移所属管理员不存在")
        receipt = repository.rpc("import_book", {
            "p_import_id": import_id, "p_owner_id": owner,
            "p_profile_id": str(uuid4()), "p_call_id": str(uuid4()),
            "p_name": manifest["author"], "p_title": manifest["title"],
            "p_source_sha256": source_hash, "p_book": book, "p_transcript": transcript,
        })
        profile_id = receipt["profile_id"]
        if receipt["source_book"] != book:
            raise AssertionError("云端完整成稿与来源不符")
        call = repository.one("calls", filters={"profile_id": "eq." + profile_id, "owner_id": "eq." + owner})
        saved_transcript = repository.one("transcripts", filters={"call_id": "eq." + call["id"], "owner_id": "eq." + owner, "track": "eq.full"})
        if saved_transcript["text"] != transcript:
            raise AssertionError("云端转写原文与来源不符")
        chapters = list(repository.rows("chapters", filters={"call_id": "eq." + call["id"], "owner_id": "eq." + owner}, order="created_at.asc,id.asc"))
        if len(chapters) != manifest["chapter_count"]:
            raise AssertionError("云端章节数量不符")
        characters = 0
        for chapter, original in zip(chapters, book["chapters"], strict=True):
            paragraphs = list(repository.rows("paragraphs", filters={"chapter_id": "eq." + chapter["id"], "owner_id": "eq." + owner}, order="position.asc"))
            if chapter["title"] != original["title"] or len(paragraphs) != len(original["paragraphs"]):
                raise AssertionError("云端章节顺序或段落数量不符")
            for paragraph, source in zip(paragraphs, original["paragraphs"], strict=True):
                if paragraph["text"] != source["text"] or paragraph["source_quote"] != source["source_quote"]:
                    raise AssertionError("云端正文或引用不符")
                evidence = list(repository.rows("paragraph_evidence", filters={"paragraph_id": "eq." + paragraph["id"], "owner_id": "eq." + owner}, columns="source_line,quote", order="source_line.asc,quote.asc"))
                expected = [{"source_line": item["line"], "quote": item["quote"]} for item in source["evidence"]]
                if sorted(evidence, key=lambda item: (item["source_line"], item["quote"])) != sorted(expected, key=lambda item: (item["source_line"], item["quote"])):
                    raise AssertionError("云端证据引用不符")
                characters += len(paragraph["text"])
        if characters != manifest["body_characters"]:
            raise AssertionError("云端正文字数不符")
        files_verified = manifest.get("cloud_import_id") == import_id and manifest.get("cloud_files_verified") is True
        manifest.update({
            "status": "cloud_sources_verified" if files_verified else "structured_book_and_transcript_verified_cloud",
            "destination_owner_id": owner, "uploaded": files_verified,
            "cloud_import_id": import_id, "cloud_profile_id": profile_id, "cloud_call_id": call["id"],
            "cloud_database_verified": True, "cloud_files_verified": files_verified,
            "pending": [] if files_verified else ["original_audio", "generation_receipt_file", "source_book_file"],
        })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_path.chmod(0o600)
        print(json.dumps({"chapters": len(chapters), "body_characters": characters, "transcript_exact": True, "evidence_exact": True, "complete_migration": files_verified, "audio_uploaded": files_verified}, ensure_ascii=False))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
