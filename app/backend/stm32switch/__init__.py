"""
stm32switch — modular STM32 parent/card pin-lane switching design generator.

Reads the analyzed ``stm32_profiles.sqlite`` engineering database and emits the
deterministic hardware-design artifacts that define what the KiCad schematic
blocks must be:

  * one canonical ``package_pin_matrix`` CSV per package, plus derived views
  * exact-pinout-group and pass-plan tables
  * a reusable hardware cell + parent service-router spec library (YAML)
  * generated documentation and validation reports

Core safety principle (enforced everywhere): a socket pin's required cell is
derived from its *complete* electrical role set, never from the dominant role.
The daughter card makes each pin safe; the parent reuses standardized services.
"""
from __future__ import annotations

__version__ = "0.1.0"
