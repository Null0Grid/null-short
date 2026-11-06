"""The running daemon: grabs keyboards with evdev, routes every key through
the Router, and re-sends what should pass via a virtual (uinput) keyboard.

Design rule: fail open. If anything goes wrong while handling an event, the
original event is passed through untouched so the keyboard keeps working.
"""

from __future__ import annotations

import asyncio
import logging
import signal

try:
    import evdev
    from evdev import ecodes
except ImportError as e:  # pragma: no cover - depends on the host
    raise SystemExit(
        "python3-evdev is missing. Install it with:  sudo apt install python3-evdev"
    ) from e

from . import apps as apps_mod, autocode, config as config_mod, registry
from .actions import Dispatcher
from .catalog import assemble
from .codes import Matcher
from .config import Config, ConfigError
from .engine import Router

log = logging.getLogger("null-short")

VIRTUAL_PREFIX = "null-short virtual"
RESCAN_SECONDS = 2.0
RELOAD_EVERY = 5          # check for new apps / config edits every 5 scans (~10 s)


def resolve_key(name: str) -> int:
    code = ecodes.ecodes.get(name)
    if not isinstance(code, int):
        raise SystemExit(f"unknown trigger key {name!r} (try KEY_CAPSLOCK)")
    return code


def is_keyboard(dev) -> bool:
    if dev.name.startswith(VIRTUAL_PREFIX):
        return False
    keys = dev.capabilities().get(ecodes.EV_KEY, [])
    return ecodes.KEY_A in keys and ecodes.KEY_UP in keys


def list_keyboards() -> list:
    found = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if is_keyboard(dev):
            found.append(dev)
        else:
            dev.close()
    return found


def _watch_state(config_path) -> tuple:
    files = [config_mod.user_path(), config_mod.SYSTEM, registry.user_path(),
             registry.BUNDLED, autocode.pins_path()]
    if config_path:
        files.append(config_mod.Path(config_path))
    stamps = []
    for f in files:
        try:
            stamps.append(f.stat().st_mtime_ns)
        except OSError:
            stamps.append(None)
    return (apps_mod.fingerprint(), tuple(stamps))


class Daemon:
    def __init__(self, cfg: Config, config_path: str | None = None):
        self.cfg = cfg
        self.config_path = config_path
        catalog = assemble(cfg)
        for w in catalog.warnings:
            log.warning(w)
        self.router = Router(resolve_key(cfg.trigger), Matcher(catalog.table()),
                             tap_passthrough=cfg.tap_passthrough)
        self.dispatcher = Dispatcher(cfg)
        self.attached: dict[str, tuple] = {}   # path -> (dev, ui, task)
        self._stop: asyncio.Event | None = None
        self._state = _watch_state(config_path)
        self._pending_reload = False
        self.code_count = len(catalog.entries)

    def reload(self, why: str = "") -> None:
        """Rebuild codes (new apps, config or registry edits) without restarting."""
        if self.router.listening:
            self._pending_reload = True      # never swap mid-code
            return
        self._pending_reload = False
        try:
            cfg = config_mod.load(self.config_path)
            catalog = assemble(cfg)
        except (ConfigError, registry.RegistryError) as e:
            log.error("reload failed, keeping current codes: %s", e)
            return
        for w in catalog.warnings:
            log.warning(w)
        if cfg.trigger != self.cfg.trigger:
            log.warning("trigger changed; restart the service to apply it")
        self.cfg.entries = cfg.entries
        self.cfg.sounds, self.cfg.notify = cfg.sounds, cfg.notify
        self.router.matcher = Matcher(catalog.table())
        self.router.tap_passthrough = cfg.tap_passthrough
        self.code_count = len(catalog.entries)
        self._state = _watch_state(self.config_path)
        log.info("codes reloaded (%s): %d codes", why or "change", self.code_count)

    # ---------------------------------------------------------------- #
    async def run(self) -> None:
        self._stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop, f"signal {sig.name}")
        loop.add_signal_handler(signal.SIGHUP, self.reload, "SIGHUP")
        log.info("null-short running: trigger %s, %d codes, panic = Ctrl+Alt+Shift+Esc",
                 self.cfg.trigger, self.code_count)
        ticks = 0
        try:
            while not self._stop.is_set():
                await self.scan()
                ticks += 1
                if self._pending_reload or (ticks % RELOAD_EVERY == 0
                                            and _watch_state(self.config_path) != self._state):
                    self.reload("files changed")
                try:
                    await asyncio.wait_for(self._stop.wait(), RESCAN_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            for path in list(self.attached):
                self.detach(path)
            log.info("null-short stopped, keyboards released")

    def stop(self, why: str = "") -> None:
        log.info("stopping (%s)", why)
        if self._stop:
            self._stop.set()

    # ---------------------------------------------------------------- #
    async def scan(self) -> None:
        for path in evdev.list_devices():
            if path in self.attached:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            if not is_keyboard(dev):
                dev.close()
                continue
            try:
                await self.attach(path, dev)
            except OSError as e:
                log.warning("could not take %s (%s): %s", dev.name, path, e)
                dev.close()

    async def attach(self, path: str, dev) -> None:
        # Never grab mid-keypress, or that key would stick down.
        for _ in range(60):
            if not dev.active_keys():
                break
            await asyncio.sleep(0.05)
        ui = evdev.UInput.from_device(dev, name=f"{VIRTUAL_PREFIX} ({dev.name})"[:79])
        try:
            dev.grab()
        except OSError:
            ui.close()
            raise
        task = asyncio.create_task(self.pump(path, dev, ui))
        self.attached[path] = (dev, ui, task)
        log.info("attached %s (%s)", dev.name, path)

    def detach(self, path: str) -> None:
        dev, ui, task = self.attached.pop(path, (None, None, None))
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        for close in (lambda: dev.ungrab(), lambda: dev.close(), lambda: ui.close()):
            try:
                close()
            except (OSError, AttributeError):
                pass
        self.router.reset()

    async def pump(self, path: str, dev, ui) -> None:
        try:
            async for ev in dev.async_read_loop():
                self.on_event(ui, ev)
        except OSError:
            log.info("keyboard gone: %s", path)
        except asyncio.CancelledError:
            return
        self.detach(path)

    # ---------------------------------------------------------------- #
    def on_event(self, ui, ev) -> None:
        if ev.type != ecodes.EV_KEY:
            ui.write_event(ev)          # SYN, MSC, LED... pass straight through
            return
        try:
            res = self.router.handle(ev.code, ev.value)
        except Exception:               # fail open
            log.exception("router error; passing key through")
            ui.write_event(ev)
            return
        if len(res.emit) == 1:
            ui.write(ecodes.EV_KEY, *res.emit[0])   # the device's own SYN follows
        else:
            for code, value in res.emit:
                ui.write(ecodes.EV_KEY, code, value)
                ui.syn()
        if res.event == "panic":
            self.stop("panic combo")
        elif res.event:
            try:
                self.dispatcher.handle(res.event, res.action)
            except Exception:
                log.exception("action failed")


def run(cfg: Config, config_path: str | None = None) -> None:
    asyncio.run(Daemon(cfg, config_path).run())
