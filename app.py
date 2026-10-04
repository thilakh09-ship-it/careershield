"""CareerShield Connect - MVP: verified student/alumni network with skill matching.
Run: pip install -r requirements.txt && python app.py  ->  http://127.0.0.1:5000
"""
import os, re, sqlite3, sys
from flask import Flask, g, request, session, redirect, url_for, render_template, flash
from jinja2 import DictLoader
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
DB = os.path.join(os.path.dirname(__file__), "careershield.db")
EDU = re.compile(r"@([\w.-]+\.(edu|ac\.[a-z]{2}|edu\.[a-z]{2}))$", re.I)  # college email check

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, pw TEXT,
  role TEXT, college TEXT, skills TEXT DEFAULT '', interests TEXT DEFAULT '', goals TEXT DEFAULT '',
  bio TEXT DEFAULT '', grad_year INTEGER, open_inter INTEGER DEFAULT 0, verified INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS connections(id INTEGER PRIMARY KEY, a INTEGER, b INTEGER,
  status TEXT DEFAULT 'pending', UNIQUE(a, b));
CREATE TABLE IF NOT EXISTS mentor_requests(id INTEGER PRIMARY KEY, student INTEGER, alumni INTEGER,
  message TEXT, status TEXT DEFAULT 'pending');
"""

ROLE_SKILLS = {  # extend, or replace with your skill-gap module
    "data analyst": {"sql", "excel", "python", "statistics", "tableau"},
    "web developer": {"html", "css", "javascript", "react", "git"},
    "ml engineer": {"python", "pytorch", "statistics", "sql", "docker"},
    "ui designer": {"figma", "typography", "prototyping", "user research"},
}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

def init_db():
    with sqlite3.connect(DB) as c: c.executescript(SCHEMA)

def me():
    return db().execute("SELECT * FROM users WHERE id=?", (session.get("uid"),)).fetchone() if "uid" in session else None

def login_required(f):
    from functools import wraps
    @wraps(f)
    def w(*a, **k):
        if not me(): return redirect(url_for("login"))
        return f(*a, **k)
    return w

def tags(s): return {t.strip().lower() for t in (s or "").split(",") if t.strip()}
def jac(a, b): return len(a & b) / len(a | b) if a | b else 0.0

def match_score(u, v):
    """Shared interests + shared skills + complementary skills (what they have that I lack)."""
    si, sk = jac(tags(u["interests"]), tags(v["interests"])), jac(tags(u["skills"]), tags(v["skills"]))
    vs, us = tags(v["skills"]), tags(u["skills"])
    comp = len(vs - us) / len(vs) if vs else 0
    return round(100 * (0.45 * si + 0.25 * sk + 0.30 * comp))

@app.context_processor
def inject(): return {"user": me()}

# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form; email = f["email"].strip().lower()
        m = EDU.search(email)
        if not m: flash("Use your college email (.edu / .ac.xx)."); return redirect(url_for("register"))
        role = f["role"] if f["role"] in ("student", "alumni") else "student"
        try:
            db().execute("INSERT INTO users(email,name,pw,role,college,grad_year,verified) VALUES(?,?,?,?,?,?,?)",
                (email, f["name"].strip(), generate_password_hash(f["password"]), role, m.group(1),
                 int(f.get("grad_year") or 0), 0 if role == "alumni" else 1))
            db().commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered."); return redirect(url_for("register"))
        flash("Account created. Sign in." + (" Alumni accounts need verification first." if role == "alumni" else ""))
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE email=?", (request.form["email"].strip().lower(),)).fetchone()
        if u and check_password_hash(u["pw"], request.form["password"]):
            session["uid"] = u["id"]; return redirect(url_for("discover"))
        flash("Wrong email or password.")
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/")
def home(): return redirect(url_for("discover"))

# ---------- profile ----------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        f = request.form
        db().execute("UPDATE users SET skills=?,interests=?,goals=?,bio=?,open_inter=? WHERE id=?",
            (f["skills"], f["interests"], f["goals"], f["bio"], 1 if f.get("open_inter") else 0, me()["id"]))
        db().commit(); flash("Profile saved."); return redirect(url_for("discover"))
    return render_template("profile.html")

# ---------- discover (matching) ----------
@app.route("/discover")
@login_required
def discover():
    u = me(); scope = request.args.get("scope", "campus")
    q = "SELECT * FROM users WHERE id!=? AND role='student'"; args = [u["id"]]
    if scope == "campus": q += " AND college=?"; args.append(u["college"])
    else: q += " AND open_inter=1"   # inter-college is opt-in only
    people = [dict(r, score=match_score(u, r)) for r in db().execute(q, args)]
    people.sort(key=lambda p: -p["score"])
    return render_template("discover.html", people=people, scope=scope)

@app.route("/connect/<int:uid>", methods=["POST"])
@login_required
def connect(uid):
    try:
        db().execute("INSERT INTO connections(a,b) VALUES(?,?)", (me()["id"], uid)); db().commit()
        flash("Request sent.")
    except sqlite3.IntegrityError: flash("Already requested.")
    return redirect(request.referrer or url_for("discover"))

# ---------- alumni mentorship ----------
@app.route("/alumni")
@login_required
def alumni():
    u = me()
    rows = db().execute("SELECT * FROM users WHERE role='alumni' AND verified=1 AND college=?", (u["college"],)).fetchall()
    return render_template("alumni.html", people=sorted((dict(r, score=match_score(u, r)) for r in rows), key=lambda p: -p["score"]))

@app.route("/mentor/<int:uid>", methods=["POST"])
@login_required
def mentor(uid):
    msg = request.form["message"].strip()[:300]   # short, structured asks protect alumni time
    db().execute("INSERT INTO mentor_requests(student,alumni,message) VALUES(?,?,?)", (me()["id"], uid, msg))
    db().commit(); flash("Mentorship request sent."); return redirect(url_for("alumni"))

# ---------- inbox ----------
@app.route("/inbox")
@login_required
def inbox():
    u = me()
    conns = db().execute("SELECT c.id,u.name,u.email FROM connections c JOIN users u ON u.id=c.a WHERE c.b=? AND c.status='pending'", (u["id"],)).fetchall()
    ments = db().execute("SELECT m.id,u.name,m.message FROM mentor_requests m JOIN users u ON u.id=m.student WHERE m.alumni=? AND m.status='pending'", (u["id"],)).fetchall()
    mine = db().execute("SELECT u.name,u.email FROM connections c JOIN users u ON u.id=(CASE WHEN c.a=? THEN c.b ELSE c.a END) WHERE (c.a=? OR c.b=?) AND c.status='accepted'", (u["id"],) * 3).fetchall()
    return render_template("inbox.html", conns=conns, ments=ments, mine=mine)

@app.route("/respond/<kind>/<int:rid>/<action>", methods=["POST"])
@login_required
def respond(kind, rid, action):
    table = {"conn": "connections", "mentor": "mentor_requests"}.get(kind)
    status = {"accept": "accepted", "decline": "declined"}.get(action)
    if table and status:
        owner = "b" if kind == "conn" else "alumni"
        db().execute(f"UPDATE {table} SET status=? WHERE id=? AND {owner}=?", (status, rid, me()["id"])); db().commit()
    return redirect(url_for("inbox"))

# ---------- career layer: skill gap (plug your AI modules in here) ----------
@app.route("/skillgap", methods=["GET", "POST"])
@login_required
def skillgap():
    result = None
    if request.method == "POST":
        role = request.form["role"]; need = ROLE_SKILLS[role]; have = tags(me()["skills"])
        helpers = [dict(r) for r in db().execute("SELECT name,skills FROM users WHERE id!=? AND college=? AND role='student'", (me()["id"], me()["college"]))
                   if tags(r["skills"]) & (need - have)]
        result = dict(role=role, have=sorted(need & have), missing=sorted(need - have), helpers=helpers[:5])
    return render_template("skillgap.html", roles=ROLE_SKILLS, result=result)

# ---------- templates ----------
T = {}
T["base.html"] = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>CareerShield Connect</title><style>
:root{--ink:#13233a;--bg:#f2f5f8;--card:#fff;--acc:#1f5eff;--mute:#5d6b7e;--line:#d8e0ea}
@media(prefers-color-scheme:dark){:root{--ink:#e8eef6;--bg:#0f1824;--card:#172334;--mute:#9aa9bd;--line:#273650}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 Georgia,serif;background:var(--bg);color:var(--ink)}
nav{display:flex;gap:1rem;flex-wrap:wrap;padding:.9rem 1.2rem;border-bottom:1px solid var(--line);font-family:system-ui,sans-serif}
nav a{color:var(--ink);text-decoration:none}nav b{margin-right:auto}main{max-width:720px;margin:0 auto;padding:1.2rem}
h1{font-size:1.7rem;margin:.2rem 0 1rem}.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:1rem;margin:.8rem 0}
input,textarea,select{width:100%;padding:.6rem;margin:.3rem 0 .9rem;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--ink);font:inherit}
button{background:var(--acc);color:#fff;border:0;padding:.55rem 1rem;border-radius:4px;font:inherit;cursor:pointer}
button.alt{background:none;color:var(--ink);border:1px solid var(--line)}.mute{color:var(--mute);font-size:.9rem}
.score{float:right;font:700 1.3rem system-ui;color:var(--acc)}.flash{background:var(--acc);color:#fff;padding:.6rem 1rem;border-radius:4px}
a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid var(--acc);outline-offset:2px}</style></head><body>
<nav><b>CareerShield Connect</b>{% if user %}<a href=/discover>Discover</a><a href=/alumni>Alumni</a><a href=/skillgap>Skill gap</a>
<a href=/inbox>Inbox</a><a href=/profile>Profile</a><a href=/logout>Sign out</a>{% endif %}</nav><main>
{% for m in get_flashed_messages() %}<p class=flash>{{m}}</p>{% endfor %}{% block body %}{% endblock %}</main></body></html>"""
T["register.html"] = """{% extends 'base.html' %}{% block body %}<h1>Create your account</h1><form method=post>
<label>Name<input name=name required></label><label>College email<input name=email type=email required></label>
<label>Password<input name=password type=password minlength=8 required></label>
<label>I am a<select name=role><option value=student>Student</option><option value=alumni>Alumnus / alumna</option></select></label>
<label>Graduation year<input name=grad_year type=number></label><button>Create account</button></form>
<p class=mute>Already registered? <a href=/login>Sign in</a></p>{% endblock %}"""
T["login.html"] = """{% extends 'base.html' %}{% block body %}<h1>Sign in</h1><form method=post>
<label>College email<input name=email type=email required></label><label>Password<input name=password type=password required></label>
<button>Sign in</button></form><p class=mute>New here? <a href=/register>Create an account</a></p>{% endblock %}"""
T["profile.html"] = """{% extends 'base.html' %}{% block body %}<h1>Your profile</h1><form method=post>
<label>Skills (comma separated)<input name=skills value="{{user.skills}}" placeholder="python, figma, sql"></label>
<label>Interests<input name=interests value="{{user.interests}}" placeholder="startups, robotics, hackathons"></label>
<label>Goals<input name=goals value="{{user.goals}}"></label><label>Bio<textarea name=bio rows=3>{{user.bio}}</textarea></label>
<label><input type=checkbox name=open_inter style="width:auto" {{'checked' if user.open_inter}}> Visible to students at other colleges</label>
<button>Save profile</button></form>{% endblock %}"""
T["discover.html"] = """{% extends 'base.html' %}{% block body %}<h1>People to team up with</h1>
<p><a href="?scope=campus">My campus</a> &nbsp; <a href="?scope=all">Other colleges (opt-in members)</a></p>
{% for p in people %}<div class=card><span class=score>{{p.score}}%</span><b>{{p.name}}</b>
<div class=mute>{{p.college}}</div><p>Skills: {{p.skills or '-'}}<br>Interests: {{p.interests or '-'}}</p>
<form method=post action="/connect/{{p.id}}"><button>Connect</button></form></div>
{% else %}<p class=mute>No matches yet. Add skills and interests in your profile, or invite a friend.</p>{% endfor %}{% endblock %}"""
T["alumni.html"] = """{% extends 'base.html' %}{% block body %}<h1>Alumni mentors</h1>
{% for p in people %}<div class=card><span class=score>{{p.score}}%</span><b>{{p.name}}</b> <span class=mute>Class of {{p.grad_year}}</span>
<p>{{p.bio or p.skills}}</p><form method=post action="/mentor/{{p.id}}"><textarea name=message maxlength=300 rows=2 required
placeholder="What do you want guidance on? (20-minute chat)"></textarea><button>Request a session</button></form></div>
{% else %}<p class=mute>No verified alumni at your college yet.</p>{% endfor %}{% endblock %}"""
T["inbox.html"] = """{% extends 'base.html' %}{% block body %}<h1>Inbox</h1>
{% for c in conns %}<div class=card>{{c.name}} wants to connect.
<form method=post action="/respond/conn/{{c.id}}/accept"><button>Accept</button></form>
<form method=post action="/respond/conn/{{c.id}}/decline"><button class=alt>Decline</button></form></div>{% endfor %}
{% for m in ments %}<div class=card><b>{{m.name}}</b> asks: {{m.message}}
<form method=post action="/respond/mentor/{{m.id}}/accept"><button>Accept</button></form>
<form method=post action="/respond/mentor/{{m.id}}/decline"><button class=alt>Decline</button></form></div>{% endfor %}
<h2>Your connections</h2>{% for p in mine %}<div class=card>{{p.name}} <span class=mute>{{p.email}}</span></div>
{% else %}<p class=mute>No connections yet. Find people in Discover.</p>{% endfor %}{% endblock %}"""
T["skillgap.html"] = """{% extends 'base.html' %}{% block body %}<h1>Skill gap</h1><form method=post>
<label>Target role<select name=role>{% for r in roles %}<option>{{r}}</option>{% endfor %}</select></label><button>Check gap</button></form>
{% if result %}<div class=card><p>You have: {{result.have|join(', ') or 'none yet'}}</p><p>Missing: {{result.missing|join(', ') or 'nothing'}}</p>
<b>Classmates who can help</b>{% for h in result.helpers %}<p>{{h.name}} <span class=mute>({{h.skills}})</span></p>{% else %}<p class=mute>No one yet.</p>{% endfor %}</div>{% endif %}{% endblock %}"""
app.jinja_loader = DictLoader(T)

if __name__ == "__main__":
    init_db()
    if len(sys.argv) == 3 and sys.argv[1] == "verify":   # python app.py verify alum@college.edu
        with sqlite3.connect(DB) as c: c.execute("UPDATE users SET verified=1 WHERE email=?", (sys.argv[2],))
        print("verified", sys.argv[2])
    else:
        app.run(debug=True)
