"""Build a portable release archive, without models or third-party scripts."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parent


def build():
    version_text = (ROOT / "package/python3.11libs/rtc_bridge/__init__.py").read_text(encoding="utf-8")
    version = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", version_text).group(1)
    name = "Houdini_RenderCore_Bridge_" + version
    output = ROOT / "release" / (name + ".zip")
    output.parent.mkdir(exist_ok=True)
    if output.exists():
        raise FileExistsError("Release already exists: " + str(output))
    files = [ROOT / p for p in ("README.md", "GUIDE.md", "CHANGELOG.md", "Install.cmd", "Install.ps1", "build_release.py", "tests/README.md", "tests/max_cycles.py", "tests/houdini_cycles.py", "tests/material_policy.py")]
    files += sorted(p for p in (ROOT / "package").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    payload = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in files}
    manifest = {path: hashlib.sha256(data).hexdigest() for path, data in payload.items()}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in payload.items():
            archive.writestr(name + "/" + path, data)
        archive.writestr(name + "/SHA256.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for path, digest in manifest.items():
            assert hashlib.sha256(archive.read(name + "/" + path)).hexdigest() == digest
    print("VERIFIED", output, len(payload) + 1, "files")


if __name__ == "__main__":
    build()
