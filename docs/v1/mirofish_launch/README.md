# v1 input bundle — MiroFish v2 launch

A drop-in input set for the original MiroFish (`/api/graph/ontology/generate`
and downstream simulation/report endpoints). Use this to run the same
scenario through v1 that `backend/seeds/mirofish_panel/` runs through v2,
so the two reports can be compared side by side.

## Files in this bundle

| File                       | What it is                                |
|----------------------------|-------------------------------------------|
| `01_product_overview.md`   | What MiroFish v2 is and is not.           |
| `02_evaluator_panel.md`    | The ten named evaluators.                 |
| `03_launch_brief.md`       | Launch date, channels, action menu, rules.|
| `prompt.txt`               | The simulation_requirement to paste in.   |

## How to use

1. Open the v1 web UI (the home page, not `/v2`).
2. In the upload form, attach all three `.md` files.
3. In the **Simulation Requirement** field, paste the contents of
   `prompt.txt`.
4. Submit and let the v1 pipeline build its ontology, graph, simulation,
   and report.

## Comparing v1 vs v2

Run both with the same panel and brief:

- **v1:** this bundle. Files + free-text prompt → ontology → graph →
  simulation → report.
- **v2:** `backend/seeds/mirofish_panel/`. Schema-driven study.json + CSVs
  → deterministic load → projection → engagement gate → narrator.

Same panelists, same brief, same launch context. Diff the reports to
see where the two pipelines agree on the headline signal and where they
diverge in the per-evaluator detail.
