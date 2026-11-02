from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW_FOMO = {ROOT / "desk" / "links.py", ROOT / "tests" / "test_links.py"}
SECRET_KEYS = (
    "TYPESAFE_API_KEY",
    "TG_TOKEN",
    "VAPID_PRIVATE_KEY",
    "APP_TOKEN",
    "DATA_PATH",
    "RPC_URL",
)


def _files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__", ".venv", "venv", "logs", ".pytest_cache"} for part in path.parts):
            continue
        # Published Plan B snapshots are generated output. They carry the
        # signup link the writer already built. Source files still cannot.
        parts = path.parts
        if "docs" in parts and "d" in parts:
            continue
        if path.suffix in {".png", ".sqlite"}:
            continue
        yield path


def test_fomo_urls_live_only_in_links():
    host = "fomo" + ".family"
    code = "savi" + "pww"
    for path in _files():
        if path.resolve() in ALLOW_FOMO:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert host not in text, path
        assert code not in text, path


def test_no_order_path_and_no_password_prompt():
    banned = ("sendTransaction", "keypair", "password", "pumpportal.fun/api/trade")
    for path in _files():
        if "desk" not in path.parts and "web" not in path.parts and "docs" not in path.parts:
            continue
        if path.suffix not in {".py", ".js", ".html", ".css"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for word in banned:
            assert word.lower() not in text, f"{word} in {path}"


def test_docs_matches_web():
    for rel in (
        "index.html",
        "404.html",
        "manifest.json",
        "css/app.css",
        "js/app.js",
        "js/chart.js",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
    ):
        web = (ROOT / "web" / rel).read_bytes()
        docs = (ROOT / "docs" / rel).read_bytes()
        assert web == docs, rel


def test_env_example_is_empty_and_dotenv_is_absent():
    assert not (ROOT / ".env").exists()
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in SECRET_KEYS:
        assert f"{key}=" in example
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key in SECRET_KEYS:
            assert value == ""
