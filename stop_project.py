"""Stop only this checkout's services, retaining both databases."""
import subprocess
import sys
from pathlib import Path
if __name__ == "__main__":
    preferred = Path(r"A:\CandidateScoringEnv\Scripts\python.exe")
    if preferred.is_file() and Path(sys.executable).resolve() != preferred.resolve():
        raise SystemExit(subprocess.call([str(preferred), str(Path(__file__).resolve())]))
    from tools.local_runtime import stop
    stop()
    print("Services stopped; MongoDB and Neo4j data retained.")
