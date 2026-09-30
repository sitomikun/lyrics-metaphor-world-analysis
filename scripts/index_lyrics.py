#!/usr/bin/env python3
"""Index UTF-8 lyrics without guessing songs, speakers, or meaning.

The JSON line ledger preserves every character and CR/LF/CRLF ending. Restore
source bytes as (BOM if utf8_bom) + ''.join(text + line_ending).encode('utf-8').
An explicit split-pattern matches the whole line, excluding its line ending.
A matching separator starts a new block; it is retained as boundary metadata,
not included in that block's section bodies. Adjacent separators retain empty
blocks. A pre-boundary block exists only when there are preceding lines.
Standalone [tag] lines mark textual sections, not verified musical forms.
All source line numbers are one-based; an empty body's range is null.
An optional reviewed song map adds posting-order song IDs and per-song lyric
line numbers, excluding standalone tags but including whitespace-only lines. Raw source
coordinates remain unchanged and are never used as display lyric coordinates.
"""

import argparse
import codecs
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


SCHEMA_VERSION = 3
LINE_RE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|$)")
TAG_RE = re.compile(r"[^\S\r\n]*(\[[^\[\]\r\n]+\])[^\S\r\n]*")


def line_ledger(text):
    """Split only CRLF, CR, and LF; preserve other Unicode separators as text."""
    result = []
    for match in LINE_RE.finditer(text):
        if match.start() == match.end():
            continue
        result.append({
            "source_line": len(result) + 1,
            "text": match.group(1),
            "line_ending": match.group(2),
        })
    return result


def line_range(lines):
    return [lines[0]["source_line"], lines[-1]["source_line"]] if lines else None


def body_record(lines):
    return {
        "body_line_range": line_range(lines),
        "body_text": "".join(line["text"] + line["line_ending"] for line in lines),
    }


def make_block(block_id, boundary, lines):
    """Retain a preamble (including empty) and every tag occurrence."""
    tag_positions = []
    for offset, line in enumerate(lines):
        match = TAG_RE.fullmatch(line["text"])
        if match and match.group(1)[1:-1].strip():
            tag_positions.append((offset, match.group(1)))

    preamble_end = tag_positions[0][0] if tag_positions else len(lines)
    preamble = {
        "section_id": block_id + "-P000",
        "kind": "preamble",
        **body_record(lines[:preamble_end]),
    }
    sections = []
    for index, (offset, raw_tag) in enumerate(tag_positions):
        end = tag_positions[index + 1][0] if index + 1 < len(tag_positions) else len(lines)
        sections.append({
            "section_id": f"{block_id}-S{index + 1:03d}",
            "kind": "tagged",
            "original_tag": raw_tag,
            "tag_source_line": lines[offset]["source_line"],
            "tag_line_text": lines[offset]["text"],
            **body_record(lines[offset + 1:end]),
        })
    covered = ([boundary] if boundary is not None else []) + lines
    return {
        "block_id": block_id,
        "boundary": boundary,
        "source_line_range": line_range(covered),
        "content_line_range": line_range(lines),
        "preamble": preamble,
        "sections": sections,
    }


def split_blocks(input_id, lines, split_pattern):
    chunks = []
    boundary = None
    current = []
    for line in lines:
        if split_pattern is not None and split_pattern.fullmatch(line["text"]):
            if boundary is not None or current:
                chunks.append((boundary, current))
            boundary, current = line, []
        else:
            current.append(line)
    # Empty files and an empty final block are both represented explicitly.
    chunks.append((boundary, current))
    return [
        make_block(f"{input_id}-B{index:03d}", separator, content)
        for index, (separator, content) in enumerate(chunks, start=1)
    ]


def exact_repetitions(blocks):
    """Group bodies containing non-whitespace by exact characters AND endings."""
    groups = {}
    for block in blocks:
        for section in [block["preamble"], *block["sections"]]:
            body = section["body_text"]
            if body.strip():
                groups.setdefault(body, []).append({
                    "block_id": block["block_id"],
                    "section_id": section["section_id"],
                    "body_line_range": section["body_line_range"],
                })
    return [
        {"body_text": body, "occurrences": occurrences}
        for body, occurrences in groups.items() if len(occurrences) > 1
    ]


def index_file(path, input_id, split_pattern=None):
    source = Path(path).resolve(strict=True)
    raw = source.read_bytes()
    # utf-8-sig removes one leading UTF-8 BOM and rejects malformed UTF-8.
    text = raw.decode("utf-8-sig", errors="strict")
    lines = line_ledger(text)
    blocks = split_blocks(input_id, lines, split_pattern)
    return {
        "input_id": input_id,
        "source_path": str(source),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
        "encoding": "UTF-8",
        "utf8_bom": raw.startswith(codecs.BOM_UTF8),
        "line_count": len(lines),
        "line_ending_counts": {
            "CRLF": sum(line["line_ending"] == "\r\n" for line in lines),
            "LF": sum(line["line_ending"] == "\n" for line in lines),
            "CR": sum(line["line_ending"] == "\r" for line in lines),
            "none": sum(line["line_ending"] == "" for line in lines),
        },
        "ends_with_newline": bool(lines and lines[-1]["line_ending"]),
        "lines": lines,
        "blocks": blocks,
        "exact_body_repetitions": exact_repetitions(blocks),
    }


def number_songs(inputs, song_map):
    """Number reviewed lyric spans in input order, without inferring metadata.

    Each map entry supplies input_id and inclusive source_line_range, optionally
    title. Spans include real section tags and all blank lines in the adopted
    lyric body, but exclude song headers/credits and inter-song separators.
    Blank preamble before the first tag is outside numbering; actual untagged
    lyrics before that tag are retained. Original Song numbers are not reused.
    """
    if not isinstance(song_map, dict) or set(song_map) != {"songs"}:
        raise ValueError('Song map must be an object with only a "songs" list')
    specs = song_map["songs"]
    if not isinstance(specs, list) or not specs:
        raise ValueError('Song map "songs" must be a non-empty list')
    by_id = {item["input_id"]: (index, item) for index, item in enumerate(inputs)}
    previous_order, previous_end = -1, 0
    songs = []
    for ordinal, spec in enumerate(specs, start=1):
        if not isinstance(spec, dict) or set(spec) - {"input_id", "source_line_range", "title"}:
            raise ValueError(f"Invalid fields in song map entry {ordinal}")
        input_id = spec.get("input_id")
        if not isinstance(input_id, str) or input_id not in by_id:
            raise ValueError(f"Unknown input_id in song map entry {ordinal}")
        order, source = by_id[input_id]
        span = spec.get("source_line_range")
        if (not isinstance(span, list) or len(span) != 2
                or any(type(n) is not int for n in span)
                or not (1 <= span[0] <= span[1] <= source["line_count"])):
            raise ValueError(f"Invalid source_line_range in song map entry {ordinal}")
        if order < previous_order or (order == previous_order and span[0] <= previous_end):
            raise ValueError("Song map spans must be non-overlapping and in input/line order")
        if "title" in spec and not isinstance(spec["title"], str):
            raise ValueError(f"Song title must be a string in entry {ordinal}")
        previous_order, previous_end = order, span[1]
        song_id = f"S{ordinal}"
        block = make_block(song_id, None, source["lines"][span[0] - 1:span[1]])
        raw_sections = list(block["sections"])
        if block["preamble"]["body_text"].strip() or not raw_sections:
            raw_sections.insert(0, block["preamble"])
        lyric_lines, sections = [], []
        for section_number, section in enumerate(raw_sections, start=1):
            section_id = f"{song_id}-SEC{section_number:02d}"
            start_count = len(lyric_lines)
            body_span = section["body_line_range"]
            body_lines = source["lines"][body_span[0] - 1:body_span[1]] if body_span else []
            for line in body_lines:
                lyric_lines.append({
                    "lyric_line": len(lyric_lines) + 1,
                    "section_id": section_id,
                    "is_blank": not bool(line["text"].strip()),
                    **line,
                })
            end_count = len(lyric_lines)
            content_count = sum(not row["is_blank"] for row in lyric_lines[start_count:end_count])
            sections.append({
                "section_id": section_id,
                "kind": section["kind"],
                "original_tag": section.get("original_tag"),
                "tag_source_line": section.get("tag_source_line"),
                "source_body_line_range": body_span,
                "lyric_line_range": [start_count + 1, end_count] if end_count > start_count else None,
                "lyric_line_count": end_count - start_count,
                "content_line_count": content_count,
                "blank_line_count": end_count - start_count - content_count,
                "has_lyrics": content_count > 0,
                # Position anchors are finalized once the whole song is counted.
                "after_lyric_line": start_count or None,
                "before_lyric_line": end_count + 1,
            })
        for section in sections:
            if section["before_lyric_line"] > len(lyric_lines):
                section["before_lyric_line"] = None
        songs.append({
            "song_number": ordinal,
            "song_id": song_id,
            "song_label": f"[Song {ordinal}]",
            "short_label": f"[{song_id}]",
            "title": spec.get("title"),
            "input_id": input_id,
            "source_line_range": span,
            "lyric_line_count": len(lyric_lines),
            "content_line_count": sum(not row["is_blank"] for row in lyric_lines),
            "blank_line_count": sum(row["is_blank"] for row in lyric_lines),
            "has_lyrics": any(not row["is_blank"] for row in lyric_lines),
            "lines": lyric_lines,
            "sections": sections,
        })
    return songs


def ensure_output_is_not_input(output, inputs):
    output_resolved = output.resolve()
    for source in inputs:
        if output_resolved == source.resolve():
            raise ValueError(f"Output must not overwrite an input: {output}")
        if output.exists() and os.path.samefile(output, source):
            raise ValueError(f"Output is a hard link to an input: {output}")
    if output.is_symlink():
        raise ValueError(f"Output must not be a symbolic link: {output}")


def write_json(output, result, inputs):
    """Write only after all inputs succeeded; replace completed output atomically."""
    ensure_output_is_not_input(output, inputs)
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=".lyrics-index-",
            suffix=".tmp", dir=output.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        ensure_output_is_not_input(output, inputs)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Index UTF-8 lyrics, retaining original lines, endings, and tag occurrences.",
        epilog=(
            "No song titles, speakers, metaphors, or musical forms are inferred. "
            "Only standalone [tag] lines are recognized as section markers. "
            "Existing JSON output may be replaced; input files are never overwritten."
        ),
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="One or more UTF-8 (optional BOM) text files")
    parser.add_argument("--output", required=True, type=Path, help="JSON path in an existing directory")
    parser.add_argument(
        "--split-pattern", metavar="REGEX",
        help="Explicit block separator regex, fullmatched against each line without its newline",
    )
    parser.add_argument(
        "--song-map", type=Path,
        help="Reviewed JSON song spans; adds posting-order songs and per-song lyric coordinates",
    )
    args = parser.parse_args(argv)
    try:
        split_pattern = re.compile(args.split_pattern) if args.split_pattern is not None else None
        protected_inputs = [*args.inputs, *([args.song_map] if args.song_map else [])]
        ensure_output_is_not_input(args.output, protected_inputs)
        result = {
            "schema_version": SCHEMA_VERSION,
            "split_pattern": args.split_pattern,
            "section_marker_rule": "Standalone [tag] line; a textual marker, not a verified musical form.",
            "repetition_rule": "Complete bodies containing non-whitespace; exact Unicode text and CR/LF endings; within each input only.",
            "inputs": [
                index_file(path, f"F{index:03d}", split_pattern)
                for index, path in enumerate(args.inputs, start=1)
            ],
        }
        result["songs"] = []
        result["song_numbering_status"] = "unassigned: supply a reviewed --song-map"
        if args.song_map:
            song_map = json.loads(args.song_map.read_text(encoding="utf-8-sig"))
            result["songs"] = number_songs(result["inputs"], song_map)
            result["song_numbering_status"] = "assigned from reviewed song spans"
        result["lyric_numbering_rule"] = (
            "Songs follow input/line order within one posting, regardless of original IDs; "
            "lyric lines reset per song and exclude standalone tags but include whitespace-only lines. "
            "Numbering starts immediately below the first tag, unless actual lyrics precede it; "
            "without tags, count every line in the reviewed body span. "
            "A final newline terminates a line and does not fabricate an extra blank line. "
            "A numbered blank line is not lyric content; use is_blank and content_line_count. "
            "Display song_label or short_label with lyric_line; source_line is for lookup only."
        )
        write_json(args.output, result, protected_inputs)
    except (OSError, UnicodeError, ValueError, re.error) as error:
        parser.exit(2, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
