from __future__ import annotations

import re
from math import ceil
from pathlib import Path
from typing import Any

from ..models import LocalModelAdapter
from ..models.adapter import model_signature
from ..paths import find_stage_artifact, project_stage_dir, stage_relative_path
from ..storage import atomic_write_json, read_json, read_jsonl, write_jsonl
from ..utils.eventlog import log_event


PLACEHOLDER_SECTIONS = {"visual", "Visual timeline", "内容提取", "知识点整理", "重点回顾", "总结", "内容概览"}
GENERIC_TITLES = {*PLACEHOLDER_SECTIONS, "Part", "Overview", "Summary", "章节", "小节"}
DEFAULT_SUBSECTION_CONFIG = {
    "enabled": True,
    "min_shots_for_model": 8,
    "min_need_score": 5,
    "max_subsections_per_chapter": 5,
    "min_shots_per_subsection": 2,
    "min_coverage_ratio": 0.6,
}
DEFAULT_OUTLINE_PLANNER_CONFIG = {
    "enabled": True,
    "prefer_section_sources": True,
    "call_model_when_ambiguous": True,
    "force_model": False,
    "max_chapters": 8,
    "min_shots_per_chapter": 2,
    "min_coverage_ratio": 0.95,
}
DEFAULT_STRUCTURE_QA_CONFIG = {
    "enabled": True,
    "risk_threshold": "high",
    "max_rewrite_chapters": 8,
    "apply_agent_suggestions": False,
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe_strings(items: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_config(config: dict[str, Any], key: str) -> int:
    return int(DEFAULT_SUBSECTION_CONFIG[key] if config.get(key) is None else config[key])


def _subsection_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("chapter_subsections")
    if not isinstance(raw, dict):
        raw = config.get("llm", {}).get("chapter_subsections")
    merged = dict(DEFAULT_SUBSECTION_CONFIG)
    if isinstance(raw, dict):
        merged.update(raw)
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["min_shots_for_model"] = max(1, _int_config(merged, "min_shots_for_model"))
    merged["min_need_score"] = max(1, _int_config(merged, "min_need_score"))
    merged["max_subsections_per_chapter"] = max(1, _int_config(merged, "max_subsections_per_chapter"))
    merged["min_shots_per_subsection"] = max(1, _int_config(merged, "min_shots_per_subsection"))
    merged["min_coverage_ratio"] = max(0.0, min(1.0, _float_value(merged.get("min_coverage_ratio"), 0.6)))
    return merged


def _outline_planner_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("outline_planner")
    if not isinstance(raw, dict):
        raw = config.get("llm", {}).get("outline_planner")
    merged = dict(DEFAULT_OUTLINE_PLANNER_CONFIG)
    if isinstance(raw, dict):
        merged.update(raw)
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["prefer_section_sources"] = bool(merged.get("prefer_section_sources", True))
    merged["call_model_when_ambiguous"] = bool(merged.get("call_model_when_ambiguous", True))
    merged["force_model"] = bool(merged.get("force_model", False))
    merged["max_chapters"] = max(1, min(8, int(_float_value(merged.get("max_chapters"), 8))))
    merged["min_shots_per_chapter"] = max(1, int(_float_value(merged.get("min_shots_per_chapter"), 2)))
    merged["min_coverage_ratio"] = max(0.0, min(1.0, _float_value(merged.get("min_coverage_ratio"), 0.95)))
    return merged


def _structure_qa_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("outline_structure_qa")
    if not isinstance(raw, dict):
        raw = config.get("llm", {}).get("outline_structure_qa")
    merged = dict(DEFAULT_STRUCTURE_QA_CONFIG)
    if isinstance(raw, dict):
        merged.update(raw)
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["max_rewrite_chapters"] = max(1, min(8, int(_float_value(merged.get("max_rewrite_chapters"), 8))))
    merged["apply_agent_suggestions"] = bool(merged.get("apply_agent_suggestions", False))
    return merged


def _manual_chapter_count(config: dict[str, Any]) -> int | None:
    raw = config.get("llm", {}).get("chapter_count", "auto")
    if raw == "auto":
        return None
    try:
        return min(8, max(1, int(raw)))
    except (TypeError, ValueError):
        return None


def _meaningful_sections(global_summary: dict[str, Any]) -> list[str]:
    sections = [str(section) for section in global_summary.get("main_sections") or [] if section]
    return [section for section in sections if section not in PLACEHOLDER_SECTIONS]


def _desired_chapter_count(config: dict[str, Any], global_summary: dict[str, Any], shot_count: int) -> int:
    raw = config.get("llm", {}).get("chapter_count", "auto")
    if raw != "auto":
        try:
            return min(8, max(1, int(raw)))
        except (TypeError, ValueError):
            pass
    try:
        suggested = int(global_summary.get("suggested_chapter_count") or 1)
    except (TypeError, ValueError):
        suggested = 1
    section_count = len(_meaningful_sections(global_summary))
    auto_count = max(suggested, section_count, 1)
    return min(8, max(1, min(auto_count, shot_count or 1)))


def _shot_order(cards: list[dict[str, Any]]) -> list[str]:
    return [str(card.get("shot_id")) for card in cards if card.get("shot_id")]


def _expand_shot_id_range(start: str, end: str, shot_ids: list[str]) -> list[str]:
    index_by_id = {shot_id: index for index, shot_id in enumerate(shot_ids)}
    if start not in index_by_id or end not in index_by_id:
        return []
    start_index = index_by_id[start]
    end_index = index_by_id[end]
    low = min(start_index, end_index)
    high = max(start_index, end_index)
    return shot_ids[low : high + 1]


def _shot_refs_from_text(text: str, shot_ids: list[str]) -> list[str]:
    matches: list[tuple[int, str]] = []
    for shot_id in shot_ids:
        match = re.search(rf"(?<![A-Za-z0-9_]){re.escape(shot_id)}(?![A-Za-z0-9_])", text)
        if match:
            matches.append((match.start(), shot_id))
    return [shot_id for _, shot_id in sorted(matches, key=lambda item: item[0])]


def _shots_for_time_range(cards: list[dict[str, Any]], start_sec: float, end_sec: float) -> list[str]:
    low = min(start_sec, end_sec)
    high = max(start_sec, end_sec)
    shot_ids: list[str] = []
    for card in cards:
        shot_id = card.get("shot_id")
        if not shot_id:
            continue
        card_start = _card_start(card)
        card_end = _card_end(card)
        if card_start is None and card_end is None:
            continue
        actual_start = card_start if card_start is not None else card_end
        actual_end = card_end if card_end is not None else card_start
        if actual_start is None or actual_end is None:
            continue
        if actual_start <= high and actual_end >= low:
            shot_ids.append(str(shot_id))
    return shot_ids


def _parse_chunk_shot_ids(summary: dict[str, Any], cards: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    shot_ids = _shot_order(cards)
    valid_shots = set(shot_ids)
    value = summary.get("shot_range")

    if isinstance(value, (list, tuple)):
        refs = [str(item) for item in value if str(item).strip()]
        if len(refs) >= 2 and refs[0] in valid_shots and refs[-1] in valid_shots:
            return _expand_shot_id_range(refs[0], refs[-1], shot_ids), warnings
        valid_refs = [ref for ref in refs if ref in valid_shots]
        if valid_refs:
            return sorted(_dedupe_strings(valid_refs), key=lambda shot_id: shot_ids.index(shot_id)), warnings
        if len(refs) >= 2:
            start = _float_value(refs[0], float("nan"))
            end = _float_value(refs[-1], float("nan"))
            if start == start and end == end:
                return _shots_for_time_range(cards, start, end), warnings

    if isinstance(value, str):
        refs = _shot_refs_from_text(value, shot_ids)
        if len(refs) >= 2:
            return _expand_shot_id_range(refs[0], refs[-1], shot_ids), warnings
        if len(refs) == 1:
            return refs, warnings
        numbers = re.findall(r"-?\d+(?:\.\d+)?", value)
        if len(numbers) >= 2:
            return _shots_for_time_range(cards, _float_value(numbers[0]), _float_value(numbers[-1])), warnings

    chunk_id = _clean_text(summary.get("chunk_id")) or "unknown"
    warnings.append(f"outline_chunk_range_unparseable:{chunk_id}")
    return [], warnings


def _chunk_shot_map(chunk_summaries: list[dict[str, Any]], cards: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    chunk_shots: dict[str, list[str]] = {}
    shot_chunks: dict[str, list[str]] = {}
    warnings: list[str] = []
    for summary in chunk_summaries:
        chunk_id = _clean_text(summary.get("chunk_id"))
        if not chunk_id:
            continue
        shot_ids, parse_warnings = _parse_chunk_shot_ids(summary, cards)
        warnings.extend(parse_warnings)
        if not shot_ids:
            continue
        chunk_shots[chunk_id] = shot_ids
        for shot_id in shot_ids:
            shot_chunks.setdefault(shot_id, []).append(chunk_id)
    return chunk_shots, shot_chunks, warnings


def _section_sources_by_title(global_summary: dict[str, Any]) -> dict[str, list[str]]:
    by_title: dict[str, list[str]] = {}
    for item in list(global_summary.get("section_sources") or []):
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
        if not title:
            continue
        chunks = _dedupe_strings(_as_list(item.get("source_chunks")))
        by_title[title] = chunks
    return by_title


def _chapter_draft_summary(title: str, source_chunks: list[str], chunk_summaries: dict[str, dict[str, Any]]) -> str:
    texts = [_clean_text(chunk_summaries.get(chunk_id, {}).get("summary")) for chunk_id in source_chunks]
    merged = " ".join(text for text in texts if text)
    if merged:
        return merged[:220]
    return title


def _section_source_chapter_drafts(
    global_summary: dict[str, Any],
    chunk_summaries: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    sections = _meaningful_sections(global_summary)
    if not sections:
        return [], ["outline_section_sources_missing"]
    if not chunk_summaries:
        return [], ["outline_chunk_summaries_missing"]

    chunk_shots, _, range_warnings = _chunk_shot_map(chunk_summaries, cards)
    warnings.extend(range_warnings)
    if not chunk_shots:
        return [], [*warnings, "outline_chunk_ranges_unusable"]

    by_title = _section_sources_by_title(global_summary)
    if not by_title:
        return [], [*warnings, "outline_section_sources_missing"]

    chunk_use_count: dict[str, int] = {}
    for chunks in by_title.values():
        for chunk_id in chunks:
            chunk_use_count[chunk_id] = chunk_use_count.get(chunk_id, 0) + 1
    repeated_chunks = sorted(chunk_id for chunk_id, count in chunk_use_count.items() if count > 1)
    if repeated_chunks:
        return [], [*warnings, f"outline_section_sources_ambiguous:{','.join(repeated_chunks)}"]

    summaries_by_id = {str(summary.get("chunk_id")): summary for summary in chunk_summaries if summary.get("chunk_id")}
    drafts: list[dict[str, Any]] = []
    missing_sections: list[str] = []
    invalid_chunks: list[str] = []
    for section in sections:
        source_chunks = by_title.get(section) or []
        valid_chunks = [chunk_id for chunk_id in source_chunks if chunk_id in chunk_shots]
        invalid_chunks.extend(chunk_id for chunk_id in source_chunks if chunk_id not in chunk_shots)
        if not valid_chunks:
            missing_sections.append(section)
            continue
        draft_shots: list[str] = []
        for chunk_id in valid_chunks:
            draft_shots.extend(chunk_shots[chunk_id])
        drafts.append(
            {
                "title": section,
                "summary": _chapter_draft_summary(section, valid_chunks, summaries_by_id),
                "shot_ids": _dedupe_strings(draft_shots),
                "source_chunks": valid_chunks,
                "strategy": "section_sources",
                "warnings": [],
            }
        )

    if invalid_chunks:
        warnings.append(f"outline_section_sources_invalid_chunks:{','.join(_dedupe_strings(invalid_chunks))}")
    if missing_sections:
        return [], [*warnings, f"outline_section_sources_incomplete:{len(missing_sections)}"]
    return drafts, warnings


def _build_shot_briefs(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for card in cards:
        shot_id = card.get("shot_id")
        if not shot_id:
            continue
        raw_tags = card.get("topic_tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        raw_entities = card.get("key_entities") or []
        if isinstance(raw_entities, str):
            raw_entities = [raw_entities]
        briefs.append(
            {
                "shot_id": str(shot_id),
                "start_sec": _card_start(card),
                "end_sec": _card_end(card),
                "text": _clean_text(card.get("merged_summary"))[:90],
                "topic_tags": [str(tag) for tag in list(raw_tags) if str(tag).strip()][:5],
                "key_entities": [str(entity) for entity in list(raw_entities) if str(entity).strip()][:5],
                "importance_score": _float_value(card.get("importance_score"), 0.0),
            }
        )
    return briefs


def _outline_agent_chapter_drafts(
    project_path: Path,
    config: dict[str, Any],
    global_summary: dict[str, Any],
    chunk_summaries: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    *,
    max_chapters: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    adapter = LocalModelAdapter(project_path, config, model_role="copywriting")
    plan = adapter.plan_outline(global_summary, chunk_summaries, _build_shot_briefs(cards), max_chapters=max_chapters)
    warnings = [str(item) for item in list(plan.get("warnings") or []) if str(item).strip()]
    drafts: list[dict[str, Any]] = []
    for chapter in list(plan.get("chapters") or []):
        if not isinstance(chapter, dict):
            continue
        title = _clean_text(chapter.get("title"))
        shot_ids = _dedupe_strings(_as_list(chapter.get("shot_ids")))
        if not title or not shot_ids:
            continue
        drafts.append(
            {
                "title": title,
                "summary": _clean_text(chapter.get("summary")) or title,
                "shot_ids": shot_ids,
                "source_chunks": [],
                "strategy": "outline_planner_agent",
                "warnings": [],
            }
        )
    if not drafts:
        warnings.append("outline_planner_agent_no_valid_chapters")
    return drafts, warnings


def _even_split_chapter_drafts(config: dict[str, Any], global_summary: dict[str, Any], cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    count = _desired_chapter_count(config, global_summary, len(cards))
    per_chapter = max(1, ceil(len(cards) / count)) if cards else 1
    sections = list(global_summary.get("main_sections") or [])
    drafts: list[dict[str, Any]] = []
    for index in range(count):
        group = cards[index * per_chapter : (index + 1) * per_chapter]
        if not group:
            continue
        title_seed = sections[index] if index < len(sections) else f"Part {index + 1}"
        representative = max(group, key=lambda item: _float_value(item.get("importance_score"), 0.0))
        drafts.append(
            {
                "title": str(title_seed),
                "summary": str(representative.get("merged_summary") or title_seed),
                "shot_ids": [str(item["shot_id"]) for item in group if item.get("shot_id")],
                "source_chunks": [],
                "strategy": "even_split_fallback",
                "warnings": ["outline_fallback_even_split"],
            }
        )
    return drafts


def _card_start(card: dict[str, Any]) -> float | None:
    value = card.get("start_sec")
    if value is None and isinstance(card.get("time_range"), dict):
        value = card["time_range"].get("start_sec")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _card_end(card: dict[str, Any]) -> float | None:
    value = card.get("end_sec")
    if value is None and isinstance(card.get("time_range"), dict):
        value = card["time_range"].get("end_sec")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chapter_duration(cards: list[dict[str, Any]]) -> float:
    starts = [value for value in (_card_start(card) for card in cards) if value is not None]
    ends = [value for value in (_card_end(card) for card in cards) if value is not None]
    if not starts or not ends:
        return 0.0
    return max(0.0, max(ends) - min(starts))


def _topic_tokens(card: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("topic_tags", "key_entities"):
        for item in _as_list(card.get(key)):
            text = _clean_text(item)
            if text and text not in PLACEHOLDER_SECTIONS:
                tokens.add(text)
    role = _clean_text(card.get("narrative_role"))
    if role and role != "unknown":
        tokens.add(role)
    return tokens


def _effective_topic_count(cards: list[dict[str, Any]]) -> int:
    topics: set[str] = set()
    for card in cards:
        topics.update(_topic_tokens(card))
    return len(topics)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _topic_shift_count(cards: list[dict[str, Any]]) -> int:
    if len(cards) < 6:
        return 0
    boundaries: list[int] = []
    window = 3
    for boundary in range(2, len(cards) - 2):
        left_tokens: set[str] = set()
        right_tokens: set[str] = set()
        for card in cards[max(0, boundary - window) : boundary]:
            left_tokens.update(_topic_tokens(card))
        for card in cards[boundary : min(len(cards), boundary + window)]:
            right_tokens.update(_topic_tokens(card))
        if left_tokens and right_tokens and _jaccard(left_tokens, right_tokens) < 0.25:
            boundaries.append(boundary)

    merged: list[int] = []
    for boundary in boundaries:
        if not merged or boundary - merged[-1] > window:
            merged.append(boundary)
    return len(merged)


def _high_importance_cluster_count(cards: list[dict[str, Any]]) -> int:
    if not cards:
        return 0
    clusters: set[int] = set()
    for index, card in enumerate(cards):
        if _float_value(card.get("importance_score"), 0.0) >= 0.7:
            clusters.add(min(2, int(index * 3 / max(1, len(cards)))))
    return len(clusters)


def _title_is_generic(title: Any) -> bool:
    text = _clean_text(title)
    if not text:
        return True
    lowered = text.lower()
    if text in GENERIC_TITLES or lowered in {item.lower() for item in GENERIC_TITLES}:
        return True
    return lowered.startswith("part ") or lowered.startswith("section ")


def _decide_chapter_subsections(
    chapter: dict[str, Any],
    cards: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    total_shot_count: int,
    chapter_count: int,
) -> dict[str, Any]:
    subsection_config = _subsection_config(config)
    shot_count = len(cards)
    duration = _chapter_duration(cards)
    topic_count = _effective_topic_count(cards)
    shift_count = _topic_shift_count(cards)
    min_shots = int(subsection_config["min_shots_for_model"])
    min_per_subsection = int(subsection_config["min_shots_per_subsection"])
    max_by_size = max(1, ceil(shot_count / 4)) if shot_count else 1
    max_subsections = min(int(subsection_config["max_subsections_per_chapter"]), max_by_size)
    target_subsections = min(max_subsections, max(2, shift_count + 1))
    base = {
        "chapter_id": chapter.get("chapter_id"),
        "shot_count": shot_count,
        "duration_sec": round(duration, 3),
        "topic_count": topic_count,
        "topic_shift_count": shift_count,
        "target_subsections": target_subsections,
        "max_subsections": max_subsections,
        "need_score": 0,
        "reason_codes": [],
        "model_called": False,
        "subsection_count": 0,
    }

    if not subsection_config["enabled"]:
        return {**base, "mode": "keep", "reason_codes": ["disabled"]}
    if shot_count <= 4 or shot_count < min_shots:
        return {**base, "mode": "keep", "reason_codes": ["few_shots"]}
    if max_subsections < 2 or shot_count < min_per_subsection * 2:
        return {**base, "mode": "keep", "reason_codes": ["not_enough_subsection_capacity"]}
    if duration and duration <= 90 and topic_count <= 2:
        return {**base, "mode": "keep", "reason_codes": ["compact_single_topic"]}

    score = 0
    reasons: list[str] = []
    if shot_count >= 8:
        score += 2
        reasons.append("many_shots")
    if shot_count >= 14:
        score += 1
        reasons.append("very_many_shots")
    if duration >= 180:
        score += 1
        reasons.append("long_duration")
    if duration >= 360:
        score += 1
        reasons.append("very_long_duration")
    if topic_count >= 3:
        score += 2
        reasons.append("many_topics")
    if topic_count >= 5:
        score += 1
        reasons.append("very_many_topics")
    if shift_count >= 2:
        score += 2
        reasons.append("topic_shift")
    if _high_importance_cluster_count(cards) >= 2:
        score += 1
        reasons.append("distributed_importance")
    if _title_is_generic(chapter.get("title")):
        score += 1
        reasons.append("generic_title")

    threshold = int(subsection_config["min_need_score"])
    dominant_chapter = total_shot_count > 0 and shot_count / total_shot_count >= 0.25 and chapter_count <= 3
    should_split = score >= threshold or (score == threshold - 1 and dominant_chapter)
    if should_split and score == threshold - 1:
        reasons.append("dominant_chapter")
    return {**base, "mode": "split" if should_split else "keep", "need_score": score, "reason_codes": reasons or ["low_need_score"]}


def _chapter_cards(chapter: dict[str, Any], cards_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [cards_by_id[shot_id] for shot_id in chapter.get("shot_ids", []) if shot_id in cards_by_id]


def _representative_shot_id(shot_ids: list[str], cards_by_id: dict[str, dict[str, Any]]) -> str:
    return str(max((cards_by_id[shot_id] for shot_id in shot_ids), key=lambda item: _float_value(item.get("importance_score"), 0.0)).get("shot_id"))


def _shot_time_range(shot_ids: list[str], cards_by_id: dict[str, dict[str, Any]]) -> tuple[float | None, float | None]:
    starts = [value for value in (_card_start(cards_by_id[shot_id]) for shot_id in shot_ids if shot_id in cards_by_id) if value is not None]
    ends = [value for value in (_card_end(cards_by_id[shot_id]) for shot_id in shot_ids if shot_id in cards_by_id) if value is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _is_important_singleton(shot_ids: list[str], cards_by_id: dict[str, dict[str, Any]]) -> bool:
    return len(shot_ids) == 1 and _float_value(cards_by_id.get(shot_ids[0], {}).get("importance_score"), 0.0) >= 0.85


def _nearest_chapter_index(shot_index: int, items: list[dict[str, Any]], index_by_shot: dict[str, int]) -> int:
    nearest_index = 0
    nearest_distance: int | None = None
    for index, item in enumerate(items):
        item_indexes = [index_by_shot[shot_id] for shot_id in item["shot_ids"] if shot_id in index_by_shot]
        if not item_indexes:
            continue
        low = min(item_indexes)
        high = max(item_indexes)
        if low <= shot_index <= high:
            return index
        distance = min(abs(shot_index - low), abs(shot_index - high))
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_index = index
    return nearest_index


def _normalize_chapter_drafts(
    drafts: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    *,
    min_shots_per_chapter: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    shot_ids = _shot_order(cards)
    index_by_shot = {shot_id: index for index, shot_id in enumerate(shot_ids)}
    cards_by_id = {str(card.get("shot_id")): card for card in cards if card.get("shot_id")}
    assigned: set[str] = set()
    items: list[dict[str, Any]] = []

    for draft in drafts:
        title = _clean_text(draft.get("title"))
        ordered = sorted(
            [
                str(shot_id)
                for shot_id in _as_list(draft.get("shot_ids"))
                if str(shot_id) in index_by_shot and str(shot_id) not in assigned
            ],
            key=lambda shot_id: index_by_shot[shot_id],
        )
        ordered = _dedupe_strings(ordered)
        if not title:
            warnings.append("outline_empty_chapter_title_removed")
            continue
        if not ordered:
            warnings.append(f"outline_empty_chapter_removed:{title}")
            continue
        assigned.update(ordered)
        items.append(
            {
                "title": title,
                "summary": _clean_text(draft.get("summary")) or title,
                "shot_ids": ordered,
                "source_chunks": _dedupe_strings(_as_list(draft.get("source_chunks"))),
                "strategy": _clean_text(draft.get("strategy")) or "unknown",
                "warnings": _dedupe_strings(_as_list(draft.get("warnings"))),
            }
        )

    if not items:
        return [], [*warnings, "outline_no_valid_chapters"]

    missing = [shot_id for shot_id in shot_ids if shot_id not in assigned]
    if missing:
        warnings.append(f"outline_missing_shots_filled:{len(missing)}")
    for shot_id in missing:
        target = _nearest_chapter_index(index_by_shot[shot_id], items, index_by_shot)
        items[target]["shot_ids"].append(shot_id)

    for item in items:
        item["shot_ids"] = sorted(_dedupe_strings(item["shot_ids"]), key=lambda shot_id: index_by_shot[shot_id])

    items.sort(key=lambda item: min(index_by_shot[shot_id] for shot_id in item["shot_ids"]))
    changed = True
    while changed and len(items) > 1:
        changed = False
        for index, item in enumerate(list(items)):
            if len(item["shot_ids"]) >= min_shots_per_chapter or _is_important_singleton(item["shot_ids"], cards_by_id):
                continue
            target_index = index - 1 if index > 0 else index + 1
            target = items[target_index]
            target["shot_ids"].extend(item["shot_ids"])
            target["shot_ids"] = sorted(_dedupe_strings(target["shot_ids"]), key=lambda shot_id: index_by_shot[shot_id])
            target["warnings"] = _dedupe_strings([*target.get("warnings", []), f"small_chapter_merged:{item['title']}"])
            del items[index]
            warnings.append(f"outline_small_chapter_merged:{item['title']}")
            changed = True
            break

    return items, warnings


def _build_chapters_from_drafts(
    drafts: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chapters: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for index, draft in enumerate(drafts, start=1):
        shot_ids = [shot_id for shot_id in draft["shot_ids"] if shot_id in cards_by_id]
        if not shot_ids:
            continue
        start_sec, end_sec = _shot_time_range(shot_ids, cards_by_id)
        chapter_id = f"chapter_{index:03d}"
        chapter: dict[str, Any] = {
            "chapter_id": chapter_id,
            "title": draft["title"],
            "summary": draft["summary"],
            "shot_ids": shot_ids,
            "representative_shot_id": _representative_shot_id(shot_ids, cards_by_id),
        }
        if start_sec is not None:
            chapter["start_sec"] = start_sec
        if end_sec is not None:
            chapter["end_sec"] = end_sec
        chapters.append(chapter)
        decisions.append(
            {
                "chapter_id": chapter_id,
                "title": draft["title"],
                "strategy": draft.get("strategy"),
                "source_chunks": list(draft.get("source_chunks") or []),
                "shot_count": len(shot_ids),
                "start_shot_id": shot_ids[0],
                "end_shot_id": shot_ids[-1],
                "warnings": list(draft.get("warnings") or []),
            }
        )
    return chapters, decisions


def _merge_small_subsections(items: list[dict[str, Any]], cards_by_id: dict[str, dict[str, Any]], min_size: int) -> list[dict[str, Any]]:
    changed = True
    while changed and len(items) > 1:
        changed = False
        for index, item in enumerate(list(items)):
            if len(item["shot_ids"]) >= min_size or _is_important_singleton(item["shot_ids"], cards_by_id):
                continue
            target_index = index - 1 if index > 0 else index + 1
            items[target_index]["shot_ids"].extend(item["shot_ids"])
            del items[index]
            changed = True
            break
    return items


def _normalize_subsections(
    chapter: dict[str, Any],
    raw_subsections: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    subsection_config = _subsection_config(config)
    order = [str(shot_id) for shot_id in chapter.get("shot_ids", []) if str(shot_id) in cards_by_id]
    index_by_shot = {shot_id: index for index, shot_id in enumerate(order)}
    assigned: set[str] = set()
    items: list[dict[str, Any]] = []

    for raw in raw_subsections:
        title = _clean_text(raw.get("title"))
        if not title:
            warnings.append("empty_subsection_title_removed")
            continue
        shot_ids = sorted(
            [str(shot_id) for shot_id in raw.get("shot_ids", []) if str(shot_id) in index_by_shot and str(shot_id) not in assigned],
            key=lambda shot_id: index_by_shot[shot_id],
        )
        if not shot_ids:
            warnings.append(f"empty_subsection_removed:{title}")
            continue
        assigned.update(shot_ids)
        items.append({"title": title, "shot_ids": shot_ids})

    if not items:
        return [], [*warnings, "no_valid_subsections"]

    for shot_id in order:
        if shot_id in assigned:
            continue
        target_index = 0
        shot_index = index_by_shot[shot_id]
        for item_index, item in enumerate(items):
            first_index = min(index_by_shot[item_shot_id] for item_shot_id in item["shot_ids"])
            if first_index <= shot_index:
                target_index = item_index
        items[target_index]["shot_ids"].append(shot_id)
        assigned.add(shot_id)

    for item in items:
        item["shot_ids"] = sorted(set(item["shot_ids"]), key=lambda shot_id: index_by_shot[shot_id])

    max_subsections = int(subsection_config["max_subsections_per_chapter"])
    if len(items) > max_subsections:
        warnings.append(f"too_many_subsections_merged:{len(items)}")
        kept = items[:max_subsections]
        for extra in items[max_subsections:]:
            kept[-1]["shot_ids"].extend(extra["shot_ids"])
        items = kept

    items = _merge_small_subsections(items, cards_by_id, int(subsection_config["min_shots_per_subsection"]))
    for item in items:
        item["shot_ids"] = sorted(set(item["shot_ids"]), key=lambda shot_id: index_by_shot[shot_id])

    coverage = len({shot_id for item in items for shot_id in item["shot_ids"]}) / max(1, len(order))
    if len(items) < 2:
        return [], [*warnings, "too_few_valid_subsections"]
    if coverage < float(subsection_config["min_coverage_ratio"]):
        return [], [*warnings, f"low_subsection_coverage:{coverage:.2f}"]

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        shot_ids = item["shot_ids"]
        start_sec, end_sec = _shot_time_range(shot_ids, cards_by_id)
        payload: dict[str, Any] = {
            "subsection_id": f"{chapter.get('chapter_id')}_sub_{index:03d}",
            "title": item["title"],
            "shot_ids": shot_ids,
            "representative_shot_id": _representative_shot_id(shot_ids, cards_by_id),
        }
        if start_sec is not None:
            payload["start_sec"] = start_sec
        if end_sec is not None:
            payload["end_sec"] = end_sec
        normalized.append(payload)
    return normalized, warnings


def _apply_chapter_subsections(
    project_path: Path,
    stage_dir: Path,
    config: dict[str, Any],
    chapters: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    cards_by_id = {str(card.get("shot_id")): card for card in cards if card.get("shot_id")}
    total_shot_count = len(cards)
    decisions: list[dict[str, Any]] = []
    warnings: list[str] = []
    adapter: LocalModelAdapter | None = None

    for chapter in chapters:
        chapter_cards = _chapter_cards(chapter, cards_by_id)
        decision = _decide_chapter_subsections(
            chapter,
            chapter_cards,
            config,
            total_shot_count=total_shot_count,
            chapter_count=len(chapters),
        )
        record = dict(decision)
        if decision["mode"] != "split":
            chapter.pop("subsections", None)
            decisions.append(record)
            continue

        if adapter is None:
            adapter = LocalModelAdapter(project_path, config, model_role="copywriting")
        record["model_called"] = True
        try:
            plan = adapter.plan_chapter_subsections(
                chapter,
                chapter_cards,
                target_subsections=int(decision["target_subsections"]),
                max_subsections=int(decision["max_subsections"]),
            )
        except Exception as exc:  # noqa: BLE001 - subsection planning should degrade to a plain chapter.
            chapter.pop("subsections", None)
            record["mode"] = "keep_fallback"
            record["warnings"] = [f"chapter_subsection_agent_failed:{exc}"]
            warnings.append(f"{chapter.get('chapter_id')}:chapter_subsection_agent_failed")
            decisions.append(record)
            continue

        record["warnings"] = list(plan.get("warnings") or [])
        if plan.get("mode") == "keep":
            chapter.pop("subsections", None)
            record["mode"] = "keep_model"
            decisions.append(record)
            continue

        subsections, normalize_warnings = _normalize_subsections(chapter, list(plan.get("subsections") or []), cards_by_id, config)
        record["warnings"].extend(normalize_warnings)
        if not subsections:
            chapter.pop("subsections", None)
            record["mode"] = "keep_fallback"
            warnings.append(f"{chapter.get('chapter_id')}:no_valid_subsections")
            decisions.append(record)
            continue

        chapter["subsections"] = subsections
        record["mode"] = "split"
        record["subsection_count"] = len(subsections)
        decisions.append(record)

    write_jsonl(stage_dir / "subsection_decisions.jsonl", decisions)
    return decisions, warnings


def _title_tokens(value: Any) -> set[str]:
    text = _clean_text(value).lower()
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z][a-z0-9_-]+", text))
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    for index in range(len(cjk_chars) - 1):
        tokens.add("".join(cjk_chars[index : index + 2]))
    return tokens


def _text_similarity(left: Any, right: Any) -> float:
    return _jaccard(_title_tokens(left), _title_tokens(right))


def _chapter_source_chunks(chapter: dict[str, Any], shot_chunks: dict[str, list[str]]) -> list[str]:
    chunks: list[str] = []
    for shot_id in chapter.get("shot_ids", []):
        chunks.extend(shot_chunks.get(str(shot_id), []))
    return _dedupe_strings(chunks)


def _detect_outline_structure_risk(
    chapters: list[dict[str, Any]],
    cards_by_id: dict[str, dict[str, Any]],
    shot_chunks: dict[str, list[str]],
    config: dict[str, Any],
) -> dict[str, Any]:
    qa_config = _structure_qa_config(config)
    metrics: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    total_shots = sum(len(chapter.get("shot_ids", [])) for chapter in chapters)

    for chapter in chapters:
        chapter_cards = _chapter_cards(chapter, cards_by_id)
        shot_count = len(chapter_cards)
        ratio = shot_count / max(1, total_shots)
        shift_count = _topic_shift_count(chapter_cards)
        source_chunks = _chapter_source_chunks(chapter, shot_chunks)
        metric = {
            "chapter_id": chapter.get("chapter_id"),
            "title": chapter.get("title"),
            "shot_count": shot_count,
            "shot_ratio": round(ratio, 3),
            "duration_sec": round(_chapter_duration(chapter_cards), 3),
            "topic_count": _effective_topic_count(chapter_cards),
            "topic_shift_count": shift_count,
            "subsection_count": len(chapter.get("subsections") or []),
            "source_chunks": source_chunks,
        }
        metrics.append(metric)
        if len(chapters) > 1 and ratio >= 0.5:
            signals.append(
                {
                    "signal": "chapter_shot_ratio",
                    "chapter_id": chapter.get("chapter_id"),
                    "value": round(ratio, 3),
                    "severity": "high" if ratio >= 0.6 else "medium",
                }
            )
        if shift_count >= 8:
            signals.append(
                {
                    "signal": "topic_shift_count",
                    "chapter_id": chapter.get("chapter_id"),
                    "value": shift_count,
                    "severity": "high",
                }
            )
        elif shift_count >= 5:
            signals.append(
                {
                    "signal": "topic_shift_count",
                    "chapter_id": chapter.get("chapter_id"),
                    "value": shift_count,
                    "severity": "medium",
                }
            )
        if len(source_chunks) >= 4:
            signals.append(
                {
                    "signal": "section_source_crossing",
                    "chapter_id": chapter.get("chapter_id"),
                    "value": len(source_chunks),
                    "severity": "medium",
                }
            )

    if len(chapters) <= 2 and total_shots >= 80:
        signals.append(
            {
                "signal": "chapter_count_low_for_long_video",
                "value": {"chapter_count": len(chapters), "shot_count": total_shots},
                "severity": "high",
            }
        )

    for index in range(len(chapters) - 1):
        score = _text_similarity(chapters[index].get("title"), chapters[index + 1].get("title"))
        if score >= 0.65:
            signals.append(
                {
                    "signal": "chapter_title_duplicate_topic",
                    "chapter_id": chapters[index].get("chapter_id"),
                    "next_chapter_id": chapters[index + 1].get("chapter_id"),
                    "value": round(score, 3),
                    "severity": "medium",
                }
            )

    chapter_titles = [(chapter.get("chapter_id"), chapter.get("title")) for chapter in chapters]
    for chapter in chapters:
        for subsection in list(chapter.get("subsections") or []):
            for other_id, other_title in chapter_titles:
                if other_id == chapter.get("chapter_id"):
                    continue
                score = _text_similarity(subsection.get("title"), other_title)
                if score >= 0.6:
                    signals.append(
                        {
                            "signal": "subsection_title_similar_to_chapter",
                            "chapter_id": chapter.get("chapter_id"),
                            "subsection_id": subsection.get("subsection_id"),
                            "other_chapter_id": other_id,
                            "value": round(score, 3),
                            "severity": "high" if score >= 0.75 else "medium",
                        }
                    )

    if any(signal.get("severity") == "high" for signal in signals):
        risk_level = "high"
    elif signals:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level if qa_config["enabled"] else "skipped",
        "signals": signals if qa_config["enabled"] else [],
        "agent_called": False,
        "actions_applied": [],
        "chapter_metrics": metrics,
    }


def _run_outline_plan_legacy(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "08_outline_plan")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    cards = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    global_summary = read_json(find_stage_artifact(project_path, "07_summary_reduce", "global_summary.json"))
    count = _desired_chapter_count(config, global_summary, len(cards))
    per_chapter = max(1, ceil(len(cards) / count)) if cards else 1
    sections = list(global_summary.get("main_sections") or [])
    chapters: list[dict[str, Any]] = []
    for index in range(count):
        group = cards[index * per_chapter : (index + 1) * per_chapter]
        if not group:
            continue
        representative = max(group, key=lambda item: float(item.get("importance_score") or 0.0))
        title_seed = sections[index] if index < len(sections) else f"Part {index + 1}"
        chapters.append(
            {
                "chapter_id": f"chapter_{index + 1:03d}",
                "title": str(title_seed),
                "summary": str(representative.get("merged_summary") or title_seed),
                "shot_ids": [str(item["shot_id"]) for item in group],
                "representative_shot_id": str(representative["shot_id"]),
            }
        )
    outline = {
        "title": "视频博客笔记",
        "description": "由视频帧文字、字幕和知识点提取生成的静态笔记。",
        "chapters": chapters,
    }
    decisions, subsection_warnings = _apply_chapter_subsections(project_path, stage_dir, config, chapters, cards)
    if subsection_warnings:
        outline["warnings"] = subsection_warnings
    atomic_write_json(stage_dir / "outline.json", outline)
    log_event(
        project_path,
        "outline_plan_done",
        chapter_count=len(chapters),
        subsection_chapter_count=sum(1 for decision in decisions if decision.get("mode") == "split"),
    )
    return {
        "outputs": [
            stage_relative_path("08_outline_plan", "outline.json"),
            stage_relative_path("08_outline_plan", "subsection_decisions.jsonl"),
        ],
        "chapter_count": len(chapters),
        "subsection_chapter_count": sum(1 for decision in decisions if decision.get("mode") == "split"),
        "model_signature": model_signature(config, "copywriting"),
    }


def run_outline_plan(project_dir: str | Path) -> dict[str, Any]:
    project_path = Path(project_dir)
    stage_dir = project_stage_dir(project_path, "08_outline_plan")
    stage_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(find_stage_artifact(project_path, "00_init", "config.json"))
    cards = read_jsonl(find_stage_artifact(project_path, "06_shot_understanding", "shot_analysis.jsonl"))
    global_summary = read_json(find_stage_artifact(project_path, "07_summary_reduce", "global_summary.json"))
    chunk_summaries = read_jsonl(find_stage_artifact(project_path, "07_summary_reduce", "chunk_summaries.jsonl"))
    planner_config = _outline_planner_config(config)
    manual_count = _manual_chapter_count(config)
    cards_by_id = {str(card.get("shot_id")): card for card in cards if card.get("shot_id")}
    planning_warnings: list[str] = []
    primary_strategy = "even_split_fallback"
    raw_drafts: list[dict[str, Any]] = []

    can_use_semantic_planning = manual_count is None or bool(planner_config["force_model"])
    if cards and can_use_semantic_planning and bool(planner_config["force_model"]):
        try:
            raw_drafts, agent_warnings = _outline_agent_chapter_drafts(
                project_path,
                config,
                global_summary,
                chunk_summaries,
                cards,
                max_chapters=int(planner_config["max_chapters"]),
            )
            planning_warnings.extend(agent_warnings)
            if raw_drafts:
                primary_strategy = "outline_planner_agent"
        except Exception as exc:  # noqa: BLE001 - outline planning must degrade to local rules.
            planning_warnings.append(f"outline_planner_agent_failed:{exc}")

    if cards and not raw_drafts and can_use_semantic_planning and bool(planner_config["prefer_section_sources"]):
        section_drafts, section_warnings = _section_source_chapter_drafts(global_summary, chunk_summaries, cards)
        planning_warnings.extend(section_warnings)
        if section_drafts:
            raw_drafts = section_drafts
            primary_strategy = "section_sources"

    should_call_agent = (
        cards
        and not raw_drafts
        and can_use_semantic_planning
        and bool(planner_config["enabled"])
        and bool(planner_config["call_model_when_ambiguous"])
    )
    if should_call_agent:
        try:
            raw_drafts, agent_warnings = _outline_agent_chapter_drafts(
                project_path,
                config,
                global_summary,
                chunk_summaries,
                cards,
                max_chapters=int(planner_config["max_chapters"]),
            )
            planning_warnings.extend(agent_warnings)
            if raw_drafts:
                primary_strategy = "outline_planner_agent"
        except Exception as exc:  # noqa: BLE001 - outline planning must degrade to local rules.
            planning_warnings.append(f"outline_planner_agent_failed:{exc}")

    if not raw_drafts:
        raw_drafts = _even_split_chapter_drafts(config, global_summary, cards)
        primary_strategy = "even_split_fallback"

    normalized_drafts, normalize_warnings = _normalize_chapter_drafts(
        raw_drafts,
        cards,
        min_shots_per_chapter=int(planner_config["min_shots_per_chapter"]),
    )
    planning_warnings.extend(normalize_warnings)
    if not normalized_drafts and primary_strategy != "even_split_fallback":
        fallback_drafts = _even_split_chapter_drafts(config, global_summary, cards)
        normalized_drafts, fallback_warnings = _normalize_chapter_drafts(
            fallback_drafts,
            cards,
            min_shots_per_chapter=int(planner_config["min_shots_per_chapter"]),
        )
        planning_warnings.extend(["outline_semantic_planning_invalid", *fallback_warnings])
        primary_strategy = "even_split_fallback"

    chapters, boundary_decisions = _build_chapters_from_drafts(normalized_drafts, cards_by_id)
    decision_warnings = _dedupe_strings(planning_warnings)
    if decision_warnings and boundary_decisions:
        boundary_decisions[0]["pipeline_warnings"] = decision_warnings
    write_jsonl(stage_dir / "chapter_boundary_decisions.jsonl", boundary_decisions)

    outline = {
        "title": _clean_text(global_summary.get("video_main_theme")) or "视频博客笔记",
        "description": "由视频帧文字、字幕和知识点提取生成的结构化笔记。",
        "chapters": chapters,
    }
    decisions, subsection_warnings = _apply_chapter_subsections(project_path, stage_dir, config, chapters, cards)
    _, shot_chunks, _ = _chunk_shot_map(chunk_summaries, cards)
    structure_qa = _detect_outline_structure_risk(chapters, cards_by_id, shot_chunks, config)
    atomic_write_json(stage_dir / "outline_structure_qa.json", structure_qa)

    outline_warnings = _dedupe_strings([*planning_warnings, *subsection_warnings])
    if structure_qa.get("risk_level") == "high":
        outline_warnings.append("outline_structure_risk_high")
    elif structure_qa.get("risk_level") == "medium":
        outline_warnings.append("outline_structure_risk_medium")
    if outline_warnings:
        outline["warnings"] = _dedupe_strings(outline_warnings)

    atomic_write_json(stage_dir / "outline.json", outline)
    log_event(
        project_path,
        "outline_plan_done",
        chapter_count=len(chapters),
        subsection_chapter_count=sum(1 for decision in decisions if decision.get("mode") == "split"),
        primary_strategy=primary_strategy,
        risk_level=structure_qa.get("risk_level"),
    )
    return {
        "outputs": [
            stage_relative_path("08_outline_plan", "outline.json"),
            stage_relative_path("08_outline_plan", "chapter_boundary_decisions.jsonl"),
            stage_relative_path("08_outline_plan", "outline_structure_qa.json"),
            stage_relative_path("08_outline_plan", "subsection_decisions.jsonl"),
        ],
        "chapter_count": len(chapters),
        "subsection_chapter_count": sum(1 for decision in decisions if decision.get("mode") == "split"),
        "primary_strategy": primary_strategy,
        "risk_level": structure_qa.get("risk_level"),
        "model_signature": model_signature(config, "copywriting"),
    }
