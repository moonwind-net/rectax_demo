#!/usr/bin/env python3
"""RecTax remote folder ingest agent.

Features:
- Poll one or more mounted directories (SMB/NFS/local path)
- Upload files to RecTax API in throttled batches
- Health-aware pause (waits for /health == ok)
- Retry with exponential backoff + Retry-After support
- Checkpoint/resume via local state file (processed file hash set)
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml


@dataclass
class AgentConfig:
    api_base_url: str
    api_prefix: str
    login_email: str
    login_password: str
    watch_dirs: list[str]
    recursive: bool
    include_patterns: list[str]
    scan_interval_seconds: int
    request_timeout_seconds: int
    health_poll_seconds: int
    batch_max_files: int
    batch_max_total_mb: int
    delay_seconds: int
    max_retries: int
    retry_base_seconds: int
    state_file: str
    log_level: str


class StopRequested(Exception):
    pass


class Agent:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.log = logging.getLogger("rectax_agent")
        self.stop_requested = False

        self.session = requests.Session()
        self.token: str | None = None

        self.state_path = Path(cfg.state_file)
        self.state = self._load_state()

    def request_stop(self, *_: Any) -> None:
        self.stop_requested = True

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        self.log.info("Agent started. watch_dirs=%s", self.cfg.watch_dirs)
        self._login()

        while not self.stop_requested:
            try:
                self._run_once()
            except StopRequested:
                break
            except Exception as exc:  # noqa: BLE001
                self.log.exception("Scan cycle failed: %s", exc)

            self._sleep_with_stop(self.cfg.scan_interval_seconds)

        self.log.info("Agent stopped.")

    def _run_once(self) -> None:
        pending = self._collect_pending_files()
        if not pending:
            self.log.info("No new files found.")
            return

        self.log.info("Found %d pending files.", len(pending))
        batches = self._build_batches(pending)
        self.log.info("Prepared %d batch(es).", len(batches))

        for i, batch in enumerate(batches, start=1):
            self._raise_if_stop()
            label = f"batch {i}/{len(batches)}"

            self._wait_for_healthy(label)
            self._upload_batch_with_retry(batch, label)

            for item in batch:
                self.state["processed"][item["fingerprint"]] = {
                    "path": item["path"],
                    "size": item["size"],
                    "uploaded_at": int(time.time()),
                }
            self._save_state()

            if i < len(batches):
                self._sleep_with_stop(self.cfg.delay_seconds)

    def _collect_pending_files(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        for watch_dir in self.cfg.watch_dirs:
            root = Path(watch_dir)
            if not root.exists():
                self.log.warning("Watch dir does not exist: %s", watch_dir)
                continue

            iterator = root.rglob("*") if self.cfg.recursive else root.glob("*")
            for path in iterator:
                if not path.is_file():
                    continue
                if not self._is_supported(path.name):
                    continue

                try:
                    size = path.stat().st_size
                    fingerprint = self._sha256_file(path)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("Skip unreadable file %s: %s", path, exc)
                    continue

                if fingerprint in self.state["processed"]:
                    continue

                entries.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "size": size,
                        "fingerprint": fingerprint,
                    }
                )

        entries.sort(key=lambda x: x["path"])
        return entries

    def _build_batches(self, pending: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        max_bytes = self.cfg.batch_max_total_mb * 1024 * 1024
        batches: list[list[dict[str, Any]]] = []

        current: list[dict[str, Any]] = []
        current_bytes = 0

        for item in pending:
            if item["size"] > max_bytes:
                self.log.warning(
                    "Skip too-large file for current config (>%dMB): %s",
                    self.cfg.batch_max_total_mb,
                    item["path"],
                )
                continue

            need_split = (
                len(current) >= self.cfg.batch_max_files
                or current_bytes + item["size"] > max_bytes
            )
            if current and need_split:
                batches.append(current)
                current = []
                current_bytes = 0

            current.append(item)
            current_bytes += item["size"]

        if current:
            batches.append(current)

        return batches

    def _upload_batch_with_retry(self, batch: list[dict[str, Any]], label: str) -> None:
        for attempt in range(self.cfg.max_retries + 1):
            self._raise_if_stop()

            try:
                self._upload_batch_once(batch)
                self.log.info("Uploaded %s (%d files)", label, len(batch))
                return
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0

                if status == 401:
                    self.log.warning("401 on %s, trying token refresh.", label)
                    self._login()
                elif status in (400, 413):
                    # Permanent request shape / payload error
                    raise

                if attempt >= self.cfg.max_retries:
                    raise

                retry_after = 0
                if exc.response is not None:
                    retry_after_raw = exc.response.headers.get("Retry-After", "0")
                    retry_after = int(retry_after_raw) if retry_after_raw.isdigit() else 0

                backoff = self.cfg.retry_base_seconds * (2**attempt)
                wait_s = max(backoff, retry_after)
                self.log.warning(
                    "%s failed (HTTP %s). Retry %d/%d in %ds.",
                    label,
                    status,
                    attempt + 1,
                    self.cfg.max_retries,
                    wait_s,
                )
                self._sleep_with_stop(wait_s)
                self._wait_for_healthy(label)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.cfg.max_retries:
                    raise
                backoff = self.cfg.retry_base_seconds * (2**attempt)
                self.log.warning(
                    "%s failed (%s). Retry %d/%d in %ds.",
                    label,
                    type(exc).__name__,
                    attempt + 1,
                    self.cfg.max_retries,
                    backoff,
                )
                self._sleep_with_stop(backoff)
                self._wait_for_healthy(label)

    def _upload_batch_once(self, batch: list[dict[str, Any]]) -> None:
        assert self.token, "token required"

        url = f"{self.cfg.api_base_url}{self.cfg.api_prefix}/ingestion/upload"
        headers = {"Authorization": f"Bearer {self.token}"}

        files_payload = []
        file_handles = []
        try:
            for item in batch:
                fh = open(item["path"], "rb")
                file_handles.append(fh)
                files_payload.append(("files", (item["name"], fh, "application/octet-stream")))

            resp = self.session.post(
                url,
                headers=headers,
                files=files_payload,
                timeout=self.cfg.request_timeout_seconds,
            )
            if resp.status_code >= 400:
                self.log.error("Upload error status=%s body=%s", resp.status_code, resp.text)
                resp.raise_for_status()
        finally:
            for fh in file_handles:
                fh.close()

    def _wait_for_healthy(self, label: str) -> None:
        url = f"{self.cfg.api_base_url}/health"

        while not self.stop_requested:
            try:
                resp = self.session.get(url, timeout=self.cfg.request_timeout_seconds)
                if resp.ok:
                    payload = resp.json()
                    if payload.get("status") == "ok":
                        return
            except Exception:  # noqa: BLE001
                pass

            self.log.warning("Health not ok before %s, retry in %ds.", label, self.cfg.health_poll_seconds)
            self._sleep_with_stop(self.cfg.health_poll_seconds)

        raise StopRequested()

    def _login(self) -> None:
        url = f"{self.cfg.api_base_url}{self.cfg.api_prefix}/auth/login"
        payload = {
            "email": self.cfg.login_email,
            "password": self.cfg.login_password,
        }
        resp = self.session.post(url, json=payload, timeout=self.cfg.request_timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("Login response missing access_token")
        self.token = token
        self.log.info("Login success for %s", self.cfg.login_email)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"processed": {}}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"processed": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _sleep_with_stop(self, seconds: int) -> None:
        for _ in range(max(0, seconds)):
            self._raise_if_stop()
            time.sleep(1)

    def _raise_if_stop(self) -> None:
        if self.stop_requested:
            raise StopRequested()

    def _is_supported(self, name: str) -> bool:
        lower = name.lower()
        return any(fnmatch.fnmatch(lower, pattern.lower()) for pattern in self.cfg.include_patterns)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()


def _parse_env_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}

    env: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _get_value(cfg: dict[str, Any], env: dict[str, str], key: str, default: Any = None) -> Any:
    env_key = f"RECTAX_AGENT_{key.upper()}"
    if env_key in env:
        return env[env_key]
    if env_key in os.environ:
        return os.environ[env_key]
    return cfg.get(key, default)


def load_config(config_path: str, env_file: str | None) -> AgentConfig:
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    env = _parse_env_file(env_file)

    watch_dirs = _get_value(cfg, env, "watch_dirs", [])
    if isinstance(watch_dirs, str):
        watch_dirs = [x.strip() for x in watch_dirs.split(",") if x.strip()]

    include_patterns = _get_value(
        cfg,
        env,
        "include_patterns",
        ["*.jpg", "*.jpeg", "*.png", "*.pdf", "*.tif", "*.tiff", "*.webp"],
    )
    if isinstance(include_patterns, str):
        include_patterns = [x.strip() for x in include_patterns.split(",") if x.strip()]

    return AgentConfig(
        api_base_url=str(_get_value(cfg, env, "api_base_url", "http://127.0.0.1:8000")),
        api_prefix=str(_get_value(cfg, env, "api_prefix", "/api/v1")),
        login_email=str(_get_value(cfg, env, "login_email", "admin@example.com")),
        login_password=str(_get_value(cfg, env, "login_password", "")),
        watch_dirs=watch_dirs,
        recursive=str(_get_value(cfg, env, "recursive", "true")).lower() in ("1", "true", "yes"),
        include_patterns=include_patterns,
        scan_interval_seconds=int(_get_value(cfg, env, "scan_interval_seconds", 30)),
        request_timeout_seconds=int(_get_value(cfg, env, "request_timeout_seconds", 60)),
        health_poll_seconds=int(_get_value(cfg, env, "health_poll_seconds", 5)),
        batch_max_files=int(_get_value(cfg, env, "batch_max_files", 3)),
        batch_max_total_mb=int(_get_value(cfg, env, "batch_max_total_mb", 100)),
        delay_seconds=int(_get_value(cfg, env, "delay_seconds", 2)),
        max_retries=int(_get_value(cfg, env, "max_retries", 3)),
        retry_base_seconds=int(_get_value(cfg, env, "retry_base_seconds", 3)),
        state_file=str(_get_value(cfg, env, "state_file", "./state/agent_state.json")),
        log_level=str(_get_value(cfg, env, "log_level", "INFO")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="RecTax remote ingest agent")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--env-file", default=".env", help="Optional env file for secrets/overrides")
    args = parser.parse_args()

    cfg = load_config(args.config, args.env_file)

    if not cfg.watch_dirs:
        print("No watch_dirs configured.", file=sys.stderr)
        return 2
    if not cfg.login_password:
        print("Missing login_password (set in env file as RECTAX_AGENT_LOGIN_PASSWORD).", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    agent = Agent(cfg)
    agent.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
