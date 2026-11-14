"""
Müzik İndirici - YouTube / YouTube Music indirme aracı (yt-dlp + ffmpeg)

Sadece kendi içeriklerini, telifsiz / Creative Commons içerikleri veya
indirme hakkın olan içerikleri indirmek için kullan.
"""
import os
import re
import sys
import queue
import shutil
import threading
import importlib.util
import json
import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from tkinter import filedialog, messagebox

import customtkinter as ctk

# --- yt-dlp'yi uygulama içinden güncelleyebilmek için -----------------------
# Güncellemeler %LOCALAPPDATA%\MuzikIndirici\updates klasörüne wheel olarak iner
# ve uygulamanın içine gömülü yt-dlp sürümünün yerine geçer.
APP_DATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")) / "MuzikIndirici"
UPDATE_DIR = APP_DATA / "updates"
UPDATE_PACKAGES = (("yt_dlp", "yt-dlp"), ("yt_dlp_ejs", "yt-dlp-ejs"))


def find_update_wheels():
    wheels = {}
    for pkg, _ in UPDATE_PACKAGES:
        try:
            files = sorted(UPDATE_DIR.glob(f"{pkg}-*.whl"), key=lambda f: f.stat().st_mtime)
        except OSError:
            continue
        if not files:
            continue
        wheels[pkg] = str(files[-1])
        for old in files[:-1]:  # eski güncellemeleri temizle
            try:
                old.unlink()
            except OSError:
                pass
    return wheels


def install_update_finder(wheels):
    """Güncelleme wheel'lerini gömülü sürümlerden önce import ettirir."""
    import importlib.machinery as machinery

    class UpdateFinder:
        @classmethod
        def find_spec(cls, name, path=None, target=None):
            wheel = wheels.get(name.partition(".")[0])
            if not wheel:
                return None
            return machinery.PathFinder.find_spec(name, [wheel] if path is None else path, target)

    sys.meta_path.insert(0, UpdateFinder)
    return UpdateFinder


_UPDATE_WHEELS = find_update_wheels()
_UPDATE_FINDER = install_update_finder(_UPDATE_WHEELS) if _UPDATE_WHEELS else None
try:
    import yt_dlp
    from yt_dlp.version import __version__ as YTDLP_VERSION
except Exception:  # bozuk güncelleme dosyası varsa gömülü sürüme dön
    if _UPDATE_FINDER is None:
        raise
    sys.meta_path.remove(_UPDATE_FINDER)
    for _name in [n for n in sys.modules if n.partition(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
        del sys.modules[_name]
    for _wheel in _UPDATE_WHEELS.values():
        try:
            os.remove(_wheel)
        except OSError:
            pass
    _UPDATE_WHEELS = {}
    import yt_dlp
    from yt_dlp.version import __version__ as YTDLP_VERSION
from yt_dlp.utils import sanitize_filename

# --noconsole ile paketlenen exe'de stdout/stderr None olur; yt-dlp yazarken çökmesin
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

APP_NAME = "Müzik İndirici"

MP3_QUALITIES = ["320 kbps", "192 kbps", "128 kbps"]
MP4_QUALITIES = ["En iyi", "1080p", "720p", "480p", "360p"]
COOKIE_FILE_LABEL = "cookies.txt dosyası"
BROWSERS = ["Yok", "Firefox", "Edge", "Chrome", "Brave", "Opera", "Opera GX", "Vivaldi", COOKIE_FILE_LABEL]

OPERA_GX_PROFILE = os.path.join(os.environ.get("APPDATA", ""), "Opera Software", "Opera GX Stable")


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- yardımcılar
def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def apply_icon(window):
    """Pencere ikonunu ayarlar (CustomTkinter varsayılan ikonu geç yüklediği için iki kez dener)."""
    path = resource_path("icon.ico")
    if not os.path.isfile(path):
        return

    def _set():
        try:
            window.iconbitmap(path)
        except Exception:  # noqa: BLE001
            pass

    _set()
    try:
        window.after(300, _set)
    except Exception:  # noqa: BLE001
        pass


def fetch_update(log):
    """PyPI'den yt-dlp ve yt-dlp-ejs'in son sürümlerini indirir. Yeni dosya indiyse True döner."""
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    changed = False
    for pkg, project in UPDATE_PACKAGES:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/json", timeout=30) as r:
            data = json.load(r)
        version = data["info"]["version"]
        if pkg == "yt_dlp" and version == YTDLP_VERSION:
            log(f"yt-dlp zaten güncel ({version}).")
            continue
        wheel = next((u for u in data["urls"]
                      if u.get("packagetype") == "bdist_wheel" and u["filename"].endswith("-none-any.whl")), None)
        if not wheel:
            log(f"{project}: uygun paket bulunamadı, atlandı.")
            continue
        target = UPDATE_DIR / wheel["filename"]
        if target.exists():
            log(f"{project} {version} daha önce indirilmiş; uygulamayı yeniden başlatman yeterli.")
            continue
        log(f"{project} {version} indiriliyor...")
        tmp = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        with urllib.request.urlopen(wheel["url"], timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != wheel["digests"]["sha256"]:
            tmp.unlink()
            raise RuntimeError(f"{project}: dosya doğrulaması (SHA256) başarısız oldu.")
        tmp.replace(target)
        changed = True
        log(f"{project} {version} indirildi.")
    return changed


def search_dirs():
    """Yan araçların (ffmpeg, deno) aranacağı klasörler."""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(getattr(sys, "_MEIPASS", ""))
        dirs.append(os.path.dirname(sys.executable))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        dirs += [here, os.path.join(here, "bin")]
    return [d for d in dirs if d]


def find_ffmpeg():
    """(klasör, bulundu_mu) döner. Klasör None ise PATH kullanılır."""
    for folder in search_dirs():
        if os.path.isfile(os.path.join(folder, "ffmpeg.exe")) and \
                os.path.isfile(os.path.join(folder, "ffprobe.exe")):
            return folder, True
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return None, True
    return None, False


def find_js_runtime():
    """yt-dlp'nin YouTube için ihtiyaç duyduğu JS runtime'ı (Deno/Node) arar."""
    for folder in search_dirs():
        p = os.path.join(folder, "deno.exe")
        if os.path.isfile(p):
            return {"deno": {"path": p}}
    p = shutil.which("deno")
    if p:
        return {"deno": {"path": p}}
    p = Path.home() / ".deno" / "bin" / "deno.exe"
    if p.is_file():
        return {"deno": {"path": str(p)}}
    p = shutil.which("node")
    if p:
        return {"node": {"path": p}}
    return None


ANSI_RE = re.compile(r"(?:\x1b)?\[[0-9;]*m")


def is_playlist_url(url):
    """Sadece playlist içeren (belirli bir video içermeyen) bağlantı mı?"""
    q = parse_qs(urlparse(url).query)
    return "list" in q and "v" not in q


def pick_error(errors):
    """Ayrıntı modunda traceback satırları da gelir; gerçek 'ERROR:' satırını seçer."""
    for chunk in reversed(errors):
        for line in reversed(ANSI_RE.sub("", chunk).splitlines()):
            if line.strip().startswith("ERROR:"):
                return line.strip()
    if errors:
        lines = [l for l in ANSI_RE.sub("", errors[-1]).splitlines() if l.strip()]
        if lines:
            return lines[-1].strip()
    return ""


def friendly_error(raw):
    raw = ANSI_RE.sub("", raw or "").strip()
    if not raw:
        return "Bilinmeyen hata"
    low = raw.lower()
    line = raw.splitlines()[-1]
    line = re.sub(r"^ERROR:\s*", "", line)
    line = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", line)
    if len(line) > 200:
        line = line[:200] + "…"

    if "cookie" in low and any(k in low for k in ("could not copy", "failed to load", "database", "decrypt")):
        hint = ("Çerezler okunamadı → tarayıcıyı tamamen kapat, Firefox seç "
                "ya da 'cookies.txt dosyası' seçeneğini kullan.")
    elif any(k in low for k in ("sign in", "not a bot", "age-restricted", "age restricted",
                              "members-only", "members only", "login")):
        hint = "Giriş gerekiyor → 'Tarayıcı çerezi' kutusundan tarayıcını seç."
    elif any(k in low for k in ("javascript", "n challenge", "requested format is not available",
                                "only images are available")):
        hint = "JS runtime (Deno) eksik veya yt-dlp eski olabilir → README'ye bak."
    elif any(k in low for k in ("private", "deleted", "removed", "terminated", "unavailable",
                                "not available in your country", "blocked")):
        hint = "Video silinmiş, özel veya bölgene kapalı."
    elif "ffmpeg" in low or "ffprobe" in low:
        hint = "ffmpeg/ffprobe bulunamadı."
    else:
        hint = ""
    return f"{line}  ({hint})" if hint else line


class FlatLogger:
    """Genel yt-dlp hatalarını log kutusuna gönderir."""

    def __init__(self, send):
        self.send = send

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.send(("log", "HATA: " + friendly_error(str(msg))))


class ItemLogger:
    """Tek bir şarkının hatalarını ve ayrıntılı çıktısını biriktirir."""

    def __init__(self):
        self.errors = []
        self.lines = []

    def debug(self, msg):
        self.lines.append(ANSI_RE.sub("", str(msg)))

    def info(self, msg):
        self.lines.append(ANSI_RE.sub("", str(msg)))

    def warning(self, msg):
        self.lines.append("WARNING: " + ANSI_RE.sub("", str(msg)))

    def error(self, msg):
        self.errors.append(str(msg))
        self.lines.append("ERROR: " + ANSI_RE.sub("", str(msg)))

    def tail(self):
        if len(self.lines) > 160:
            return self.lines[:60] + ["   [...]"] + self.lines[-100:]
        return list(self.lines)


def esc(s):
    return s.replace("%", "%%")


# ------------------------------------------------------------ şarkı seçme penceresi
class SelectDialog(ctk.CTkToplevel):
    """Playlist'teki şarkılardan indirilecekleri seçtirir."""

    def __init__(self, master, items, on_done):
        super().__init__(master)
        self.title("Şarkıları seç")
        self.geometry("640x620")
        self.minsize(520, 420)
        self.items = items
        self.on_done = on_done
        self.finished = False
        self.vars = [ctk.BooleanVar(value=True) for _ in items]

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(self, text=f"{len(items)} şarkı bulundu. İndirmek istediklerini işaretle.",
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkButton(bar, text="Tümünü seç", width=120, command=lambda: self._set_all(True)) \
            .pack(side="left")
        ctk.CTkButton(bar, text="Hiçbirini seçme", width=120, command=lambda: self._set_all(False)) \
            .pack(side="left", padx=8)

        self.list = ctk.CTkScrollableFrame(self)
        self.list.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        for item, var in zip(items, self.vars):
            text = item["label"]
            if len(text) > 80:
                text = text[:77] + "..."
            ctk.CTkCheckBox(self.list, text=text, variable=var, command=self._update_count) \
                .pack(anchor="w", pady=3, padx=4)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        foot.grid_columnconfigure(0, weight=1)
        self.ok_btn = ctk.CTkButton(foot, text="", height=38, command=self._confirm)
        self.ok_btn.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(foot, text="Vazgeç", height=38, width=110, fg_color="#b23b3b",
                      hover_color="#8f2f2f", command=self._cancel).grid(row=0, column=1, padx=(8, 0))

        self._update_count()
        apply_icon(self)
        self.after(150, self._grab)

    def _grab(self):
        if self.finished:
            return
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass

    def _update_count(self):
        n = sum(1 for v in self.vars if v.get())
        self.ok_btn.configure(text=f"{n} şarkıyı indir", state="normal" if n else "disabled")

    def _set_all(self, value):
        for v in self.vars:
            v.set(value)
        self._update_count()

    def _close(self, selected):
        if self.finished:
            return
        self.finished = True
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
        self.on_done(selected)

    def _confirm(self):
        self._close([it for it, v in zip(self.items, self.vars) if v.get()])

    def _cancel(self):
        self._close([])


# ------------------------------------------------------------------ uygulama
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("780x900")
        self.minsize(700, 820)
        apply_icon(self)
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.q = queue.Queue()
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.partial = {}
        self.done_count = 0
        self.total = 0
        self.worker = None
        self.cookie_file = ""
        self.outdir = ""
        self.search_fallback = False
        self.select_event = threading.Event()
        self.select_result = []

        self.ffmpeg_dir, self.ffmpeg_ok = find_ffmpeg()
        self.js_runtime = find_js_runtime()
        self.has_ejs = importlib.util.find_spec("yt_dlp_ejs") is not None

        self._build_ui()
        self.after(100, self._poll_queue)

        if "yt_dlp" in _UPDATE_WHEELS:
            self._log(f"Uygulama içinden indirilen yt-dlp güncellemesi kullanılıyor ({YTDLP_VERSION}).")

        if not self.ffmpeg_ok:
            self._log("UYARI: ffmpeg/ffprobe bulunamadı. MP3 dönüştürme ve MP4 birleştirme çalışmaz.")
        if not self.js_runtime:
            self._log("UYARI: Deno/Node bulunamadı. YouTube'da bazı videolar 'mevcut değil' görünebilir "
                      "ve indirme yavaşlayabilir. README'deki Deno kurulumunu yap.")
        if not self.has_ejs:
            self._log("Bilgi: yt-dlp-ejs paketi yok; çözücü betik internetten çekilecek. "
                      "Önerilen: pip install -U \"yt-dlp[default]\"")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 20}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(18, 2), **pad)
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Müzik İndirici", font=ctk.CTkFont(size=24, weight="bold")) \
            .grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text=f"yt-dlp {YTDLP_VERSION}", text_color=("gray40", "gray60")) \
            .grid(row=0, column=1, padx=(0, 10))
        self.update_btn = ctk.CTkButton(header, text="yt-dlp'yi güncelle", width=140, height=28,
                                        command=self._update_ytdlp)
        self.update_btn.grid(row=0, column=2)
        ctk.CTkLabel(self, text="YouTube / YouTube Music video ve playlist bağlantıları",
                     text_color=("gray40", "gray60")) \
            .grid(row=1, column=0, sticky="w", pady=(0, 10), **pad)

        ctk.CTkLabel(self, text="Bağlantılar (her satıra bir link)") \
            .grid(row=2, column=0, sticky="w", **pad)
        self.url_box = ctk.CTkTextbox(self, height=90)
        self.url_box.grid(row=3, column=0, sticky="ew", pady=(4, 10), **pad)

        opts = ctk.CTkFrame(self)
        opts.grid(row=4, column=0, sticky="ew", pady=(0, 10), **pad)
        opts.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(opts, text="Format").grid(row=0, column=0, padx=(12, 6), pady=(12, 6), sticky="w")
        self.format_var = ctk.StringVar(value="MP3")
        ctk.CTkSegmentedButton(opts, values=["MP3", "MP4"], variable=self.format_var,
                               command=self._on_format_change).grid(row=0, column=1, padx=6, pady=(12, 6), sticky="w")

        ctk.CTkLabel(opts, text="Kalite").grid(row=0, column=2, padx=(18, 6), pady=(12, 6), sticky="w")
        self.quality_var = ctk.StringVar(value=MP3_QUALITIES[0])
        self.quality_menu = ctk.CTkOptionMenu(opts, values=MP3_QUALITIES, variable=self.quality_var, width=120)
        self.quality_menu.grid(row=0, column=3, sticky="w", padx=6, pady=(12, 6))

        self.playlist_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Playlist olarak indir (numaralı klasöre)", variable=self.playlist_var) \
            .grid(row=1, column=0, columnspan=3, sticky="w", padx=12, pady=6)

        self.cover_var = ctk.BooleanVar(value=True)
        self.cover_check = ctk.CTkCheckBox(opts, text="MP3'e kapak resmi göm (kapalıysa daha hızlı)",
                                           variable=self.cover_var)
        self.cover_check.grid(row=1, column=3, sticky="w", padx=6, pady=6)

        ctk.CTkLabel(opts, text="Eşzamanlı indirme").grid(row=2, column=0, padx=(12, 6), pady=(6, 12), sticky="w")
        self.workers_var = ctk.StringVar(value="3")
        ctk.CTkOptionMenu(opts, values=["1", "2", "3", "4"], variable=self.workers_var, width=70) \
            .grid(row=2, column=1, padx=6, pady=(6, 12), sticky="w")

        ctk.CTkLabel(opts, text="Tarayıcı çerezi").grid(row=2, column=2, padx=(18, 6), pady=(6, 12), sticky="w")
        self.browser_var = ctk.StringVar(value="Yok")
        ctk.CTkOptionMenu(opts, values=BROWSERS, variable=self.browser_var, width=170,
                          command=self._on_browser_change) \
            .grid(row=2, column=3, padx=6, pady=(6, 12), sticky="w")

        self.search_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts, text="İnemeyen şarkıları YouTube'da arayıp dene (farklı sürüm çıkabilir)",
                        variable=self.search_var) \
            .grid(row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 6))

        self.skip_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(opts, text="Daha önce indirilenleri atla (geçmiş)", variable=self.skip_var) \
            .grid(row=4, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 6))
        ctk.CTkButton(opts, text="Geçmişi temizle", width=130, height=26, fg_color="gray40",
                      hover_color="gray30", command=self._clear_history) \
            .grid(row=4, column=3, sticky="w", padx=6, pady=(0, 6))

        self.preview_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="İndirmeden önce şarkıları seçeyim (liste göster)",
                        variable=self.preview_var) \
            .grid(row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 12))

        folder_row = ctk.CTkFrame(self, fg_color="transparent")
        folder_row.grid(row=5, column=0, sticky="ew", pady=(0, 10), **pad)
        folder_row.grid_columnconfigure(0, weight=1)
        default_dir = Path.home() / "Music"
        if not default_dir.exists():
            default_dir = Path.home() / "Downloads"
        self.dir_var = ctk.StringVar(value=str(default_dir))
        ctk.CTkEntry(folder_row, textvariable=self.dir_var).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(folder_row, text="Klasör Seç", width=110, command=self._choose_dir) \
            .grid(row=0, column=1, padx=(8, 0))

        self.log_box = ctk.CTkTextbox(self, state="disabled")
        self.log_box.grid(row=6, column=0, sticky="nsew", pady=(0, 10), **pad)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.grid(row=7, column=0, sticky="ew", **pad)
        self.status_label = ctk.CTkLabel(self, text="Hazır", anchor="w")
        self.status_label.grid(row=8, column=0, sticky="ew", pady=(4, 8), **pad)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=9, column=0, sticky="ew", pady=(0, 18), **pad)
        btns.grid_columnconfigure(0, weight=1)
        self.download_btn = ctk.CTkButton(btns, text="İndir", height=40, command=self._start)
        self.download_btn.grid(row=0, column=0, sticky="ew")
        self.cancel_btn = ctk.CTkButton(btns, text="İptal", height=40, width=110, state="disabled",
                                        fg_color="#b23b3b", hover_color="#8f2f2f", command=self._cancel)
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))

    def _on_format_change(self, value):
        values = MP3_QUALITIES if value == "MP3" else MP4_QUALITIES
        self.quality_menu.configure(values=values)
        self.quality_var.set(values[0])
        self.cover_check.configure(state="normal" if value == "MP3" else "disabled")

    def _on_browser_change(self, value):
        if value != COOKIE_FILE_LABEL:
            return
        path = filedialog.askopenfilename(
            title="cookies.txt dosyasını seç",
            filetypes=[("Çerez dosyası", "*.txt"), ("Tüm dosyalar", "*.*")])
        if path:
            self.cookie_file = path
            self._log(f"Çerez dosyası seçildi: {path}")
        else:
            self.browser_var.set("Yok")

    def _choose_dir(self):
        folder = filedialog.askdirectory(initialdir=self.dir_var.get())
        if folder:
            self.dir_var.set(folder)

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text.strip() + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------- kontrol
    def _start(self):
        urls = [u.strip() for u in self.url_box.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning(APP_NAME, "Lütfen en az bir bağlantı gir.")
            return
        outdir = self.dir_var.get().strip()
        if not outdir:
            messagebox.showwarning(APP_NAME, "Lütfen bir indirme klasörü seç.")
            return
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Klasör oluşturulamadı:\n{e}")
            return
        if self.browser_var.get() == "Opera GX" and not os.path.isdir(OPERA_GX_PROFILE):
            messagebox.showwarning(APP_NAME, f"Opera GX profil klasörü bulunamadı:\n{OPERA_GX_PROFILE}")
            return
        if not self.ffmpeg_ok and not messagebox.askyesno(
                APP_NAME, "ffmpeg bulunamadı, indirmeler başarısız olabilir.\nYine de devam edilsin mi?"):
            return

        settings = {
            "urls": urls,
            "outdir": outdir,
            "fmt": self.format_var.get(),
            "quality": self.quality_var.get(),
            "playlist": self.playlist_var.get(),
            "cover": self.cover_var.get(),
            "workers": int(self.workers_var.get()),
            "browser": self.browser_var.get(),
            "cookie_file": self.cookie_file,
            "search": self.search_var.get(),
            "skip_done": self.skip_var.get(),
            "preview": self.preview_var.get(),
        }

        self.outdir = outdir
        self.search_fallback = settings["search"]
        try:
            open(os.path.join(outdir, "hata_kaydi.txt"), "w", encoding="utf-8").close()
        except OSError:
            pass
        self.cancel_event.clear()
        self.partial = {}
        self.done_count = 0
        self.total = 0
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.set(0)
        self.status_label.configure(text="Bağlantılar çözümleniyor...")
        self._log(f"--- {len(urls)} bağlantı için işlem başladı ---")

        self.worker = threading.Thread(target=self._run, args=(settings,), daemon=True)
        self.worker.start()

    def _cancel(self):
        self.cancel_event.set()
        self.status_label.configure(text="İptal ediliyor...")
        self.cancel_btn.configure(state="disabled")

    def _clear_history(self):
        files = list(APP_DATA.glob("gecmis_*.txt"))
        if not files:
            messagebox.showinfo(APP_NAME, "İndirme geçmişi zaten boş.")
            return
        if not messagebox.askyesno(
                APP_NAME, "İndirme geçmişi silinsin mi?\nSilinirse daha önce indirilen şarkılar tekrar indirilebilir."):
            return
        for f in files:
            try:
                f.unlink()
            except OSError:
                pass
        self._log("İndirme geçmişi temizlendi.")

    def _update_ytdlp(self):
        self.update_btn.configure(state="disabled", text="Güncelleniyor...")
        self._log("yt-dlp güncellemesi kontrol ediliyor...")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            changed = fetch_update(lambda m: self.q.put(("log", m)))
            self.q.put(("update_done", changed, ""))
        except Exception as e:  # noqa: BLE001
            self.q.put(("update_done", False, str(e)))

    def _on_update_done(self, changed, error):
        self.update_btn.configure(state="normal", text="yt-dlp'yi güncelle")
        if error:
            self._log(f"Güncelleme başarısız: {error}")
        elif changed:
            self._log("Güncelleme indirildi. Uygulamayı kapatıp yeniden aç.")
            messagebox.showinfo(APP_NAME, "Güncelleme indirildi.\nEtkili olması için uygulamayı kapatıp yeniden aç.")
        else:
            self._log("Her şey güncel.")

    def _on_selected(self, selected):
        self.select_result = selected
        self.select_event.set()

    # ------------------------------------------------------------ yt-dlp
    def _common_opts(self, s):
        opts = {
            "quiet": True,
            "noprogress": True,
            "windowsfilenames": True,
            "retries": 5,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 4,
        }
        if self.ffmpeg_dir:
            opts["ffmpeg_location"] = self.ffmpeg_dir
        if self.js_runtime:
            opts["js_runtimes"] = self.js_runtime
        if not self.has_ejs:
            opts["remote_components"] = ["ejs:github"]
        if s["browser"] == "Opera GX":
            # yt-dlp'nin "opera" desteği GX'in profil klasörüyle çalışır
            opts["cookiesfrombrowser"] = ("opera", OPERA_GX_PROFILE)
        elif s["browser"] == COOKIE_FILE_LABEL:
            if s["cookie_file"]:
                opts["cookiefile"] = s["cookie_file"]
        elif s["browser"] != "Yok":
            opts["cookiesfrombrowser"] = (s["browser"].lower(),)
        return opts

    def _format_opts(self, s):
        fmt, quality = s["fmt"], s["quality"]
        if fmt == "MP3":
            pps = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                 "preferredquality": quality.split()[0]},
                {"key": "FFmpegMetadata", "add_metadata": True},
            ]
            opts = {"format": "bestaudio/best", "postprocessors": pps}
            if s["cover"]:
                opts["writethumbnail"] = True
                pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
                pps.append({"key": "EmbedThumbnail"})
            return opts
        h = None if quality == "En iyi" else quality.rstrip("p")
        hf = f"[height<={h}]" if h else ""
        return {
            # Uyumluluk için önce H.264 + AAC dene
            "format": f"bv*{hf}[vcodec^=avc1]+ba[ext=m4a]/bv*{hf}+ba/b{hf}/b",
            "merge_output_format": "mp4",
        }

    def _make_item(self, s, url, title, folder=None, index=None, width=3, vid=None, artist=None):
        if folder:
            folder_name = sanitize_filename(folder, restricted=False) or "Playlist"
            prefix = f"{index:0{width}d} - " if index else ""
            outtmpl = os.path.join(s["outdir"], esc(folder_name), esc(prefix) + "%(title)s.%(ext)s")
        else:
            prefix = ""
            outtmpl = os.path.join(s["outdir"], "%(title)s.%(ext)s")
        label = f"{prefix}{title}" if title else url
        if not vid:
            m = re.search(r"[?&]v=([\w-]{11})", url)
            vid = m.group(1) if m else None
        name_tmpl = os.path.join(os.path.dirname(outtmpl),
                                 esc(sanitize_filename(f"{prefix}{title}" if title else "indirilen", restricted=False))
                                 + ".%(ext)s")
        return {"url": url, "label": label, "outtmpl": outtmpl, "id": vid, "title": title,
                "artist": artist, "name_tmpl": name_tmpl, "key": f"{len(url)}-{index}-{url}"}

    def _expand(self, s, url):
        """Bir bağlantıyı indirilecek öğelere çevirir."""
        if not s["playlist"] and not is_playlist_url(url):
            return [self._make_item(s, url, None)]
        if not s["playlist"]:
            self.q.put(("log", "Playlist bağlantısı algılandı, playlist modu bu bağlantı için otomatik açıldı."))

        opts = self._common_opts(s)
        opts.update({
            "extract_flat": "in_playlist",
            "noplaylist": False,
            "ignoreerrors": True,
            "logger": FlatLogger(self.q.put),
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001
            self.q.put(("log", f"HATA: {friendly_error(str(e))}"))
            return []
        if not info:
            self.q.put(("log", f"HATA: Bağlantı çözümlenemedi: {url}"))
            return []

        entries = info.get("entries")
        if entries is None:  # tek video
            return [self._make_item(s, info.get("webpage_url") or url, info.get("title"))]

        entries = [e for e in entries if e]
        folder = info.get("title") or "Playlist"
        width = max(3, len(str(len(entries))))
        self.q.put(("log", f"Playlist: {folder} ({len(entries)} öğe)"))
        items = []
        for i, e in enumerate(entries, 1):
            eurl = e.get("url") or e.get("webpage_url")
            if eurl and not eurl.startswith("http") and e.get("id"):
                eurl = f"https://www.youtube.com/watch?v={e['id']}"
            if not eurl and e.get("id"):
                eurl = f"https://www.youtube.com/watch?v={e['id']}"
            if not eurl:
                continue
            artist = (e.get("channel") or e.get("uploader") or "")
            artist = re.sub(r"\s*-\s*Topic$", "", artist).strip() or None
            items.append(self._make_item(s, eurl, e.get("title"), folder, i, width, e.get("id"), artist))
        return items

    def _push_progress(self, text):
        with self.lock:
            total = max(self.total, 1)
            frac = (self.done_count + sum(self.partial.values())) / total
        self.q.put(("progress", min(frac, 1.0), text))

    def _attempts(self, item):
        """Sırayla denenecek yöntemler: (url, ek ayarlar, açıklama)."""
        attempts = [(item["url"], {}, "")]
        vid = item.get("id")
        if vid:
            murl = f"https://music.youtube.com/watch?v={vid}"
            attempts.append((murl, {}, "music.youtube.com"))
            attempts.append((murl, {"extractor_args": {"youtube": {
                "player_client": ["web_music", "tv", "web_safari"]}}}, "farklı istemci"))
        if self.search_fallback and item.get("title"):
            q = f"{item['artist']} - {item['title']}" if item.get("artist") else item["title"]
            attempts.append((f"ytsearch1:{q}", {"outtmpl": item["name_tmpl"]}, "arama"))
        return attempts

    def _write_trace(self, item, trace):
        try:
            path = os.path.join(self.outdir, "hata_kaydi.txt")
            with self.lock, open(path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {item['label']} =====\n")
                for url, note, lines in trace:
                    f.write(f"\n--- yöntem: {note or 'normal'} | {url} ---\n")
                    f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def _download_item(self, item, base_opts):
        key = item["key"]
        if self.cancel_event.is_set():
            return {"item": item, "status": "cancelled"}

        seen = {"title": ""}

        def progress_hook(d):
            if self.cancel_event.is_set():
                raise Cancelled()
            if d["status"] == "downloading":
                info = d.get("info_dict") or {}
                if info.get("title"):
                    seen["title"] = info["title"]
                size = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = (d.get("downloaded_bytes", 0) / size) if size else 0
                with self.lock:
                    self.partial[key] = min(pct, 0.99)
                    done, total, active = self.done_count, self.total, len(self.partial)
                self._push_progress(f"{done}/{total} tamamlandı • {active} aktif • {item['label']}")

        def pp_hook(d):
            if d["status"] == "started":
                with self.lock:
                    self.partial[key] = 0.99
                self._push_progress(f"Dönüştürülüyor: {item['label']}")

        retry_keys = ("unavailable", "sign in", "not a bot", "requested format",
                      "not available", "javascript", "403")
        attempts = self._attempts(item)
        result = {"item": item, "status": "fail", "error": ""}
        first_error = ""
        trace = []

        for n, (url, extra, note) in enumerate(attempts):
            if self.cancel_event.is_set():
                result["status"] = "cancelled"
                break
            logger = ItemLogger()
            opts = dict(base_opts)
            opts.update({
                "outtmpl": item["outtmpl"],
                "logger": logger,
                "verbose": True,
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [pp_hook],
            })
            opts.update(extra)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                if any("already been recorded in the archive" in line for line in logger.lines):
                    result["status"] = "skipped"
                    self.q.put(("log", f"↷ {item['label']}  (daha önce indirilmiş, atlandı)"))
                    break
                result["status"] = "ok"
                if note == "arama":
                    suffix = f"  (arama ile bulundu: {seen['title'] or '?'})"
                elif note:
                    suffix = f"  ({note} ile indirildi)"
                else:
                    suffix = ""
                self.q.put(("log", f"✓ {item['label']}{suffix}"))
                break
            except Cancelled:
                result["status"] = "cancelled"
                break
            except Exception as e:  # noqa: BLE001
                raw = pick_error(logger.errors) or str(e)
                trace.append((url, note, logger.tail()))
                err = friendly_error(raw)
                if n == 0:
                    first_error = err
                can_retry = n < len(attempts) - 1 and any(k in raw.lower() for k in retry_keys)
                if can_retry:
                    continue
                result["error"] = first_error or err
                if n > 0:
                    result["error"] += "  [yedek yöntemler de denendi]"
                self.q.put(("log", f"✗ {item['label']}: {result['error']}"))
                break

        if result["status"] == "fail":
            self._write_trace(item, trace)

        with self.lock:
            self.partial.pop(key, None)
            if result["status"] != "cancelled":
                self.done_count += 1
            done, total = self.done_count, self.total
        self._push_progress(f"{done}/{total} tamamlandı")
        return result

    def _run(self, s):
        items = []
        for url in s["urls"]:
            if self.cancel_event.is_set():
                break
            items += self._expand(s, url)

        if not items:
            self.q.put(("done", [], self.cancel_event.is_set()))
            return

        if s["preview"] and len(items) > 1 and not self.cancel_event.is_set():
            self.select_result = []
            self.select_event.clear()
            self.q.put(("progress", 0, "Şarkı seçimi bekleniyor..."))
            self.q.put(("select", items))
            self.select_event.wait()
            items = self.select_result
            if not items:
                self.q.put(("log", "Hiç şarkı seçilmedi, işlem iptal edildi."))
                self.q.put(("done", [], True))
                return

        with self.lock:
            self.total = len(items)

        self.q.put(("log", f"Toplam {len(items)} öğe indirilecek ({s['workers']} eşzamanlı)."))

        base_opts = self._common_opts(s)
        base_opts.update(self._format_opts(s))
        base_opts["noplaylist"] = True  # her öğe tek bir video
        if s["skip_done"]:
            try:
                APP_DATA.mkdir(parents=True, exist_ok=True)
                base_opts["download_archive"] = str(APP_DATA / f"gecmis_{s['fmt'].lower()}.txt")
            except OSError:
                pass

        results = []
        try:
            with ThreadPoolExecutor(max_workers=s["workers"]) as ex:
                futures = [ex.submit(self._download_item, it, base_opts) for it in items]
                for f in futures:
                    results.append(f.result())
        except Exception as e:  # noqa: BLE001
            self.q.put(("log", f"HATA: {e}"))
        self.q.put(("done", results, self.cancel_event.is_set()))

    # ------------------------------------------------------ UI <- thread
    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress.set(msg[1])
                    self.status_label.configure(text=msg[2])
                elif kind == "log":
                    self._log(msg[1])
                elif kind == "done":
                    self._finish(msg[1], msg[2])
                elif kind == "select":
                    SelectDialog(self, msg[1], self._on_selected)
                elif kind == "update_done":
                    self._on_update_done(msg[1], msg[2])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _finish(self, results, cancelled):
        self.download_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        ok = sum(1 for r in results if r["status"] == "ok")
        skipped = sum(1 for r in results if r["status"] == "skipped")
        failed = [r for r in results if r["status"] == "fail"]
        if cancelled:
            text = f"İptal edildi. {ok} öğe indirildi."
        elif not results:
            text = "İndirme başlatılamadı. Log'daki hatalara bak."
        else:
            extra = f", {skipped} atlandı" if skipped else ""
            text = f"Bitti: {ok} başarılı{extra}, {len(failed)} hatalı."
            self.progress.set(1 if not failed else self.progress.get())
        self.status_label.configure(text=text)
        self._log(f"--- {text} ---")
        if failed:
            self._log("İndirilemeyenler:")
            for r in failed:
                self._log(f"  • {r['item']['label']} → {r['error']}")
                if r["item"].get("id"):
                    self._log(f"      https://music.youtube.com/watch?v={r['item']['id']}")
            self._log(f"Ayrıntılı teknik kayıt: {os.path.join(self.outdir, 'hata_kaydi.txt')}")
            if any("unavailable" in r["error"].lower() for r in failed):
                self._log("İpucu: Bazı parçalar yaş kısıtlı veya giriş gerektirebilir. 'Tarayıcı çerezi' kutusundan "
                          "YouTube Music'e giriş yaptığın tarayıcıyı seçip tekrar dene. Hepsi hata veriyorsa "
                          "Deno'yu ve yt-dlp güncelliğini kontrol et.")


if __name__ == "__main__":
    App().mainloop()