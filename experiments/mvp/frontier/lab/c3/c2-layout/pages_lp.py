"""Closed-cover constructor restriction: ordinary heavy cargo must use hold."""
from pages import Model as PreviousModel


class Model(PreviousModel):
    def _static(self):
        out = super()._static()
        if self.closed_hold and self.T.e6_threshold is not None:
            for b in self.boxes:
                box = self.p.boxes[self.T.box_ids[b]]
                if (self.T.mass[b] >= self.T.e6_threshold and not box.dg
                        and not box.oog and not box.special_stowage):
                    out[b,self.T.slot_deck] = False
        return out
