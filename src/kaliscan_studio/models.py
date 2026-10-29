"""Modelos pequeños, serializables y separados de la interfaz."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


ConditionalMode = Literal["off", "ask", "auto"]


@dataclass
class ScanOptions:
    target_mode: Literal["ip", "web"]
    targets: list[str]
    exclusions: list[str] = field(default_factory=list)
    port_mode: Literal["top100", "all", "custom"] = "top100"
    custom_ports: str = ""
    timing: int = 3
    version_detection: bool = False
    script_categories: list[str] = field(default_factory=list)
    script_names: list[str] = field(default_factory=list)
    web_tools: list[str] = field(default_factory=list)
    conditional: dict[str, ConditionalMode] = field(default_factory=dict)
    formats: list[str] = field(default_factory=lambda: ["txt", "json"])
    wordlist: str = "/usr/share/wordlists/dirb/common.txt"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ScanOptions":
        return cls(**value)


@dataclass(frozen=True)
class PlannedTask:
    tool: str
    target: str
    reason: str
    conditional: bool = False
    outputs: tuple[str, ...] = ("txt",)


@dataclass
class Plan:
    options: ScanOptions
    tasks: list[PlannedTask]
    possible_tasks: list[PlannedTask]
    warnings: list[str]

    @property
    def planned_files(self) -> int:
        return sum(len(task.outputs) for task in self.tasks) + len({"json", "csv", "html"} & set(self.options.formats))

    @property
    def possible_files(self) -> int:
        return sum(len(task.outputs) for task in self.possible_tasks)
