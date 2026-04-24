from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


DiscoveryStrategy = Callable[[Path, dict[str, Any]], list[dict[str, Any]]]


class ExtractorStrategy(Protocol):
    def __call__(self, repo_root: Path, module: dict[str, Any], config: dict[str, Any], *, evidence_hash: str) -> dict[str, Any]: ...


EvidenceStrategy = Callable[[Path, list[str], dict[str, Any]], str]


@dataclass(frozen=True)
class StrategyNotFoundError(RuntimeError):
    kind: str
    name: str
    available: list[str]

    def __str__(self) -> str:
        return f"strategy not found: kind={self.kind} name={self.name}"


_DISCOVERY: dict[str, DiscoveryStrategy] = {}
_EXTRACTOR: dict[str, ExtractorStrategy] = {}
_EVIDENCE: dict[str, EvidenceStrategy] = {}


def register_discovery(name: str) -> Callable[[DiscoveryStrategy], DiscoveryStrategy]:
    def deco(fn: DiscoveryStrategy) -> DiscoveryStrategy:
        _DISCOVERY[name] = fn
        return fn

    return deco


def register_extractor(name: str) -> Callable[[ExtractorStrategy], ExtractorStrategy]:
    def deco(fn: ExtractorStrategy) -> ExtractorStrategy:
        _EXTRACTOR[name] = fn
        return fn

    return deco


def register_evidence(name: str) -> Callable[[EvidenceStrategy], EvidenceStrategy]:
    def deco(fn: EvidenceStrategy) -> EvidenceStrategy:
        _EVIDENCE[name] = fn
        return fn

    return deco


def resolve_discovery(name: str) -> DiscoveryStrategy:
    fn = _DISCOVERY.get(name)
    if fn is None:
        raise StrategyNotFoundError(kind="discovery", name=name, available=sorted(_DISCOVERY.keys()))
    return fn


def resolve_extractor(name: str) -> ExtractorStrategy:
    fn = _EXTRACTOR.get(name)
    if fn is None:
        raise StrategyNotFoundError(kind="extractor", name=name, available=sorted(_EXTRACTOR.keys()))
    return fn


def resolve_evidence(name: str) -> EvidenceStrategy:
    fn = _EVIDENCE.get(name)
    if fn is None:
        raise StrategyNotFoundError(kind="evidence", name=name, available=sorted(_EVIDENCE.keys()))
    return fn
