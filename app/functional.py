"""Fresh synthetic-call lifecycle, with model work and engine work isolated."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
from time import monotonic
import traceback
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CALLS = ROOT / "experiments/mvp/ui/functional/calls"
MAX_UPLOAD = 10 * 1024 * 1024
LABEL = "Synthetic ship and cargo, simulated port"
PLAN_READY_STATES = {"ready", "done", "failed", "checked_plan", "no_checked_plan", "relaxed", "relaxed_plan", "stopped"}
CALL_ID = re.compile(r"c\d{8}-\d{6}-[a-f0-9]{6}\Z")
PLANNER_PAYLOADS = frozenset({"ship", "boxes", "plan-before", "plan-after", "moves", "meters", "checks", "package", "state"})


def canonical_functional_path(path, *, decoded=False):
    """Decode a request path once into an unambiguous, idempotent routing path."""
    route = path if decoded else urlsplit(path).path
    if not decoded and "%" in route:
        route = unquote(route, encoding="utf-8", errors="strict")
    if any(character in route for character in "%?#\\") or any(ord(character) < 32 or ord(character) == 127 for character in route):
        raise ValueError("The Functional app path has an ambiguous encoding.")
    return route


class APIError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


@lru_cache(maxsize=64)
def _legacy_intake_summary(filename, modified_ns, size):
    # Old calls have only the worker receipt. Cache its public projection, never
    # its multi-megabyte frozen context, so repeated polls do not parse it again.
    value = _read(filename)
    return {key: item for key, item in value.items() if key != "raw"} if isinstance(value, dict) else None


def _intake_summary(path):
    summary = _read(path / "intake-view.json")
    if isinstance(summary, dict):
        return {key: value for key, value in summary.items() if key != "raw"}
    receipt = path / "intake.json"
    try:
        stat = receipt.stat()
    except OSError:
        return None
    return _legacy_intake_summary(str(receipt), stat.st_mtime_ns, stat.st_size)


def _booking_progress(path):
    index = path / "intake-bookings.json"
    try:
        # Legacy indices embed full receipts; the retry worker migrates them.
        # A poll must never open those receipts or individual booking files.
        if index.stat().st_size > 1024 * 1024:
            return []
    except OSError:
        return []
    value = _read(index, {})
    fields = ("booking_ref", "status", "boxes", "wall_s", "cost_usd", "file", "sha256")
    return [{**{key: row.get(key) for key in fields},
             **{key: row[key] for key in ("kept", "open_questions") if key in row}} for row in value.get("bookings", [])
            if isinstance(row, dict) and "view" not in row]


def _write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}-{secrets.token_hex(5)}.tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _write(path, value):
    _write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def _lines(path):
    try:
        rows = Path(path).read_text().splitlines()
    except OSError:
        return []
    result = []
    for row in rows:
        try:
            value = json.loads(row)
        except ValueError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _stage_views(path):
    """Keep the latest update of each stage instance in first-start order."""
    stages, keys, pending = [], {}, {}
    for row in _lines(path):
        stage, instance, began = row.get("stage"), row.get("instance_id"), row.get("started_utc")
        key = (row.get("attempt"), stage, "id", instance) if instance is not None else (row.get("attempt"), stage, "time", began) if began else None
        index = keys.get(key) if key is not None else None
        if index is None and instance is None and row.get("status") != "running":
            index = next((candidate for candidate in reversed(pending.get(stage, []))
                          if stages[candidate].get("status") == "running"
                          and stages[candidate].get("attempt") == row.get("attempt")
                          and stages[candidate].get("who") == row.get("who")), None)
        if index is None:
            index = len(stages)
            stages.append(dict(row))
            if row.get("status") == "running":
                pending.setdefault(stage, []).append(index)
        else:
            original = stages[index]
            stages[index] = {**original, **row, "started_utc": original.get("started_utc") or began}
        if key is not None:
            keys[key] = index
    return stages


_ENV_NAMES = frozenset({"MODEL_API_KEY", "STOWLM_ACCEPT_PAID_CALLS", "SPARK_CREDIT_READY", "STOWLM_FUNCTIONAL_CAP_USD"})


def load_env_file(path):
    """Parse our small dotenv subset without executing text or changing the environment."""
    path = Path(path)
    try:
        if not path.exists():
            return {}
        if path.stat().st_size > 64 * 1024:
            raise ValueError("The env file is too large; use a small KEY=VALUE file.")
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError("The env file could not be read; check its path and permissions.") from None
    values = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if name not in _ENV_NAMES:
            continue
        if not separator or "\x00" in value:
            raise ValueError("The env file needs valid KEY=VALUE lines.")
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValueError("The env file has an invalid quoted value.") from None
            if not isinstance(value, str):
                raise ValueError("The env file needs text values.")
        elif value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise ValueError("The env file has an invalid quoted value.")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


class KeyProvider:
    """One launch's credentials, consent and cap; public summaries never include keys."""

    def __init__(self, env_values=None, env_file=None, *, environ=None):
        self._lock = threading.RLock()
        self._mutation_lock = threading.Lock()
        self.env_file = Path(env_file) if env_file is not None else None
        file_values = (load_env_file(self.env_file) if self.env_file is not None else {}) if env_values is None else dict(env_values)
        environment = os.environ if environ is None else environ
        settings = {name: value for name, value in file_values.items() if name in _ENV_NAMES}
        settings.update({name: environment[name] for name in _ENV_NAMES if name in environment})
        self._environment_key = self._valid_key(environment.get("MODEL_API_KEY"))
        self._file_key = self._valid_key(file_values.get("MODEL_API_KEY")) if "MODEL_API_KEY" not in environment else None
        self._entered_key = None
        self._keys = {key for key in (self._environment_key, self._file_key) if key}
        self._accepted = any(settings.get(name) == "1" for name in ("STOWLM_ACCEPT_PAID_CALLS", "SPARK_CREDIT_READY"))
        # None delegates to the model layer's launch default. Explicit caps,
        # including zero, remain the planner's authorization.
        self._cap = self._valid_cap(settings["STOWLM_FUNCTIONAL_CAP_USD"]) if "STOWLM_FUNCTIONAL_CAP_USD" in settings else None
        self._ledger_active = False
        self.launch_id = secrets.token_hex(16)
        self.saved_to_env_file = False

    @staticmethod
    def _valid_key(value):
        if value is None or value == "":
            return None
        if not isinstance(value, str) or len(value) > 4096 or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("Enter a nonempty API key without spaces or line breaks.")
        return value

    @staticmethod
    def _valid_cap(value):
        try:
            if isinstance(value, bool):
                raise ValueError
            cap = float(value)
            if not math.isfinite(cap) or cap < 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise ValueError("The paid-call cap must be a finite, nonnegative dollar amount.") from None
        return cap

    def _source(self):
        return ("environment" if self._environment_key else "env_file" if self._file_key
                else "entered_in_app" if self._entered_key else None)

    def get_key(self):
        with self._lock:
            return self._environment_key or self._file_key or self._entered_key

    def status(self):
        with self._lock:
            summary = {"present": bool(self.get_key()), "source": self._source(),
                       "paid_calls_accepted": self._accepted, "cap_usd": self._cap}
            read_ledger = summary["present"] or self._ledger_active or self._cap is None
            launch_id = self.launch_id
        spent = 0.0
        if read_ledger:
            models = importlib.import_module("app.functional_models")
            spend = models.spend_status(launch_id=launch_id, cap_usd=summary["cap_usd"])
            if not spend.get("available"):
                raise APIError(503, "The Functional app spend ledger cannot be read; paid calls are stopped.")
            with self._lock:
                self._ledger_active = True
                if self._cap is None:
                    self._cap = self._valid_cap(spend["cap_usd"])
                if summary["cap_usd"] is None:
                    summary["cap_usd"] = self._cap
            spent = float(spend["spent_usd"])
        return {**summary, "spent_usd": spent}

    summary = status

    def availability_status(self):
        """Credential and consent presence only; no ledger wait or credential value."""
        with self._lock:
            return {"present": bool(self.get_key()), "source": self._source(),
                    "paid_calls_accepted": self._accepted, "cap_usd": self._cap}

    def worker_env(self):
        with self._lock:
            accepted = bool(self.get_key()) and self._accepted
            result = {"STOWLM_FUNCTIONAL_LAUNCH_ID": self.launch_id,
                      "STOWLM_ACCEPT_PAID_CALLS": "1" if accepted else "0",
                      "SPARK_CREDIT_READY": "1" if accepted else "0"}
            if self._cap is not None:
                result["STOWLM_FUNCTIONAL_CAP_USD"] = str(self._cap)
            if accepted:
                result["MODEL_API_KEY"] = self.get_key()
            return result

    def _save(self, key, accepted):
        path = ROOT / ".env"
        directory = ROOT / "experiments/mvp/ui/functional/spend"
        temporary = directory / f".env-{secrets.token_hex(6)}.tmp"
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            previous = path.read_text() if path.exists() else ""
            lines = [line for line in previous.splitlines()
                     if line.strip().removeprefix("export ").partition("=")[0].strip() not in _ENV_NAMES]
            lines.extend(["MODEL_API_KEY=" + json.dumps(key),
                          "STOWLM_ACCEPT_PAID_CALLS=" + ("1" if accepted else "0"),
                          "STOWLM_FUNCTIONAL_CAP_USD=" + str(self._cap)])
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write("\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, UnicodeError):
            raise APIError(400, "The key could not be saved to .env; check the repository permissions.") from None
        finally:
            temporary.unlink(missing_ok=True)

    def update(self, body):
        with self._mutation_lock:
            with self._lock:
                if not isinstance(body, dict) or set(body) - {"key", "accept_paid_calls", "cap_usd", "save_to_env_file"}:
                    raise APIError(400, "Supply a key, paid-call consent and an optional dollar cap.")
                for name in ("accept_paid_calls", "save_to_env_file"):
                    if name in body and not isinstance(body[name], bool):
                        raise APIError(400, "Paid-call consent and saving must be true or false.")
                key = self._valid_key(body.get("key")) if "key" in body else self._entered_key
                if "key" in body and key is None:
                    raise APIError(400, "Enter a nonempty API key.")
                cap = self._valid_cap(body["cap_usd"]) if "cap_usd" in body else self._cap
            models = importlib.import_module("app.functional_models")
            spend = models.configure_spend(launch_id=self.launch_id, **({"cap_usd": cap} if cap is not None else {}))
            if not spend.get("available"):
                raise APIError(503, "The Functional app spend ledger cannot be read; paid calls are stopped.")
            cap = self._valid_cap(spend["cap_usd"])
            with self._lock:
                self._ledger_active = True
                self._cap = cap
                self._entered_key = key
                if key:
                    self._keys.add(key)
                self._accepted = body.get("accept_paid_calls", self._accepted)
                self.saved_to_env_file = False
                save_key, save_accepted = self.get_key(), self._accepted
            if body.get("save_to_env_file"):
                if save_key is None:
                    raise APIError(400, "Enter an API key before saving it to .env.")
                self._save(save_key, save_accepted)
                with self._lock:
                    self.saved_to_env_file = True
        return self.status()

    set = update

    def clear(self):
        with self._mutation_lock:
            with self._lock:
                self._environment_key = self._file_key = self._entered_key = None
                self._accepted = False
                self.saved_to_env_file = False
        return self.status()

    def redact(self, value):
        with self._lock:
            keys = tuple(self._keys)
        text = str(value)
        for key in sorted(keys, key=len, reverse=True):
            text = text.replace(key, "[redacted]")
        return text


class FunctionalApp:
    """One server's jobs and single lazily-created on-device reader."""

    def __init__(self, adapter_dir=None, model_dir=None, root_dir=None, *, key_provider=None, env_file=None):
        self.adapter_dir = str(adapter_dir) if adapter_dir is not None else None
        self.model_dir = str(model_dir) if model_dir is not None else None
        self.root_dir = Path(root_dir) if root_dir is not None else CALLS
        self.key_provider = key_provider if key_provider is not None else KeyProvider(env_file=env_file)
        self._lock = threading.RLock()
        self._call_locks = {}
        self._reader_lock = threading.Lock()
        self._reader = None
        self._jobs = {}
        self._engine_call = None

    def _models(self):
        return importlib.import_module("app.functional_models")

    def _cases(self):
        return importlib.import_module("app.functional_cases")

    def _availability(self):
        provider = self.key_provider.availability_status() if hasattr(self.key_provider, "availability_status") else self.key_provider
        result = self._models().availability(key_provider=provider, adapter_dir=self.adapter_dir, model_dir=self.model_dir)
        if self._reader is not None and "reader" in result:
            result["reader"]["loaded"] = bool(getattr(self._reader, "loaded", result["reader"].get("loaded", False)))
        return result

    def _clean(self, value):
        if isinstance(value, str):
            return self.key_provider.redact(value)
        if isinstance(value, dict):
            return {self._clean(key): self._clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean(item) for item in value]
        return value

    def _directory(self, identity):
        if not CALL_ID.fullmatch(identity):
            raise APIError(404, "That call was not found.")
        path = self.root_dir / identity
        if not (path / "request.json").is_file() or path.is_symlink():
            raise APIError(404, "That call was not found.")
        return path

    def _state(self, path, state, message, **facts):
        _write(path / "worker-state.json", self._clean({"state": state, "message": message, **facts}))

    def _control(self, path, **values):
        control = _read(path / "worker-control.json", {})
        if "documents" in values or values.get("use_generated_load_list") or values.get("use_generated_papers"):
            control.pop("answers", None)
            control.pop("use_generator_records_for", None)
        if "use_generator_records_for" in values:
            control.pop("answers", None)
        if values.get("use_generated_load_list"):
            control.pop("documents", None)
            control["use_generated_papers"] = False
        control.update(adapter_dir=self.adapter_dir, model_dir=self.model_dir, **values)
        _write(path / "worker-control.json", self._clean(control))

    def _idle(self, path, allowed):
        if self._jobs.get(path.name, {}).get("active"):
            raise APIError(409, "A stage is already running for this call.")
        if _read(path / "worker-state.json", {}).get("state") not in allowed:
            raise APIError(409, "This call is not ready for that step.")

    def _available(self, path, stage, notes=None):
        info = self._availability()[stage]
        if not info.get("available"):
            reason = info.get("reason") or "The model is not available in this launch."
            self._stage(path, stage if stage != "spark" else "intake", "unavailable", mode="unavailable",
                        who=info.get("who"), where=info.get("where"), model=info.get("model"),
                        notes=f"{notes}. {reason}" if notes else reason)
            raise APIError(503, f"{info.get('who', stage)} unavailable: {reason}. Nothing was replayed in its place.")

    def _stage(self, path, stage, status="done", **fields):
        # Keep the exact shared stage shape in the worker and server.
        from app.functional_worker import append_stage
        if stage == "engine" and "attempt" not in fields:
            attempt = _read(path / "worker-control.json", {}).get("attempt")
            if attempt is not None:
                fields["attempt"] = attempt
        elif stage in {"reader", "voice", "confirm"} and "attempt" not in fields:
            # Instruction work belongs to the upcoming attempt, including a reread.
            runs = path / "runs"
            numbers = [int(item.name) for item in runs.iterdir() if item.is_dir() and item.name.isdigit()] if runs.is_dir() else []
            fields["attempt"] = max(numbers + [1 if (path / "run").exists() else 0]) + 1
        append_stage(path, stage, status=status, **self._clean(fields))

    def _finish_attempt(self, path, job):
        """Persist a small result once; polling never reparses historical runner receipts."""
        number = job.get("attempt")
        if number is None:
            return
        records = _read(path / "attempts.json", [])
        record = next((row for row in records if row.get("attempt") == number), None)
        if record is None:
            return
        raw = _read(path / "runs" / str(number) / "outcome.json", {})
        state = _read(path / "worker-state.json", {})
        record.update(status="stopped" if job.get("stopped") else raw.get("status") or "failed",
                      wall_s=(raw.get("timing") or {}).get("wall_s", job.get("elapsed_s")),
                      message=state.get("message"))
        _write(path / "attempts.json", self._clean(records))
        _write(path / "runs" / str(number) / "attempt.json", self._clean(record))

    @staticmethod
    def _stopped_plan(path):
        attempts = _read(path / "attempts.json", [])
        state = _read(path / "worker-state.json", {})
        return bool((attempts and attempts[-1].get("status") == "stopped") or
                    (state.get("stopped") and state.get("step", "plan") == "plan"))

    def _prepare_attempt(self, path, profile, instruction):
        """Archive a legacy run, then atomically redirect the compatibility path."""
        records = _read(path / "attempts.json", [])
        runs, latest = path / "runs", path / "run"
        if not runs.resolve().is_relative_to(path.resolve()) or not latest.resolve().is_relative_to(path.resolve()):
            raise APIError(409, "Plan attempt folders must stay inside this call folder.")
        runs.mkdir(exist_ok=True)
        if latest.exists() and not latest.is_symlink():
            # Existing calls predate attempt numbering. Keep their original runner bytes.
            destination = runs / "1"
            if destination.exists():
                raise APIError(409, "The legacy run cannot be archived safely; its first attempt folder already exists.")
            latest.rename(destination)
            raw = _read(destination / "outcome.json", {})
            packet = raw.get("packet") or _read(path / "packet.json", {})
            sentences = packet.get("voyage_sentences") or []
            old_sentence = (sentences[0].get("text") if isinstance(sentences[0], dict) else sentences[0]) if sentences else None
            matches = [row.get("id") for row in _read(path / "readings.json", []) if old_sentence and row.get("sentence") == old_sentence]
            old_reading = matches[0] if len(matches) == 1 else None
            previous_control = _read(path / "worker-control.json", {})
            records.append({"attempt": 1, "instruction": "confirmed" if sentences else "none",
                            "sentence": old_sentence,
                            "reading": old_reading,
                            "profile": packet.get("profile", previous_control.get("profile", "standard")),
                            "status": "stopped" if _read(path / "worker-state.json", {}).get("stopped") else raw.get("status", "failed"),
                            "wall_s": (raw.get("timing") or {}).get("wall_s"), "started_utc": None})
            latest.symlink_to(Path("runs/1"), target_is_directory=True)
            _write(path / "attempts.json", self._clean(records))
            journal = _lines(path / "stages.jsonl")
            for row in journal:
                if row.get("stage") in {"engine", "checker", "ladder", "package"} or row.get("source") == "runner_receipt":
                    row.setdefault("attempt", 1)
            temporary = path / "stages.jsonl.tmp"
            temporary.write_text("".join(json.dumps(row) + "\n" for row in journal))
            temporary.replace(path / "stages.jsonl")
        numbers = [int(item.name) for item in runs.iterdir() if item.is_dir() and item.name.isdigit()]
        for previous in records:
            if previous.get("status") == "running":
                raw = _read(runs / str(previous["attempt"]) / "outcome.json", {})
                previous.update(status=raw.get("status", "failed"),
                                wall_s=(raw.get("timing") or {}).get("wall_s"),
                                message="The previous plan worker ended before its attempt was finalized.")
        number = max(numbers + [row.get("attempt", 0) for row in records] + [0]) + 1
        directory = runs / str(number)
        directory.mkdir()
        confirmation = _read(path / "confirmation.json", {}) if instruction == "confirmed" else {}
        reading = next((row for row in _read(path / "readings.json", []) if row.get("id") == confirmation.get("reading")), {})
        record = {"attempt": number, "instruction": instruction, "sentence": reading.get("sentence"),
                  "reading": confirmation.get("reading"), "profile": profile, "status": "running",
                  "wall_s": None, "started_utc": _utc()}
        records.append(record)
        try:
            _write(directory / "attempt.json", self._clean(record))
            _write(path / "attempts.json", self._clean(records))
            temporary = path / ".latest-run"
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(Path("runs") / str(number), target_is_directory=True)
            temporary.replace(latest)
            (path / "admission.json").unlink(missing_ok=True)
            self._control(path, profile=profile, instruction=instruction, attempt=number)
        except Exception:
            record.update(status="failed", message="The plan attempt could not be prepared. See the server log.")
            _write(path / "attempts.json", self._clean(records))
            self._state(path, "failed", record["message"], step="plan", attempt=number)
            raise
        return number

    def dispatch(self, method, path, body=None):
        """Return an HTTP status and a JSON object (or an allowed download Path)."""
        began = monotonic()
        try:
            method = method.upper()
            route = canonical_functional_path(path)
            prefix = "/api/functional/"
            relative = route[len(prefix):] if route.startswith(prefix) else route.lstrip("/")
            parts = relative.split("/")
            if method != "GET" and len(parts) >= 2 and parts[0] == "calls" and CALL_ID.fullmatch(parts[1]):
                with self._call_lock(parts[1]):
                    status, payload = self._dispatch(method, route, body)
            else:
                status, payload = self._dispatch(method, route, body)
            return status, self._clean(payload)
        except APIError as error:
            return error.status, {"error": self.key_provider.redact(str(error))}
        except (TypeError, ValueError, binascii.Error):
            return 400, {"error": "Correct the request fields and try again."}
        except Exception:
            # Unexpected exceptions belong in the server log, never in API responses.
            sys.stderr.write(self.key_provider.redact(traceback.format_exc()))
            return 500, {"error": "The Functional app could not complete that request. See the server log."}
        finally:
            elapsed_ms = (monotonic() - began) * 1000
            if elapsed_ms > 1000:
                # Log route templates, never caller-controlled filenames, query strings or bodies.
                try:
                    safe_parts = relative.split("/")
                    safe_route = "/api/functional/unknown"
                    if relative in {"status", "options", "calls", "key"}:
                        safe_route = "/api/functional/" + relative
                    elif len(safe_parts) >= 2 and safe_parts[0] == "calls":
                        safe_route = "/api/functional/calls/<call>"
                        if len(safe_parts) > 2 and safe_parts[2] in {"papers", "answers", "audio", "read", "confirm", "plan", "stop", "plan-view", "file", "planner"}:
                            safe_route += "/" + safe_parts[2]
                    sys.stderr.write(self.key_provider.redact(f"{safe_route} {elapsed_ms:.0f} ms\n"))
                except Exception:
                    sys.stderr.write(f"/api/functional/unknown {elapsed_ms:.0f} ms\n")

    def _call_lock(self, identity):
        """Registry ownership is brief; disk and model work use only the named call's lock."""
        with self._lock:
            return self._call_locks.setdefault(identity, threading.RLock())

    def _dispatch(self, method, route, body):
        prefix = "/api/functional/"
        route = route[len(prefix):] if route.startswith(prefix) else route.lstrip("/")
        parts = route.split("/")
        if method == "GET" and route == "status":
            manifest = ROOT / "experiments/mvp/frontier/engine/source-manifest-0.13.0-review-fixes.json"
            engine_call = self._engine_call
            return 200, {"on": True, "availability": self._availability(), "key": self.key_provider.summary(),
                         "engine": {"version": "0.13.0", "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None,
                                    "busy": engine_call is not None, "call": engine_call},
                         "envelope": self._cases().options().get("envelope"), "label": "Functional app"}
        if method == "GET" and route == "options":
            return 200, {**self._cases().options(), "new_seed": self._cases().new_seed()}
        if method == "GET" and route == "calls":
            return 200, {"calls": self.recent_calls()}
        if route == "key" and method in {"POST", "DELETE"}:
            if method == "DELETE":
                key = self.key_provider.clear()
            else:
                if not isinstance(body, dict):
                    raise APIError(400, "Supply a key, paid-call consent and a spending cap.")
                key = self.key_provider.update(body)
            return 200, {"key": key,
                         "saved_to_env_file": bool(getattr(self.key_provider, "saved_to_env_file", False))}
        if method == "POST":
            body = {} if body is None else body
            if not isinstance(body, dict):
                raise APIError(400, "The request must be a JSON object.")
            if self._clean(body) != body:
                raise APIError(400, "Keep API credentials out of synthetic call inputs.")
        if method == "POST" and route == "calls":
            return self._new_call(body)
        if len(parts) < 2 or parts[0] != "calls":
            raise APIError(404, "That Functional app route was not found.")
        path = self._directory(parts[1])
        if method == "GET" and len(parts) == 2:
            return 200, self.view(path)
        action = parts[2] if len(parts) > 2 else ""
        if method == "GET" and action == "file":
            return 200, self._file(path, "/".join(parts[3:]))
        if method == "GET" and action == "planner" and len(parts) == 4:
            if parts[3] not in PLANNER_PAYLOADS:
                raise APIError(404, "That planner payload was not found.")
            planner = importlib.import_module("app.functional_planner")
            try:
                with self._call_lock(path.name):
                    if parts[3] in {"plan-after", "moves", "meters", "checks", "package"} and self._stopped_plan(path):
                        raise APIError(409, "The planner stopped this run; no plan was released.")
                    return 200, planner.payload(path, parts[3])
            except planner.NoPlan as error:
                raise APIError(409, str(error)) from None
        if len(parts) != 3:
            raise APIError(404, "That Functional app route was not found.")
        if method == "GET" and action == "plan-view":
            from app.functional_view import NoPlan, plan_view
            try:
                with self._call_lock(path.name):
                    if self._stopped_plan(path):
                        raise APIError(409, "The planner stopped this run; no plan was released.")
                    return 200, plan_view(path)
            except NoPlan as error:
                raise APIError(409, str(error)) from None
        if method != "POST":
            raise APIError(404, "That Functional app route was not found.")
        if action == "papers":
            return self._papers(path, body)
        if action == "answers":
            self._idle(path, {"needs_answers"})
            if not isinstance(body.get("answers"), dict) or not body["answers"]:
                raise APIError(400, "Supply answers to the intake questions.")
            self._control(path, answers=body["answers"])
            self._start_worker(path, "intake")
            return 202, self.view(path)
        if action == "audio":
            self._idle(path, PLAN_READY_STATES | {"needs_papers", "needs_answers"})
            origin = body.get("origin", "upload")
            if not isinstance(origin, str) or origin not in {"browser", "upload"}:
                raise APIError(400, "Choose browser or upload as the audio origin.")
            generated = body.get("generated", body.get("generated_voice", False))
            if not isinstance(generated, bool) or ("generated_voice" in body and
                    (not isinstance(body["generated_voice"], bool) or body["generated_voice"] != generated)):
                raise APIError(400, "Mark generated voice as true or false.")
            notes = "Generated voice" if generated else "Recorded in this browser" if origin == "browser" else "Uploaded audio"
            self._available(path, "voice", notes=notes)
            documents = self._uploads(path, [body], prefix="audio")
            previous = _read(path / "worker-state.json", {})
            self._start_thread(path, "voice", lambda: {**self._models().transcribe(Path(documents[0]["path"]), key_provider=self.key_provider),
                                                       "origin": origin, "generated": generated, "generated_voice": generated}, previous, notes=notes)
            return 202, self.view(path)
        if action == "read":
            return self._read_sentence(path, body)
        if action == "confirm":
            return self._confirm(path, body)
        if action == "plan":
            return self._plan(path, body)
        if action == "stop":
            self._stop(path)
            return 200, self.view(path)
        raise APIError(404, "That Functional app route was not found.")

    def _new_call(self, body):
        if self._clean(body) != body:
            raise APIError(400, "Keep API credentials out of synthetic call inputs.")
        options = self._cases().options()
        request = {**options.get("defaults", {}), **body}
        required = ("ship", "size_class", "fill_pct", "generator", "port")
        if any(key not in request for key in required):
            raise APIError(400, "Choose a ship, size, fill, generator and port.")
        for field, choices in (("ship", [row["id"] for row in options.get("ships", [])]),
                               ("size_class", options.get("size_classes", [])),
                               ("generator", options.get("generators", [])), ("port", options.get("ports", []))):
            if choices and request[field] not in choices:
                raise APIError(400, f"Choose one of the offered {field.replace('_', ' ')} values.")
        fill = request["fill_pct"]
        limits = options.get("fill_pct", {})
        if isinstance(fill, bool) or not isinstance(fill, (int, float)) or not limits.get("min", 10) <= fill <= limits.get("max", 100):
            raise APIError(400, "Fill must be a number within the offered limits.")
        seed = request.get("seed")
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise APIError(400, "The seed must be a non-negative integer.")
            if self._cases().seed_status(seed).get("sealed"):
                raise APIError(400, "A sealed seed cannot be used for a Functional call.")
        identity = "c" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        path = self.root_dir / identity
        path.mkdir(parents=True)
        # Keep the request exactly as received; defaults remain generator-owned.
        _write(path / "request.json", body)
        self._control(path)
        self._start_worker(path, "generate")
        return 202, self.view(path)

    def _uploads(self, path, documents, prefix="paper"):
        from app.functional_worker import document_metadata
        if not isinstance(documents, list) or not documents:
            raise APIError(400, "Supply at least one document.")
        decoded = []
        total = 0
        for document in documents:
            if not isinstance(document, dict):
                raise APIError(400, "Each document must be a JSON object.")
            metadata = document_metadata(document)
            name = document.get("name")
            media = document.get("media_type", "audio/wav" if prefix == "audio" else "text/plain" if "text" in document else "application/octet-stream")
            if not isinstance(name, str) or not name.strip() or not isinstance(media, str):
                raise APIError(400, "Each document needs a name and media type.")
            media = media.split(";", 1)[0].strip().lower()
            name = re.sub(r"[^a-zA-Z0-9._-]", "_", name.replace("\\", "/").rsplit("/", 1)[-1])[:120].strip(".") or "document"
            if "text" in document and "data_b64" not in document and isinstance(document["text"], str):
                data, origin = document["text"].encode(), "pasted"
            elif "data_b64" in document and "text" not in document and isinstance(document["data_b64"], str):
                if len(document["data_b64"]) > ((MAX_UPLOAD + 2) // 3) * 4:
                    raise APIError(413, "Uploads must total no more than 10 MB.")
                data, origin = base64.b64decode(document["data_b64"], validate=True), "uploaded"
            else:
                raise APIError(400, "Supply each document as text or base64 bytes.")
            total += len(data)
            if total > MAX_UPLOAD:
                raise APIError(413, "Uploads must total no more than 10 MB.")
            self._validate_content(data, media, audio=prefix == "audio")
            decoded_text = data.decode("utf-8", errors="ignore")
            if self.key_provider.redact(decoded_text) != decoded_text:
                raise APIError(400, "Keep API credentials out of synthetic uploads.")
            decoded.append((name, media, data, origin, metadata))
        rows = []
        token = secrets.token_hex(3)
        for index, (name, media, data, origin, metadata) in enumerate(decoded, 1):
            destination = path / "papers" / f"{index}-{prefix}-{token}-{name}"
            _write_bytes(destination, data)
            rows.append({"name": name, "media_type": media, "path": str(destination.resolve()), "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest(), "origin": origin, **metadata})
        return rows

    @staticmethod
    def _validate_content(data, media, *, audio=False):
        if audio:
            if media not in {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}:
                raise APIError(400, "Upload mono PCM16 WAV audio at 16 or 24 kHz, up to 60 seconds.")
            import io
            import wave
            try:
                with wave.open(io.BytesIO(data), "rb") as wav:
                    frames = wav.readframes(wav.getnframes())
                    valid = (wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getcomptype() == "NONE"
                             and wav.getframerate() in {16000, 24000} and 0 < wav.getnframes() / wav.getframerate() <= 60
                             and len(frames) == wav.getnframes() * 2 and any(frames))
            except (wave.Error, EOFError, ValueError, OSError):
                valid = False
            if not valid:
                raise APIError(400, "Use a complete, non-silent mono PCM16 WAV at 16 or 24 kHz, up to 60 seconds.")
            return
        if media.startswith("text/") or media in {"application/json", "application/csv"}:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeError:
                raise APIError(400, "Cargo text and CSV papers must be valid UTF-8.") from None
            if not text.strip():
                raise APIError(400, "Supply nonempty synthetic cargo text or CSV.")
            return
        pictures = {"image/png": len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR",
                    "image/jpeg": len(data) >= 4 and data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"),
                    "image/webp": len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
        if media not in pictures:
            raise APIError(400, "Supply cargo papers as UTF-8 text/CSV or PNG, JPEG, or WebP pictures; PDF is not supported.")
        if not pictures[media]:
            raise APIError(400, "Picture bytes must match their PNG, JPEG, or WebP media type.")

    def _papers(self, path, body):
        self._idle(path, {"needs_papers", "needs_answers", "ready", "failed"})
        if not (path / "case.json").is_file():
            raise APIError(409, "Generate the synthetic call before choosing its load list.")
        if (path / "run").exists():
            raise APIError(409, "This call already has an engine run. Generate a fresh call to change its cargo.")
        choices = (body.get("use_generated_load_list") is True, body.get("use_generated_papers") is True,
                   "documents" in body, "use_generator_records_for" in body)
        if sum(choices) > 1:
            raise APIError(400, "Choose uploaded papers, generated papers, the generated load list, or specific generator records.")
        if "use_generator_records_for" in body:
            boxes = body["use_generator_records_for"]
            if (not isinstance(boxes, list) or not boxes or
                    any(not isinstance(box, str) or not box.strip() or len(box) > 100 for box in boxes) or
                    len(set(boxes)) != len(boxes)):
                raise APIError(400, "Choose a nonempty list of unique container IDs for generator records.")
            from app.functional_worker import generator_records_input
            try:
                self._models().merge_generator_records(generator_records_input(path), path / "base-instance.json", boxes)
            except ValueError as error:
                raise APIError(400, str(error)) from None
            self._control(path, use_generator_records_for=boxes, use_generated_load_list=False)
            self._invalidate_reading(path)
            self._start_worker(path, "intake")
        elif body.get("use_generated_load_list") is True:
            self._control(path, use_generated_load_list=True)
            skipped = {"skipped": True, "why": "The planner chose the generated load list, not read by a model."}
            _write(path / "intake.json", skipped)
            _write(path / "intake-view.json", skipped)
            self._stage(path, "intake", "skipped", mode="skipped", who="Generated load list", where="software",
                        cost_usd=0., model=None, model_call=False,
                        notes="The planner chose the generated load list, not read by a model; no model call.")
            self._invalidate_reading(path)
            self._start_worker(path, "assemble")
        else:
            self._available(path, "spark")
            generated = body.get("use_generated_papers") is True
            if generated:
                from app.functional_worker import generated_papers
                documents = generated_papers(path)
            else:
                documents = self._uploads(path, body.get("documents"))
            _write(path / "papers/meta.json", {"documents": documents})
            _write(path / "papers.json", {"documents": documents})
            self._control(path, documents=documents, use_generated_load_list=False, use_generated_papers=generated)
            # Receipts remain available for exact-match paid-read reuse. A prior
            # merged load list is not the result of this new reading, however.
            (path / "intake.json").unlink(missing_ok=True)
            (path / "intake-view.json").unlink(missing_ok=True)
            self._invalidate_reading(path)
            self._start_worker(path, "intake")
        return 202, self.view(path)

    @staticmethod
    def _invalidate_reading(path):
        # A changed load list has a new context; its old reading cannot be confirmed or run.
        for name in ("readings.json", "confirmation.json", "context.json", "assembled-instance.json", "packet.json"):
            (path / name).unlink(missing_ok=True)

    def _read_sentence(self, path, body):
        self._idle(path, PLAN_READY_STATES)
        if not (path / "context.json").is_file():
            raise APIError(409, "Assemble the call before reading an instruction.")
        sentence, origin = body.get("sentence"), body.get("origin", "typed")
        if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > 20000:
            raise APIError(400, "Supply a nonempty instruction of at most 20,000 characters.")
        if origin not in {"typed", "voice", "planner_edit"}:
            raise APIError(400, "Choose a typed, voice or planner_edit instruction origin.")
        self._available(path, "reader")
        context = _read(path / "context.json")

        def read_now():
            with self._reader_lock:
                if self._reader is None:
                    self._reader = self._models().Reader(adapter_dir=self.adapter_dir, model_dir=self.model_dir)
                reading = self._reader.read(sentence, context)
            return {**reading, "id": "r" + secrets.token_hex(6), "origin": origin, "confirmed": False}

        self._start_thread(path, "reader", read_now, _read(path / "worker-state.json", {}))
        return 202, self.view(path)

    def _confirm(self, path, body):
        from app.v6 import _rule_digest, _sha256
        self._idle(path, PLAN_READY_STATES)
        readings = _read(path / "readings.json", [])
        reading = next((row for row in readings if row.get("id") == body.get("reading")), None)
        if not reading or reading.get("status") != "constraint_ready":
            raise APIError(409, "Choose a ready reading from this call before confirming.")
        binding = {"sentence_sha256": _sha256(reading["sentence"]), "rule_sha256": _rule_digest(reading["answer"])}
        if any(body.get(key) != value for key, value in binding.items()):
            raise APIError(409, "The sentence or rule changed. Review the reading and confirm its current hashes.")
        _write(path / "confirmation.json", {"reading": reading["id"], **binding,
                                             "source": "functional_app_planner_click", "confirmed_utc": _utc()})
        for row in readings:
            row["confirmed"] = row.get("id") == reading["id"]
        _write(path / "readings.json", readings)
        self._stage(path, "confirm", who="Planner", where="this laptop", input_sha256=binding["sentence_sha256"],
                    output_sha256=binding["rule_sha256"], notes="The planner confirmed this exact sentence and rule.")
        return 200, self.view(path)

    def _plan(self, path, body):
        self._idle(path, PLAN_READY_STATES)
        if not (path / "context.json").is_file():
            raise APIError(409, "Assemble the call before planning.")
        from app.functional_worker import app_envelope
        case = _read(path / "case.json", {})
        envelope = app_envelope(case, _read(path / "envelope.json") or _read(path / "runner-envelope.json") or case.get("envelope") or {})
        if not envelope.get("passed", envelope.get("in_envelope", False)):
            message = "; ".join(row.get("message", "") for row in envelope.get("reasons", []))
            self._state(path, "out_of_envelope", message or "This call is outside the supported design envelope.")
            raise APIError(409, message or "This call is outside the supported design envelope.")
        if self._engine_call is not None:
            raise APIError(409, "An engine job is already running in this server. Wait for it or stop that call.")
        profile, instruction = body.get("profile", "standard"), body.get("instruction")
        if profile not in {"live", "standard"} or instruction not in {"confirmed", "none"}:
            raise APIError(400, "Choose live or standard and a confirmed instruction or none.")
        if instruction == "confirmed" and not (path / "confirmation.json").is_file():
            raise APIError(409, "Confirm a ready reading before planning with that instruction.")
        with self._lock:
            if self._engine_call is not None:
                raise APIError(409, "An engine job is already running in this server. Wait for it or stop that call.")
            if getattr(self, "_closing", False):
                raise APIError(409, "This server is shutting down; no new stage can start.")
            self._engine_call = path.name
        try:
            attempt = self._prepare_attempt(path, profile, instruction)
            if instruction == "none":
                self._stage(path, "reader", "skipped", mode="skipped", who="StowLM Reader", where="this laptop",
                            provenance="no_instruction", model_call=False,
                            attempt=attempt,
                            notes="The planner chose no instruction; the reader had nothing to do.")
            self._start_worker(path, "plan")
        except Exception:
            self._finish_attempt(path, {"attempt": locals().get("attempt")})
            with self._lock:
                if self._engine_call == path.name:
                    self._engine_call = None
            raise
        return 202, self.view(path)

    def _start_worker(self, path, step):
        with self._call_lock(path.name):
            return self._launch_worker(path, step)

    def _launch_worker(self, path, step):
        if getattr(self, "_closing", False):
            raise APIError(409, "This server is shutting down; no new stage can start.")
        states = {"generate": ("generating", "Generating fresh synthetic ship and cargo."),
                  "intake": ("reading_papers", "Reading synthetic cargo papers."),
                  "assemble": ("assembling", "Assembling and screening the synthetic call."),
                  "plan": ("planning", "Searching for a checked plan.")}
        if step == "intake" and "use_generator_records_for" in _read(path / "worker-control.json", {}):
            states["intake"] = ("reading_papers", "Merging the selected generator records with the retained Muse Spark reading; no model call.")
        self._state(path, *states[step])
        environment = os.environ.copy()
        provider = getattr(self, "key_provider", None)
        for name in ("MODEL_API_KEY", "SPARK_CREDIT_READY", "STOWLM_ACCEPT_PAID_CALLS", "STOWLM_FUNCTIONAL_CAP_USD"):
            environment.pop(name, None)
        if provider is not None:
            environment.update(provider.worker_env())
        launch_key = environment.get("MODEL_API_KEY")
        environment.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
        paths = [str(ROOT / "src"), str(ROOT / "experiments/mvp/frontier"), str(ROOT / "experiments/mvp/frontier/lab"), str(ROOT)]
        if environment.get("PYTHONPATH"):
            paths.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(paths)
        command = ["nice", "-n", "5", sys.executable, "-m", "app.functional_worker", step, str(path.resolve())]
        log_path = path / f"worker-{step}.log"
        log_path.touch()
        started_utc = _utc()
        engine_instance = "engine-plan-" + secrets.token_hex(8) if step == "plan" else None
        if engine_instance:
            self._stage(path, "engine", "running", instance_id=engine_instance, started_utc=started_utc,
                        who="Frontier Engine 0.13.0", where="software", cost_usd=0.,
                        notes="Searching for a checked plan.")
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except Exception:
            self._state(path, "failed", "The worker could not start. See its worker log.")
            if engine_instance:
                self._stage(path, "engine", "failed", instance_id=engine_instance, started_utc=started_utc,
                            notes="The plan worker could not start. See its worker log.")
            raise
        job = {"active": True, "path": path, "process": process, "pgid": process.pid, "step": step,
               "started": monotonic(), "started_utc": started_utc, "stopped": False,
               "group_exited": threading.Event(), "cleanup_lock": threading.Lock()}
        if step == "plan":
            job["attempt"] = _read(path / "worker-control.json", {}).get("attempt")
        with self._lock:
            self._jobs[path.name] = job

        def redact(text):
            text = str(text)
            if launch_key:
                text = text.replace(launch_key, "[redacted]")
            return provider.redact(text) if provider is not None else text

        def drain_log():
            stream = getattr(process, "stdout", None)
            if stream is None:
                return
            try:
                with log_path.open("a") as log:
                    # A whole line retains keys split across child writes before redaction.
                    for line in iter(stream.readline, b""):
                        if not line:
                            break
                        log.write(redact(line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line))
                        log.flush()
            finally:
                stream.close()

        def cleanup_group():
            # Only this launch's recorded process group is ever inspected or signalled.
            # Lightweight fake processes have no native group to inspect.
            if not callable(getattr(process, "poll", None)):
                job["group_exited"].set()
                return
            with job["cleanup_lock"]:
                if job["group_exited"].is_set():
                    return True
                if job.get("cleanup_failed"):
                    return False
                deadline = monotonic() + 1.0
                final_deadline = deadline + 1.0
                terminated = killed = False
                pause = threading.Event()
                while True:
                    try:
                        os.killpg(job["pgid"], 0)
                        if not terminated:
                            os.killpg(job["pgid"], signal.SIGTERM)
                            terminated = True
                        if not killed and monotonic() >= deadline:
                            os.killpg(job["pgid"], signal.SIGKILL)
                            killed = True
                    except ProcessLookupError:
                        job["group_exited"].set()
                        return True
                    except PermissionError:
                        # macOS may briefly refuse inspection while a group is exiting.
                        # Retry within the same bounded deadline; never infer that it exited.
                        pass
                    if monotonic() >= final_deadline:
                        job["cleanup_failed"] = True
                        reason = "Owned worker cleanup could not complete; its process group remains reserved."
                        with self._call_lock(path.name):
                            if job.get("stopped"):
                                job["finalize_stop"](reason)
                            else:
                                self._state(path, "failed", reason)
                                self._stage(path, "engine" if step == "plan" else step, "failed", notes=reason)
                        return False
                    # A bounded failure retains ownership instead of releasing a live group.
                    pause.wait(.02)

        job["redact"], job["cleanup_group"] = redact, cleanup_group
        job["log_thread"] = threading.Thread(target=drain_log, name=f"functional-log-{path.name}", daemon=True)
        job["log_thread"].start()
        job["thread"] = threading.Thread(target=self._watch, args=(path, job), name=f"functional-{step}-{path.name}", daemon=True)
        job["thread"].start()
        if getattr(self, "_closing", False):
            self._stop(path)

    def _watch(self, path, job):
        process, step = job["process"], job["step"]
        try:
            if callable(getattr(process, "poll", None)):
                while True:
                    try:
                        code = process.wait(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        if job.get("cleanup_failed"):
                            return
            else:
                code = process.wait()
            # A leader may exit while its engine descendants remain in the owned group.
            if job["cleanup_group"]() is False:
                return
            job["log_thread"].join(timeout=1)
            with self._call_lock(path.name):
                with self._lock:
                    job.update(active=False, elapsed_s=round(monotonic() - job["started"], 3))
                    if step == "plan" and self._engine_call == path.name:
                        self._engine_call = None
                if job["stopped"]:
                    job["finalize_stop"]()
                    self._finish_attempt(path, job)
                    return
                state = _read(path / "worker-state.json", {})
                if code != 0 and state.get("state") not in {"failed", "out_of_envelope", "needs_answers"}:
                    self._state(path, "failed", f"The {step} worker failed. See worker-{step}.log for details.")
                elif step == "intake" and code == 0 and state.get("state") == "assembling":
                    self._start_worker(path, "assemble")
                if step == "plan":
                    self._fail_open_engine_stage(path, state.get("message") if state.get("state") == "failed" else None)
                    self._finish_attempt(path, job)
        except Exception:
            with (path / f"worker-{step}.log").open("a") as log:
                log.write(job["redact"](traceback.format_exc()))
            # Failed monitoring must not release an engine slot with owned children alive.
            if job["cleanup_group"]() is False:
                return
            with self._call_lock(path.name):
                with self._lock:
                    job["active"] = False
                    if self._engine_call == path.name:
                        self._engine_call = None
                if job["stopped"]:
                    job["finalize_stop"]()
                else:
                    self._state(path, "failed", f"The {step} worker could not complete. See its worker log.")
                    if step == "plan":
                        self._fail_open_engine_stage(path)
                self._finish_attempt(path, job)

    def _fail_open_engine_stage(self, path, message=None):
        # A crashed worker cannot publish its receipt. Close only its still-open
        # stage; completed receipt-backed stages retain their own verdicts.
        attempt = _read(path / "worker-control.json", {}).get("attempt")
        for row in _stage_views(path / "stages.jsonl"):
            if row.get("stage") == "engine" and row.get("status") == "running" and row.get("attempt") == attempt:
                self._stage(path, "engine", "failed", instance_id=row.get("instance_id"),
                            attempt=row.get("attempt"),
                            started_utc=row.get("started_utc"),
                            notes=message or "The plan worker finished without an engine completion record. See its worker log.")

    def _start_thread(self, path, stage, function, previous, *, notes=None):
        with self._call_lock(path.name):
            return self._launch_thread(path, stage, function, previous, notes=notes)

    def _launch_thread(self, path, stage, function, previous, *, notes=None):
        if getattr(self, "_closing", False):
            raise APIError(409, "This server is shutting down; no new stage can start.")
        job = {"active": True, "path": path, "process": None, "step": stage, "started": monotonic(), "stopped": False}
        with self._lock:
            self._jobs[path.name] = job
        info = self._availability().get(stage, {})
        fields = {key: info.get(key) for key in ("who", "where", "model", "adapter_sha256")}
        if stage == "reader":
            fields.update(provenance="model_call", source="model_call", model_call=True)
        started = _utc()
        job["started_utc"] = started
        self._stage(path, stage, "running", **fields, started_utc=started, notes=notes)
        provider = getattr(self, "key_provider", None)
        launch_key = provider.get_key() if provider is not None else None

        def redact(text):
            text = str(text)
            if launch_key:
                text = text.replace(launch_key, "[redacted]")
            return provider.redact(text) if provider is not None else text

        def run():
            try:
                result = function()
                if hasattr(self, "_clean"):
                    result = self._clean(result)
                with self._call_lock(path.name):
                    if job["stopped"]:
                        return
                    if stage == "reader":
                        rows = _read(path / "readings.json", [])
                        rows.append(result)
                        _write(path / "readings.json", rows)
                        status = "refused" if result.get("status") == "refused" else "failed" if result.get("status") == "invalid" else "done"
                    else:
                        _write(path / "voice.json", result)
                        status = "done"
                    evidence = result.get("evidence") or {}
                    self._stage(path, stage, status, **fields, started_utc=started,
                                wall_s=result.get("wall_s", round(monotonic() - job["started"], 3)), cost_usd=result.get("cost_usd", evidence.get("cost_usd")),
                                cold_load_s=result.get("cold_load_s"),
                                input_sha256=result.get("audio_sha256"),
                                notes=notes or (result.get("refusal", {}).get("reason") if result.get("refusal") else None))
            except Exception as error:
                with (path / f"worker-{stage}.log").open("a") as log:
                    log.write(redact(traceback.format_exc()))
                with self._call_lock(path.name):
                    if not job["stopped"]:
                        unavailable = isinstance(error, self._models().Unavailable)
                        model_error_type = getattr(self._models(), "ModelError", ())
                        known_error = unavailable or isinstance(error, model_error_type)
                        reason = redact(error.reason) if known_error else f"The {stage} stage failed. See worker-{stage}.log for details."
                        self._stage(path, stage, "unavailable" if unavailable else "failed", **fields, started_utc=started,
                                    mode="unavailable" if unavailable else "live", wall_s=round(monotonic() - job["started"], 3),
                                    notes=f"{notes}. {reason}" if notes else reason)
                        self._state(path, previous.get("state", "ready"), reason)
            finally:
                with self._call_lock(path.name):
                    if job["stopped"]:
                        job["finalize_stop"]()
                    with self._lock:
                        job["active"] = False
                        job["elapsed_s"] = round(monotonic() - job["started"], 3)

        job["thread"] = threading.Thread(target=run, name=f"functional-{stage}-{path.name}", daemon=True)
        job["thread"].start()
        if getattr(self, "_closing", False):
            self._stop(path)

    def _stop(self, path):
        with self._call_lock(path.name):
            return self._request_stop(path)

    def _request_stop(self, path):
        job = self._jobs.get(path.name)
        if not job or not job.get("active"):
            raise APIError(409, "No stage is running for this call.")
        if job.get("stopped"):
            return
        job["stopped"] = True
        stopped_rows = [dict(row) for row in _stage_views(path / "stages.jsonl") if row.get("status") == "running"
                        and (job["step"] != "plan" or row.get("attempt") == job.get("attempt"))]
        if not stopped_rows:
            stopped_rows = [{"stage": "engine" if job["step"] == "plan" else job["step"],
                             "started_utc": job.get("started_utc"), **({"attempt": job["attempt"]} if job.get("attempt") else {})}]

        def mark_stopped(final=False, reason=None):
            note = f"Stopped by planner. {reason}" if reason else "Stopped by planner. The active stage has finished." if final else "Stopped by planner. Waiting for the active stage to finish."
            rows = list(stopped_rows)
            if final:
                original_keys = {(row.get("stage"), row.get("instance_id"), row.get("started_utc")) for row in rows}
                for row in _stage_views(path / "stages.jsonl"):
                    key = (row.get("stage"), row.get("instance_id"), row.get("started_utc"))
                    if row.get("status") == "running" and key not in original_keys and (job["step"] != "plan" or row.get("attempt") == job.get("attempt")):
                        rows.append(row)
            for row in rows:
                fields = {key: value for key, value in row.items() if key not in {"stage", "status", "notes", "wall_s"}}
                if row.get("stage") == "intake" and row.get("who") == "Muse Spark":
                    from app.functional_worker import intake_accounting
                    accounting = intake_accounting(path)
                    fields.update(where="Meta Model API, cloud", cost_usd=accounting.get("settled_cost_usd", 0.),
                                  cost_scope="cumulative_booking_total", cumulative_cost_usd=accounting.get("settled_cost_usd", 0.),
                                  incremental_cost_usd=accounting.get("incremental_cost_usd", 0.))
                self._stage(path, row["stage"], "failed", **fields,
                            wall_s=round(monotonic() - job["started"], 3), notes=note)
            self._state(path, "failed", note, stopped=True, step=job["step"], stop_reason="planner_stop",
                        **({"attempt": job["attempt"]} if job.get("attempt") is not None else {}))

        job["finalize_stop"] = lambda reason=None: mark_stopped(final=True, reason=reason)
        process = job.get("process")
        if process is not None:
            try:
                os.killpg(job.get("pgid", process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            # Escalation runs independently of wait(), which may block on a TERM-resistant leader.
            job["cleanup_thread"] = threading.Thread(target=job["cleanup_group"],
                                                       name=f"functional-stop-{path.name}", daemon=True)
            job["cleanup_thread"].start()
        # Stopping owned work must still happen when its journal cannot be written.
        try:
            mark_stopped()
        except OSError:
            job["stop_journal_failed"] = True

    def close(self):
        deadline = monotonic() + 5.0
        with self._lock:
            self._closing = True
            identities = tuple(dict.fromkeys([*self._call_locks, *self._jobs]))
        jobs = []
        for identity in identities:
            call_lock = self._call_lock(identity)
            if not call_lock.acquire(timeout=max(0, deadline - monotonic())):
                continue
            try:
                job = self._jobs.get(identity)
                if job is not None:
                    jobs.append((identity, job))
                    if job.get("active"):
                        self._stop(job.get("path", self.root_dir / identity))
            finally:
                call_lock.release()
        # Finalizers need their call lock; join with no ownership or call lock held.
        for _, job in jobs:
            for key in ("thread", "cleanup_thread", "log_thread"):
                thread = job.get(key)
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=max(0, deadline - monotonic()))

    def _file(self, path, name):
        relative = Path(name)
        public_name = name
        if len(relative.parts) >= 3 and relative.parts[0] == "runs" and re.fullmatch(r"[1-9][0-9]*", relative.parts[1]):
            public_name = "run/" + "/".join(relative.parts[2:])
        allowed = public_name in {"case.json", "run/outcome.json"} or (
            name.startswith("papers/generated/") and len(relative.parts) >= 3) or (
            public_name in {f"run/package/{item}" for item in ("bay-plan.html", "crane-sequence.csv", "receipt.json", "note-to-master.md", "package.json")})
        destination = (path / relative).resolve()
        if not allowed or ".." in relative.parts or not destination.is_relative_to(path.resolve()) or not destination.is_file():
            raise APIError(404, "That file was not found.")
        return destination

    def recent_calls(self):
        calls = []
        for path in sorted(self.root_dir.glob("c*"), reverse=True):
            if not CALL_ID.fullmatch(path.name) or path.is_symlink() or not (path / "request.json").is_file():
                continue
            request = _read(path / "request.json", {})
            case = _read(path / "case.json", {})
            state = _read(path / "worker-state.json", {})
            intake = _intake_summary(path) or {}
            calls.append({"id": path.name,
                          "created_utc": datetime.fromtimestamp((path / "request.json").stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                          "state": state.get("state", "failed"), "ship": case.get("ship", request.get("ship")),
                          "boxes": intake.get("boxes", case.get("boxes")) if not intake.get("skipped") else case.get("boxes")})
            if len(calls) == 50:
                break
        return calls

    def view(self, path):
        from app.functional_worker import outcome_view, app_envelope, display_message
        path = self._directory(path) if isinstance(path, str) else path
        case = _read(path / "case.json")
        intake = _intake_summary(path)
        state = _read(path / "worker-state.json", {"state": "failed", "message": "This call has no completed state record."})
        documents = _read(path / "papers/meta.json", _read(path / "papers.json", {})).get("documents", [])
        seen = {str(Path(row["path"]).resolve()) for row in documents if row.get("path")}
        generated = (case or {}).get("papers", [])
        if (path / "papers/generated/manifest.json").is_file():
            from app.functional_worker import generated_papers
            try:
                generated = generated_papers(path)
            except ValueError:
                pass  # The explicit intake route reports a damaged manifest as bad input.
        documents = list(documents)
        for paper in generated:
            relative = Path(paper.get("path", ""))
            file = relative if relative.is_absolute() else path / relative
            if (file.is_file() and file.resolve().is_relative_to((path / "papers/generated").resolve())
                    and str(file.resolve()) not in seen):
                data = file.read_bytes()
                documents.append({**paper, "name": paper.get("name", file.name), "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest(), "origin": "generated",
                                  "label": "Synthetic document", "synthetic": True})
                seen.add(str(file.resolve()))
        fields = ("name", "media_type", "bytes", "sha256", "origin", "role", "booking_ref",
                  "complete_booking_list", "synthetic", "label", "container_ids")
        public_documents = [{**{key: row.get(key) for key in fields}, "role": row.get("role", "unspecified")}
                            for row in documents]
        from_intake = bool(intake and intake.get("complete") and not intake.get("skipped"))
        instance = {} if from_intake else _read(path / "assembled-instance.json") or _read(path / "base-instance.json", {})
        rows = intake.get("rows") if from_intake else instance.get("containers", instance.get("cargo", []))
        rows = rows if isinstance(rows, list) else []
        if not from_intake:
            load_ids = instance.get("load_list")
            if isinstance(load_ids, list):
                rows = [row for row in rows if row.get("id") in load_ids]
        if from_intake:
            lengths = [row.get("length_ft") for row in rows]
            teu = sum(lengths) / 20 if rows and all(type(length) in (int, float) for length in lengths) else None
            boxes = intake.get("boxes", len(rows))
        else:
            teu, boxes = (case or {}).get("teu"), (case or {}).get("boxes")
        control = _read(path / "worker-control.json", {})
        generator_ids = set((intake or {}).get("generator_record_ids") or []) if from_intake else set()
        generated_boxes = sum(row.get("id") in generator_ids for row in rows)
        load_origin = (f"Muse Spark, with {generated_boxes} boxes from the generator (not read by a model)"
                       if generated_boxes else "intake" if from_intake else "generator")
        public_rows = [{**row, "origin": "generator, not read by a model" if not from_intake or row.get("id") in generator_ids else "muse_spark"}
                       for row in rows[:200]]
        job = self._jobs.get(path.name, {})
        attempts = _read(path / "attempts.json", [])
        latest = attempts[-1] if attempts else {}
        number = latest.get("attempt")
        run_path = path / "runs" / str(number) if number is not None else path / "run"
        plan_job = job if job.get("step") == "plan" and (number is None or job.get("attempt") == number) else {}
        plan_active = plan_job.get("active") or (latest.get("status") == "running" and self._engine_call == path.name)
        events = _lines(run_path / "events.jsonl")
        run = None
        if latest or plan_job or run_path.exists() or (state.get("stopped") and state.get("step") == "plan"):
            incumbents = []
            for event in events:
                data = event.get("data") or {}
                if event.get("kind") == "incumbent" and data.get("checked") and data.get("lifts") is not None:
                    incumbents.append({"t_s": event.get("elapsed_s", event.get("t_s")), "lifts": data["lifts"],
                                       "plan_sha256": data.get("plan_sha256"), "lane": data.get("lane")})
            raw_outcome = _read(run_path / "outcome.json", {})
            stopped = latest.get("status") == "stopped" or plan_job.get("stopped") or (state.get("stopped") and state.get("step") == "plan")
            profile = latest.get("profile", control.get("profile", "standard"))
            orphan = latest.get("status") == "running" and not plan_active and not raw_outcome
            failed = orphan or latest.get("status") in {"failed", "error", "needs_confirmation", "admission_failed"} or (not latest and state.get("state") == "failed")
            run = {"attempt": number, "status": "stopped" if stopped else "running" if plan_active else "failed" if failed else "done",
                   "profile": profile, "engine_deadline_s": 720 if profile == "live" else 1800,
                   "instruction": latest.get("instruction", control.get("instruction")),
                   "sentence": latest.get("sentence"),
                   "confirmed_reading": latest.get("reading") if latest else _read(path / "confirmation.json", {}).get("reading") if control.get("instruction") == "confirmed" else None,
                   "elapsed_s": round(monotonic() - plan_job["started"], 3) if plan_job.get("active") else latest.get("wall_s", plan_job.get("elapsed_s", (raw_outcome.get("timing") or {}).get("wall_s"))),
                   "admission": raw_outcome.get("admission") or _read(path / "admission.json"), "incumbents": incumbents, "events_tail": events[-20:]}
        readings = _read(path / "readings.json", [])
        # Publish hashes alongside each reading so the planner can confirm exactly what was shown.
        if readings:
            from app.v6 import _rule_digest, _sha256
            readings = [{**row, "sentence_sha256": _sha256(row["sentence"]),
                         "rule_sha256": _rule_digest(row["answer"]) if row.get("status") == "constraint_ready" else None} for row in readings]
        projected_outcome = outcome_view(path, attempt=number) if number is not None else outcome_view(path)
        message = display_message(state.get("message"), run["profile"] if run else control.get("profile", "standard"))
        if run and projected_outcome and not job.get("active"):
            message = projected_outcome.get("message") or message
        if run and run["status"] == "running" and run["incumbents"]:
            message = "First checked plan available. Continuing to improve."
        elif run and run["status"] == "stopped":
            count = len(run["incumbents"])
            message = (f"The planner stopped the engine. {count} checked plans were found before the stop; "
                       "none was released, because a plan is released only when the run finishes." if count else
                       "The planner stopped the engine before any checked plan was found.")
            if job.get("cleanup_failed") or "cleanup could not complete" in (state.get("message") or ""):
                message += " " + state.get("message", "Owned worker cleanup could not complete.")
        elif run and run["status"] == "failed":
            message = "This server has no active worker record for this unfinished attempt; no plan has been released." if orphan else message or "The plan worker failed."
        if run:
            run["message"] = "The run stopped before a plan could be released: " + message if run["status"] == "failed" else message
        raw_gate = _read(path / "runner-envelope.json", (case or {}).get("envelope"))
        stages = _stage_views(path / "stages.jsonl")
        for row in stages:
            stage_profile = next((item["profile"] for item in attempts if item.get("attempt") == row.get("attempt")), control.get("profile", "standard"))
            row["notes"] = display_message(row.get("notes"), stage_profile)
        bookings = _booking_progress(path)
        if (intake or {}).get("generator_record_ids"):
            # The index preserves paid receipts; its original questions can be
            # closed by the separate software merge without rewriting that evidence.
            remaining = {}
            closed_refs = set()
            for question in (intake or {}).get("questions") or []:
                identity = question.get("id", "")
                ref = unquote(identity.split("::", 1)[0]) if "::" in identity else bookings[0].get("booking_ref") if len(bookings) == 1 else None
                remaining[ref] = remaining.get(ref, 0) + 1
            for choice in (intake or {}).get("generator_choices") or []:
                for question in choice.get("closed_questions") or []:
                    identity = question.get("id", "")
                    closed_refs.add(unquote(identity.split("::", 1)[0]) if "::" in identity else bookings[0].get("booking_ref") if len(bookings) == 1 else None)
            for booking in bookings:
                ref = booking.get("booking_ref")
                if booking.get("status") == "needs_answers" and ((intake or {}).get("complete") or ref in closed_refs):
                    count = remaining.get(ref, 0)
                    booking.update(status="needs_answers" if count else "done", open_questions=count)
        cap_stop = (intake or {}).get("cap_stop")
        cap_refusal = None
        if isinstance(cap_stop, dict):
            cap_refusal = {**cap_stop, "charged_usd": 0., "reason": (intake or {}).get("message")}
            for booking in bookings:
                if booking.get("booking_ref") == cap_stop.get("booking_ref"):
                    booking["cap_refusal"] = cap_refusal
        return {"id": path.name, "created_utc": datetime.fromtimestamp((path / "request.json").stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                "label": LABEL, "app": "functional", "state": state.get("state"), "message": message,
                "request": _read(path / "request.json"), "case": case,
                "runner_envelope": raw_gate,
                "envelope": app_envelope(case or {}, _read(path / "envelope.json") or raw_gate or {}) if case else None,
                "papers": {"documents": public_documents, "intake": intake, "bookings": bookings, "cap_refusal": cap_refusal},
                "load_list": {"origin": load_origin, "boxes": boxes,
                              "teu": teu, "rows": public_rows},
                "voice": _read(path / "voice.json"), "readings": readings, "run": run, "outcome": projected_outcome,
                "attempts": [{key: value for key, value in row.items() if key != "message"} for row in attempts[:-1][-10:]],
                "stages": stages, "availability": self._availability()}
