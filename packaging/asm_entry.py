"""PyInstaller entry point for AjaxSubmittalManager.exe."""
import multiprocessing

from asm.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
