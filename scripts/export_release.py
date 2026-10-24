"""Export compact trusted artifacts for the scheduled worker; no historical Parquet in git."""
import json
import shutil
from pathlib import Path
from quantpilot.config import settings
from quantpilot.data.archives import dataset_hashes

coverage = json.loads((settings.data_dir/"coverage.json").read_text(encoding="utf-8-sig"))
if not coverage.get("release_ready") or coverage["dataset_hashes"] != dataset_hashes(settings.data_dir):
    raise SystemExit("Release blocked: coverage or dataset hashes")
target = Path("release")
target.mkdir(exist_ok=True)
for name in ("released.joblib", "evaluation.json"):
    source = settings.artifact_dir/name
    if not source.exists(): raise SystemExit(f"Missing trained artifact {source}")
    shutil.copy2(source, target/name)
print("Exported trusted worker model and compact evaluation. Publish research snapshots separately.")

