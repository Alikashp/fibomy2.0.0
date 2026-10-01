"""CONVERT: PPTX → PDF через LibreOffice headless (D-001, спайк 3.2).

Каждая конвертация — со своим профилем LibreOffice (-env:UserInstallation),
иначе параллельные вызовы мешают друг другу. Не больше
LIBREOFFICE_MAX_PARALLEL конвертаций на процесс одновременно (03_ARCHITECTURE.md, 4.3).
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0


class ConvertError(RuntimeError):
    """LibreOffice не установлен, упал или не уложился в таймаут."""


def soffice_bin() -> str | None:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _get_semaphore(limit: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != limit:
        _semaphore, _semaphore_size = asyncio.Semaphore(limit), limit
    return _semaphore


async def pptx_to_pdf(pptx: bytes, timeout: float = DEFAULT_TIMEOUT, max_parallel: int = 2) -> bytes:
    binary = soffice_bin()
    if not binary:
        raise ConvertError("LibreOffice (soffice) not found")
    async with _get_semaphore(max_parallel):
        with tempfile.TemporaryDirectory(prefix="lo_") as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "deck.pptx"
            src.write_bytes(pptx)
            profile = tmp_path / "profile"
            proc = await asyncio.create_subprocess_exec(
                binary, "--headless", "--norestore", "--nologo", "--nodefault",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", "pdf", "--outdir", str(tmp_path), str(src),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ConvertError(f"LibreOffice timeout after {timeout:.0f}s")
            out = tmp_path / "deck.pdf"
            if proc.returncode != 0 or not out.exists():
                raise ConvertError(f"LibreOffice failed (code {proc.returncode}): "
                                   f"{stderr.decode(errors='replace')[-500:]}")
            return out.read_bytes()
