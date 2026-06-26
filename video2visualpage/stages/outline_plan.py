from __future__ import annotations

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


def run_outline_plan(project_dir: str | Path) -> dict[str, Any]:
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
