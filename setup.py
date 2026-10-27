from setuptools import setup
import glob
import os
import tkinter

# コード署名を無効化
os.environ['CODESIGN_ALLOCATE'] = '/usr/bin/true'

# macOS 15 でも動くように、python.org の Python（macOS 11 以降向けに作られている）でビルドする:
#   venv-pyorg/bin/python setup.py py2app
# HomebrewのPythonでビルドするときは、Tcl/Tkライブラリ（init.tcl、tk.tcl等）をContents/Resourcesに同梱する
# （py2appはdylibしかコピーしないため、これが無いとHomebrewが入っていないMacで起動できない）。
# python.org のPythonは Tcl/Tk を Tcl.framework・Tk.framework として持っていて、py2app がそのまま同梱する
TCL_LIBRARY = tkinter.Tcl().eval('info library')
TK_LIBRARY = glob.glob(os.path.join(os.path.dirname(TCL_LIBRARY), 'tk[0-9]*'))

APP = ['split_gui.py']
DATA_FILES = [TCL_LIBRARY, TK_LIBRARY[0]] if TK_LIBRARY and ".framework" not in TCL_LIBRARY else []
OPTIONS = {
    'argv_emulation': False,
    'packages': ['tkinter', 'mutagen'],
    'includes': ['tkinter', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.scrolledtext', 'tkinter.ttk'],
    'excludes': [],
    'plist': {
        'CFBundleName': 'チャプター分割ツール',
        'CFBundleDisplayName': 'チャプター分割ツール',
        'CFBundleGetInfoString': "動画・音声ファイルをチャプターごとに分割",
        'CFBundleIdentifier': "com.audiochaptersplitter.app",
        # 実行ファイルの名前が日本語だと codesign が見つけられず署名が壊れる（他のMacで「壊れている」と出る）ので英字にする
        'CFBundleExecutable': "ChapterSplitter",
        'CFBundleVersion': "1.0.13",
        'CFBundleShortVersionString': "1.0.13",
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '15.0',
        'NSAppearance': 'NSAppearanceNameAqua',
    },
    # 起動のたびにその Mac のシェルの設定を読み込むと、人によって起動に失敗することがあるのでやめる（ffmpeg は決まった場所を直接探す）
    'emulate_shell_environment': False,
}

setup(
    app=APP,
    name='AudioChapterSplitter',
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
