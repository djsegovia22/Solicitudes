import json, os, hashlib, requests
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambiar-en-produccion-abc123")

WA_PHONE  = os.environ.get("WA_PHONE",  "")
WA_APIKEY = os.environ.get("WA_APIKEY", "")

USERS_FILE = "users.json"
SOLS_FILE  = "solicitudes.json"
WA_FILE    = "wa_config.json"

# ════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════
def hp(p): return hashlib.sha256(p.encode()).hexdigest()

def load(f, d):
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else d

def save(f, data):
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)

def get_users(): return load(USERS_FILE, [])
def get_sols():  return load(SOLS_FILE,  [])
def get_wa():    return load(WA_FILE, {"phone": WA_PHONE, "apikey": WA_APIKEY})

def init_users():
    if not os.path.exists(USERS_FILE):
        save(USERS_FILE, [
            {"id":1, "username":"superadmin", "password":hp("super123"), "rol":"superadmin"},
            {"id":2, "username":"admin1",      "password":hp("admin123"), "rol":"admin"},
            {"id":3, "username":"rocio",       "password":hp("rocio123"), "rol":"superadmin"},
            {"id":4, "username":"javier",      "password":hp("javier123"), "rol":"admin"},
        ])
        print("[INIT] Usuarios creados:")
        print("  rocio / rocio123")
        print("  javier / javier123")

def login_req(f):
    @wraps(f)
    def w(*a,**k):
        if "user" not in session: return jsonify({"error":"No autenticado"}), 401
        return f(*a,**k)
    return w

def role(*roles):
    def d(f):
        @wraps(f)
        def w(*a,**k):
            if "user" not in session: return jsonify({"error":"No autenticado"}), 401
            if session["user"]["rol"] not in roles: return jsonify({"error":"Sin permiso"}), 403
            return f(*a,**k)
        return w
    return d

# ════════════════════════════════════════════════════════════
#  FRONTEND
# ════════════════════════════════════════════════════════════
@app.route("/")
def index():
    html_path = os.path.join(os.path.dirname(__file__), "solicitudes.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()

# ════════════════════════════════════════════════════════════
#  AUTH
# ════════════════════════════════════════════════════════════
@app.route("/api/login", methods=["POST"])
def login():
    d = request.json
    u = next((x for x in get_users()
              if x["username"] == d.get("username","").strip()
              and x["password"] == hp(d.get("password",""))), None)
    if not u: return jsonify({"ok":False,"error":"Usuario o contraseña incorrectos"}), 401
    safe = {k:u[k] for k in ("id","username","rol")}
    session["user"] = safe
    return jsonify({"ok":True,"user":safe})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok":True})

@app.route("/api/me")
def me():
    return jsonify({"user": session.get("user")})

# ════════════════════════════════════════════════════════════
#  SOLICITUDES (público — sin login)
# ════════════════════════════════════════════════════════════
@app.route("/api/solicitudes", methods=["POST"])
def crear_solicitud():
    d    = request.json
    sols = get_sols()
    sol  = {
        "id":          int(datetime.now().timestamp() * 1000),
        "nombre":      d.get("nombre","").strip(),
        "tipo":        d.get("tipo","").strip(),
        "descripcion": d.get("descripcion","").strip(),
        "prioridad":   d.get("prioridad","Normal"),
        "solicitante": d.get("nombre","Anónimo").strip(),
        "asignadoA":   None,
        "estado":      "Pendiente",
        "fecha":       datetime.now().strftime("%d/%m/%y %H:%M"),
    }
    sols.insert(0, sol)
    save(SOLS_FILE, sols)
    wa_ok = enviar_whatsapp(sol)
    return jsonify({"ok":True, "solicitud":sol, "wa_enviado":wa_ok})

@app.route("/api/solicitudes", methods=["GET"])
@login_req
def listar_solicitudes():
    u    = session["user"]
    sols = get_sols()
    if u["rol"] == "admin":
        sols = [s for s in sols if s.get("asignadoA") == u["username"]]
    return jsonify(sols)

@app.route("/api/solicitudes/<int:sid>/estado", methods=["PATCH"])
@login_req
def cambiar_estado(sid):
    u    = session["user"]
    d    = request.json
    sols = get_sols()
    s    = next((x for x in sols if x["id"] == sid), None)
    if not s: return jsonify({"error":"No encontrada"}), 404
    if u["rol"] == "admin" and s.get("asignadoA") != u["username"]:
        return jsonify({"error":"Sin permiso"}), 403
    s["estado"] = d.get("estado", s["estado"])
    save(SOLS_FILE, sols)
    return jsonify({"ok":True})

@app.route("/api/solicitudes/<int:sid>/asignar", methods=["PATCH"])
@role("superadmin")
def asignar(sid):
    d    = request.json
    sols = get_sols()
    s    = next((x for x in sols if x["id"] == sid), None)
    if not s: return jsonify({"error":"No encontrada"}), 404
    s["asignadoA"] = d.get("asignadoA") or None
    save(SOLS_FILE, sols)
    return jsonify({"ok":True})

# ════════════════════════════════════════════════════════════
#  USUARIOS (solo superadmin)
# ════════════════════════════════════════════════════════════
@app.route("/api/usuarios", methods=["GET"])
@role("superadmin")
def listar_usuarios():
    return jsonify([{k:u[k] for k in ("id","username","rol")} for u in get_users()])

@app.route("/api/usuarios", methods=["POST"])
@role("superadmin")
def crear_usuario():
    d        = request.json
    username = d.get("username","").strip()
    password = d.get("password","").strip()
    rol      = d.get("rol","admin")
    if not username or not password:
        return jsonify({"ok":False,"error":"Faltan datos"}), 400
    users = get_users()
    if any(u["username"] == username for u in users):
        return jsonify({"ok":False,"error":"Ya existe"}), 400
    nuevo = {"id":int(datetime.now().timestamp()*1000), "username":username,
             "password":hp(password), "rol":rol}
    users.append(nuevo)
    save(USERS_FILE, users)
    return jsonify({"ok":True,"user":{"id":nuevo["id"],"username":username,"rol":rol}})

@app.route("/api/usuarios/<int:uid>", methods=["DELETE"])
@role("superadmin")
def eliminar_usuario(uid):
    users = get_users()
    u = next((x for x in users if x["id"] == uid), None)
    if not u: return jsonify({"error":"No encontrado"}), 404
    if u["username"] == "superadmin":
        return jsonify({"error":"No se puede eliminar"}), 403
    save(USERS_FILE, [x for x in users if x["id"] != uid])
    return jsonify({"ok":True})

# ════════════════════════════════════════════════════════════
#  WHATSAPP
# ════════════════════════════════════════════════════════════
def enviar_whatsapp(sol, cfg=None):
    c = cfg or get_wa()
    if not c.get("phone") or not c.get("apikey"): return False
    emoji = {"Urgente":"🔴","Alta":"🟠","Normal":"🟢"}.get(sol.get("prioridad","Normal"),"🟢")
    texto = (f"📋 Nueva solicitud\n"
             f"👤 {sol.get('nombre', sol.get('solicitante',''))}\n"
             f"📁 {sol['tipo']}\n{emoji} Prioridad: {sol['prioridad']}\n"
             f"📝 {sol['descripcion']}\n🕐 {sol['fecha']}")
    url = (f"https://api.callmebot.com/whatsapp.php"
           f"?phone={c['phone']}&text={requests.utils.quote(texto)}&apikey={c['apikey']}")
    try:
        r = requests.get(url, timeout=10)
        print(f"[WA] {'OK' if r.ok else 'Error: '+r.text[:80]}")
        return r.ok
    except Exception as e:
        print(f"[WA] Excepción: {e}"); return False

@app.route("/api/wa/config", methods=["GET"])
@role("superadmin")
def wa_get():
    c = get_wa()
    return jsonify({"configurado": bool(c.get("phone") and c.get("apikey")),
                    "phone": c.get("phone",""), "apikey": c.get("apikey","")})

@app.route("/api/wa/config", methods=["POST"])
@role("superadmin")
def wa_set():
    d = request.json
    save(WA_FILE, {"phone":d.get("phone","").strip(), "apikey":d.get("apikey","").strip()})
    return jsonify({"ok":True})

@app.route("/api/wa/test", methods=["POST"])
@role("superadmin")
def wa_test():
    sol = {"nombre":"Sistema","tipo":"Prueba","prioridad":"Normal",
           "descripcion":"Mensaje de prueba.","fecha":datetime.now().strftime("%d/%m/%y %H:%M")}
    ok = enviar_whatsapp(sol)
    return jsonify({"ok":ok}) if ok else (jsonify({"ok":False,"error":"No se pudo enviar"}), 500)

# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    init_users()
    print("="*55)
    print("  Sistema de Solicitudes · http://localhost:5000")
    print(f"  WhatsApp: {'✓ Configurado' if WA_PHONE and WA_APIKEY else '✗ Sin configurar'}")
    print("="*55)
    app.run(debug=True, port=5000)
