import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VM_DATA", tempfile.mkdtemp(prefix="vm-test-"))
os.environ.setdefault("VM_ROLE", "web")          # tests never start the scraper thread
