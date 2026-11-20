"""Durable local coordination; transport adapters authenticate external events."""
from .core import Controller, InvalidEvent, EventConflict, JobConflict

__all__ = ["Controller", "InvalidEvent", "EventConflict", "JobConflict"]
