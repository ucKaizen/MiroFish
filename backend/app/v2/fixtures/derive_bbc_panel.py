"""
One-shot fixture builder.

Reads tinytroupe-panel/data/panel_seed.yaml + sherwood_brief.yaml from a sibling
repo and emits the BBC panel as a star-schema study under seeds/bbc_panel/.

Run from anywhere:
    python -m app.v2.fixtures.derive_bbc_panel \
        --tinytroupe ../../../tinytroupe-panel \
        --out backend/seeds/bbc_panel

Re-run is idempotent. Drops files into <out>/ and prints a one-line summary.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def derive(tinytroupe_root: Path, out: Path) -> dict[str, int]:
    panel = _load_yaml(tinytroupe_root / "data" / "panel_seed.yaml")["panel"]
    brief = _load_yaml(tinytroupe_root / "data" / "sherwood_brief.yaml")

    panelists: list[dict[str, Any]] = []
    genre_rows: list[dict[str, Any]] = []
    slot_rows: list[dict[str, Any]] = []
    voice_rows: list[dict[str, Any]] = []
    vocab_rows: list[dict[str, Any]] = []

    for p in panel:
        pid = p["panelist_id"]
        length_lo, length_hi = p["length_range_chars"]
        panelists.append({
            "panelist_id":               pid,
            "name":                      p["name"],
            "age":                       p["age"],
            "gender":                    p["gender"],
            "region":                    p["region"],
            "occupation":                p["occupation"],
            "household":                 p["household"],
            "voice_register":            p["voice_register"],
            "rater_bias":                p["rater_bias"],
            "clarity_sensitivity":       p["clarity_sensitivity"],
            "delayed_exposure_propensity": p["delayed_exposure_propensity"],
            "length_min_chars":          length_lo,
            "length_max_chars":          length_hi,
            "recorded_behaviour":        p["recorded_behaviour_summary"].strip(),
        })
        for genre, prop in p["genre_propensity"].items():
            genre_rows.append({"panelist_id": pid, "genre": genre, "propensity": prop})
        for slot, prop in p["slot_propensity"].items():
            slot_rows.append({"panelist_id": pid, "slot": slot, "propensity": prop})
        for i, ex in enumerate(p["voice_examples"]):
            voice_rows.append({"panelist_id": pid, "idx": i, "example": ex})
        for term in p.get("vocabulary_required") or []:
            vocab_rows.append({"panelist_id": pid, "polarity": "required", "term": term})
        for term in p.get("vocabulary_forbidden") or []:
            vocab_rows.append({"panelist_id": pid, "polarity": "forbidden", "term": term})

    panelist_columns = list(panelists[0].keys())
    _write_csv(out / "panelists.csv", panelists, panelist_columns)
    _write_csv(out / "panelist_genre_propensity.csv", genre_rows,
               ["panelist_id", "genre", "propensity"])
    _write_csv(out / "panelist_slot_propensity.csv", slot_rows,
               ["panelist_id", "slot", "propensity"])
    _write_csv(out / "panelist_voice_examples.csv", voice_rows,
               ["panelist_id", "idx", "example"])
    _write_csv(out / "panelist_vocabulary.csv", vocab_rows,
               ["panelist_id", "polarity", "term"])

    study = {
        "study_id": "bbc_panel_sherwood_s2_2024",
        "name": "BBC One Sunday Panel — Sherwood S2 prediction",
        "description": "10 UK viewers; predict Sherwood S2 episode 1 launch on 2024-08-25.",
        "identity": {
            "label": "Panelist",
            "key": "panelist_id",
            "csv": "panelists.csv",
            "columns": [
                {"name": "panelist_id",                 "type": "string"},
                {"name": "name",                        "type": "string"},
                {"name": "age",                         "type": "int"},
                {"name": "gender",                      "type": "string", "enum": ["F", "M", "X"]},
                {"name": "region",                      "type": "string"},
                {"name": "occupation",                  "type": "string"},
                {"name": "household",                   "type": "string"},
                {"name": "voice_register",              "type": "string"},
                {"name": "rater_bias",                  "type": "float"},
                {"name": "clarity_sensitivity",         "type": "string", "enum": ["low", "medium", "high"]},
                {"name": "delayed_exposure_propensity", "type": "float"},
                {"name": "length_min_chars",            "type": "int"},
                {"name": "length_max_chars",            "type": "int"},
                {"name": "recorded_behaviour",          "type": "string"}
            ]
        },
        "dimensions": [
            {
                "kind": "edge",
                "name": "genre_propensity",
                "csv": "panelist_genre_propensity.csv",
                "join_on": "panelist_id",
                "edge": "PROPENSITY_FOR_GENRE",
                "target": {"label": "Genre", "key": "genre"},
                "columns": [
                    {"name": "panelist_id", "type": "string"},
                    {"name": "genre",       "type": "string"},
                    {"name": "propensity",  "type": "float"}
                ]
            },
            {
                "kind": "edge",
                "name": "slot_propensity",
                "csv": "panelist_slot_propensity.csv",
                "join_on": "panelist_id",
                "edge": "PROPENSITY_FOR_SLOT",
                "target": {"label": "Slot", "key": "slot"},
                "columns": [
                    {"name": "panelist_id", "type": "string"},
                    {"name": "slot",        "type": "string"},
                    {"name": "propensity",  "type": "float"}
                ]
            },
            {
                "kind": "attribute",
                "name": "voice_examples",
                "as": "voice_examples",
                "csv": "panelist_voice_examples.csv",
                "join_on": "panelist_id",
                "columns": [
                    {"name": "panelist_id", "type": "string"},
                    {"name": "idx",         "type": "int"},
                    {"name": "example",     "type": "string"}
                ]
            },
            {
                "kind": "attribute",
                "name": "vocabulary",
                "as": "vocabulary",
                "csv": "panelist_vocabulary.csv",
                "join_on": "panelist_id",
                "columns": [
                    {"name": "panelist_id", "type": "string"},
                    {"name": "polarity",    "type": "string", "enum": ["required", "forbidden"]},
                    {"name": "term",        "type": "string"}
                ]
            }
        ],
        "brief": {
            "content_id":      brief["content_id"],
            "title":           brief["title"],
            "genre":           brief["genre"],
            "slot":            brief["slot"],
            "channel":         brief["channel"],
            "runtime_minutes": brief["runtime_minutes"],
            "air_date":        brief["air_date"],
            "synopsis":        brief["synopsis"].strip(),
            "rules":           brief["rules"]
        },
        "engagement": {
            "salience_threshold": 0.35,
            "daily_action_cap":   6,
            "embedding_model":    "text-embedding-3-small"
        }
    }
    with (out / "study.json").open("w", encoding="utf-8") as f:
        json.dump(study, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return {
        "panelists":     len(panelists),
        "genre_rows":    len(genre_rows),
        "slot_rows":     len(slot_rows),
        "voice_rows":    len(voice_rows),
        "vocab_rows":    len(vocab_rows),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tinytroupe", required=True, type=Path,
                    help="Path to the tinytroupe-panel repo root")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output directory for the study + CSVs")
    args = ap.parse_args()

    counts = derive(args.tinytroupe.resolve(), args.out.resolve())
    print(f"OK  panelists={counts['panelists']}  "
          f"genre_rows={counts['genre_rows']}  "
          f"slot_rows={counts['slot_rows']}  "
          f"voice_rows={counts['voice_rows']}  "
          f"vocab_rows={counts['vocab_rows']}  "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
