"""Agent Pack execution (spec 11, 12): runs only inside the Pack's own Python environment (EV-002 CHG-07).
The LLMOps server never imports this package; it starts `python -m packrt.runner` / `python -m packrt.probe` in its sandbox."""
