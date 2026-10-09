from __future__ import annotations

from ..config import Config
from .api import ApiProvider
from .base import Answer, EmptyReply, FatalSolveError, Provider, SolveError, solve_task


def make_provider(cfg: Config) -> Provider:
    return ApiProvider(cfg.api)


__all__ = ["Answer", "ApiProvider", "EmptyReply", "FatalSolveError", "Provider", "SolveError", "make_provider", "solve_task"]
