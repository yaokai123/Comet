"""Export unlabeled LoCoMo person-name pairs for independent human annotation."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:[ '-][A-Z][a-z]+){0,2}\b")
_STOP = {
    "A", "And", "But", "Can", "Cool", "Do", "Good", "Great", "Have", "Hey",
    "How", "I", "If", "It", "Keep", "Let", "Nice", "Oh", "So", "Take", "Thanks",
    "That", "The", "They", "This", "We", "Well", "What", "Wow", "Yeah", "Yes", "You",
    "Your",
}


def _pair_id(sample_id: str, left: str, right: str) -> str:
    value = "\0".join((sample_id, *sorted((left, right))))
    return "locomo-pair-" + hashlib.sha256(value.encode()).hexdigest()[:20]


def _names(text: str) -> set[str]:
    values = set()
    for raw in _NAME_RE.findall(text):
        name = raw.removeprefix("Hey ").strip()
        if name not in _STOP and len(name) >= 2:
            values.add(name)
    return values


def export_candidates(
    source: str | Path,
    destination: str | Path,
    *,
    limit: int = 500,
    seed: int = 42,
) -> list[dict]:
    samples = json.loads(Path(source).read_text(encoding="utf-8"))
    candidates = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        conversation = sample["conversation"]
        evidence: dict[str, list[dict]] = defaultdict(list)
        counts: Counter[str] = Counter()
        speakers = {
            str(conversation.get("speaker_a", "")).strip(),
            str(conversation.get("speaker_b", "")).strip(),
        } - {""}
        for key in sorted(conversation):
            if not re.fullmatch(r"session_\d+", key):
                continue
            for turn in conversation[key]:
                text = str(turn.get("text", ""))
                for name in _names(text) | {str(turn.get("speaker", "")).strip()}:
                    if not name:
                        continue
                    counts[name] += 1
                    if len(evidence[name]) < 3:
                        evidence[name].append({
                            "session_id": key,
                            "dia_id": turn.get("dia_id"),
                            "speaker": turn.get("speaker"),
                            "text": text,
                        })
        names = sorted(name for name, count in counts.items() if count >= 2)
        local = []
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                ratio = difflib.SequenceMatcher(None, left.lower(), right.lower()).ratio()
                contains = left.lower() in right.lower() or right.lower() in left.lower()
                speaker_pair = left in speakers or right in speakers
                if ratio < 0.45 and not contains and not speaker_pair:
                    continue
                difficulty_hint = (
                    "possible_alias" if ratio >= 0.65 or contains else "hard_negative"
                )
                local.append({
                    "id": _pair_id(sample_id, left, right),
                    "sample_id": sample_id,
                    "left": left,
                    "right": right,
                    "label": None,
                    "difficulty_hint": difficulty_hint,
                    # Capitalization alone cannot reliably distinguish people,
                    # organizations, places and named concepts. Human annotators
                    # fill this field after reviewing the attached evidence.
                    "entity_type": None,
                    "name_similarity": round(ratio, 6),
                    "left_mentions": counts[left],
                    "right_mentions": counts[right],
                    "left_evidence": evidence[left],
                    "right_evidence": evidence[right],
                    "annotation_note": "",
                })
        candidates.extend(local)

    rng = random.Random(seed)
    aliases = [row for row in candidates if row["difficulty_hint"] == "possible_alias"]
    negatives = [row for row in candidates if row["difficulty_hint"] == "hard_negative"]
    rng.shuffle(aliases)
    rng.shuffle(negatives)
    alias_target = min(len(aliases), limit // 2)
    negative_target = min(len(negatives), limit - alias_target)
    selected = aliases[:alias_target] + negatives[:negative_target]
    remaining = aliases[alias_target:] + negatives[negative_target:]
    rng.shuffle(remaining)
    selected.extend(remaining[:limit - len(selected)])
    rng.shuffle(selected)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
        encoding="utf-8",
    )
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rows = export_candidates(args.source, args.destination, limit=args.limit, seed=args.seed)
    print(f"Exported {len(rows)} unlabeled LoCoMo candidate pairs to {args.destination}")


if __name__ == "__main__":
    main()
