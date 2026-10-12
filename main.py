import sys
import os

# Add backend directory to python path
backend_path = os.path.join(os.path.dirname(__file__), "backend")
if os.path.exists(backend_path) and backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Forward FastAPI app instance
from main import app
