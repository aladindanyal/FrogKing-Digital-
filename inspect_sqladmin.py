import os
import sqladmin
import jinja2
import starlette
from pathlib import Path

print("sqladmin version:", getattr(sqladmin, "__version__", "unknown"))
print("sqladmin path:", Path(sqladmin.__file__).resolve())
print("jinja2 version:", jinja2.__version__)
print("starlette version:", starlette.__version__)

root = Path(sqladmin.__file__).resolve().parent
for path in root.rglob("*.html"):
    print(path.relative_to(root))
