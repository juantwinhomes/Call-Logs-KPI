# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for TwinCallTracker.exe.

Two build shapes, selected by the BUILD_ONEFILE environment variable:

  one-folder (default)  dist\\TwinCallTracker\\TwinCallTracker.exe
      Recommended for production. Starts faster, and support staff can see the
      files, so a missing dependency is obvious rather than mysterious.

  one-file  (BUILD_ONEFILE=1)
      dist\\TwinCallTracker.exe, a single file that unpacks to a temporary
      folder on each launch. Convenient to copy around, slower to start, and
      some antivirus products are suspicious of it.

console=False in both, so no Command Prompt window appears (requirement 25).
"""
import os
from PyInstaller.utils.hooks import collect_submodules

ONEFILE = os.environ.get("BUILD_ONEFILE", "") == "1"
APP_NAME = "TwinCallTracker"

block_cipher = None

# keyring finds its Windows backend at runtime through entry points, which the
# analysis cannot see, so the backends are named explicitly.
hidden = [
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.null",
    "win32timezone",                    # imported lazily by pywin32 on Windows
    "google.auth.transport.requests",
    "googleapiclient.discovery",
]
hidden += collect_submodules("googleapiclient")

datas = []
# Ship the icon when it exists; the build works without it.
if os.path.isfile(os.path.join("resources", "app.ico")):
    datas.append((os.path.join("resources", "app.ico"), "resources"))

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt ships far more than a form-based app needs; dropping these keeps the
    # build to a sensible size without touching anything the app uses.
    excludes=[
        "tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest", "_pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngine",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtQuick",
        "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtBluetooth", "PySide6.QtSerialPort",
        "PySide6.QtPositioning", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
        "PySide6.QtDesigner", "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtPdf",
        "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio", "PySide6.QtHelp",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --------------------------------------------------------------------------- #
# Trim the payload
#
# Two things dominate an untrimmed build and neither is needed:
#
#   * google-api-python-client ships a static discovery document for every Google
#     API - several hundred JSON files, around 95 MB. This app builds exactly one
#     service, so every document except the Sheets one is dropped.
#   * Qt pulls QtQuick, QtQml, QtPdf and friends in as shared-library
#     dependencies even when the excludes above keep their Python bindings out.
#     A widgets-only application never loads them.
#
# Both filters are conservative: they name what to drop rather than what to keep,
# so a new dependency is included by default rather than silently lost.
# --------------------------------------------------------------------------- #

_KEEP_DISCOVERY = ("sheets.v4.json",)


def _wanted_data(entry):
    dest = entry[0].replace("\\", "/")
    if "googleapiclient/discovery_cache/documents/" in dest:
        return dest.endswith(_KEEP_DISCOVERY)
    if "/Qt/translations/" in dest or dest.startswith("PySide6/Qt/translations/"):
        return False              # the interface is English only
    return True


_DROP_QT_LIBS = (
    "qt6quick", "qt6qml", "qt6quicktemplates", "qt6quickcontrols", "qt6quickparticles",
    "qt6quickwidgets", "qt6quickshapes", "qt6quicktest", "qt6quicklayouts",
    "qt6pdf", "qt6pdfwidgets", "qt6webengine", "qt6webchannel", "qt6websockets",
    "qt6multimedia", "qt6multimediawidgets", "qt63d", "qt6charts", "qt6datavisualization",
    "qt6designer", "qt6test", "qt6sql", "qt6bluetooth", "qt6nfc", "qt6serialport",
    "qt6positioning", "qt6sensors", "qt6spatialaudio", "qt6help", "qt6opengl",
    "qt6statemachine", "qt6scxml", "qt6remoteobjects", "qt6texttospeech",
)


def _wanted_binary(entry):
    name = os.path.basename(entry[0]).lower()
    stem = name.split(".so")[0].replace("lib", "", 1) if ".so" in name else name.replace(".dll", "")
    if stem in _DROP_QT_LIBS:
        return False
    # Qt plugin folders for subsystems the app does not use.
    dest = entry[0].replace("\\", "/").lower()
    for folder in ("/qml/", "/plugins/multimedia", "/plugins/sqldrivers",
                   "/plugins/webengine", "/plugins/designer", "/plugins/sceneparsers",
                   "/plugins/renderers", "/plugins/geometryloaders", "/plugins/texttospeech",
                   "/plugins/position", "/plugins/sensors"):
        if folder in dest:
            return False
    return True


a.datas = [d for d in a.datas if _wanted_data(d)]
a.binaries = [b for b in a.binaries if _wanted_binary(b)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon = os.path.join("resources", "app.ico")
icon_arg = icon if os.path.isfile(icon) else None

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                     # UPX trips antivirus more often than it saves space
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,                 # no Command Prompt window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_arg,
        version="version_info.txt" if os.path.isfile("version_info.txt") else None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,                 # no Command Prompt window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon_arg,
        version="version_info.txt" if os.path.isfile("version_info.txt") else None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
