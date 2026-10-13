# Build on the target OS: Windows produces an exe folder; macOS produces an app.
import sys
from pathlib import Path

project = Path(SPECPATH)
analysis = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["TkAgg"]}},
    excludes=[
        "pandas", "scipy", "skin_analysis.statistics", "IPython", "pytest",
        "notebook", "jupyter", "tornado", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "numpy.f2py", "numpy.testing", "numpy.tests", "matplotlib.tests",
        "matplotlib.testing", "tkinter.test",
    ],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ErrorBandStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name="ErrorBandStudio")
if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="ErrorBandStudio.app",
        bundle_identifier="local.cap-s.error-band-studio",
        version="1.0.0",
        info_plist={"NSHighResolutionCapable": True, "CFBundleDisplayName": "Error Band Studio"},
    )
