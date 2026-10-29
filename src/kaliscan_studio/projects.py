"""Persistencia local de proyectos, ejecuciones y evidencias."""

from __future__ import annotations

import csv
import html
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from .models import ScanOptions


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    clean = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-.")
    return clean[:80] or "item"


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "kaliscan-studio"


def _atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class Project:
    def __init__(self, root: Path):
        self.root = root.resolve()
        meta_path = self.root / "project.json"
        if not meta_path.is_file():
            raise ValueError("La carpeta no contiene un proyecto KaliScan Studio.")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.db_path = self.root / "project.sqlite3"
        self._init_db()

    @classmethod
    def create(cls, parent: Path, name: str, targets: list[str] | None = None) -> "Project":
        clean = safe_name(name)
        if not name.strip():
            raise ValueError("El proyecto necesita un nombre.")
        root = parent.expanduser().resolve() / clean
        root.mkdir(parents=True, exist_ok=False)
        _atomic_json(root / "project.json", {
            "schema": 1,
            "name": name.strip(),
            "created_at": now(),
            "targets": targets or [],
        })
        project = cls(root)
        remember_project(project.root)
        return project

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    target_mode TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'plan'
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    tool TEXT NOT NULL,
                    target TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    exit_code INTEGER,
                    stdout_path TEXT,
                    stderr_path TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    path TEXT NOT NULL,
                    format TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    target TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    observation_id INTEGER PRIMARY KEY REFERENCES observations(id),
                    status TEXT NOT NULL DEFAULT 'indicio',
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_obs_target ON observations(target, kind);
                CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id);
            """)

            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            if "kind" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'plan'")

    def new_run(self, options: ScanOptions, kind: str = "plan") -> str:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs (id,started_at,ended_at,status,target_mode,options_json,kind) VALUES (?, ?, NULL, 'running', ?, ?, ?)",
                (run_id, now(), options.target_mode, json.dumps(options.to_dict(), ensure_ascii=False), kind),
            )
        self._touch_execution()
        return run_id

    def end_run(self, run_id: str, status: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE runs SET ended_at=?, status=? WHERE id=?", (now(), status, run_id))
        self._touch_execution()

    def reopen_run(self, run_id: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("La ejecución no existe.")
            if row["status"] == "complete":
                raise ValueError("Esta ejecución ya está completa.")
            db.execute("UPDATE runs SET ended_at=NULL,status='running' WHERE id=?", (run_id,))
        self._touch_execution()

    def _touch_execution(self) -> None:
        self.meta["last_executed_at"] = now()
        _atomic_json(se…733 tokens truncated…ct() as db:
            return [dict(row) for row in db.execute("SELECT * FROM observations WHERE run_id=? ORDER BY id", (run_id,))]

    def compare_jobs(self) -> list[dict]:
        """Compara las dos ejecuciones más recientes de cada herramienta/objetivo."""
        groups: dict[tuple[str, str], list[dict]] = {}
        for job in self.jobs():
            if job["status"] == "complete" and job["tool"] != "resumen":
                groups.setdefault((job["tool"], job["target"]), []).append(job)
        changes: list[dict] = []
        with self._connect() as db:
            for (tool, target), jobs in groups.items():
                if len(jobs) < 2:
                    continue
                old, new = jobs[-2:]
                def values(job_id: str) -> set[tuple[str, str]]:
                    return {(row["kind"], row["value"]) for row in db.execute(
                        "SELECT kind,value FROM observations WHERE job_id=?", (job_id,)
                    )}
                previous, current = values(old["id"]), values(new["id"])
                if previous != current:
                    changes.append({
                        "tool": tool, "target": target,
                        "old_run": old["run_id"], "new_run": new["run_id"],
                        "added": sorted(current - previous),
                        "removed": sorted(previous - current),
                    })
        return changes

    def artifacts(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT artifacts.*,jobs.tool,jobs.target,jobs.run_id FROM artifacts JOIN jobs ON jobs.id=artifacts.job_id ORDER BY artifacts.id")]

    def latest_options(self) -> ScanOptions | None:
        runs = self.runs()
        return ScanOptions.from_dict(json.loads(runs[0]["options_json"])) if runs else None

    def dismiss_suggestion(self, tool: str, target: str) -> None:
        dismissed = set(self.meta.get("dismissed_suggestions", []))
        dismissed.add(f"{tool}|{target}")
        self.meta["dismissed_suggestions"] = sorted(dismissed)
        _atomic_json(self.root / "project.json", self.meta)

    def reviews(self) -> dict[int, dict]:
        with self._connect() as db:
            return {row["observation_id"]: dict(row) for row in db.execute("SELECT * FROM reviews")}

    def save_review(self, observation_id: int, status: str, note: str) -> None:
        if status not in {"indicio", "comprobado", "descartado"}:
            raise ValueError("Estado de hallazgo inválido.")
        with self._connect() as db:
            db.execute(
                "INSERT INTO reviews(observation_id,status,note) VALUES(?,?,?) "
                "ON CONFLICT(observation_id) DO UPDATE SET status=excluded.status,note=excluded.note",
                (observation_id, status, note),
            )

    def export_report(self) -> Path:
        observations = self.observations()
        reviews = self.reviews()
        evidence: dict[str, list[str]] = {}
        for artifact in self.artifacts():
            evidence.setdefault(artifact["job_id"], []).append(artifact["path"])
        rows = []
        for row in observations:
            review = reviews.get(row["id"], {})
            links = " ".join(
                f"<a href='../{quote(Path(path).as_posix())}'>{html.escape(Path(path).name)}</a>"
                for path in evidence.get(row["job_id"], [])[:3]
            )
            rows.append("<tr>" + "".join(
                f"<td>{html.escape(str(value))}</td>" for value in (
                    row["target"], row["kind"], row["value"],
                    review.get("status", "indicio"), review.get("note", ""),
                )
            ) + f"<td>{links}</td></tr>")
        folder = self.root / "informes"
        folder.mkdir(exist_ok=True)
        path = folder / f"informe-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        path.write_text(
            "<!doctype html><html lang='es'><meta charset='utf-8'><title>Informe KaliScan Studio</title>"
            "<style>body{font:16px sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5rem;vertical-align:top}"
            "th{text-align:left;background:#eee}</style>"
            f"<h1>{html.escape(self.meta['name'])}</h1><p>Generado: {html.escape(now())}</p>"
            "<p>Los resultados automáticos requieren revisión humana. Los archivos originales están en este proyecto.</p>"
            "<table><thead><tr><th>Objetivo</th><th>Tipo</th><th>Observación</th><th>Estado</th><th>Nota</th><th>Evidencia</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></html>", encoding="utf-8",
        )
        return path

    def export_summary(self, run_id: str, formats: list[str]) -> list[Path]:
        """Exporta índice normalizado sin reemplazar las salidas originales."""
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT target,kind,value,detail_json FROM observations WHERE run_id=? ORDER BY id", (run_id,)
            )]
        folder = self.root / f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}" / "resumen" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        base = folder / "observaciones"
        paths: list[Path] = []
        if "json" in formats:
            path = base.with_suffix(".json")
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            paths.append(path)
        if "csv" in formats:
            path = base.with_suffix(".csv")
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["target", "kind", "value", "detail_json"])
                writer.writeheader()
                writer.writerows(rows)
            paths.append(path)
        if "html" in formats:
            path = base.with_suffix(".html")
            body = "\n".join(
                "<tr>" + "".join(f"<td>{html.escape(str(row[col]))}</td>" for col in ("target", "kind", "value")) + "</tr>"
                for row in rows
            )
            path.write_text(
                "<!doctype html><html lang='es'><meta charset='utf-8'><title>Observaciones</title>"
                "<style>body{font:16px sans-serif;margin:2rem}table{border-collapse:collapse;width:100%}"
                "td,th{border:1px solid #ccc;padding:.5rem}th{text-align:left}</style>"
                f"<h1>Observaciones: {html.escape(self.meta['name'])}</h1>"
                f"<table><thead><tr><th>Objetivo</th><th>Tipo</th><th>Valor</th></tr></thead><tbody>{body}</tbody></table></html>",
                encoding="utf-8",
            )
            paths.append(path)
        return paths


def remember_project(root: Path) -> None:
    registry = data_dir() / "recent.json"
    try:
        items = json.loads(registry.read_text(encoding="utf-8")) if registry.is_file() else []
    except (ValueError, OSError):
        items = []
    path = str(root.resolve())
    items = [item for item in items if item.get("path") != path]
    items.insert(0, {"path": path, "last_run": now()})
    _atomic_json(registry, items[:100])


def recent_projects() -> list[dict]:
    registry = data_dir() / "recent.json"
    try:
        items = json.loads(registry.read_text(encoding="utf-8")) if registry.is_file() else []
    except (ValueError, OSError):
        return []
    return [item for item in items if Path(item.get("path", "")).joinpath("project.json").is_file()]
