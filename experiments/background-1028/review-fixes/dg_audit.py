"""Read-only DG inventory observation for D5; no shipment fact is supplied.

These helpers neither consume a generator RNG nor modify an input/output box.
An emitted inventory is a generated booking, not a checked cargo loading plan.
"""
from collections import Counter
from collections.abc import Callable, Iterable
from functools import wraps


def inventory_histogram(boxes: Iterable) -> dict:
    boxes = list(boxes)
    dangerous = [box for box in boxes if box.dg is not None]
    missing = Counter()
    classes = Counter()
    for box in dangerous:
        from stowage.generate.cargo_shipments import shipment_requirements
        from stowage.rules.imdg.dgl import lookup_dgl
        entry = lookup_dgl(int(box.dg.un_number), box.dg.packing_group)
        classes[entry.hazard_class] += 1
        for field in shipment_requirements(entry):
            if getattr(box.dg, field) is None:
                missing[f"{box.dg.un_number}:{field}"] += 1
    return {
        "boxes": len(boxes), "dg_boxes": len(dangerous),
        "by_un": dict(sorted(Counter(box.dg.un_number for box in dangerous).items())),
        "by_class": dict(sorted(classes.items())),
        "by_declared_class": dict(sorted(Counter(box.dg.imdg_class for box in dangerous).items())),
        "by_un_class_pg": dict(sorted(Counter(
            f"{box.dg.un_number}|{box.dg.imdg_class}|{box.dg.packing_group or '-'}" for box in dangerous).items())),
        "unknown_required_facts_by_un": dict(sorted(missing.items())),
        "unknown_sg_verification_by_un": dict(sorted(Counter(box.dg.un_number for box in dangerous
            if set(box.dg.segregation_codes) & {"SG27", "SG31", "SG34", "SG50", "SG53", "SG72"}
            and box.dg.verified_segregation_codes is None).items())),
    }


def requested_mix(params=None) -> dict:
    from stowage.params import load_param_file
    if params is None or isinstance(params, str):
        params = load_param_file("cargo", params or "feeder_export")
    classes = params["dg"]["classes"]
    class_total = sum(float(row["share"]["value"]) for row in classes)
    by_un, by_class = Counter(), Counter()
    for row in classes:
        share = float(row["share"]["value"])/class_total
        by_class[row["id"]] += share
        un_total = sum(float(un["share"]["value"]) for un in row["un_numbers"])
        for un in row["un_numbers"]:
            by_un[f"{int(un['un_number']):04d}"] += share*float(un["share"]["value"])/un_total
    eligible = set(params["dg"]["eligible_equipment_ids"])
    return dict(eligible_loaded_box_dg_probability=float(params["dg"]["share_of_eligible_loaded_boxes"]["value"]),
        eligible_iso_types=sorted({row["iso_size_type"] for row in params["equipment"] if row["id"] in eligible}),
        conditional_dg_un_probability=dict(sorted(by_un.items())),
        conditional_dg_class_probability=dict(sorted(by_class.items())))


def install_cargo_observer(events: list) -> Callable[[], None]:
    """Observe provider calls; restore() removes only the wrappers installed here.

    Import after adding this experiment directory to sys.path. Install once per
    process, then clear the supplied list before each independent generator call.
    Both generators' imported function references are handled explicitly.
    """
    from stowage.generate import cargo, instances, random_yard
    original = cargo.generate_load_list
    @wraps(original)
    def observed(*args, **kwargs):
        requested = requested_mix(kwargs.get("params"))
        event = dict(seed=kwargs.get("seed"), requested=requested,
            requested_n_boxes=args[2] if len(args) > 2 else kwargs.get("n_boxes"),
            requested_teu=kwargs.get("teu"))
        try:
            boxes = original(*args, **kwargs)
            eligible = set(requested["eligible_iso_types"])
            event.update(inventory=inventory_histogram(boxes),
                realised_eligible_loaded_boxes=sum(not box.empty and box.iso_type in eligible for box in boxes))
            return boxes
        except Exception as error:
            event["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            events.append(event)
    replaced = []
    for module in (cargo, instances, random_yard):
        if module.generate_load_list is original:
            module.generate_load_list = observed
            replaced.append(module)
    def restore():
        for module in replaced:
            if module.generate_load_list is observed:
                module.generate_load_list = original
    return restore
