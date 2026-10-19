import glob
import json
import os
import subprocess
import sys
import threading
import time
import traceback

# py2appでバンドルされた場合、同梱したTcl/Tkライブラリを使う（Homebrewが無いMacでも起動するため）
if getattr(sys, 'frozen', False) and os.environ.get('RESOURCEPATH'):
    for env_name, pattern in [('TCL_LIBRARY', 'tcl[0-9]*'), ('TK_LIBRARY', 'tk[0-9]*')]:
        found = glob.glob(os.path.join(os.environ['RESOURCEPATH'], pattern))
        if found:
            os.environ[env_name] = found[0]

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import re

# Tk 9.0以降では明示的な色指定で十分対応可能

def clean_title(title):
    return re.sub(r"^\d+\.\s*", "", title)

def parse_time_to_ms(time_str):
    if not re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', time_str):
        raise ValueError(f"不正な時間形式: {time_str}")
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    elif len(parts) == 2:
        return (parts[0] * 60 + parts[1]) * 1000
    return 0

def format_ms(ms):
    return f"{ms / 1000.0:.6f}"

def format_clock(seconds):
    """再生位置の表示（例: 2:45、1:51:05）"""
    seconds = int(seconds)
    hours, minutes, secs = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

def format_min_sec(seconds):
    """時間の長さの表示（例: 26s、4m01s、160m22s）"""
    seconds = int(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"

def format_elapsed(seconds):
    """処理時間の表示（例: 2.0s、1m05s）"""
    return f"{seconds:.1f}s" if round(seconds, 1) < 60 else format_min_sec(round(seconds))

# ffmpegの同じ種類の警告をログに表示する最大回数（チャプターごと）
MAX_SAME_WARNINGS = 3

END_LINE_HINT = (
    "テキストからJSONを作った場合は、次を確認してください:\n"
    "・最後の行に「END 3:03:10」のような終了行があるか\n"
    "・途中に「END」だけの行が混ざっていないか（END行は最後の行だけが終了として使われます）\n"
    "・時刻が上から順番に増えているか"
)

def find_invalid_chapters(chapters):
    """終了時刻が開始時刻以前、または時刻が読めないチャプターを返す"""
    problems = []
    for i, chapter in enumerate(chapters):
        number = chapter.get("id", i) + 1
        title = chapter.get("tags", {}).get("title", "chapter")
        try:
            start = float(chapter["start_time"])
            end = float(chapter["end_time"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"・{number}. {title}（開始/終了時刻が読み取れません）")
            continue
        if end <= start:
            problems.append(f"・{number}. {title}（開始 {format_min_sec(start)} / 終了 {format_min_sec(end)}）")
    return problems

# ffmpegのエラーメッセージと、その日本語での説明
FFMPEG_ERROR_HINTS = [
    ("-to value smaller than -ss", "終了時刻が開始時刻以前になっています。"),
    ("No such file or directory", "ファイルが見つかりません。外付けドライブが外れていないか確認してください。"),
    ("Permission denied", "ファイルの読み書きが許可されていません。"),
    ("No space left on device", "保存先のディスクの空き容量が足りません。"),
    ("Invalid data found", "ファイルが壊れているか、対応していない形式です。"),
    ("does not contain any stream", "ファイルに音声が含まれていません。"),
    ("matches no streams", "ファイルに音声が含まれていません。"),
]

def explain_ffmpeg_error(output_lines):
    text = "\n".join(output_lines)
    for pattern, hint in FFMPEG_ERROR_HINTS:
        if pattern in text:
            return hint
    return "原因を特定できませんでした。下のffmpegのメッセージを確認してください。"

def get_ffmpeg_path():
    for path in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return "ffmpeg"

def get_ffprobe_path():
    for path in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "ffprobe"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return "ffprobe"

# アプリケーションのエラーハンドリング
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(f"エラーが発生しました:\n{error_msg}", file=sys.stderr)
    
    # GUIが利用可能な場合は、エラーダイアログを表示
    try:
        import tkinter.messagebox as mb
        mb.showerror("エラー", f"アプリケーションエラーが発生しました:\n\n{str(exc_value)}\n\n詳細はコンソールを確認してください。")
    except:
        pass

sys.excepthook = handle_exception

try:
    root = tk.Tk()

    # Tkのデフォルトオプションを設定（テキストウィジェットのみ）
    root.option_add('*Text*background', 'white')
    root.option_add('*Text*foreground', 'black')
    root.option_add('*Text*insertBackground', 'black')

    root.title("チャプター分割ツール")
    root.geometry("600x750")

except Exception as e:
    print(f"GUIの初期化に失敗しました: {e}", file=sys.stderr)
    import tkinter.messagebox as mb
    mb.showerror("エラー", f"アプリケーションの起動に失敗しました:\n{e}")
    sys.exit(1)

# macOSでウィンドウを前面に表示
root.lift()
root.attributes('-topmost', True)
root.after_idle(root.attributes, '-topmost', False)

# 処理停止フラグ
stop_flag = False

def stop_processing():
    global stop_flag
    stop_flag = True
    log("⚠️ 処理を中断します...")

progress_var = tk.DoubleVar()
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100)
progress_bar.pack(fill="x", padx=10, pady=10)

current_label = tk.Label(root, text="現在のチャプター: 未処理")
current_label.pack(padx=10, pady=(0, 5))

output_box = scrolledtext.ScrolledText(root, wrap="word", height=12, bg="white", fg="black", insertbackground="black")
output_box.pack(padx=10, pady=10, fill="both", expand=True)

# Tk 9.1 + macOSでは、wrap="word"の欄をクリックすると短い行でもスペースごとに折り返して表示されることがあるため、
# 1行が短い曲リストの入力欄は折り返さない
text_input = scrolledtext.ScrolledText(root, wrap="none", height=12, bg="white", fg="black", insertbackground="black")
text_input.pack(padx=10, pady=(10, 5), fill="both", expand=True)

def log(msg):
    output_box.insert(tk.END, msg + "\n")
    output_box.see(tk.END)
    root.update_idletasks()

def convert_text_to_json():
    input_text = text_input.get("1.0", tk.END).strip()
    lines = [line for line in input_text.splitlines() if line.strip()]

    if not lines:
        messagebox.showwarning("警告", "テキストを入力してください。")
        return

    final_end = None
    final_end_line = None

    try:
        # 最後の行の曲名がちょうど「END」のときだけ、全体の終了時刻として扱う
        # （「Worlds end」のような曲名や、途中のEND行を終了と取り違えないため）
        last_parts = lines[-1].strip().rsplit(" ", 1)
        if len(last_parts) == 2 and clean_title(last_parts[0].strip()).upper() == "END":
            final_end = parse_time_to_ms(last_parts[1])
            final_end_line = len(lines) - 1

        parsed = []
        for idx, line in enumerate(lines):
            if idx == final_end_line:
                continue
            parts = line.strip().rsplit(" ", 1)
            if len(parts) != 2:
                raise ValueError(f"無効な行の形式: {line}")
            title, time_str = parts
            start_ms = parse_time_to_ms(time_str)
            parsed.append((idx, title.strip(), start_ms))
    except ValueError as ve:
        messagebox.showerror("エラー", f"テキストの形式が正しくありません:\n{ve}")
        return

    chapters = []
    for i, (cid, title, start_ms) in enumerate(parsed):
        end_ms = parsed[i + 1][2] if i + 1 < len(parsed) else final_end or start_ms
        chapters.append({
            "id": cid,
            "time_base": "1/1000",
            "start": start_ms,
            "start_time": format_ms(start_ms),
            "end": end_ms,
            "end_time": format_ms(end_ms),
            "tags": {"title": clean_title(title)}
        })

    # 終了時刻が開始時刻以前のチャプターがあれば、原因を説明して保存しない
    problems = []
    for i, (cid, title, start_ms) in enumerate(parsed):
        end_ms = chapters[i]["end"]
        if end_ms > start_ms:
            continue
        label = f"・{clean_title(title)}（開始 {format_min_sec(start_ms / 1000)}）"
        relation = "と同じです" if end_ms == start_ms else "より前です"
        if i + 1 < len(parsed):
            reason = f"  次の「{clean_title(parsed[i + 1][1])}」の開始時刻 {format_min_sec(end_ms / 1000)} がこれ{relation}。時刻を確認してください。"
            if clean_title(title).upper() == "END":
                reason += "\n  END行は最後の行にだけ書けます。途中のEND行は削除してください。"
            problems.append(f"{label}\n{reason}")
        elif final_end is None:
            problems.append(f"{label}\n  最後のチャプターですが、END行が無いため終了時刻が決まりません。\n  末尾に「END 3:03:10」のような行を追加してください。")
        else:
            problems.append(f"{label}\n  END行の時刻 {format_min_sec(final_end / 1000)} がこれ{relation}。")
    if problems:
        details = "\n".join(problems)
        log(f"❌ JSONに変換できないチャプターがあります:\n{details}")
        messagebox.showerror("エラー", f"次のチャプターは終了時刻が開始時刻以前になるため、JSONを保存しませんでした。\n\n{details}")
        return

    output = {"chapters": chapters}

    # JSONファイルの保存先を選択
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    json_path = filedialog.asksaveasfilename(
        title="JSONファイルを保存",
        initialdir=desktop_path,
        initialfile="chapters.json",
        defaultextension=".json",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
    )

    if not json_path:
        return

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    messagebox.showinfo("成功", f"JSONを保存しました:\n{os.path.basename(json_path)}")
    log(f"✅ JSON書き出し成功: {json_path}")

def extract_chapters_from_video():
    def run():
        video_path = filedialog.askopenfilename(
            title="動画ファイルを選択",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.avi *.mkv *.m4v"),
                ("All files", "*.*")
            ]
        )
        if not video_path:
            return

        ffprobe_path = get_ffprobe_path()
        log(f"▶ ffprobe path: {ffprobe_path}")
        log(f"📹 動画ファイル: {video_path}")

        # ffprobeでチャプター情報を抽出
        cmd = [
            ffprobe_path, "-i", video_path,
            "-print_format", "json",
            "-show_chapters",
            "-loglevel", "error"
        ]

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace'
            )
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                error_msg = stderr.strip() if stderr else "エラーが発生しました"
                messagebox.showerror("エラー", f"ffprobeの実行に失敗しました:\n{error_msg}")
                log(f"❌ エラー: {error_msg}")
                return

            # JSONをパース
            data = json.loads(stdout)
            chapters = data.get("chapters", [])

            if not chapters:
                messagebox.showwarning("警告", "この動画ファイルにはチャプター情報が含まれていません。")
                log("⚠️ チャプター情報が見つかりませんでした")
                return

            log(f"✅ {len(chapters)}個のチャプターを検出しました")

            # JSONファイルの保存先を選択
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            json_path = filedialog.asksaveasfilename(
                title="JSONファイルを保存",
                initialdir=desktop_path,
                initialfile="chapters.json",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )

            if not json_path:
                return

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            messagebox.showinfo("成功", f"チャプター情報を保存しました:\n{os.path.basename(json_path)}\n（{len(chapters)}個のチャプター）")
            log(f"✅ JSON書き出し成功: {json_path}")
            log(f"📊 チャプター数: {len(chapters)}")

        except json.JSONDecodeError as e:
            messagebox.showerror("エラー", f"JSONの解析に失敗しました:\n{e}")
            log(f"❌ JSON解析エラー: {e}")
        except Exception as e:
            messagebox.showerror("エラー", f"エラーが発生しました:\n{e}")
            log(f"❌ エラー: {e}")

    threading.Thread(target=run).start()

def split_audio_fast():
    def run():
        media_path = filedialog.askopenfilename(
            title="音声ファイルまたは動画ファイルを選択",
            filetypes=[
                ("Audio files", "*.m4a *.mp3 *.wav"),
                ("Video files", "*.mp4 *.mov *.avi *.mkv"),
                ("All files", "*.*")
            ]
        )
        if not media_path:
            return

        # ファイルの拡張子で動画か音声かを判定
        ext = os.path.splitext(media_path)[1].lower()
        is_video = ext in ['.mp4', '.mov', '.avi', '.mkv', '.flv', '.webm', '.m4v']

        log(f"📹 選択されたファイル: {media_path}")
        log(f"📋 ファイルタイプ: {'動画' if is_video else '音声'}")

        # 動画ファイルの場合は、動画からチャプター情報を自動抽出
        if is_video:
            ffprobe_path = get_ffprobe_path()
            log(f"▶ ffprobe path: {ffprobe_path}")
            log("📊 動画からチャプター情報を抽出中...")

            cmd = [
                ffprobe_path, "-i", media_path,
                "-print_format", "json",
                "-show_chapters",
                "-loglevel", "error"
            ]

            try:
                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace'
                )
                stdout, stderr = process.communicate()

                if process.returncode != 0:
                    error_msg = stderr.strip() if stderr else "エラーが発生しました"
                    messagebox.showerror("エラー", f"チャプター情報の抽出に失敗しました:\n{error_msg}")
                    log(f"❌ エラー: {error_msg}")
                    return

                # JSONをパース
                data = json.loads(stdout)
                chapters = data.get("chapters", [])

                if not chapters:
                    log("⚠️ 動画にチャプター情報が見つかりませんでした")
                    # JSONファイルを選択するか確認
                    if messagebox.askyesno("チャプター情報なし",
                                          "この動画ファイルにはチャプター情報が含まれていません。\n\nJSONファイルを選択して分割しますか？"):
                        # JSONファイルを選択
                        json_path = filedialog.askopenfilename(
                            title="チャプターJSONファイルを選択",
                            filetypes=[("JSON files", "*.json")],
                            initialdir=os.path.join(os.path.expanduser("~"), "Desktop")
                        )
                        if not json_path:
                            log("❌ JSONファイルが選択されませんでした")
                            return

                        log(f"📄 JSONファイル: {json_path}")
                        with open(json_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        chapters = data.get("chapters", [])

                        if not chapters:
                            messagebox.showerror("エラー", "JSONファイルにチャプター情報が含まれていません。")
                            log("❌ JSONファイルにチャプター情報がありません")
                            return
                        log(f"✅ JSONから{len(chapters)}個のチャプターを読み込みました")
                    else:
                        log("❌ 処理をキャンセルしました")
                        return
                else:
                    log(f"✅ {len(chapters)}個のチャプターを検出しました")

            except json.JSONDecodeError as e:
                messagebox.showerror("エラー", f"JSONの解析に失敗しました:\n{e}")
                log(f"❌ JSON解析エラー: {e}")
                return
            except Exception as e:
                messagebox.showerror("エラー", f"エラーが発生しました:\n{e}")
                log(f"❌ エラー: {e}")
                return

        else:
            # 音声ファイルの場合は、JSONファイルを選択
            json_path = filedialog.askopenfilename(
                title="チャプターJSONファイルを選択",
                filetypes=[("JSON files", "*.json")],
                initialdir=os.path.join(os.path.expanduser("~"), "Desktop")
            )
            if not json_path:
                return
            log(f"📄 JSONファイル: {json_path}")

            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError as e:
                messagebox.showerror("エラー", f"JSONファイルの形式が正しくありません:\n{os.path.basename(json_path)}\n\n{e.lineno}行目 {e.colno}文字目: {e.msg}")
                log(f"❌ JSON解析エラー: {e}")
                return

            chapters = data.get("chapters", [])
            if not chapters:
                messagebox.showerror("エラー", "JSONファイルにチャプター情報が含まれていません。")
                log("❌ JSONファイルにチャプター情報がありません")
                return

        # 分割を始める前に、分割できないチャプターが無いか確認
        problems = find_invalid_chapters(chapters)
        if problems:
            details = "\n".join(problems)
            log(f"❌ 分割できないチャプターがあります:\n{details}")
            messagebox.showerror(
                "チャプター情報のエラー",
                f"次のチャプターは終了時刻が開始時刻以前になっているため、分割できません。\n\n{details}\n\n{END_LINE_HINT}"
            )
            return

        media_filename = os.path.splitext(os.path.basename(media_path))[0]
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        output_dir = os.path.join(desktop_path, media_filename)
        os.makedirs(output_dir, exist_ok=True)
        log(f"💾 出力先: {output_dir}")

        ffmpeg_path = get_ffmpeg_path()
        ffprobe_path = get_ffprobe_path()
        log(f"▶ ffmpeg path: {ffmpeg_path}")

        # 元のファイルからメタデータを取得
        metadata = {}
        try:
            cmd_metadata = [
                ffprobe_path, "-i", media_path,
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                "-loglevel", "error"
            ]
            process_metadata = subprocess.Popen(
                cmd_metadata, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace'
            )
            stdout_metadata, stderr_metadata = process_metadata.communicate()
            if process_metadata.returncode == 0:
                format_data = json.loads(stdout_metadata)

                # formatタグとストリームタグの両方を確認
                all_tags = {}
                format_tags = format_data.get("format", {}).get("tags", {})
                all_tags.update(format_tags)

                # ストリームタグも確認（特に音声ストリーム）
                streams = format_data.get("streams", [])
                for stream in streams:
                    if stream.get("codec_type") == "audio":
                        stream_tags = stream.get("tags", {})
                        all_tags.update(stream_tags)
                        break

                log(f"🔍 検出されたメタデータタグ: {list(all_tags.keys())}")

                # 継承するメタデータのマッピング定義
                metadata_mapping = {
                    "album": ["album", "ALBUM", "Album", "©alb"],
                    "artist": ["artist", "ARTIST", "Artist", "©ART", "album_artist", "ALBUM_ARTIST"],
                    "album_artist": ["album_artist", "ALBUM_ARTIST", "albumartist"],
                    "genre": ["genre", "GENRE", "Genre", "©gen"],
                    "date": ["date", "DATE", "Date", "year", "YEAR", "©day"],
                    "composer": ["composer", "COMPOSER", "Composer", "©wrt"],
                    "comment": ["comment", "COMMENT", "Comment", "©cmt"],
                    "copyright": ["copyright", "COPYRIGHT", "Copyright", "©cpy"],
                    "publisher": ["publisher", "PUBLISHER", "Publisher", "label"],
                    "description": ["description", "DESCRIPTION", "Description"],
                }

                # 各メタデータを検索して取得
                for meta_key, possible_keys in metadata_mapping.items():
                    for key in possible_keys:
                        if key in all_tags and all_tags[key] and str(all_tags[key]).strip():
                            metadata[meta_key] = str(all_tags[key]).strip()
                            log(f"  📌 {meta_key}: {metadata[meta_key]}")
                            break

                # 動画ファイルの場合、元のファイルのタイトルをアルバムタイトルとして設定
                if is_video:
                    # 動画のタイトルタグを取得
                    video_title = None
                    for key in ["title", "TITLE", "Title", "©nam"]:
                        if key in all_tags and all_tags[key] and str(all_tags[key]).strip():
                            video_title = str(all_tags[key]).strip()
                            break

                    # タイトルが見つかった場合、それをアルバムタイトルとして設定
                    if video_title:
                        metadata["album"] = video_title
                        log(f"  🎬 動画タイトルをアルバムに設定: {video_title}")
                    # タイトルタグがない場合はファイル名をアルバムタイトルとして使用
                    else:
                        metadata["album"] = media_filename
                        log(f"  📁 ファイル名をアルバムに設定: {media_filename}")

                if metadata:
                    log(f"📋 継承するメタデータ: {list(metadata.keys())}")
                else:
                    log(f"⚠️ メタデータが見つかりませんでした")
        except Exception as e:
            log(f"⚠️ メタデータの取得に失敗（継続します）: {e}")

        # 出力ファイルのリストを保存（音声ファイルの場合、後でアートワークを追加）
        output_files = []
        split_started = time.time()

        for i, chapter in enumerate(chapters):
            # 停止フラグをチェック
            global stop_flag
            if stop_flag:
                log("❌ 処理が中断されました")
                messagebox.showwarning("中断", "処理を中断しました。")
                stop_flag = False
                current_label.config(text="処理が中断されました")
                progress_var.set(0)
                return

            start = chapter["start_time"]
            end = chapter["end_time"]
            title = chapter.get("tags", {}).get("title", "chapter")
            safe_title = title.replace(" ", "_").replace("/", "_")[:50]
            chapter_id = chapter.get("id", 0)
            track_number = chapter_id + 1

            # 出力ファイルは常にm4a形式
            output_file = os.path.join(output_dir, f"{track_number:02d}_{safe_title}.m4a")
            output_files.append(output_file)

            # 動画の場合は音声のみを抽出、音声の場合はそのまま処理
            # 高品質AACエンコード（途切れのない正確な分割）
            if is_video:
                # 動画：-ss/-toを-iの前に配置（アートワーク継承に必須）
                cmd = [
                    ffmpeg_path, "-y",
                    "-ss", start,
                    "-to", end,
                    "-i", media_path,
                ]
            else:
                # 音声：-ss/-toを-iの後に配置（0057704の方法で途切れ防止）
                cmd = [
                    ffmpeg_path, "-y", "-i", media_path,
                    "-ss", start,
                    "-to", end,
                ]

            # 音声ストリームのマッピング
            cmd.extend([
                "-map", "0:a:0",  # 最初の音声ストリームのみ
                "-c:a", "aac",  # 再エンコードで正確な分割
                "-b:a", "256k",  # 高品質ビットレート
            ])

            # 動画の場合のみアートワークを含める（音声は後で一斉に追加）
            if is_video:
                # 動画の場合：ストリーム#0:2以降がattached_pic（#0:0はメインビデオ、#0:1は音声）
                cmd.extend([
                    "-map", "0:v:1?",  # 2番目のビデオストリーム（attached_pic）
                    "-map", "0:v:2?",  # 3番目のビデオストリーム（attached_pic）
                    "-c:v", "copy",  # アートワークをコピー
                    "-disposition:v", "attached_pic",  # アートワークとして設定
                ])

            cmd.extend(["-f", "mp4"])  # MP4コンテナを明示

            # メタデータをクリアしてから設定
            cmd.extend(["-map_metadata", "-1"])

            # 取得したメタデータを明示的に設定
            for meta_key, meta_value in metadata.items():
                cmd.extend(["-metadata", f"{meta_key}={meta_value}"])

            # チャプターごとのタイトルとトラック番号を設定
            cmd.extend([
                "-metadata", f"title={title}",
                "-metadata", f"track={track_number}",
                output_file
            ])

            log(f"{track_number}: {title} ({format_clock(float(start))}-{format_clock(float(end))}, {format_min_sec(float(end) - float(start))})")
            current_label.config(text=f"現在のチャプター: {track_number} - {title}")

            chapter_started = time.time()
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace'
            )
            ffmpeg_output = []
            # 壊れたファイルでは同じ警告が数万行出てログ表示が追いつかなくなるため、
            # 同じ種類の警告は最初の数回だけ表示し、残りは件数だけ表示する
            warning_counts = {}
            suppressed = 0
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                ffmpeg_output.append(line)
                if line.startswith("[") or line.startswith("Last message repeated"):
                    key = re.sub(r"0x[0-9a-f]+|-?\d+", "#", line)
                    warning_counts[key] = warning_counts.get(key, 0) + 1
                    if warning_counts[key] > MAX_SAME_WARNINGS:
                        suppressed += 1
                        continue
                log(line)
            process.wait()
            if suppressed:
                log(f"（同じ警告が続いたため {suppressed} 行を省略しました）")

            # エラーチェック
            if process.returncode != 0:
                log(f"❌ ffmpegがエラーコード{process.returncode}で終了しました")
                messagebox.showerror(
                    "エラー",
                    f"チャプター{track_number}「{title}」（{format_min_sec(float(start))}〜{format_min_sec(float(end))}）の処理に失敗しました。\n\n"
                    f"原因: {explain_ffmpeg_error(ffmpeg_output)}\n\n"
                    f"ffmpegのメッセージ（エラーコード{process.returncode}）:\n" + "\n".join(ffmpeg_output[-5:])
                )
                return

            log(f"-> {format_elapsed(time.time() - chapter_started)}")

            progress = ((i + 1) / len(chapters)) * 100
            progress_var.set(progress)
            root.update_idletasks()

        # 音声ファイルの場合、分割後に一斉にアートワークを追加
        if not is_video and output_files:
            log("🎨 アートワークを追加中...")
            current_label.config(text="アートワークを追加中...")

            for idx, output_file in enumerate(output_files):
                temp_file = output_file + ".temp.m4a"

                artwork_cmd = [
                    ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", output_file,  # 分割済みファイル
                    "-i", media_path,   # 元のファイル（アートワーク取得用）
                    "-map", "0:a",      # 分割済みファイルの音声
                    "-map", "1:v?",     # 元のファイルのアートワーク
                    "-c:a", "copy",     # 音声はコピー
                    "-c:v", "copy",     # アートワークもコピー
                    "-disposition:v:0", "attached_pic",
                    "-map_metadata", "0",  # 分割済みファイルのメタデータを保持
                    temp_file
                ]

                process = subprocess.Popen(
                    artwork_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace'
                )
                artwork_output, _ = process.communicate()

                if process.returncode == 0:
                    os.replace(temp_file, output_file)
                    log(f"  ✅ {os.path.basename(output_file)}")
                else:
                    error_lines = [l for l in artwork_output.splitlines() if l.strip()]
                    log(f"  ⚠️ {os.path.basename(output_file)} - アートワーク追加失敗: {explain_ffmpeg_error(error_lines)}")
                    for l in error_lines[-3:]:
                        log(f"     {l}")
                    if os.path.exists(temp_file):
                        os.remove(temp_file)

                progress = ((idx + 1) / len(output_files)) * 100
                progress_var.set(progress)
                root.update_idletasks()

        messagebox.showinfo("完了", "チャプター分割が完了しました。")
        log(f"分割完了 (合計 {format_elapsed(time.time() - split_started)})")
        current_label.config(text="すべて完了")
        progress_var.set(0)

    threading.Thread(target=run).start()

btn_convert = tk.Button(root, text="📝 テキスト → JSON変換", command=convert_text_to_json)
btn_convert.pack(fill="x", padx=10, pady=5)

btn_extract = tk.Button(root, text="🎬 動画からチャプター抽出", command=extract_chapters_from_video)
btn_extract.pack(fill="x", padx=10, pady=5)

btn_split = tk.Button(root, text="🎬 動画/音声 → 分割", command=split_audio_fast)
btn_split.pack(fill="x", padx=10, pady=5)

btn_stop = tk.Button(root, text="⛔ 処理を中断", command=stop_processing)
btn_stop.pack(fill="x", padx=10, pady=5)

try:
    root.mainloop()
except Exception as e:
    print(f"アプリケーション実行中のエラー: {e}", file=sys.stderr)
    import tkinter.messagebox as mb
    mb.showerror("エラー", f"アプリケーション実行中のエラーが発生しました:\n{e}")
    sys.exit(1)