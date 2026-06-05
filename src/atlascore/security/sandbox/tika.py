"""Sandboxed Apache Tika text extraction for SEC filings (B22, CLAUDE.md §14).

Fetched filing bytes are hostile (malformed/poisoned PDFs/HTML), so extraction runs **out-of-process**
and resource-bounded: ``subprocess`` mode shells out to ``java -jar tika-app.jar --text`` under a
``preexec_fn`` that applies ``RLIMIT_AS``/``RLIMIT_CPU`` (POSIX) with a wall-clock timeout and **no
network** (file-in / text-out needs none); ``server`` mode PUTs to a localhost ``tika-server``.

Everything heavy (``subprocess``/``resource``/``httpx``) is imported lazily so this module imports
cleanly with no Java present, and :func:`tika_available` is False — the SEC connector then falls back
to metadata-only filings, keeping CI/sandbox green. Run Tika **non-root** in deployment (runbook).
"""

from __future__ import annotations

import shutil

from atlascore.core.errors import ConfigError, SecurityError
from atlascore.core.settings import Settings, get_settings


def tika_available(settings: Settings | None = None) -> bool:
    """True if the configured Tika mode is actually usable (checked WITHOUT running Java / network)."""
    settings = settings or get_settings()
    if settings.tika_mode == "subprocess":
        jar = settings.tika_jar_path
        return bool(jar and jar.exists() and shutil.which("java"))
    if settings.tika_mode == "server":
        return bool(settings.tika_server_url)
    return False


class TikaClient:
    """Extract plain text from a filing's bytes via a sandboxed Tika (subprocess or server)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def extract_text(self, data: bytes, *, content_type: str | None = None) -> str:
        """Return Tika's plain-text extraction of ``data`` (capped at ``tika_max_bytes``).

        Raises :class:`ConfigError` if Tika is not available (callers must guard with
        :func:`tika_available`) and :class:`SecurityError` on a sandbox failure/timeout.
        """
        if not tika_available(self._settings):
            raise ConfigError(
                f"Tika is not available (TIKA_MODE={self._settings.tika_mode!r}); install + configure "
                "tika-app.jar (subprocess) or a localhost tika-server, then set TIKA_MODE"
            )
        if self._settings.tika_mode == "subprocess":
            return self._extract_subprocess(data)
        return self._extract_server(data, content_type=content_type)

    def _extract_subprocess(self, data: bytes) -> str:
        import resource
        import subprocess

        s = self._settings

        def _limits() -> None:  # pragma: no cover - runs in the forked child
            # Bound address space + CPU time so a malicious file can't exhaust the host.
            resource.setrlimit(resource.RLIMIT_AS, (s.tika_rlimit_as_bytes, s.tika_rlimit_as_bytes))
            resource.setrlimit(
                resource.RLIMIT_CPU, (s.tika_rlimit_cpu_seconds, s.tika_rlimit_cpu_seconds)
            )

        cmd = ["java", "-jar", str(s.tika_jar_path), "--text"]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell; input piped, output capped
                cmd,
                input=data,
                capture_output=True,
                timeout=s.tika_timeout_seconds,
                preexec_fn=_limits,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SecurityError("Tika extraction timed out (sandbox limit)") from exc
        if proc.returncode != 0:
            raise SecurityError(f"Tika extraction failed (exit {proc.returncode})")
        return proc.stdout[: s.tika_max_bytes].decode("utf-8", errors="replace")

    def _extract_server(self, data: bytes, *, content_type: str | None) -> str:
        import httpx

        s = self._settings
        url = f"{(s.tika_server_url or '').rstrip('/')}/tika"
        headers = {"Accept": "text/plain"}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            resp = httpx.put(url, content=data, headers=headers, timeout=s.tika_timeout_seconds)
        except httpx.HTTPError as exc:
            raise SecurityError("Tika server request failed") from exc
        if resp.status_code >= 400:
            raise SecurityError(f"Tika server returned HTTP {resp.status_code}")
        return resp.text[: s.tika_max_bytes]
