# Seeds

Reference data sets, organised by which MiroFish pipeline consumes them.

```
seeds/
  v1/
    <study>/
      panel.md     — narrative description of the panel (one .md per panel)
      brief.md     — what's being launched / predicted
      prompt.txt   — pasted into the v1 form's "Simulation Requirement" field
  v2/
    <study>/
      study.json                          — schema-validated study definition
      panelists.csv                       — identity table
      panelist_genre_propensity.csv       — edge dimension (interest categories)
      panelist_slot_propensity.csv        — edge dimension (discovery channels)
      panelist_voice_examples.csv         — attribute dimension
      panelist_vocabulary.csv             — attribute dimension
```

## v1 vs v2

**v1** (`POST /api/graph/ontology/generate`): takes uploaded `.md/.pdf/.txt`
files plus a free-text `simulation_requirement`. Parses your documents into
an ontology, builds a graph, runs a simulation, generates a report. Good for
quick exploratory studies where the structure is still in flux.

**v2** (`POST /api/v2/runs`, UI at `/v2`): takes a schema-validated
`study.json` plus CSV dimensions. No ontology generation step — the schema
is the contract. Deterministic loading, inspectable Neo4j graph per study,
reproducible runs.

You can run the same conceptual study through both pipelines and diff the
reports. That's what the `bbc_panel` pair is for.

## bbc_panel — what's there

Same panel, same brief, two formats:

- `seeds/v1/bbc_panel/` — `panel.md` + `brief.md` + `prompt.txt`. Drop the
  two `.md` files into the v1 upload form and paste `prompt.txt` into the
  Simulation Requirement field.
- `seeds/v2/bbc_panel/` — `study.json` + 5 CSVs. Either zip the directory
  and upload via `/v2`, or hit `POST /api/v2/studies/from-disk` with
  `{"path": "seeds/v2/bbc_panel/study.json"}`.

The scenario is BBC One's Sherwood Series 2, Episode 1, broadcasting at
21:00 on Sunday 25 August 2024, predicted by ten UK viewers.

## Adding a new study (template)

1. **v2 first.** Author `study.json` against the schema (see
   `backend/app/v2/schemas/study.schema.json`) plus the dimension CSVs.
   Keep the column names — `persona.py` reads `panelist_id`, `name`, `age`,
   `region`, `occupation`, `household`, `voice_register`, `rater_bias`,
   `clarity_sensitivity`, `delayed_exposure_propensity`, `length_min_chars`,
   `length_max_chars`, `recorded_behaviour` by name.
2. **v1 mirror.** From the same source, write `panel.md` (one block per
   panelist), `brief.md` (what's being launched), `prompt.txt` (the
   simulation_requirement).

Run both, diff the reports.
