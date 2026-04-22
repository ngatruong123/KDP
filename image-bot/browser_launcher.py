"""Launch fingerprint-chromium via subprocess and connect Playwright over CDP.

Ported from pod-designer. Use with --fingerprint flag to replace default
launch_persistent_context with a proper fingerprint-chromium instance.
"""

import asyncio
import hashlib
import os
import re
import shutil
import subprocess

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

CHROME_PATH = os.environ.get("FINGERPRINT_CHROMIUM_PATH", "")
SEED_SECRET = os.environ.get("FINGERPRINT_SEED_SECRET", "default-seed")

# Cache dirs to clean before each browser launch (saves disk, avoids stale state)
CACHE_DIRS = [
    "Cache", "Code Cache", "GPUCache", "GrShaderCache",
    "GraphiteDawnCache", "ShaderCache", "DawnCache", "Service Worker",
]


def _generate_seed(profile_name: str) -> int:
    """Deterministic fingerprint seed from profile name + secret."""
    h = hashlib.sha256(f"{profile_name}-{SEED_SECRET}".encode()).hexdigest()
    return int(h[:8], 16)


def _clean_cache(profile_dir: str) -> None:
    """Remove cache subdirectories to save disk and avoid stale data."""
    from pathlib import Path
    base = Path(profile_dir)
    for search_dir in [base, base / "Default"]:
        if not search_dir.exists():
            continue
        for cache_name in CACHE_DIRS:
            cache_path = search_dir / cache_name
            if cache_path.exists():
                shutil.rmtree(cache_path, ignore_errors=True)


async def launch_fingerprint_browser(
    acc_name: str,
    headless: bool = False,
    proxy: str | None = None,
    port: int = 9222,
) -> tuple[BrowserContext, Playwright, subprocess.Popen]:
    """Launch fingerprint-chromium and connect Playwright via CDP.

    Returns (context, playwright, process) - caller must call
    close_fingerprint_browser() in finally block.
    """
    if not CHROME_PATH or not os.path.isfile(CHROME_PATH):
        raise FileNotFoundError(
            f"fingerprint-chromium not found at: {CHROME_PATH!r}. "
            "Set FINGERPRINT_CHROMIUM_PATH env var."
        )

    # Use existing chrome_profile_{acc} directory structure
    profile_dir = os.path.abspath(f"chrome_profile_{acc_name}" if acc_name != "default" else "chrome_profile")
    os.makedirs(profile_dir, exist_ok=True)
    _clean_cache(profile_dir)

    seed = _generate_seed(acc_name)
    args = [
        CHROME_PATH,
        f"--fingerprint={seed}",
        f"--user-data-dir={profile_dir}",
        "--fingerprint-platform=windows",
        "--fingerprint-brand=Chrome",
        "--lang=en-US",
        "--no-first-run",
        "--disable-sync",
        f"--remote-debugging-port={port}",
        "--no-sandbox",
        # RAM/CPU optimization for multi-bot
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-translate",
        "--disable-component-update",
        "--disable-background-timer-throttling",
        "--js-flags=--max-old-space-size=256",
    ]

    if headless:
        args.append("--headless=new")

    if proxy:
        args.append(f"--proxy-server={proxy}")

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # Wait for process to start, verify it's alive
    await asyncio.sleep(2)
    if proc.poll() is not None:
        stderr_out = proc.stderr.read().decode() if proc.stderr else ""
        raise RuntimeError(
            f"fingerprint-chromium exited with code {proc.returncode}. "
            f"stderr: {stderr_out[:500]}"
        )

    # Connect Playwright via CDP with retry
    pw = await async_playwright().start()
    browser = await _connect_with_retry(pw, port)

    ctx = browser.contexts[0]
    return ctx, pw, proc


async def _connect_with_retry(
    pw: Playwright, port: int, max_retries: int = 10, delay: float = 1.0
) -> Browser:
    """Retry CDP connection until browser is ready."""
    for attempt in range(max_retries):
        try:
            return await pw.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception:
            if attempt == max_retries - 1:
                raise RuntimeError(
                    f"Failed to connect to CDP on port {port} "
                    f"after {max_retries} attempts"
                )
            await asyncio.sleep(delay)


async def close_fingerprint_browser(
    context: BrowserContext, pw: Playwright, proc: subprocess.Popen
) -> None:
    """Gracefully close browser context, terminate process, stop Playwright."""
    try:
        await context.close()
    except Exception:
        pass
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        await pw.stop()
    except Exception:
        pass
