"""The lab file policy, in one place. Imported by scan.py and convert.py."""
import os

MB = 1_000_000

EXPERIMENTAL = {"fastq", "fq", "sam", "bam", "cram", "fast5", "pod5",
                "czi", "lif", "nd2", "ims", "fcs", "mzml", "mzxml"}
IMAGE_VIEW = {"jpg", "jpeg", "png", "webp", "gif"}
IMAGE_SOURCE = {"tif", "tiff", "bmp", "psd", "heic", "eps", "ai"}
PDF = {"pdf"}
WORD = {"docx", "doc", "rtf", "odt"}
EXCEL = {"xlsx", "xlsm", "xls", "ods"}
POWERPOINT = {"pptx", "ppt", "odp"}
ARCHIVE = {"zip", "7z", "rar", "tar", "tgz", "tbz", "txz", "gz", "bz2", "xz", "zst"}
VIDEO_AUDIO = {"mp4", "mov", "avi", "mkv", "webm", "m4v", "wmv", "mp3", "wav", "flac", "m4a"}
DATA_BINARY = {"npy", "npz", "pkl", "pickle", "h5", "hdf5", "mat", "parquet", "feather",
               "db", "sqlite", "sqlite3", "rds", "rdata", "pt", "pth", "ckpt", "safetensors",
               "joblib", "arrow", "bcolz", "zarr"}
EXECUTABLE = {"exe", "dll", "so", "dylib", "bin", "o", "a", "class", "jar", "wasm", "msi",
              "dmg", "pkg", "deb", "rpm", "app"}
TEXT = {"md", "markdown", "txt", "rst", "tex", "bib", "cls", "sty",
        "py", "r", "rmd", "ipynb", "jl", "m", "sh", "bash", "zsh", "fish", "ps1", "bat",
        "c", "h", "cpp", "cc", "hpp", "cxx", "java", "kt", "go", "rs", "swift", "pl", "php",
        "rb", "lua", "js", "jsx", "ts", "tsx", "vue", "css", "scss", "html", "htm", "xml",
        "svg", "json", "jsonl", "ndjson", "yaml", "yml", "toml", "ini", "cfg", "conf",
        "properties", "env_example", "sql", "graphql", "proto", "snakefile", "nf", "cwl",
        "dockerfile", "makefile", "cmake", "gradle", "gitignore", "gitattributes",
        "log", "diff", "patch", "sbatch", "slurm", "smk", "wdl", "do", "sas", "f90", "f",
        "nb", "gp", "gnuplot", "csl", "lock", "editorconfig"}

TEXT_NAMES = {"makefile", "dockerfile", "snakefile", "license", "licence", "readme",
              "changelog", "authors", "notice", "codeowners", "requirements", "pipfile"}

# extensions that are conversion sources or migration candidates -> worth hashing
# Excel is deliberately excluded here: it is never migrated, only ever excluded (see classify()).
CONVERTIBLE = IMAGE_SOURCE | IMAGE_VIEW | PDF | WORD

REFUSED_BASENAMES = {"desktop", "downloads", "documents", "pictures", "movies", "music",
                     "library", "applications", "dropbox", "onedrive", "icloud drive",
                     "google drive", "mobile documents", "public"}


# Written once at setup, then left alone. Type-level only: the skill never adds per-file
# entries, because it only ever uploads paths the user approved in the review.
IGNORE_TEMPLATE = """# Lab file policy: these never go to GitHub.
# Written once by /synctogit. Add your own rules below; they are never touched.
*.fastq
*.fq
*.fastq.gz
*.fq.gz
*.sam
*.bam
*.cram
*.fast5
*.pod5
*.czi
*.lif
*.nd2
*.ims
*.fcs
*.mzML
*.mzXML
*.pptx
*.ppt
*.xlsx
*.xlsm
*.xls
*.ods
*.tif
*.tiff
*.zip
*.npy
*.npz
*.h5
*.hdf5
*.mat
_archive/
"""


def ext_of(path):
    """Normalized extension, aware of double extensions like .fastq.gz."""
    low = os.path.basename(path).lower()
    parts = low.split(".")
    if len(parts) >= 3 and parts[-1] in ("gz", "bz2", "xz", "zst") and parts[-2] in EXPERIMENTAL:
        return parts[-2]          # .fastq.gz stays an experimental read
    if len(parts) >= 2:
        return parts[-1]
    return ""


def refused_root(path):
    """True when this folder must never be treated as a project root."""
    p = os.path.abspath(os.path.expanduser(path)).rstrip(os.sep)
    home = os.path.abspath(os.path.expanduser("~")).rstrip(os.sep)
    if p in ("", os.sep) or p == home:
        return True
    if os.path.dirname(p) == home and os.path.basename(p).lower() in REFUSED_BASENAMES:
        return True
    low = p.lower()
    for marker in ("/library/mobile documents", "/dropbox", "/onedrive", "/google drive"):
        if low.endswith(marker):
            return True
    return False


def output_name(src, kind):
    """Stable output name derived from the source name."""
    base, _ = os.path.splitext(src)
    return {"image": base + ".preview.jpg",
            "pdf": base + ".git.pdf",
            "word": base + ".md"}[kind]


def classify(relpath, size, is_symlink_outside=False, unreadable=False, placeholder=False):
    """Return (category, decision, reason, conversion_kind).

    decision is one of: upload, convert, migrate, exclude
    """
    if is_symlink_outside:
        return "symlink", "exclude", "symlink pointing outside the project", None
    if placeholder:
        return "cloud", "exclude", "cloud file not present locally", None
    if unreadable:
        return "unreadable", "exclude", "file could not be read", None

    e = ext_of(relpath)
    name = os.path.basename(relpath).lower()

    if e in EXPERIMENTAL:
        return "experimental", "exclude", "raw experimental format", None
    if e in POWERPOINT:
        return "powerpoint", "exclude", "PowerPoint: managed locally only", None
    if e in ARCHIVE:
        return "archive", "exclude", "unsupported binary format (archive)", None
    if e in VIDEO_AUDIO:
        return "media", "exclude", "unsupported binary format (video/audio)", None
    if e in DATA_BINARY:
        return "data-binary", "exclude", "unsupported binary format", None
    if e in EXECUTABLE:
        return "executable", "exclude", "unsupported binary format", None

    if e == "csv":
        if size > MB:
            return "csv", "exclude", "CSV exceeds 1 MB", None
        return "csv", "upload", None, None

    if e in EXCEL:
        return "excel", "exclude", "Excel: managed locally only", None
    if e in WORD:
        return "word", "migrate", "document excluded; offer one-time Markdown migration", "word"
    if e in PDF:
        return "pdf", "convert", "compress automatically", "pdf"

    if e in IMAGE_SOURCE:
        return "image-source", "convert", "heavy image source; needs a viewing copy", "image"
    if e in IMAGE_VIEW:
        if size > MB:
            return "image", "convert", "image above 1 MB; needs compression", "image"
        return "image", "upload", None, None

    if e in TEXT or name in TEXT_NAMES or name.split(".")[0] in TEXT_NAMES:
        return "text", "upload", None, None

    return "unknown", None, None, None       # caller sniffs for binary content
