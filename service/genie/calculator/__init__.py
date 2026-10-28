"""The campaign calculator: budget, dates and places in; the broadcast campaign that buys the
most plays out. Pure computation over one `calculator-inputs` snapshot (CALCULATOR-INPUTS-API.md):
no network, no model, and the same inputs always give the same plan.

  inputs.py   the snapshot, read and checked; the rates half is what Genie caches
  engine.py   which screens fit, what they cost the brand, and the plan
"""
