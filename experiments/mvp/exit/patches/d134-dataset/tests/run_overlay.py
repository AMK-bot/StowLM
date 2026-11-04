"""Run existing tests against staged files without writing production source."""
from pathlib import Path
import sys
import llm.exit
STAGE=Path(__file__).resolve().parents[1]
ROOT=next(p for p in STAGE.parents if (p/'pyproject.toml').exists())
llm.exit.__path__.insert(0,str(STAGE))
from llm.exit import data,runner
# Staged files preserve their installed-path ROOT expression; overlay supplies
# the real repository root as installation would, without editing live modules.
data.ROOT=runner.ROOT=ROOT
import pytest
if __name__=='__main__':raise SystemExit(pytest.main(sys.argv[1:]))
