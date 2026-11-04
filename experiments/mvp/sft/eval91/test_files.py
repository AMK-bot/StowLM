"""The 30 frozen test v2.1 inputs (15 tiny + 15 small), from the corpus hash list."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "experiments/mvp/corpus"


def test_files(rungs=("tiny", "small")) -> list[str]:
    out = []
    for line in (CORPUS / "test/SHA256SUMS").read_text().splitlines():
        rel = line.split()[1]
        if rel.endswith(".json") and not rel.endswith(".audit.json") and rel.split("/")[0] in rungs:
            out.append(str((CORPUS / "test" / rel).relative_to(ROOT)))
    return sorted(out)


if __name__ == "__main__":
    import sys
    print(" ".join(test_files(tuple(sys.argv[1:]) or ("tiny", "small"))))
