"""Runs every QA file with the interpreter running this script, against the gnuradio.serorx it imports.

The fake receiver directory goes on PYTHONPATH. Exit status 1 when any QA file fails.
"""
import glob
import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = dict(os.environ, PYTHONPATH=os.path.join(root, "fake-grx"))
qa_files = sorted(glob.glob(os.path.join(root, "gr-serorx", "python", "serorx", "qa", "qa_*.py")))
failed = []
for qa in qa_files:
    print("==", os.path.basename(qa), flush=True)
    if subprocess.run([sys.executable, qa], env=env).returncode != 0:
        failed.append(os.path.basename(qa))
print(f"{len(qa_files) - len(failed)} of {len(qa_files)} QA files passed", flush=True)
if failed:
    print("failed:", " ".join(failed), flush=True)
    sys.exit(1)
