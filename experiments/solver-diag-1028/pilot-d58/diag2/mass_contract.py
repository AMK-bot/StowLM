"""Check the independent constructor's mass diagnosis against this exact pin."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    lashing = HERE / "frozen/src/stowage/checker/lashing.py"
    source = lashing.read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_check_lashing")
    expected = ast.parse("""for q in loads.values():
    if q.container.gross_mass_kg > 30500:
        violations.append(problem('Container exceeds synthetic IRS Table 3.3.1 mass rating.', RULE,
            slots=[q.slot], containers=[] if getattr(q.container, 'background', False) else [q.container.id],
            measured=q.container.gross_mass_kg, limit=30500., unit='kg'))
""").body[0]
    guards = [n for n in function.body if ast.dump(n) == ast.dump(expected)]
    assert len(guards) == 1, "Pinned unconditional mass guard changed"
    assert 'severity="hard"' in source
    full = HERE / "frozen/src/stowage/checker/full.py"
    assert '"Every load-list box must be aboard at departure."' in full.read_text()
    rows = []
    for case in json.loads((HERE / "notes/selection.json").read_text())["selected"]:
        path = Path(case["input"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["input_sha256"]
        instance = json.loads(path.read_text())
        required = set(instance["load_list"])
        bad = [dict(id=c["id"], gross_mass_kg=c["gross_mass_kg"])
               for c in instance["containers"] if c["id"] in required and c["gross_mass_kg"] > 30500]
        rows.append(dict(index=case["index"], input_sha256=case["input_sha256"],
                         checker_model_mass_contradiction=bool(bad), violating_required_boxes=bad))
    result = dict(checker_lashing_sha256=hashlib.sha256(lashing.read_bytes()).hexdigest(),
                  checker_full_sha256=hashlib.sha256(full.read_bytes()).hexdigest(),
                  guard_line=guards[0].lineno, guard_ast_verified=True, mass_limit_kg=30500,
                  cases=rows, contradiction_cases=sum(r["checker_model_mass_contradiction"] for r in rows),
                  proof="All load-list boxes must be aboard. Every completed cargo graph has the pinned unconditional hard per-container mass check. Required gross mass is invariant under slot/ballast/consumable choices. An invalid graph already fails securing; a valid graph containing these boxes fails the mass guard. This is a checker-model contradiction, not a physical impossibility claim.",
                  provenance="Independent constructor report prompted this check; its whole-file checker hash differs, so this script verifies the guard directly in our own source pin.")
    (HERE / "mass-contract.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(guard_ast_verified=True, contradiction_indices=[r["index"] for r in rows if r["checker_model_mass_contradiction"]])))


if __name__ == "__main__":
    main()
