# Resume here

## Where we stopped

Converting the `scripts/*.py` entry points into **executed Jupyter notebooks** —
each cell run, with outputs saved — because NiceGUI's Vue frontend will not load
through VS Code's webview/port-forward (it needs ES modules + an importmap, and
the asset URLs are root-absolute).

Toolchain is installed in `.venv`: `nbformat`, `nbclient`, `ipykernel`, `ipython`.
Nothing has been built yet.

## Planned notebooks

One per existing script, mirroring what already works:

| Notebook | Replaces | Needs |
|---|---|---|
| `01_foundation_trust_boundary.ipynb` | `check_gate1.py` | nothing — pure Python |
| `02_multi_source_graph.ipynb` | `ingest.py` | Neo4j |
| `03_entity_resolution.ipynb` | `resolve_diseases.py` | Neo4j + open APIs |
| `04_schema_evolution_fail_open.ipynb` | `demo_fail_open.py` | Neo4j |
| `05_conflict_detection.ipynb` | `detect_conflicts.py` | Neo4j |
| `06_four_arm_evaluation.ipynb` | `run_four_arm_eval.py` | Bedrock |

Design intent: notebooks **read from the already-populated graph** rather than
re-ingesting, so they execute in seconds. Re-ingestion stays in the scripts.

## State as of the pause

- Pushed and public: https://github.com/Ramu-DE/BioMedicalGraph-neurosymbolic-governance-
  (97 files, `main`, commit `c1fda54`; `.env` verified absent)
- **197 tests** pass with no credentials and no network
- Graph in Aura: ~6,854 nodes, 8,034 relationships, 4 live sources
- 555 resolutions · 178 need review · 37 conflicts · 36 contested
- UI still running on :8099 — serves HTTP 200 but the Vue frontend can't
  initialise through VS Code

## Credentials — rotate these

`.env` is gitignored and was never committed, but the Aura password, Qdrant API
key and AWS STS credentials were all shared in plaintext during this session.
The AWS ones are session tokens and will expire on their own.

## Two options for the UI, for when we come back

1. **Notebooks** (chosen) — render natively in VS Code, no browser, no assets.
2. **Zero-JavaScript fallback route** — server-rendered plain HTML on the same
   FastAPI app, works through any proxy or webview. Still pure Python. Was about
   to be built when we switched to notebooks; worth keeping as a fallback since
   HTML forms would even let the review queue work without JS.

## Honest gaps still open (unchanged)

- no ontology / SHACL in v2 (8 TTL modules from v1 never ported)
- ~7k nodes vs the spec's 100k floor
- MONDO is a disease ontology; phenotype conditions (`Bradycardia`,
  `Albuminuria`) need HPO as a second authority
- trials and genes unresolved
- the two vocabulary "releases" are reconstructed from HGNC `prev_symbol`, not a
  diff of two dated release files
