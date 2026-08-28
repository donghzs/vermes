"""Capability catalog package.

Backend half of the P0 "model capability catalog" feature. Aggregates
models.dev's full provider registry into a curated subset (pinned /
mainstream / longtail) for the Settings UI. Does NOT replace any existing
logic - it reads the same on-disk models.dev cache that ``agent.models_dev``
uses. Local model discovery and the chat-model dropdown are untouched.
"""
