from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path, to_project_path
from ..progress import ProgressReporter
from ..storage import atomic_write_json, read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_event


DEFAULT_MAX_SHOTS_PER_CALL = 20


def _int_config(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _chapter_write_config(config: dict[str, Any]) -> dict[str, int]:
    raw = config.get("chapter_write")
    if not isinstance(raw, dict):
        llm = config.get("llm", {})
        raw = llm.get("chapter_write") if isinstance(llm, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    return {"max_shots_per_call": max(1, _int_config(raw.get("max_shots_per_call"), DEFAULT_MAX_SHOTS_PER_CALL))}


def _dedupe_strings(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = re.sub(r"\s+", " ", str(item)).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _frame_candidates(cards: list[dict[str, Any]], referenced_shots: list[str]) -> list[str]:
    by_id = {str(card.get("shot_id")): card for card in cards if card.get("shot_id")}
    referenced_set = set(referenced_shots)
    ordered_cards = [by_id[shot_id] for shot_id in referenced_shots if shot_id in by_id]
    ordered_cards.extend(card for card in cards if str(card.get("shot_id")) not in referenced_set)

    frames: list[str] = []
    for card in ordered_cards:
        recommended = str(card.get("recommended_display_frame") or "").strip()
        if recommended:
            frames.append(recommended)
        for frame in list(card.get("frames") or []):
            frame_text = str(frame or "").strip()
            if frame_text:
                frames.append(frame_text)
    return _dedupe_strings(frames)


def _select_representative_frame(cards: list[dict[str, Any]], referenced_shots: list[str], preferred: list[str]) -> str | None:
    candidates = _frame_candidates(cards, referenced_shots)
    candidate_set = set(candidates)
    for frame in preferred:
        if frame and frame in candidate_set:
            return frame
    return candidates[0] if candidates else None


def _ordered_refs(refs: list[str], order: list[str]) -> list[str]:
    order_index = {shot_id: index for index, shot_id in enumerate(order)}
    return sorted(_dedupe_strings(refs), key=lambda shot_id: (order_index.get(shot_id, len(order_index)), shot_id))


def _strip_duplicate_subsection_heading(body: str, title: str) -> str:
    lines = body.strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    first = lines[0].strip()
    normalized = re.sub(r"^#{1,6}\s*", "", first).strip()
    if normalized == title:
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _subsection_units(chapter: dict[str, Any], cards_by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    units: list[dict[str, Any]] = []
    chapter_shot_ids = [str(shot_id) for shot_id in list(chapter.get("shot_ids") or [])]
    chapter_shot_set = set(chapter_shot_ids)
    seen_ids: set[str] = set()

    for index, subsection in enumerate(list(chapter.get("subsections") or []), start=1):
        raw_id = str(subsection.get("subsection_id") or f"{chapter.get('chapter_id')}_sub_{index:03d}").strip()
        subsection_id = raw_id
        if subsection_id in seen_ids:
            warnings.append(f"duplicate_subsection_id_renamed:{subsection_id}")
            subsection_id = f"{subsection_id}_dup_{index:03d}"
        seen_ids.add(subsection_id)

        raw_shot_ids = [str(shot_id) for shot_id in list(subsection.get("shot_ids") or [])]
        valid_shot_ids = [shot_id for shot_id in raw_shot_ids if shot_id in chapter_shot_set and shot_id in cards_by_id]
        invalid = [shot_id for shot_id in raw_shot_ids if shot_id not in chapter_shot_set or shot_id not in cards_by_id]
        if invalid:
            warnings.append(f"{subsection_id}:invalid_subsection_shots_skipped:{','.join(_dedupe_strings(invalid))}")

        unit = {
            **subsection,
            "subsection_id": subsection_id,
            "title": str(subsection.get("title") or subsection_id),
            "shot_ids": valid_shot_ids,
        }
        representative = str(unit.get("representative_shot_id") or "")
        if representative and representative not in valid_shot_ids:
            unit.pop("representative_shot_id", None)
        units.append(unit)

    return units, warnings


def _batch_subsection_units(units: list[dict[str, Any]], max_shots_per_call: int) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_shots = 0

    for unit in units:
        shot_count = len(list(unit.get("shot_ids") or []))
        if shot_count > max_shots_per_call:
            if current:
                batches.append(current)
                current = []
                current_shots = 0
            batches.append([unit])
            continue
        if current and current_shots + shot_count > max_shots_per_call:
            batches.append(current)
            current = []
            current_shots = 0
        current.append(unit)
        current_shots += shot_count

    if current:
        batches.append(current)
    return batches


def _cards_for_units(units: list[dict[str, Any]], cards_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    shot_ids = _dedupe_strings([str(shot_id) for unit in units for shot_id in list(unit.get("shot_ids") or [])])
    return [cards_by_id[shot_id] for shot_id in shot_ids if shot_id in cards_by_id]


def _append_subsection_outputs(
    outputs_by_id: dict[str, dict[str, Any]],
    result: dict[str, Any],
    expected_units: list[dict[str, Any]],
) -> list[str]:
    expected_ids = {str(unit.get("subsection_id")) for unit in expected_units}
    warnings: list[str] = []
    for item in list(result.get("subsections") or []):
        subsection_id = str(item.get("subsection_id") or "")
        if subsection_id not in expected_ids:
            warnings.append(f"unexpected_subsection_output_ignored:{subsection_id}")
            continue
        outputs_by_id[subsection_id] = item
    missing = sorted(expected_ids - set(outputs_by_id))
    if missing:
        raise ValueError(f"missing subsection outputs after parse: {', '.join(missing)}")
    return warnings


def _assemble_subsection_chapter(
    chapter: dict[str, Any],
    units: list[dict[str, Any]],
    outputs_by_id: dict[str, dict[str, Any]],
    chapter_cards: list[dict[str, Any]],
    global_summary: dict[str, Any],
    warnings: list[str],
    batch_records: list[dict[str, Any]],
) -> dict[str, Any]:
    body_parts: list[str] = []
    key_points: list[str] = []
    referenced_shots: list[str] = []
    preferred_frames: list[str] = []
    chapter_shot_order = [str(shot_id) for shot_id in list(chapter.get("shot_ids") or [])]

    for unit in units:
        subsection_id = str(unit.get("subsection_id"))
        title = str(unit.get("title") or subsection_id)
        output = outputs_by_id.get(subsection_id)
        if not output:
            raise ValueError(f"missing subsection output: {subsection_id}")
        body = _strip_duplicate_subsection_heading(str(output.get("body_markdown") or ""), title)
        if not body:
            raise ValueError(f"empty subsection body: {subsection_id}")
        body_parts.append(f"## {title}\n\n{body}")
        key_points.extend(str(item) for item in list(output.get("key_points") or []) if str(item).strip())
        referenced_shots.extend(str(item) for item in list(output.get("referenced_shots") or []) if str(item).strip())
        frame = str(output.get("representative_frame") or "").strip()
        if frame:
            preferred_frames.append(frame)
        for warning in list(output.get("warnings") or []):
            warnings.append(f"{subsection_id}:{warning}")

    ordered_references = _ordered_refs(referenced_shots, chapter_shot_order)
    representative_frame = _select_representative_frame(chapter_cards, ordered_references, preferred_frames)
    fallback_point = str(chapter.get("summary") or chapter.get("title") or "").strip()
    return {
        "chapter_id": str(chapter.get("chapter_id")),
        "title": str(chapter.get("title") or chapter.get("chapter_id")),
        "representative_frame": representative_frame,
        "body_markdown": "\n\n".join(body_parts),
        "key_points": _dedupe_strings(key_points) or ([fallback_point] if fallback_point else []),
        "referenced_shots": ordered_references,
        "warnings": _dedupe_strings(warnings),
        "global_theme": global_summary.get("video_main_theme"),
        "model_output_format": "tagged_chapter_subsection_batches_v1",
        "subsection_count": len(units),
        "write_batch_count": len(batch_records),
        "write_batches": batch_records,
    }


def _write_chapter_from_subsections(
    adapter: LocalModelAdapter,
    chapter: dict[str, Any],
    chapter_cards: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
    global_summary: dict[str, Any],
    *,
    max_shots_per_call: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    units, warnings = _subsection_units(chapter, cards_by_id)
    if not units:
        output = adapter.write_chapter(chapter, chapter_cards, global_summary)
        output["warnings"] = _dedupe_strings([*list(output.get("warnings") or []), "subsections_empty_fallback_whole_chapter"])
        return output, []

    batches = _batch_subsection_units(units, max_shots_per_call)
    outputs_by_id: dict[str, dict[str, Any]] = {}
    batch_records: list[dict[str, Any]] = []

    for batch_index, batch in enumerate(batches, start=1):
        subsection_ids = [str(unit.get("subsection_id")) for unit in batch]
        shot_count = sum(len(list(unit.get("shot_ids") or [])) for unit in batch)
        oversized = [str(unit.get("subsection_id")) for unit in batch if len(list(unit.get("shot_ids") or [])) > max_shots_per_call]
        if oversized:
            warnings.append(f"oversized_subsection:{','.join(oversized)}")

        record = {
            "chapter_id": chapter.get("chapter_id"),
            "batch_id": f"{chapter.get('chapter_id')}_batch_{batch_index:03d}",
            "subsection_ids": subsection_ids,
            "shot_count": shot_count,
            "max_shots_per_call": max_shots_per_call,
            "oversized_subsection_ids": oversized,
            "status": "pending",
        }
        try:
            result = adapter.write_chapter_subsections(chapter, batch, _cards_for_units(batch, cards_by_id), global_summary)
            record["status"] = "done"
            record["warnings"] = list(result.get("warnings") or [])
            warnings.extend(str(item) for item in list(result.get("warnings") or []))
            warnings.extend(_append_subsection_outputs(outputs_by_id, result, batch))
            batch_records.append(record)
            continue
        except Exception as exc:
            record["status"] = "retry_single"
            record["error"] = str(exc)
            batch_records.append(record)
            if len(batch) == 1:
                raise

        for unit in batch:
            subsection_id = str(unit.get("subsection_id"))
            shot_count = len(list(unit.get("shot_ids") or []))
            single_record = {
                "chapter_id": chapter.get("chapter_id"),
                "batch_id": f"{chapter.get('chapter_id')}_{subsection_id}_retry",
                "subsection_ids": [subsection_id],
                "shot_count": shot_count,
                "max_shots_per_call": max_shots_per_call,
                "oversized_subsection_ids": [subsection_id] if shot_count > max_shots_per_call else [],
                "status": "pending",
                "retry_of": record["batch_id"],
            }
            try:
                result = adapter.write_chapter_subsections(chapter, [unit], _cards_for_units([unit], cards_by_id), global_summary)
                single_record["status"] = "done"
                single_record["warnings"] = list(result.get("warnings") or [])
                warnings.extend(str(item) for item in list(result.get("warnings") or []))
                warnings.extend(_append_subsection_outputs(outputs_by_id, result, [unit]))
            except Exception as exc:
                single_record["status"] = "failed"
                single_record["error"] = str(exc)
                batch_records.append(single_record)
                raise
            batch_records.append(single_record)

    return _assemble_subsection_chapter(chapter, units, outputs_by_id, chapter_cards, global_summary, warnings, batch_records), batch_records


def run_chapter_write(project_dir: str | Path, *, chapter_id: str | None = None) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "09_chapter_write")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    write_config = _chapter_write_config(config)
    outline = read_json(find_stage_artifact(project_path, "08_outline_plan", "outline.json"))
    cards = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    global_summary = read_json(find_stage_artifact(project_path, "07_summary_reduce", "global_summary.json"))
    cards_by_id = {str(card.get("shot_id")): card for card in cards if card.get("shot_id")}
    progress = ProgressReporter("09_chapter_write")
    planned_chapters = [
        chapter
        for chapter in outline.get("chapters", [])
        if not chapter_id or chapter.get("chapter_id") == chapter_id
    ]
    progress.start("准备生成章节", f"chapters={len(planned_chapters)}")
    adapter = LocalModelAdapter(project_path, config, model_role="copywriting", progress=progress)
    chapters = []
    subsection_batch_records: list[dict[str, Any]] = []
    total = len(planned_chapters)

    for index, chapter in enumerate(planned_chapters, start=1):
        current_chapter_id = str(chapter.get("chapter_id") or f"chapter_{index:03d}")
        chapter_cards = [cards_by_id[str(shot_id)] for shot_id in chapter.get("shot_ids", []) if str(shot_id) in cards_by_id]
        progress.item_start(
            index,
            total,
            "生成章节",
            current_chapter_id,
            f"title={chapter.get('title')}; shots={len(chapter_cards)}",
        )
        if chapter.get("subsections"):
            output, chapter_batch_records = _write_chapter_from_subsections(
                adapter,
                chapter,
                chapter_cards,
                cards_by_id,
                global_summary,
                max_shots_per_call=int(write_config["max_shots_per_call"]),
            )
            subsection_batch_records.extend(chapter_batch_records)
        else:
            output = adapter.write_chapter(chapter, chapter_cards, global_summary)

        chapter_path = stage_dir / f"{output['chapter_id']}.json"
        atomic_write_json(chapter_path, output)
        chapters.append(
            {
                "chapter_id": output["chapter_id"],
                "title": output["title"],
                "path": to_project_path(project_path, chapter_path),
                "referenced_shots": output.get("referenced_shots", []),
            }
        )
        progress.item_done(index, total, "完成章节", current_chapter_id, f"title={output.get('title')}")

    existing_index = stage_dir / "chapters_index.json"
    if chapter_id and existing_index.exists():
        index_payload = read_json(existing_index)
        by_id = {item["chapter_id"]: item for item in index_payload.get("chapters", [])}
        for item in chapters:
            by_id[item["chapter_id"]] = item
        chapters = [by_id[key] for key in sorted(by_id)]

    atomic_write_json(stage_dir / "chapters_index.json", {"chapters": chapters})

    batch_record_path = stage_dir / "subsection_write_batches.jsonl"
    if chapter_id and batch_record_path.exists():
        existing_records = [item for item in read_jsonl(batch_record_path) if str(item.get("chapter_id")) != str(chapter_id)]
        subsection_batch_records = [*existing_records, *subsection_batch_records]
    write_jsonl(batch_record_path, subsection_batch_records)

    log_event(project_path, "chapter_write_done", chapter_count=len(chapters), chapter_id=chapter_id)
    progress.done("章节生成完成", f"chapters={len(chapters)}")
    outputs = [
        stage_relative_path("09_chapter_write", "chapters_index.json"),
        stage_relative_path("09_chapter_write", "subsection_write_batches.jsonl"),
        *[item["path"] for item in chapters],
    ]
    return {
        "outputs": outputs,
        "chapter_count": len(chapters),
        "subsection_batch_count": len(subsection_batch_records),
        "chapter_write_config": write_config,
        "model_signature": model_signature(config, "copywriting"),
    }
