import argparse
import hashlib
import json
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description="核实已选成稿及关联资料，生成私有迁移清单，不上传或调用模型")
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--book-key", required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    product = args.product.resolve(strict=True)
    data = (product / "data").resolve(strict=True)

    def data_file(relative: str | Path) -> Path:
        path = (product / relative).resolve(strict=True)
        if not path.is_relative_to(data) or not path.is_file():
            raise ValueError("迁移输入必须位于产品 data 目录")
        return path

    showcase = read_json(data / "showcase-books.json")
    matches = [book for book in showcase if book["key"] == args.book_key]
    if len(matches) != 1:
        raise ValueError("必须唯一匹配一本已展示的回忆录")
    selected = matches[0]
    book_path = data_file(selected["source"])
    book = read_json(book_path)
    result_path = data_file(book_path.parent / "result.json")
    result = read_json(result_path)
    transcript = data_file(args.transcript)
    audio = data_file(args.audio)
    if result["status"] != "completed":
        raise ValueError("选定写作任务尚未完成")
    if digest(transcript) != result["source_sha256"]:
        raise ValueError("转写原文与成稿记录的来源校验值不符")
    chapters = [
        {"title": chapter["title"], "paragraphs": [paragraph["text"] for paragraph in chapter["paragraphs"]]}
        for chapter in book["chapters"]
    ]
    if chapters != selected["chapters"]:
        raise ValueError("展示正文与选定成稿不一致")
    characters = sum(len(text) for chapter in chapters for text in chapter["paragraphs"])
    if characters != result["body_characters"] or characters != selected["characters"]:
        raise ValueError("正文字数与写作结果不一致")
    if len(chapters) != result["chapters"] or not chapters:
        raise ValueError("章节数量不一致")
    if audio.stat().st_size == 0:
        raise ValueError("原始录音为空")
    files = []
    for role, path in [("memoir", book_path), ("generation_receipt", result_path), ("transcript", transcript), ("original_audio", audio)]:
        files.append({"role": role, "path": str(path.relative_to(product)), "bytes": path.stat().st_size, "sha256": digest(path)})
    manifest = {
        "schema_version": 1,
        "status": "verified_local_sources",
        "visibility": "family_private",
        "destination_owner_id": None,
        "uploaded": False,
        "book_key": selected["key"],
        "author": selected["author"],
        "title": selected["title"],
        "model": result["model"],
        "chapter_count": len(chapters),
        "paragraph_count": sum(len(chapter["paragraphs"]) for chapter in chapters),
        "body_characters": characters,
        "chapter_titles": [chapter["title"] for chapter in chapters],
        "files": files,
        "validation": {"source_hash_matches": True, "showcase_matches_complete_book": True, "counts_match": True},
    }
    # 清单包含私人资料位置，仅允许写入已忽略的数据目录。
    output = args.output.resolve()
    if not output.is_relative_to(data):
        raise ValueError("迁移清单必须保存在产品 data 目录")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.chmod(0o600)
    if read_json(output) != manifest:
        raise ValueError("清单写入验证失败")
    print(json.dumps({"verified": True, "chapters": len(chapters), "body_characters": characters, "files": len(files), "bytes": sum(item["bytes"] for item in files), "uploaded": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
