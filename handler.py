"""ms-cursos · cursos, módulos, matrículas y materiales (S3).  Productor de ModuloPublicado.

Base de datos: Amazon RDS MySQL, un esquema por tenant(cursos_utec, cursos_norte, ...).
El aislamiento lo da el esquema, no un WHERE.
  cursos(curso_id PK, docente_id ...)
  modulos(curso_id, modulo_id PK, FK curso)            -> ON DELETE CASCADE
  matriculas(curso_id, alumno_id PK, FK curso, idx alumno_id)
Los datos de cursos son relacionales (curso -> módulos -> matrículas): por eso SQL aquí.
"""
import json
import re
import uuid

from common import (ApiError, body, exigir_rol, log, now_iso, path_params, protegido,
                    publicar_evento, query_params, requerido, url_firmada, verificar_tenant)
from sqldb import Tenant, actualizar, crear_cursor, fila, leer_cursor, pymysql

SERVICIO = "cursos"


def _db(ctx):
    return Tenant(SERVICIO, ctx["tenant_id"])


# --------------------------------------------------------------- helpers
def _curso_o_404(t, curso_id):
    c = t.uno("SELECT * FROM {s}.cursos WHERE curso_id = %s", (curso_id,))
    if not c:  # otro tenant => otro esquema => "no existe" (no revela que existe)
        raise ApiError(404, "CURSO_NO_ENCONTRADO", "El curso no existe en tu tenant")
    return fila(c, t.tenant_id)


def _exigir_dueno(ctx, curso):
    exigir_rol(ctx, "docente", "admin")
    if ctx["rol"] == "docente" and curso.get("docente_id") != ctx["user_id"]:
        raise ApiError(403, "NO_ES_DOCENTE_DEL_CURSO", "Solo el docente del curso puede hacer esto")


def _esta_matriculado(t, ctx, curso_id):
    return bool(t.uno("SELECT 1 AS x FROM {s}.matriculas WHERE curso_id = %s AND alumno_id = %s",
                      (curso_id, ctx["user_id"])))


def _exigir_acceso_lectura(t, ctx, curso):
    if ctx["rol"] == "alumno" and not _esta_matriculado(t, ctx, curso["curso_id"]):
        raise ApiError(403, "NO_MATRICULADO", "No estás matriculado en este curso")
    if ctx["rol"] == "docente" and curso.get("docente_id") != ctx["user_id"]:
        raise ApiError(403, "NO_ES_DOCENTE_DEL_CURSO", "Este curso pertenece a otro docente")


def _modulo(f, tenant_id):
    m = fila(f, tenant_id, ("publicado",))
    m["curso_modulo"] = f"{m['curso_id']}#{m['modulo_id']}"
    m["materiales"] = json.loads(f.get("materiales") or "[]")
    return m


# ---------------------------------------------------------------- cursos
@protegido
def listar_cursos(event, ctx):
    t = _db(ctx)
    verificar_tenant(ctx, query_params(event))
    if ctx["rol"] == "alumno":
        filas = t.todos("SELECT c.*, m.avance_pct FROM {s}.matriculas m JOIN {s}.cursos c "
                        "ON c.curso_id = m.curso_id WHERE m.alumno_id = %s ORDER BY c.curso_id LIMIT 2000",
                        (ctx["user_id"],))
    elif ctx["rol"] == "docente":
        filas = t.todos("SELECT * FROM {s}.cursos WHERE docente_id = %s ORDER BY curso_id LIMIT 2000",
                        (ctx["user_id"],))
    else:
        filas = t.todos("SELECT * FROM {s}.cursos ORDER BY curso_id LIMIT 2000")
    cursos = [fila(f, ctx["tenant_id"]) for f in filas]
    return {"cursos": cursos, "total": len(cursos)}


@protegido
def crear_curso(event, ctx):
    exigir_rol(ctx, "docente", "admin")
    d = body(event)
    verificar_tenant(ctx, d)
    t = _db(ctx)
    docente_id = ctx["user_id"] if ctx["rol"] == "docente" else d.get("docente_id", ctx["user_id"])
    item = {
        "curso_id": "C" + uuid.uuid4().hex[:6].upper(),
        "codigo": requerido(d, "codigo", 20), "nombre": requerido(d, "nombre", 160),
        "descripcion": str(d.get("descripcion", ""))[:600], "docente_id": docente_id,
        "docente_nombre": ctx["nombre"] if docente_id == ctx["user_id"] else d.get("docente_nombre", ""),
        "ciclo": str(d.get("ciclo", "2026-2"))[:10], "creditos": int(d.get("creditos", 3)),
        "estado": "ACTIVO", "color": str(d.get("color", "#2677a6"))[:9], "creado_en": now_iso(),
    }
    cols = list(item)
    t.ejecutar(f"INSERT INTO {{s}}.cursos ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
               [item[c] for c in cols])
    return 201, {"tenant_id": ctx["tenant_id"], **item}


@protegido
def modificar_curso(event, ctx):
    curso_id = path_params(event)["curso_id"]
    d = body(event)
    verificar_tenant(ctx, d)
    t = _db(ctx)
    _exigir_dueno(ctx, _curso_o_404(t, curso_id))
    if "creditos" in d:
        d["creditos"] = int(d["creditos"])
    actualizar(t, "cursos", {"curso_id": curso_id}, d,
               ["codigo", "nombre", "descripcion", "ciclo", "creditos", "estado", "color"])
    return _curso_o_404(t, curso_id)


@protegido
def eliminar_curso(event, ctx):
    curso_id = path_params(event)["curso_id"]
    verificar_tenant(ctx, query_params(event))
    t = _db(ctx)
    _exigir_dueno(ctx, _curso_o_404(t, curso_id))
    t.ejecutar("DELETE FROM {s}.cursos WHERE curso_id = %s", (curso_id,))  # borra módulos y matrículas (CASCADE)
    log("INFO", accion="curso_eliminado", tenant_id=ctx["tenant_id"], curso_id=curso_id)
    return {"eliminado": curso_id}


# --------------------------------------------------------------- módulos
@protegido
def listar_modulos(event, ctx):
    t = _db(ctx)
    curso = _curso_o_404(t, path_params(event)["curso_id"])
    _exigir_acceso_lectura(t, ctx, curso)
    extra = " AND publicado = 1" if ctx["rol"] == "alumno" else ""
    mods = t.todos("SELECT * FROM {s}.modulos WHERE curso_id = %s" + extra + " ORDER BY numero",
                   (curso["curso_id"],))
    return {"curso_id": curso["curso_id"], "modulos": [_modulo(m, ctx["tenant_id"]) for m in mods]}


@protegido
def publicar_modulo(event, ctx):
    t = _db(ctx)
    curso = _curso_o_404(t, path_params(event)["curso_id"])
    _exigir_dueno(ctx, curso)
    d = body(event)
    verificar_tenant(ctx, d)
    cid = curso["curso_id"]
    numero = (t.uno("SELECT COALESCE(MAX(numero), 0) AS n FROM {s}.modulos WHERE curso_id = %s", (cid,)) or {"n": 0})["n"] + 1
    modulo_id = "M" + uuid.uuid4().hex[:6].upper()
    titulo = requerido(d, "titulo", 160)
    ahora = now_iso()
    t.ejecutar("INSERT INTO {s}.modulos (curso_id, modulo_id, numero, titulo, descripcion, publicado, "
               "materiales, fecha_publicacion) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)",
               (cid, modulo_id, int(numero), titulo, str(d.get("descripcion", ""))[:600], "[]", ahora))
    item = {"tenant_id": ctx["tenant_id"], "curso_modulo": f"{cid}#{modulo_id}", "curso_id": cid,
            "modulo_id": modulo_id, "numero": int(numero), "titulo": titulo,
            "descripcion": str(d.get("descripcion", ""))[:600], "publicado": True, "materiales": [],
            "fecha_publicacion": ahora}
    alumnos = [m["alumno_id"] for m in t.todos(
        "SELECT alumno_id FROM {s}.matriculas WHERE curso_id = %s ORDER BY alumno_id LIMIT 500", (cid,))]
    # EVENTO: ms-cursos no sabe quién lo consume (ms-notificaciones, ms-practica...). Solo publica.
    publicar_evento("ModuloPublicado", ctx["tenant_id"], {
        "curso_id": cid, "curso_nombre": curso["nombre"], "modulo_id": modulo_id,
        "titulo": titulo, "docente_id": curso["docente_id"], "alumnos": alumnos})
    return 201, item


# ------------------------------------------------------------ matrículas
@protegido
def matricular_estudiante(event, ctx):
    t = _db(ctx)
    curso = _curso_o_404(t, path_params(event)["curso_id"])
    d = body(event)
    verificar_tenant(ctx, d)
    if ctx["rol"] == "alumno":  # autoservicio: el alumno solo puede matricular(se) a sí mismo
        alumno_id, alumno_nombre = ctx["user_id"], ctx["nombre"]
    else:
        _exigir_dueno(ctx, curso)
        alumno_id, alumno_nombre = requerido(d, "alumno_id", 40), str(d.get("alumno_nombre", ""))[:80]
    cid = curso["curso_id"]
    ahora = now_iso()
    try:
        t.ejecutar("INSERT INTO {s}.matriculas (curso_id, alumno_id, alumno_nombre, estado, avance_pct, "
                   "matriculado_en) VALUES (%s, %s, %s, 'ACTIVA', 0, %s)", (cid, alumno_id, alumno_nombre, ahora))
    except pymysql.err.IntegrityError as e:
        if e.args and e.args[0] == 1062:  # clave duplicada: ya existe la matrícula
            raise ApiError(409, "YA_MATRICULADO", "El alumno ya está matriculado")
        raise
    return 201, {"tenant_id": ctx["tenant_id"], "curso_alumno": f"{cid}#{alumno_id}",
                 "alumno_curso": f"{alumno_id}#{cid}", "curso_id": cid, "alumno_id": alumno_id,
                 "alumno_nombre": alumno_nombre, "estado": "ACTIVA", "avance_pct": 0, "matriculado_en": ahora}


@protegido
def listar_matriculas(event, ctx):
    t = _db(ctx)
    curso = _curso_o_404(t, path_params(event)["curso_id"])
    _exigir_dueno(ctx, curso)
    limite, ultima = leer_cursor(query_params(event), ctx["tenant_id"])
    filas = t.todos("SELECT * FROM {s}.matriculas WHERE curso_id = %s AND alumno_id > %s "
                    "ORDER BY alumno_id LIMIT %s", (curso["curso_id"], ultima or "", limite + 1))
    hay_mas = len(filas) > limite
    filas = filas[:limite]
    items = []
    for f in filas:
        m = fila(f, ctx["tenant_id"])
        m["curso_alumno"], m["alumno_curso"] = f"{m['curso_id']}#{m['alumno_id']}", f"{m['alumno_id']}#{m['curso_id']}"
        items.append(m)
    return {"matriculas": items, "cursor": crear_cursor(ctx["tenant_id"], filas[-1]["alumno_id"]) if hay_mas else None}


# ------------------------------------------------------------- materiales (S3)
def _prefijo(tenant_id):
    return f"tenant_id={tenant_id}/"


@protegido
def agregar_material(event, ctx):
    """Registra el material en el módulo y devuelve una URL prefirmada para SUBIRLO a S3."""
    cid, mid = path_params(event)["curso_id"], path_params(event)["modulo_id"]
    t = _db(ctx)
    _exigir_dueno(ctx, _curso_o_404(t, cid))
    d = body(event)
    nombre = re.sub(r"[^A-Za-z0-9._-]", "_", requerido(d, "nombre_archivo", 120))[:80]
    content_type = str(d.get("content_type", "application/octet-stream"))[:100]
    mod = t.uno("SELECT materiales FROM {s}.modulos WHERE curso_id = %s AND modulo_id = %s", (cid, mid))
    if not mod:
        raise ApiError(404, "MODULO_NO_ENCONTRADO", "El módulo no existe en tu tenant")
    material_id = uuid.uuid4().hex[:8]
    key = f"{_prefijo(ctx['tenant_id'])}materiales/{cid}/{mid}/{material_id}-{nombre}"
    material = {"material_id": material_id, "nombre": nombre, "key": key,
                "content_type": content_type, "subido_en": now_iso()}
    lista = json.loads(mod["materiales"] or "[]") + [material]
    actualizar(t, "modulos", {"curso_id": cid, "modulo_id": mid}, {"materiales": json.dumps(lista)}, ["materiales"])
    return 201, {"material": material, "upload_url": url_firmada("put_object", key, content_type),
                 "metodo": "PUT", "expira_seg": 300}


@protegido
def descargar_material(event, ctx):
    p = path_params(event)
    t = _db(ctx)
    curso = _curso_o_404(t, p["curso_id"])
    _exigir_acceso_lectura(t, ctx, curso)
    mod = t.uno("SELECT materiales FROM {s}.modulos WHERE curso_id = %s AND modulo_id = %s",
                (p["curso_id"], p["modulo_id"]))
    mat = next((m for m in json.loads((mod or {}).get("materiales") or "[]")
                if m["material_id"] == p["material_id"]), None)
    if not mat:
        raise ApiError(404, "MATERIAL_NO_ENCONTRADO", "El material no existe")
    if not mat["key"].startswith(_prefijo(ctx["tenant_id"])):  # defensa en profundidad (T-ISO-2)
        log("SEGURIDAD", evento="PrefijoS3Ajeno", tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])
        raise ApiError(403, "PREFIJO_AJENO", "Ese archivo no pertenece a tu tenant")
    return {"url": url_firmada("get_object", mat["key"]), "nombre": mat["nombre"], "expira_seg": 300}
