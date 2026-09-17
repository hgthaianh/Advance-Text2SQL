"""Placeholder for intent, schema linking and SQL accuracy evaluation."""
import json
from pathlib import Path


def main() -> None:
    dataset = json.loads(Path(__file__).with_name("dataset.json").read_text(encoding="utf-8"))
    # TODO: evaluate against labeled Vietnamese examples.
    print(f"Loaded {len(dataset)} examples. Evaluation is not implemented.")


if __name__ == "__main__":
    main()
