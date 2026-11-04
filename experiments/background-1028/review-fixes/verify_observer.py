"""Check that the experiment's cargo observer preserves exact generated demand."""
import json
from dg_audit import install_cargo_observer, requested_mix
from stowage.generate import cargo, random_yard, instances


def main():
    before = cargo.generate_load_list("straits", 0, 500, seed=72)
    events = []
    restore = install_cargo_observer(events)
    try:
        after = cargo.generate_load_list("straits", 0, 500, seed=72)
        assert [box.model_dump() for box in before] == [box.model_dump() for box in after]
        assert len(events) == 1
        inventory = events[0]["inventory"]
        assert inventory["boxes"] == 500
        assert sum(inventory["by_un"].values()) == inventory["dg_boxes"]
        assert sum(inventory["by_class"].values()) == inventory["dg_boxes"]
        assert random_yard.generate_load_list is cargo.generate_load_list
        assert instances.generate_load_list is cargo.generate_load_list
        mix = requested_mix()
        assert abs(sum(mix["conditional_dg_un_probability"].values())-1.) < 1e-12
        assert abs(sum(mix["conditional_dg_class_probability"].values())-1.) < 1e-12
    finally:
        restore()
    print(json.dumps(dict(exact_inventory_preserved=True, boxes=500, dg_boxes=inventory["dg_boxes"],
                         provider_references_restored=(cargo.generate_load_list is random_yard.generate_load_list
                                                      is instances.generate_load_list))))


if __name__ == "__main__":
    main()
