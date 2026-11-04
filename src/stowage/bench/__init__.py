"""Public yard benchmark adapters, reusable by solver validation (tasks 4.6/4.7).

These helpers evaluate the simplified block relocation problem only. A BRP
result is not a certificate of ship stability or a full StowLM checker pass.
"""

from stowage.bench.loaders import BRPInstance, iter_zhu_instances, parse_brp_text

__all__ = ["BRPInstance", "iter_zhu_instances", "parse_brp_text"]
