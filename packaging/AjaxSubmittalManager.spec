# PyInstaller spec - build on Windows:  pyinstaller packaging/AjaxSubmittalManager.spec --noconfirm
# Produces dist/AjaxSubmittalManager.exe (single file, no console window).
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ONEDIR = os.environ.get("ASM_ONEDIR") == "1"

hidden = (collect_submodules("asm") + collect_submodules("uvicorn") + collect_submodules("anthropic")
          + ["python_multipart", "multipart", "tkinter", "tkinter.ttk", "pymupdf", "fitz", "yaml", "dotenv"])
datas = [
    (os.path.join(ROOT, "frontend", "dist"), os.path.join("frontend", "dist")),
    (os.path.join(ROOT, "config"), "config"),
    (os.path.join(ROOT, "prompts"), "prompts"),
    (os.path.join(SPECPATH, "asm.ico"), "."),
    (os.path.join(SPECPATH, "asm-16.png"), "."),
    (os.path.join(SPECPATH, "asm-32.png"), "."),
    (os.path.join(SPECPATH, "asm-48.png"), "."),
    (os.path.join(SPECPATH, "asm-256.png"), "."),
] + collect_data_files("anthropic")

a = Analysis(
    [os.path.join(SPECPATH, "asm_entry.py")],
    pathex=[os.path.join(ROOT, "backend")],
    datas=datas,
    hiddenimports=hidden,
    excludes=["pytest", "IPython", "matplotlib", "numpy", "scipy", "pandas", "PIL", "cv2", "torch", "playwright", "notebook", "jupyter_client"],
    noarchive=False,
)
# pyinstaller-hooks-contrib >= 2026.10 collects every pymupdf data file, including ~45 MB of ONNX
# layout/table models (pymupdf.layout) this app never uses. Keep them out of the exe.
_UNUSED = ("pymupdf/layout/", "pymupdf4llm/")
a.datas = [d for d in a.datas if not any(u in d[0].replace("\\", "/") for u in _UNUSED)]
pyz = PYZ(a.pure)
common = dict(name="AjaxSubmittalManager", debug=False, strip=False, upx=False, console=False,
              icon=os.path.join(SPECPATH, "asm.ico"))
if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AjaxSubmittalManager")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
