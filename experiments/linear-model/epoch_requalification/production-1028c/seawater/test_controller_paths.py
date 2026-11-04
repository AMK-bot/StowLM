"""Verify diagnostic relocation changes storage paths and nothing scientific."""
import ast
import copy
import sys
import unittest
from unittest.mock import patch

import campaign


class NormalizeStorage(ast.NodeTransformer):
    def visit_Assign(self,node):
        if any(isinstance(target,ast.Name) and target.id in ("HERE","ROOT") for target in node.targets):
            return None
        return self.generic_visit(node)

    def visit_BinOp(self,node):
        if (isinstance(node.op,ast.Div) and isinstance(node.left,ast.Attribute)
                and isinstance(node.left.value,ast.Name) and node.left.value.id=="HERE"
                and node.left.attr=="parent" and isinstance(node.right,ast.Constant)
                and node.right.value in ("driver.py","edge_grid.py")):
            return ast.Name(id="IMMUTABLE_"+node.right.value,ctx=ast.Load())
        return self.generic_visit(node)

    def visit_Call(self,node):
        if (isinstance(node.func,ast.Name) and node.func.id=="Path" and len(node.args)==1
                and isinstance(node.args[0],ast.Constant)):
            for filename in ("driver.py","edge_grid.py"):
                if node.args[0].value==str(campaign.OWNER/filename):
                    return ast.Name(id="IMMUTABLE_"+filename,ctx=ast.Load())
        return self.generic_visit(node)


class DiagnosticRelocationTests(unittest.TestCase):
    def test_original_scientific_ast_is_preserved(self):
        self.assertTrue((campaign.ROOT/"pyproject.toml").exists())
        for name in ("claude-freeze-review-v2","claude-edge-cause","claude-face-pilot","claude-face-budget-pilot"):
            with self.subTest(producer=name):
                captured=[]
                def capture_compile(source,filename,mode):
                    captured.append((source,filename,mode))
                    return None
                saved_argv=sys.argv[:]
                try:
                    with patch.dict(campaign.__dict__,{"compile":capture_compile,"exec":lambda *args:None}):
                        campaign.diagnostic_worker(name)
                finally:
                    sys.argv=saved_argv
                self.assertEqual(len(captured),1)
                relocated,filename,mode=captured[0]
                original_path=campaign.ORIGINAL/(name+".py")
                self.assertEqual(filename,str(original_path))
                self.assertEqual(mode,"exec")
                original=ast.parse(original_path.read_text())
                changed=ast.parse(relocated)
                first=NormalizeStorage().visit(copy.deepcopy(original))
                second=NormalizeStorage().visit(copy.deepcopy(changed))
                self.assertEqual(ast.dump(first,include_attributes=False),ast.dump(second,include_attributes=False))


if __name__=="__main__": unittest.main()
