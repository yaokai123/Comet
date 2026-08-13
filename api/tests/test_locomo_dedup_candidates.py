import json

from eval.benchmarks.entity_dedup.locomo_candidates import export_candidates


def test_export_candidates_keeps_labels_blank_and_evidence(tmp_path):
    source = tmp_path / "locomo.json"
    source.write_text(json.dumps([{
        "sample_id": "conv-1",
        "conversation": {
            "speaker_a": "Caroline",
            "speaker_b": "Melanie",
            "session_1": [
                {"dia_id": "D1:1", "speaker": "Caroline", "text": "Hi Mel!"},
                {"dia_id": "D1:2", "speaker": "Melanie", "text": "Hi Caroline!"},
                {"dia_id": "D1:3", "speaker": "Caroline", "text": "Mel helped Alice."},
                {"dia_id": "D1:4", "speaker": "Melanie", "text": "Alice thanked Mel."},
            ],
        },
    }]), encoding="utf-8")
    destination = tmp_path / "pairs.jsonl"
    first = export_candidates(source, destination, limit=10, seed=42)
    second = export_candidates(source, destination, limit=10, seed=42)
    assert first == second
    assert first
    assert all(row["label"] is None for row in first)
    assert all(row["entity_type"] is None for row in first)
    assert all(row["left_evidence"] and row["right_evidence"] for row in first)
    persisted = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert persisted == first
