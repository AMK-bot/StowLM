"""Restricted page master with soft checker-derived physical rows.

Pricing remains the inherited heuristic; it does not certify this expanded LP.
"""
from __future__ import annotations
import copy
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csc_matrix, vstack, hstack, eye
from pages import Model
from hydro import HydroFeatures


class PhysicalModel(Model):
    def __init__(self, problem, **kwargs):
        super().__init__(problem, **kwargs)
        self.hydro = HydroFeatures(self)
        self.feature_names = self.hydro.feature_names
        self.fixed_columns = []
        self.physical_rows = self.hydro.rows()
        self.last_physical = {}

    def features(self, column):
        return self.hydro.vector(column)

    def clone(self):
        out = copy.copy(self)
        for key, value in self.__dict__.items():
            if isinstance(value, (dict, list, set)):
                setattr(out, key, copy.copy(value))
        out.hydro = copy.copy(self.hydro)
        out.hydro.model = out
        out.fixed_columns = []
        return out

    def solve_lp(self, *, active_boxes, active_stacks, hold_full, phase):
        boxes = sorted(active_boxes, key=self.row.get)
        rows = {b: k for k, b in enumerate(boxes)}
        stacks = sorted(active_stacks)
        srow = {j: len(boxes)+k for k,j in enumerate(stacks)}
        use = [k for k,c in enumerate(self.columns) if k not in self.banned
               and set(c.boxes) <= active_boxes and set(self.pages[c.page].stacks) <= active_stacks]
        data, ri, ci = [], [], []
        for i,k in enumerate(use):
            c = self.columns[k]
            for b in c.boxes:
                ri.append(rows[b]); ci.append(i); data.append(1.)
            for j in self.pages[c.page].stacks:
                ri.append(srow[j]); ci.append(i); data.append(1.)
        n = len(use)+len(boxes)
        for i,b in enumerate(boxes):
            ri.append(i); ci.append(len(use)+i); data.append(1.)
        A = csc_matrix((data,(ri,ci)),shape=(len(boxes)+len(stacks),n))
        F = np.column_stack([self.features(self.columns[k]) for k in use]) if use else np.zeros((len(self.feature_names),0))
        fixed = sum((self.features(self.columns[k]) for k in self.fixed_columns),np.zeros(len(self.feature_names)))
        side, rhs = [], []
        for row in self.physical_rows:
            lo, hi = row.lower, row.upper
            coefficients = row.coefficients
            projected = coefficients@F
            scale = max(1., float(np.max(np.abs(projected))) if len(use) else 1.)
            fixed_value = float(coefficients@fixed)
            if hi is not None and np.isfinite(hi):
                a = np.zeros(n); a[:len(use)] = projected/scale
                side.append(a); rhs.append((hi-fixed_value)/scale)
            if lo is not None and np.isfinite(lo):
                a = np.zeros(n); a[:len(use)] = -projected/scale
                side.append(a); rhs.append((fixed_value-lo)/scale)
        m = len(side)
        upper = vstack([A[len(boxes):],csc_matrix(np.array(side).reshape(m,n))],format='csc')
        slack = vstack([csc_matrix((len(stacks),m)),-eye(m)],format='csc')
        upper = hstack([upper,slack],format='csc')
        eq = hstack([A[:len(boxes)],csc_matrix((len(boxes),m))],format='csc')
        cost = [self.column_cost(self.columns[k],phase) for k in use]+[1000.]*len(boxes)+[10.]*m
        res = linprog(cost,A_eq=eq,b_eq=np.ones(len(boxes)),A_ub=upper,
                      b_ub=np.r_[np.ones(len(stacks)),rhs],bounds=(0,None),method='highs')
        if not res.success:
            raise RuntimeError(res.message)
        prices = np.zeros(len(self.boxes))
        for b,i in rows.items(): prices[self.row[b]] = res.eqlin.marginals[i]
        stack_prices = {j:float(res.ineqlin.marginals[i]) for i,j in enumerate(stacks)}
        x = {k:float(res.x[i]) for i,k in enumerate(use) if res.x[i]>1e-7}
        tail = res.x[len(use):len(use)+len(boxes)]
        self.short = {b:float(v) for b,v in zip(boxes,tail) if v>1e-6}
        self.last_physical = dict(values=dict(zip(self.feature_names,(F@res.x[:len(use)]+fixed).tolist())),
                                  normalized_slack=res.x[n:].tolist())
        return float(res.fun),x,prices,stack_prices,float(tail.sum())

    def dive(self, **kwargs):
        # Parent uses the same chosen list throughout retreat/repricing.
        self.fixed_columns = []
        return super().dive(**kwargs)
