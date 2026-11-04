"""Run existing DPO tests against the frozen-source patch without applying it."""
from pathlib import Path
import sys
import llm.exit
llm.exit.__path__.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
if __name__=='__main__':raise SystemExit(pytest.main(sys.argv[1:]))
