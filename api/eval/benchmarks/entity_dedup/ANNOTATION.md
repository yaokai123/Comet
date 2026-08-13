# LoCoMo entity-pair annotation protocol

The candidate exporter creates **unlabeled** pairs. Similarity hints are only for
sampling and must never be treated as gold labels.

For each JSONL row, independently inspect `left_evidence` and `right_evidence`:

1. Set `entity_type` to `person`, `organization`, `place`, `work`, `other`, or
   `exclude` when the extractor did not find a named entity.
2. Set `label` to `true` only when both mentions identify the same real entity;
   set it to `false` when they identify different entities.
3. Put ambiguous cases in `annotation_note` and leave `label` as `null` until
   adjudication. Never infer identity from name similarity alone.
4. Two annotators label every row independently. A third person adjudicates all
   disagreements. Only rows with a Boolean `label` enter Precision/Recall/F1.

Report candidate-source SHA-256, exporter version/commit, sample count, class
balance, agreement (Cohen's kappa), adjudicated count, and the final confusion
matrix. LoCoMo is public conversational data, so results must be described as a
public conversational-memory entity benchmark—not as private personal memory.
