# Notebooks

Every entry point as an executed notebook, **with outputs saved**. Open any of
them and the results are already there — no credentials needed to read, no
browser, no server.

Run in order; `03` ingests a small slice the later notebooks read.

| Notebook | Replaces | Needs | Runtime |
|---|---|---|---|
| [`01_verify_services.ipynb`](01_verify_services.ipynb) | `verify_services.py` | Neo4j · Qdrant · Bedrock | 7s |
| [`02_foundation_gate.ipynb`](02_foundation_gate.ipynb) | `check_gate1.py` | **nothing** | 2s |
| [`03_multi_source_ingest.ipynb`](03_multi_source_ingest.ipynb) | `ingest.py` | Neo4j + open APIs | 13s |
| [`04_entity_resolution.ipynb`](04_entity_resolution.ipynb) | `resolve_diseases.py` | Neo4j + open APIs | 26s |
| [`05_governance_decision_trace.ipynb`](05_governance_decision_trace.ipynb) | `demo_end_to_end.py` | nothing | 1s |
| [`06_vector_identity_filtering.ipynb`](06_vector_identity_filtering.ipynb) | `demo_vectors.py` | Qdrant | 6s |
| [`07_schema_evolution_fail_open.ipynb`](07_schema_evolution_fail_open.ipynb) | `demo_fail_open.py` + `check_rule_identifiers.py` | Neo4j | 4s |
| [`08_conflict_detection.ipynb`](08_conflict_detection.ipynb) | `detect_conflicts.py` | Neo4j | 17s |
| [`09_four_arm_evaluation.ipynb`](09_four_arm_evaluation.ipynb) | `run_four_arm_eval.py` | Bedrock | 28s |

`02` needs no credentials at all — it is the one to read first, and the one a
reviewer can verify in two minutes.

## Where to look

- **The reviewer's questions answered:** `02` (trust boundary, `CANNOT_EVALUATE`,
  claims lint) and `05` (a decision trace with the facts-withheld panel).
- **The claim that got withdrawn:** `09`. Giving the LLM judge the same facts the
  rules get closed most of the measured gap — the original comparison was
  confounded, exactly as the reviewer said.
- **Defects found in this code:** `04` (three, including fabricated confidence
  scores) and `08` (four resolver bugs surfaced by looking for disagreement).
- **The fail-open:** `07`. Same rule, same code, same facts — only the vocabulary
  release differs, and the rule silently stops protecting anything.

## Re-running

```bash
pip install -r ../requirements.txt
pip install nbformat nbclient ipykernel
python -m ipykernel install --user --name biomed --display-name "BioMedical (.venv)"
```

Then open in VS Code or Jupyter, or execute headlessly:

```bash
jupyter nbconvert --execute --inplace --to notebook 02_foundation_gate.ipynb
```

A notebook that raises fails execution, so `07`'s integrity section still works
as a CI gate — the semantics the script's exit code used to provide.

## Two things to expect

**`03` and `04` mutate the graph.** They ingest a small slice and resolve
entities. Re-running converges rather than duplicating (MERGE on natural keys),
but the counts in saved outputs will drift as the graph grows.

**`09` does not reproduce exactly.** The LLM judge is not deterministic across
runs even at temperature 0; the blind arm moved by one case between runs. The
notebook's final cell documents both runs rather than hiding the one that
disagreed.
