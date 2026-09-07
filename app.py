import os, json, secrets, subprocess
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = os.getenv("PANEL_SECRET", secrets.token_hex(32))

DATA = Path("data")
BOTS = Path("bots")
DATA.mkdir(exist_ok=True)
BOTS.mkdir(exist_ok=True)
DB_FILE = DATA / "bots.json"

DEFAULT_LIMIT = int(os.getenv("DEFAULT_BOT_LIMIT", "3"))

def load_bots():
    if not DB_FILE.exists():
        return []
    try:
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_bots(bots):
    DB_FILE.write_text(json.dumps(bots, indent=2, ensure_ascii=False), encoding="utf-8")

def docker_available():
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5, check=True)
        return True
    except Exception:
        return False

def container_name(bot_id):
    return f"discordhost_{bot_id}"

def status(bot):
    if not docker_available():
        return "OFFLINE"
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name(bot["id"])],
            capture_output=True, text=True, timeout=5
        )
        return "ONLINE" if r.stdout.strip() == "true" else "OFFLINE"
    except Exception:
        return "OFFLINE"

def run_docker(args):
    if not docker_available():
        return False, "Docker não está disponível neste servidor."
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
        if r.returncode:
            return False, r.stderr.strip() or "Erro do Docker."
        return True, r.stdout.strip()
    except Exception as e:
        return False, str(e)

@app.route("/")
def index():
    bots = load_bots()
    for b in bots:
        b["status"] = status(b)
    return render_template("index.html", bots=bots, limit=DEFAULT_LIMIT)

@app.post("/bots/create")
def create_bot():
    bots = load_bots()
    if len(bots) >= DEFAULT_LIMIT:
        flash(f"Limite atingido: {DEFAULT_LIMIT} bots.", "error")
        return redirect(url_for("index"))

    name = request.form.get("name", "").strip()
    bot_id = request.form.get("bot_id", "").strip()
    if not name or not bot_id:
        flash("Informe o nome e o ID do bot.", "error")
        return redirect(url_for("index"))

    safe_id = "".join(c for c in bot_id if c.isalnum() or c in "-_")
    if not safe_id:
        flash("ID inválido.", "error")
        return redirect(url_for("index"))

    if any(b["id"] == safe_id for b in bots):
        flash("Esse bot já está cadastrado.", "error")
        return redirect(url_for("index"))

    bot_dir = BOTS / safe_id
    bot_dir.mkdir(exist_ok=True)

    # O token NÃO é armazenado pelo painel. O cliente deve configurá-lo
    # como segredo/variável de ambiente na infraestrutura.
    bots.append({"id": safe_id, "name": name})
    save_bots(bots)
    flash("Bot cadastrado. Coloque os arquivos do bot na pasta criada e configure o segredo antes de iniciar.", "ok")
    return redirect(url_for("index"))

@app.post("/bots/<bot_id>/action")
def action(bot_id):
    action_name = request.form.get("action")
    bots = load_bots()
    bot = next((b for b in bots if b["id"] == bot_id), None)
    if not bot:
        flash("Bot não encontrado.", "error")
        return redirect(url_for("index"))

    cname = container_name(bot_id)

    if action_name == "start":
        ok, msg = run_docker([
            "run", "-d", "--name", cname,
            "--restart", "unless-stopped",
            "--memory", "512m",
            "--cpus", "0.50",
            "-w", "/app",
            "-v", f"{(BOTS / bot_id).resolve()}:/app",
            "python:3.12-slim",
            "sh", "-c",
            "pip install -q -r requirements.txt 2>/dev/null || true; python bot.py"
        ])
        flash("Bot iniciado." if ok else msg, "ok" if ok else "error")

    elif action_name == "stop":
        ok, msg = run_docker(["stop", cname])
        flash("Bot parado." if ok else msg, "ok" if ok else "error")

    elif action_name == "restart":
        ok, msg = run_docker(["restart", cname])
        flash("Bot reiniciado." if ok else msg, "ok" if ok else "error")

    elif action_name == "remove":
        run_docker(["rm", "-f", cname])
        bot_dir = BOTS / bot_id
        for p in bot_dir.iterdir():
            if p.is_file():
                p.unlink()
        bots = [b for b in bots if b["id"] != bot_id]
        save_bots(bots)
        flash("Bot removido.", "ok")

    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
