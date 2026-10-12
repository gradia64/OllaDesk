"""Riduce PySide6 nell'AppDir a ciò che OllaDesk usa.

Uso: python3 trim-qt.py <cartella PySide6 nell'AppDir>

Si tengono i moduli Python importati da OllaDesk e i plugin Qt utili su un
desktop Linux (X11, Wayland, portali, immagini, metodi di input). Le
librerie in Qt/lib restano solo se servono a qualcosa di ciò che resta:
le dipendenze si leggono dalle voci NEEDED degli ELF (readelf -d), così un
plugin non può perdere una libreria per errore. Il resto (QML, Quick,
Designer, strumenti, traduzioni, stub .pyi) se ne va.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork"}
PLUGINS = {
    "platforms": {"libqxcb.so", "libqwayland.so", "libqoffscreen.so", "libqminimal.so"},
    "wayland-decoration-client": None,           # None: tutta la cartella
    "wayland-shell-integration": None,
    "wayland-graphics-integration-client": None,
    "xcbglintegrations": None,
    "platformthemes": {"libqxdgdesktopportal.so"},   # finestre dei file dal portale
    "platforminputcontexts": {"libcomposeplatforminputcontextplugin.so",
                              "libibusplatforminputcontextplugin.so"},
    "iconengines": None,
    "imageformats": {"libqsvg.so", "libqjpeg.so", "libqgif.so", "libqico.so", "libqwebp.so"},
    "tls": None,
}
KEEP_TOP = {"__init__.py", "_config.py", "_git_pyside_version.py", "py.typed",
            "support", "Qt", "PySide6_Essentials.json"}


def needed(elf: Path) -> set[str]:
    out = subprocess.run(["readelf", "-d", str(elf)], capture_output=True,
                         text=True, check=True).stdout
    return set(re.findall(r"\(NEEDED\).*\[(.+)\]", out))


def remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def main(root: Path) -> None:
    qt = root / "Qt"
    keep_mods = {f"{m}.abi3.so" for m in MODULES}
    for p in root.iterdir():
        if p.name in KEEP_TOP or p.name in keep_mods or p.name.startswith("libpyside6.abi3.so"):
            continue
        remove(p)

    plugins = qt / "plugins"
    for d in plugins.iterdir():
        allowed = PLUGINS.get(d.name, set())
        if allowed is None:
            continue
        for f in d.iterdir():
            if f.name not in allowed:
                remove(f)
        if not any(d.iterdir()):
            d.rmdir()
    for name in ("qml", "translations", "metatypes", "libexec"):
        if (qt / name).exists():
            remove(qt / name)

    # chiusura delle dipendenze dentro Qt/lib, partendo da moduli e plugin
    lib = qt / "lib"
    available = {p.name for p in lib.iterdir()}
    todo = [p for p in root.iterdir() if p.suffix == ".so" or ".so." in p.name]
    todo += [p for p in plugins.rglob("*.so")]
    keep, seen = set(), set()
    while todo:
        elf = todo.pop()
        if elf in seen:
            continue
        seen.add(elf)
        for dep in needed(elf) & available:
            if dep not in keep:
                keep.add(dep)
                todo.append(lib / dep)
    for p in lib.iterdir():
        if p.name not in keep:
            remove(p)
    print("Qt/lib:", " ".join(sorted(keep)))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
