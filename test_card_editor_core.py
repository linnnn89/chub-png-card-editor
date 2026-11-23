import base64
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chub_card_editor import (
    PngChunk,
    _make_text_chunk,
    load_card,
    normalize_for_editing,
    read_png_chunks,
    save_card_to_png,
    write_png_chunks,
)


def make_card(name: str, description: str) -> dict:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": description,
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "creator_notes": "",
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [],
            "tags": [],
            "creator": "",
            "character_version": "",
            "extensions": {},
        },
    }


def encode_card(card: dict) -> str:
    return base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8")).decode("ascii")


def make_png(path: Path, *metadata: PngChunk) -> None:
    chunks = [
        PngChunk(
            b"IHDR",
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00",
        ),
        *metadata,
        PngChunk(b"IDAT", zlib.compress(b"\x00\x0c\x22\x38\xff")),
        PngChunk(b"IEND", b""),
    ]
    write_png_chunks(path, chunks)


class CardEditorCoreTests(unittest.TestCase):
    def test_load_and_save_chinese_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            png_path = Path(tmp) / "card.png"
            card = make_card("测试角色", "中文描述 and English text")
            make_png(png_path, _make_text_chunk("chara", encode_card(card)))

            loaded = load_card(png_path)
            self.assertEqual(loaded.chunk_keyword, "chara")
            self.assertEqual(loaded.card["data"]["name"], "测试角色")

            edited = normalize_for_editing(loaded.card)
            edited["data"]["scenario"] = "补充场景"
            save_card_to_png(png_path, png_path, edited, loaded.chunk_keyword)

            reloaded = load_card(png_path)
            self.assertEqual(reloaded.card["data"]["scenario"], "补充场景")

    def test_ccv3_takes_priority_over_chara(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            png_path = Path(tmp) / "card.png"
            v2 = make_card("old", "old")
            v3 = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "new", "description": "v3"}}
            make_png(
                png_path,
                _make_text_chunk("chara", encode_card(v2)),
                _make_text_chunk("ccv3", encode_card(v3)),
            )

            loaded = load_card(png_path)
            self.assertEqual(loaded.chunk_keyword, "ccv3")
            self.assertEqual(loaded.card["data"]["name"], "new")

    def test_save_as_preserves_unrelated_metadata_and_unhandled_itxt_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "source.png", Path(tmp) / "edited.png"
            card = make_card("星澄", "海边的 café")
            editable = _make_text_chunk("chara", encode_card(card))
            author = PngChunk(b"zTXt", b"Author\0\0" + zlib.compress("André".encode("latin-1")))
            caption = PngChunk(b"iTXt", b"Caption\0\0\0en\0\0" + "海辺".encode())
            # Valid uncompressed iTXt with a card keyword, currently unhandled.
            v3 = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "原稿"}}
            preserved = PngChunk(b"iTXt", b"ccv3\0\0\0en\0\0" + encode_card(v3).encode("ascii"))
            make_png(source, author, editable, caption, preserved, _make_text_chunk("Comment", "café"))
            original = source.read_bytes()
            before = read_png_chunks(source)

            with patch("chub_card_editor.zlib.decompress", side_effect=AssertionError("decoded unrelated zTXt")):
                loaded = load_card(source)
                self.assertEqual((loaded.chunk_keyword, loaded.card), ("chara", card))
                loaded.card["data"]["description"] = "另存后的描述"
                save_card_to_png(source, target, loaded.card, loaded.chunk_keyword)
                self.assertEqual(load_card(target).card, loaded.card)

            after = read_png_chunks(target)
            self.assertEqual(source.read_bytes(), original)
            # read_png_chunks checks each CRC; compare all preserved kinds and payloads in order.
            self.assertEqual(after[:-2], [chunk for chunk in before[:-1] if chunk != editable])
            self.assertEqual(after[-2].kind, b"tEXt")
            self.assertTrue(after[-2].data.startswith(b"chara\0"))
            self.assertEqual(after[-1], before[-1])

    def test_compressed_card_keeps_priority_and_is_replaced_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "source.png", Path(tmp) / "edited.png"
            v2 = _make_text_chunk("chara", encode_card(make_card("旧卡", "")))
            v3 = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "新卡"}}
            compressed = PngChunk(b"zTXt", b"ccv3\0\0" + zlib.compress(encode_card(v3).encode("ascii")))
            make_png(source, compressed, v2)
            loaded = load_card(source)
            self.assertEqual((loaded.chunk_keyword, loaded.card), ("ccv3", v3))
            loaded.card["data"]["name"] = "已编辑"
            save_card_to_png(source, target, loaded.card, loaded.chunk_keyword)
            chunks = read_png_chunks(target)
            self.assertEqual([c.kind for c in chunks], [b"IHDR", b"IDAT", b"tEXt", b"IEND"])
            reloaded = load_card(target)
            self.assertEqual((reloaded.chunk_keyword, reloaded.card), ("ccv3", loaded.card))


if __name__ == "__main__":
    unittest.main()
