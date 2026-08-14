# PyInstaller spec for the FQHC Prospect Intelligence desktop build.
#
#   pyinstaller desktop/fqhc.spec --noconfirm
#
# Run from the project root. PyInstaller does not cross-compile: build the
# macOS bundle on macOS, the Windows executable on Windows.

import os
import sys
from pathlib import Path

# PyInstaller builds for the architecture of the Python running it, so an Apple
# Silicon Mac produces an arm64-only app that will not launch on an Intel Mac.
# Set FQHC_TARGET_ARCH=universal2 to build for both -- which additionally
# requires a universal2 Python and universal2 wheels for every dependency.
TARGET_ARCH = os.environ.get("FQHC_TARGET_ARCH") if sys.platform == "darwin" else None

# SPECPATH is set by PyInstaller to the directory holding this file.
PROJECT_ROOT = Path(SPECPATH).parent  # noqa: F821

# Jinja templates, brand CSS/JS and the default config are loaded from disk at
# runtime, so PyInstaller cannot discover them by following imports.
datas = [
    (str(PROJECT_ROOT / "app" / "templates"), "app/templates"),
    (str(PROJECT_ROOT / "app" / "static"), "app/static"),
    (str(PROJECT_ROOT / "config.yaml"), "."),
]

# uvicorn resolves these by name at runtime rather than importing them.
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "app.main",
    "pipeline.run",
]

analysis = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "desktop" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Test-only and build-only packages; excluding them keeps the bundle small.
    excludes=["pytest", "playwright", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="FQHC Prospect Intelligence",
    debug=False,
    strip=False,
    upx=False,
    # No terminal window: this is a windowed application.
    console=False,
    target_arch=TARGET_ARCH,
)

collection = COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="FQHC Prospect Intelligence",
)

if sys.platform == "darwin":
    icon = PROJECT_ROOT / "desktop" / "icon.icns"
    app = BUNDLE(  # noqa: F821
        collection,
        name="FQHC Prospect Intelligence.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="partners.allstar.fqhc-prospect-intelligence",
        info_plist={
            "CFBundleName": "FQHC Prospect Intelligence",
            "CFBundleDisplayName": "FQHC Prospect Intelligence",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHumanReadableCopyright": "Allstar Partners",
            "LSMinimumSystemVersion": "11.0",
            # Without this the webview renders at 1x and looks blurry on Retina.
            "NSHighResolutionCapable": True,
            # The app only ever talks to 127.0.0.1; the pipeline's outbound
            # calls to data.hrsa.gov and ProPublica are plain HTTPS.
            "LSApplicationCategoryType": "public.app-category.business",
        },
    )
