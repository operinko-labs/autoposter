"""Operator-triggered bulk artwork operations (Phase 7b).

Each mode -- backup, restore, poster reset, remove-overlays revert, and the
logo updater/revert -- is a job fanned to the worker pool via its own
``job.kind``. ``base`` holds the scaffolding every mode shares; the individual
modes live in their own modules alongside it.
"""
