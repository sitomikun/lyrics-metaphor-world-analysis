"""Observable numbering and source-preservation checks; standard library only."""

import codecs
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "index_lyrics.py"
SPEC = importlib.util.spec_from_file_location("index_lyrics", MODULE_PATH)
indexer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(indexer)


class NumberingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, text, name="lyrics.txt", input_id="F001"):
        path = self.root / name
        path.write_bytes(text.encode("utf-8"))
        return path, indexer.index_file(path, input_id)

    def number(self, indexed, spans):
        return indexer.number_songs(indexed, {"songs": spans})

    def span(self, first, last, input_id="F001", title=None):
        result = {"input_id": input_id, "source_line_range": [first, last]}
        if title is not None:
            result["title"] = title
        return result

    def test_blank_lines_count_but_headers_and_tags_do_not(self):
        path, indexed = self.source(
            '\ufeff[Song 8]\r\n[作者「例」歌詞]\r\n\r\n[Verse 1]\r\n'
            '一行目\r\n二行目\r\n \t\r\n[Pre-Chorus]\r\n三行目\r\n四行目\r\n'
            '[Interlude]\r\n　\r\n[Chorus]\r\n五行目\r\n六行目\r\n七行目'
        )
        song = self.number([indexed], [self.span(4, 16)])[0]
        self.assertEqual(song["song_label"], "[Song 1]")
        self.assertEqual(song["short_label"], "[S1]")
        self.assertEqual(song["lyric_line_count"], 9)
        self.assertEqual((song["content_line_count"], song["blank_line_count"]), (7, 2))
        self.assertEqual([r["lyric_line"] for r in song["lines"]], list(range(1, 10)))
        self.assertEqual([r["source_line"] for r in song["lines"]], [5, 6, 7, 9, 10, 12, 14, 15, 16])
        self.assertEqual([s["lyric_line_range"] for s in song["sections"]], [[1, 3], [4, 5], [6, 6], [7, 9]])
        empty = song["sections"][2]
        self.assertEqual((empty["lyric_line_count"], empty["after_lyric_line"], empty["before_lyric_line"]), (1, 5, 7))
        self.assertEqual((empty["content_line_count"], empty["blank_line_count"], empty["has_lyrics"]), (0, 1, False))
        self.assertEqual([r["text"] for r in song["lines"] if r["is_blank"]], [" \t", "　"])
        restored = codecs.BOM_UTF8 + "".join(r["text"] + r["line_ending"] for r in indexed["lines"]).encode("utf-8")
        self.assertEqual(restored, path.read_bytes())

    def test_posting_order_across_files_and_original_numbers(self):
        _, first = self.source('[Song 91]\n[Verse]\n同じ歌詞\n[Song 4]\n[Verse]\n同じ歌詞\n')
        _, second = self.source('[Song 800]\n[Verse]\n別の歌詞\n', "second.txt", "F002")
        songs = self.number([first, second], [self.span(2, 3, title="同名"), self.span(5, 6, title="同名"), self.span(2, 3, "F002")])
        self.assertEqual([s["song_id"] for s in songs], ["S1", "S2", "S3"])
        self.assertEqual([s["lines"][0]["lyric_line"] for s in songs], [1, 1, 1])
        self.assertEqual(songs[1]["sections"][0]["section_id"], "S2-SEC01")

    def test_untagged_prefix_and_tail_preserve_mixed_endings(self):
        path, indexed = self.source('先頭\r[Verse]\r\n次\n[Outro]\n最後')
        song = self.number([indexed], [self.span(1, 5)])[0]
        self.assertEqual([r["text"] for r in song["lines"]], ["先頭", "次", "最後"])
        self.assertEqual([s["lyric_line_range"] for s in song["sections"]], [[1, 1], [2, 2], [3, 3]])
        self.assertEqual("".join(r["text"] + r["line_ending"] for r in indexed["lines"]).encode("utf-8"), path.read_bytes())

    def test_fully_untagged_text(self):
        _, indexed = self.source('\n一\n　\n二\n')
        song = self.number([indexed], [self.span(1, 4)])[0]
        self.assertEqual(song["lyric_line_count"], 4)
        self.assertEqual((song["content_line_count"], song["blank_line_count"]), (2, 2))
        self.assertEqual(song["sections"][0]["lyric_line_range"], [1, 4])

    def test_leading_trailing_and_consecutive_empty_sections(self):
        _, indexed = self.source('[Intro]\n[Verse]\n本文\n[Interlude]\n[Outro]\n')
        song = self.number([indexed], [self.span(1, 5)])[0]
        self.assertEqual([s["lyric_line_count"] for s in song["sections"]], [0, 1, 0, 0])
        self.assertEqual([(s["after_lyric_line"], s["before_lyric_line"]) for s in song["sections"]], [(None, 1), (None, None), (1, None), (1, None)])

    def test_blank_only_sections_have_numbers_but_no_lyrics(self):
        _, indexed = self.source('[Intro]\n\n[Interlude]\n\t\n')
        song = self.number([indexed], [self.span(1, 4)])[0]
        self.assertEqual(song["lyric_line_count"], 2)
        self.assertFalse(song["has_lyrics"])
        self.assertEqual(song["content_line_count"], 0)
        self.assertEqual([s["lyric_line_range"] for s in song["sections"]], [[1, 1], [2, 2]])
        self.assertTrue(all(not s["has_lyrics"] for s in song["sections"]))

    def test_immediate_blank_below_first_tag_is_line_one(self):
        _, indexed = self.source('\n[Verse]\n\n本文\n')
        song = self.number([indexed], [self.span(1, 4)])[0]
        self.assertEqual([(r["lyric_line"], r["source_line"], r["is_blank"]) for r in song["lines"]], [(1, 3, True), (2, 4, False)])

    def test_terminal_newline_is_not_an_extra_blank_line(self):
        for text, expected_count in [('[Verse]\n本文\n', 1), ('[Verse]\n本文\n\n', 2), ('[Verse]\n本文\n\n\n', 3)]:
            with self.subTest(text=text):
                _, indexed = self.source(text)
                song = self.number([indexed], [self.span(1, indexed["line_count"])])[0]
                self.assertEqual(song["lyric_line_count"], expected_count)
                self.assertEqual(song["content_line_count"], 1)
                self.assertEqual(song["blank_line_count"], expected_count - 1)

    def test_full_section_layout_keeps_requested_positions(self):
        layout = [('[Verse 1]', 2), ('[Pre-Chorus]', 2), ('[Chorus]', 3),
                  ('[Verse 2]', 2), ('[Pre-Chorus]', 2), ('[Chorus]', 4),
                  ('[Instrumental Interlude]', 0), ('[Bridge]', 2), ('[Chorus]', 5)]
        lines = ['[Song 8]', '[作者「架空例」歌詞]', '']
        word_line = 0
        for tag, count in layout:
            lines.append(tag)
            for _ in range(count):
                word_line += 1
                lines.append(f'語句のある行{word_line}')
            lines.append('')
        _, indexed = self.source('\n'.join(lines) + '\n')
        song = self.number([indexed], [self.span(4, indexed['line_count'])])[0]
        positions = {r['text']: r['lyric_line'] for r in song['lines'] if not r['is_blank']}
        self.assertEqual([positions[f'語句のある行{n}'] for n in [1, 3, 7, 22]], [1, 4, 9, 30])
        self.assertEqual(song['sections'][6]['lyric_line_range'], [22, 22])
        self.assertFalse(song['sections'][6]['has_lyrics'])
        self.assertEqual((song['lyric_line_count'], song['content_line_count'], song['blank_line_count']), (31, 22, 9))

    def test_inline_tag_is_a_lyric_line_and_repeated_lines_are_counted(self):
        _, indexed = self.source('[Verse] 本文\n\t　[Chorus]　\t\n同じ\n同じ\n')
        song = self.number([indexed], [self.span(1, 4)])[0]
        self.assertEqual([r["lyric_line"] for r in song["lines"]], [1, 2, 3])
        self.assertEqual(song["lines"][0]["text"], "[Verse] 本文")

    def test_bad_or_overlapping_ranges_are_rejected(self):
        _, indexed = self.source('[Verse]\n一\n[Chorus]\n二\n')
        bad_maps = [
            [self.span(0, 2)], [self.span(1, 5)], [self.span(3, 2)], [self.span(True, 2)],
            [self.span(1, 3), self.span(3, 4)], [self.span(3, 4), self.span(1, 2)],
            [self.span(1, 2, "F009")], [{"input_id": "F001", "source_line_range": [1, 2], "song_number": 8}],
        ]
        for spans in bad_maps:
            with self.subTest(spans=spans), self.assertRaises(ValueError):
                self.number([indexed], spans)

    def test_reversed_file_order_is_rejected(self):
        _, first = self.source('一')
        _, second = self.source('二', "second.txt", "F002")
        with self.assertRaises(ValueError):
            self.number([first, second], [self.span(1, 1, "F002"), self.span(1, 1)])

    def test_cli_map_and_unassigned_mode(self):
        path, _ = self.source('[Song 8]\n[Verse]\n一\n二\n')
        song_map = self.root / "map.json"
        song_map.write_text(json.dumps({"songs": [self.span(2, 4)]}), encoding="utf-8")
        output = self.root / "index.json"
        self.assertEqual(indexer.main([str(path), "--song-map", str(song_map), "--output", str(output)]), 0)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["songs"][0]["lyric_line_count"], 2)
        self.assertEqual(indexer.main([str(path), "--output", str(output)]), 0)
        raw_only = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(raw_only["songs"], [])
        self.assertIn("unassigned", raw_only["song_numbering_status"])

    def test_map_and_lyrics_cannot_be_overwritten(self):
        path, _ = self.source('[Verse]\n一\n')
        song_map = self.root / "map.json"
        song_map.write_text(json.dumps({"songs": [self.span(1, 2)]}), encoding="utf-8")
        before = {p: p.read_bytes() for p in [path, song_map]}
        for destination in [path, song_map]:
            with self.subTest(destination=destination), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                indexer.main([str(path), "--song-map", str(song_map), "--output", str(destination)])
            self.assertEqual(caught.exception.code, 2)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items()))

    def test_invalid_map_does_not_replace_existing_output(self):
        path, _ = self.source('[Verse]\n一\n')
        song_map = self.root / "map.json"
        song_map.write_text('{"songs": [{"input_id": "F001", "source_line_range": [1, 99]}]}', encoding="utf-8")
        output = self.root / "index.json"
        output.write_bytes(b"previous output")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            indexer.main([str(path), "--song-map", str(song_map), "--output", str(output)])
        self.assertEqual(output.read_bytes(), b"previous output")


if __name__ == "__main__":
    unittest.main()
