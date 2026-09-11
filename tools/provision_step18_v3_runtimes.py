"""Attempt declared Step18 optional providers in project-local isolated targets."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "workspace" / "test-temp" / "step18-runtimes"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation"


def main() -> int:
    records = []
    for name, requirement in (("matching", "valentine==1.0.0"), ("entity-resolution", "splink==4.0.17")):
        target = BASE / name
        temp = target / "temp"
        cache = target / "pip-cache"
        target.mkdir(parents=True, exist_ok=True)
        temp.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.update({"TEMP":str(temp),"TMP":str(temp),"PIP_CACHE_DIR":str(cache),"PYTHONNOUSERSITE":"1"})
        command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--timeout", "5", "--target", str(target / "site-packages"), requirement]
        completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=45)
        records.append({"runtime":name,"requirement":requirement,"command":command,"returncode":completed.returncode,"stdout":completed.stdout[-4000:],"stderr":completed.stderr[-4000:],"status":"PASS" if completed.returncode == 0 else "BLOCKED"})
        shutil.rmtree(target, ignore_errors=True)
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / "runtime_provisioning.json").write_text(json.dumps({"schema_version":"3","runtimes":records}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0 if all(item["status"] == "PASS" for item in records) else 2


if __name__ == "__main__":
    raise SystemExit(main())
