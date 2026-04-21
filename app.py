"""
Sistema de Solicitudes — Flask sin base de datos
Los datos se guardan en memoria (se pierden al reiniciar).
Cuando quieras conectar MySQL, usa la version app_mysql.py
"""

import os
import hashlib
import requests
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esto-en-produccion")

WA_PHONE  = os.environ.get("WA_PHONE",  "")
WA_APIKEY = os.environ.get("WA_APIKEY", "")

# ════════════════════════════════════════════════════════════
#  DATOS EN MEMORIA
# ════════════════════════════════════════════════════════════
def hp(p): return hashlib.sha256(p.encode()).hexdigest()

USUARIOS = [
    {"id": 1, "username": "superadmin", "password": hp("super123"), "rol": "superadmin"},
    {"id": 2, "username": "admin1",     "password": hp("admin123"), "rol": "admin"},
    {"id": 3, "username": "admin2",     "password": hp("admin456"), "rol": "admin"},
]

SOLICITUDES  = []
COMENTARIOS  = []
WA_CONFIG    = {"phone": WA_PHONE, "apikey": WA_APIKEY}

# ════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════
def login_req(f):
    @wraps(f)
    def w(*a, **k):
        if "user" not in session:
            return jsonify({"error": "No autenticado"}), 401
        return f(*a, **k)
    return w

def role(*roles):
    def d(f):
        @wraps(f)
        def w(*a, **k):
            if "user" not in session:
                return jsonify({"error": "No autenticado"}), 401
            if session["user"]["rol"] not in roles:
                return jsonify({"error": "Sin permiso"}), 403
            return f(*a, **k)
        return w
    return d

def sol_to_dict(s, include_comments=False):
    d = dict(s)
    d["solicitante"] = s["nombre"]
    d["comentarios"] = []
    if include_comments:
        d["comentarios"] = [c for c in COMENTARIOS if c["solicitudId"] == s["id"]]
    return d

def now_str():
    return datetime.now().strftime("%d/%m/%y %H:%M")

def new_id():
    return int(datetime.now().timestamp() * 1000)

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
    u = next((x for x in USUARIOS
               if x["username"] == d.get("username","").strip()
               and x["password"] == hp(d.get("password",""))), None)
    if not u:
        return jsonify({"ok": False, "error": "Usuario o contrasena incorrectos"}), 401
    safe = {"id": u["id"], "username": u["username"], "rol": u["rol"]}
    session["user"] = safe
    return jsonify({"ok": True, "user": safe})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me")
def me():
    return jsonify({"user": session.get("user")})

# ════════════════════════════════════════════════════════════
#  SOLICITUDES
# ════════════════════════════════════════════════════════════
@app.route("/api/solicitudes", methods=["POST"])
def crear_solicitud():
    d   = request.json
    sol = {
        "id":          new_id(),
        "nombre":      d.get("nombre","").strip(),
        "tipo":        d.get("tipo","").strip(),
        "descripcion": d.get("descripcion","").strip(),
        "prioridad":   d.get("prioridad","Normal"),
        "estado":      "Pendiente",
        "asignadoA":   None,
        "fecha":       now_str(),
    }
    SOLICITUDES.insert(0, sol)
    wa_ok = enviar_whatsapp(sol)
    return jsonify({"ok": True, "solicitud": sol_to_dict(sol, True), "wa_enviado": wa_ok})

@app.route("/api/solicitudes", methods=["GET"])
@login_req
def listar_solicitudes():
    u = session["user"]
    if u["rol"] == "superadmin":
        sols = SOLICITUDES
    else:
        sols = [s for s in SOLICITUDES if s.get("asignadoA") == u["username"]]
    return jsonify([sol_to_dict(s, True) for s in sols])

@app.route("/api/solicitudes/<int:sid>/estado", methods=["PATCH"])
@login_req
def cambiar_estado(sid):
    u    = session["user"]
    data = request.json
    sol  = next((s for s in SOLICITUDES if s["id"] == sid), None)
    if not sol:
        return jsonify({"error": "No encontrada"}), 404
    if u["rol"] == "admin" and sol.get("asignadoA") != u["username"]:
        return jsonify({"error": "Sin permiso"}), 403
    sol["estado"] = data.get("estado", sol["estado"])
    texto = (data.get("comentario") or "").strip()
    if texto:
        COMENTARIOS.append({
            "id":          new_id(),
            "solicitudId": sid,
            "autor":       u["username"],
            "rolAutor":    u["rol"],
            "texto":       texto,
            "estadoRef":   sol["estado"],
            "fecha":       now_str(),
        })
    return jsonify({"ok": True, "solicitud": sol_to_dict(sol, True)})

@app.route("/api/solicitudes/<int:sid>/comentarios", methods=["POST"])
@login_req
def agregar_comentario(sid):
    u    = session["user"]
    data = request.json
    sol  = next((s for s in SOLICITUDES if s["id"] == sid), None)
    if not sol:
        return jsonify({"error": "No encontrada"}), 404
    if u["rol"] == "admin" and sol.get("asignadoA") != u["username"]:
        return jsonify({"error": "Sin permiso"}), 403
    texto = (data.get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "El comentario no puede estar vacio"}), 400
    com = {
        "id":          new_id(),
        "solicitudId": sid,
        "autor":       u["username"],
        "rolAutor":    u["rol"],
        "texto":       texto,
        "estadoRef":   sol["estado"],
        "fecha":       now_str(),
    }
    COMENTARIOS.append(com)
    return jsonify({"ok": True, "comentario": com})

@app.route("/api/solicitudes/<int:sid>/prioridad", methods=["PATCH"])
@role("superadmin")
def cambiar_prioridad(sid):
    sol = next((s for s in SOLICITUDES if s["id"] == sid), None)
    if not sol:
        return jsonify({"error": "No encontrada"}), 404
    p = request.json.get("prioridad","Normal")
    if p not in ("Normal","Alta","Urgente"):
        return jsonify({"error": "Prioridad invalida"}), 400
    sol["prioridad"] = p
    return jsonify({"ok": True})


@app.route("/api/solicitudes/<int:sid>/asignar", methods=["PATCH"])
@role("superadmin")
def asignar(sid):
    sol = next((s for s in SOLICITUDES if s["id"] == sid), None)
    if not sol:
        return jsonify({"error": "No encontrada"}), 404
    sol["asignadoA"] = request.json.get("asignadoA") or None
    return jsonify({"ok": True})

# ════════════════════════════════════════════════════════════
#  USUARIOS
# ════════════════════════════════════════════════════════════
@app.route("/api/usuarios", methods=["GET"])
@role("superadmin")
def listar_usuarios():
    return jsonify([{"id":u["id"],"username":u["username"],"rol":u["rol"]} for u in USUARIOS])

@app.route("/api/usuarios", methods=["POST"])
@role("superadmin")
def crear_usuario():
    d        = request.json
    username = d.get("username","").strip()
    password = d.get("password","").strip()
    rol      = d.get("rol","admin")
    if not username or not password:
        return jsonify({"ok": False, "error": "Faltan datos"}), 400
    if any(u["username"] == username for u in USUARIOS):
        return jsonify({"ok": False, "error": "Ya existe"}), 400
    nuevo = {"id": new_id(), "username": username, "password": hp(password), "rol": rol}
    USUARIOS.append(nuevo)
    return jsonify({"ok": True, "user": {"id": nuevo["id"], "username": username, "rol": rol}})

@app.route("/api/usuarios/<int:uid>", methods=["DELETE"])
@role("superadmin")
def eliminar_usuario(uid):
    u = next((x for x in USUARIOS if x["id"] == uid), None)
    if not u:
        return jsonify({"error": "No encontrado"}), 404
    if u["username"] == "superadmin":
        return jsonify({"error": "No se puede eliminar"}), 403
    USUARIOS.remove(u)
    return jsonify({"ok": True})

# ════════════════════════════════════════════════════════════
#  WHATSAPP
# ════════════════════════════════════════════════════════════
def enviar_whatsapp(sol):
    phone  = WA_CONFIG.get("phone")  or WA_PHONE
    apikey = WA_CONFIG.get("apikey") or WA_APIKEY
    if not phone or not apikey: return False
    emoji = {"Urgente":"🔴","Alta":"🟠","Normal":"🟢"}.get(sol.get("prioridad","Normal"),"🟢")
    texto = (f"📋 Nueva solicitud\n"
             f"👤 {sol.get('nombre','')}\n"
             f"📁 {sol['tipo']}\n"
             f"{emoji} Prioridad: {sol['prioridad']}\n"
             f"📝 {sol['descripcion']}\n"
             f"🕐 {sol['fecha']}")
    url = (f"https://api.callmebot.com/whatsapp.php"
           f"?phone={phone}&text={requests.utils.quote(texto)}&apikey={apikey}")
    try:
        r = requests.get(url, timeout=10)
        print(f"[WA] {'OK' if r.ok else 'Error'}")
        return r.ok
    except Exception as e:
        print(f"[WA] Error: {e}"); return False

@app.route("/api/wa/config", methods=["GET"])
@role("superadmin")
def wa_get():
    return jsonify({
        "configurado": bool(WA_CONFIG.get("phone") and WA_CONFIG.get("apikey")),
        "phone":  WA_CONFIG.get("phone",""),
        "apikey": WA_CONFIG.get("apikey",""),
    })

@app.route("/api/wa/config", methods=["POST"])
@role("superadmin")
def wa_set():
    d = request.json
    WA_CONFIG["phone"]  = d.get("phone","").strip()
    WA_CONFIG["apikey"] = d.get("apikey","").strip()
    return jsonify({"ok": True})

@app.route("/api/wa/test", methods=["POST"])
@role("superadmin")
def wa_test():
    sol = {"nombre":"Sistema","tipo":"Prueba","prioridad":"Normal",
           "descripcion":"Mensaje de prueba.","fecha":now_str()}
    ok = enviar_whatsapp(sol)
    return jsonify({"ok": ok, "error": "" if ok else "No se pudo enviar"})

# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 50)
    print(f"  http://localhost:{port}")
    print(f"  superadmin / super123")
    print(f"  admin1     / admin123")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=True)
