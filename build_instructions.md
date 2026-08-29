# Build Instructions

These build the program into a single `.exe` installer. They assume Python
3.13 on Windows, with the dependencies from
[`requirements.txt`](requirements.txt) installed.

1. Install the required modules if you have not already:

   ```sh
   pip install -r requirements.txt
   ```

2. Open a terminal in the root directory.

3. Use a Markdown-to-PDF converter to generate a `manual.pdf` from
   [`src/assets/manual.md`](src/assets/manual.md) and place it in
   `./src/assets`. The <kbd>Help</kbd> button in the software opens that file.

4. Install [NSIS](https://nsis.sourceforge.io/) if you have not already, and
   add its install location to your `PATH`.

5. Build:

   ```sh
   pyinstaller --noconfirm --windowed --name open-mcr ^
       --icon src/assets/icon.ico ^
       --add-data "src/assets;src/assets" ^
       open_mcr.py
   makensis installer.nsi
   ```

   (In PowerShell use a backtick `` ` `` for line continuation instead of `^`,
   or just put it all on one line.)

## Notes on the build command

**The entry point is `open_mcr.py`, not `src/main_gui.py`.** PyInstaller runs
its entry script as `__main__` with no parent package, and the modules under
`src/` import each other relatively, so pointing PyInstaller straight at
`src/main_gui.py` produces an executable that dies on startup with
`ImportError: attempted relative import with no known parent package`. The
`open_mcr.py` launcher in the repository root exists to import the package
properly and hand over.

**`--add-data "src/assets;src/assets"`** puts the sheets, icon, and manual
where the code looks for them. `user_interface.py` and `sheet_generation.py`
find their assets relative to their own `__file__`, which inside the bundle is
`.../src/`, so the assets have to land in a matching `src/assets`. The
separator is `;` on Windows and `:` elsewhere.

**PyInstaller 6 changed the output layout.** Where older versions put
everything beside the executable, a onedir build now looks like:

```
dist/open-mcr/
  open-mcr.exe
  _internal/
    ... Python, the libraries, and src/assets/ ...
```

`installer.nsi` refers to those paths, so if you change `--name` or the
`--add-data` destination you have to update the `srcdir`, `appdir`, and
`iconrel` defines at the top of it to match.

## Checking the build

The GUI is hard to smoke-test automatically. The quickest manual check is to
run `dist/open-mcr/open-mcr.exe`, confirm the window opens, press
<kbd>Print Form</kbd>, and confirm the CAJCL sheet opens in a PDF viewer — that
exercises the packaged assets as well as the code.
