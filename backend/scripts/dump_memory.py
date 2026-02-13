#!/usr/bin/env python3
"""
Dump HippoRAG memory from a base directory (e.g. /tmp/terarchitect_memory).
Prints passages, entities, and triples from each project's openie_results_ner_*.json.
"""
import argparse
import json
import os


def dump_project(project_dir: str, project_id: str) -> None:
    """Dump one project's memory from openie_results_ner_*.json files."""
    for name in os.listdir(project_dir):
        if name.startswith("openie_results_ner_") and name.endswith(".json"):
            path = os.path.join(project_dir, name)
            with open(path) as f:
                data = json.load(f)
            docs = data.get("docs", [])
            print(f"\n{'='*60}")
            print(f"Project: {project_id}")
            print(f"File: {name}")
            print(f"Chunks: {len(docs)}")
            if data.get("avg_ent_chars") is not None:
                print(f"Avg entity chars/words: {data.get('avg_ent_chars')} / {data.get('avg_ent_words')}")
            print("=" * 60)
            for i, doc in enumerate(docs):
                idx = doc.get("idx", "?")
                passage = doc.get("passage", "")
                entities = doc.get("extracted_entities", [])
                triples = doc.get("extracted_triples", [])
                print(f"\n--- Chunk {i+1} ({idx}) ---")
                print(f"Passage: {passage[:500]}{'...' if len(passage) > 500 else ''}")
                if entities:
                    print(f"Entities: {entities}")
                if triples:
                    print("Triples:")
                    for t in triples:
                        if isinstance(t, (list, tuple)) and len(t) >= 3:
                            print(f"  ({t[0]}, {t[1]}, {t[2]})")
                        else:
                            print(f"  {t}")
            return
    print(f"\nProject {project_id}: no openie_results_ner_*.json found in {project_dir}")


def main():
    p = argparse.ArgumentParser(description="Dump HippoRAG memories from a base directory")
    p.add_argument(
        "base_dir",
        nargs="?",
        default="/tmp/terarchitect_memory",
        help="Base directory containing project UUID subdirs (default: /tmp/terarchitect_memory)",
    )
    args = p.parse_args()
    base = os.path.abspath(args.base_dir)
    if not os.path.isdir(base):
        print(f"Not a directory: {base}")
        return 1
    subdirs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    if not subdirs:
        print(f"No project subdirs in {base}")
        return 0
    for project_id in sorted(subdirs):
        dump_project(os.path.join(base, project_id), project_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
