"""API REST de loco (v1) : les clés, les droits et les outils du serveur MCP, en requêtes HTTP ordinaires.

Une clé créée dans Administration › MCP et API sert aussi ici (en-tête « Authorization: Bearer … »). Chaque route
correspond à un outil, que la clé doit avoir coché ; elle agit avec les droits actuels de son compte, sur le parc de son
profil, et ses actions sont au journal comme celles du serveur MCP (« API « nom de la clé » »). Les lectures sont des GET
(arguments dans l'adresse), les actions des POST ou DELETE (arguments en JSON). Les réponses sont en JSON ; une erreur
est {"error": "…"}, avec 400 (argument), 401 (clé), 403 (outil non accordé, origine refusée), 404 (introuvable),
405 (méthode), 413 (requête trop grande) ou 503 (fonctionnalité coupée, serveur de secours). La description complète
(OpenAPI 3.1) est servie sans clé par GET /api/v1/openapi.json.
"""
import json
import re

from flask import current_app, g, request
from werkzeug.exceptions import HTTPException

from . import app, config, features, mcp
from .db import db

PREFIX = "/api/v1"
# Méthode, chemin ({nom} : argument de l'outil pris dans l'adresse), outil.
ROUTES = (
    ("GET", "/devices", "list_devices"),
    ("GET", "/devices/{device}", "get_device"),
    ("GET", "/devices/{device}/resources", "get_device_resources"),
    ("POST", "/devices/{device}/scan", "scan_device"),
    ("POST", "/devices/{device}/update", "update_device"),
    ("POST", "/devices/{device}/reboot", "reboot_device"),
    ("POST", "/devices/{device}/wake", "wake_device"),
    ("POST", "/devices/{device}/install", "install_software"),
    ("DELETE", "/devices/{device}/actions/{action_id}", "cancel_device_action"),
    ("GET", "/resources", "list_resources"),
    ("POST", "/updates", "update_devices"),
    ("GET", "/groups", "list_device_groups"),
    ("GET", "/software", "find_installed_software"),
    ("GET", "/catalog", "list_software_catalog"),
    ("GET", "/scripts", "list_scripts"),
    ("GET", "/scripts/{script}", "get_script"),
    ("POST", "/scripts/{script}/run", "run_script"),
    ("GET", "/runs", "list_script_runs"),
    ("GET", "/runs/{run_id}", "get_script_run"),
    ("GET", "/overview", "get_overview"),
    ("GET", "/server", "get_server_status"),
    ("GET", "/journal", "search_journal"),
)
PATTERNS = [(method, path, tool, re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$"))
            for method, path, tool in ROUTES]
class TooLarge(Exception):
    pass


NOT_FOUND = re.compile(r"^No (device|device group|action \d+|software|script|script run) ")


def _json(payload, status=200, headers=None):
    body = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    response = current_app.response_class(body + "\n", status=status, mimetype="application/json")
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def _error(message, status, **extra):
    headers = {"WWW-Authenticate": 'Bearer realm="loco"'} if status == 401 else None
    return _json(dict({"error": message}, **extra), status, headers)


def _match(path):
    """(outil, arguments de l'adresse) de la route, ou (None, méthodes permises sur ce chemin)."""
    allowed = []
    for method, _path, tool, pattern in PATTERNS:
        found = pattern.match(path)
        if found:
            if method == request.method:
                return mcp.TOOLS.get(tool), found.groupdict()
            allowed.append(method)
    return None, allowed


def _arguments(t, from_path):
    """Arguments de l'outil : adresse, puis paramètres (GET) ou corps JSON (POST, DELETE)."""
    properties = t["schema"]["properties"]
    values = {}
    if request.method == "GET":
        for key in request.args:
            spec = properties.get(key, {})
            values[key] = request.args.getlist(key) if spec.get("type") == "array" else request.args.get(key)
    elif request.get_data(cache=True):
        if (request.content_length or 0) > mcp.MAX_BODY or len(request.get_data(cache=True)) > mcp.MAX_BODY:
            raise TooLarge()
        try:
            body = json.loads(request.get_data(cache=True))
        except (ValueError, UnicodeDecodeError):
            raise mcp.ToolError("The body must be a JSON object.")
        if not isinstance(body, dict):
            raise mcp.ToolError("The body must be a JSON object.")
        values.update(body)
    for key, value in from_path.items():
        if key in values and str(values[key]) != value:
            raise mcp.ToolError(f"Argument {key} is already given by the address.")
        values[key] = value
    return mcp.arguments(t, values)


def _origin_ok():
    """Comme le serveur MCP : une page web d'une autre origine ne parle pas à l'API (DNS rebinding)."""
    return mcp._origin_ok()


@app.route(PREFIX, defaults={"path": ""}, methods=["GET", "POST", "DELETE"])
@app.route(PREFIX + "/<path:path>", methods=["GET", "POST", "DELETE"])
def api_v1(path):
    path = "/" + path.strip("/") if path.strip("/") else ""
    if path == "/openapi.json" and request.method == "GET":
        return _json(openapi())
    if not _origin_ok():
        return _error("Origin not allowed.", 403)
    if not features.enabled("mcp"):
        return _error("The loco API (shared with the MCP server) is disabled on this server: Administration > Features.", 503)
    found = mcp.authenticate()
    if found is None:
        return _error("Missing, invalid, expired or revoked key: send Authorization: Bearer <key>. "
                      "Keys are created in Administration > MCP and API.", 401)
    token, user = found
    usable, effective = mcp.grants(token, user)
    g.user, g.permissions = user, effective
    g.mcp = {"token": token, "tools": usable, "via": "API"}
    mcp._touch({"name": "API", "version": (request.headers.get("User-Agent") or "").split(" ")[0][:20]})
    if path == "":
        if request.method != "GET":
            return _error("Use GET on /api/v1.", 405, allowed=["GET"])
        return _json(index(token, user, usable))
    t, extra = _match(path)
    if t is None:
        if extra:
            return _error(f"Method {request.method} not allowed here.", 405, allowed=sorted(set(extra)))
        return _error(f"Unknown API route {PREFIX}{path}.", 404, see=config.portal_url() + PREFIX + "/openapi.json")
    if t not in usable:
        return _error(f"This key may not use {t['name']} (tick it on the key, within the rights of its loco "
                      "account: Administration > MCP and API).", 403, tool=t["name"])
    try:
        args = _arguments(t, extra)
    except TooLarge:
        return _error("Request too large.", 413)
    except mcp.ToolError as e:
        return _error(str(e), 400, tool=t["name"])
    try:
        data = t["handler"](args)
    except mcp.ToolError as e:
        db().rollback()
        return _error(str(e), 404 if NOT_FOUND.search(str(e)) else 400, tool=t["name"])
    except HTTPException as e:                              # abort() d'une fonction partagée avec l'interface
        db().rollback()
        return _error(str(e.description or e.name), e.code if e.code in (400, 403, 404, 409) else 400, tool=t["name"])
    except Exception:
        db().rollback()
        current_app.logger.exception("api: échec de l'outil %s", t["name"])
        return _error("Internal error in loco (see the server log).", 500, tool=t["name"])
    return _json(dict(data))


def index(token, user, usable):
    granted = {t["name"] for t in usable}
    return {"api": "loco", "version": "v1", "loco_version": config.VERSION,
            "token": {"name": token["name"], "account": user["username"]},
            "openapi": config.portal_url() + PREFIX + "/openapi.json",
            "routes": [{"method": method, "path": PREFIX + path, "tool": tool, "allowed": tool in granted,
                        "read_only": mcp.TOOLS[tool]["read_only"]} for method, path, tool in ROUTES if tool in mcp.TOOLS]}


def openapi():
    """Description OpenAPI 3.1 de l'API, tirée des outils : arguments, types et textes sont ceux du serveur MCP."""
    paths = {}
    for method, path, tool in ROUTES:
        t = mcp.TOOLS.get(tool)
        if t is None:
            continue
        props, required = t["schema"]["properties"], set(t["schema"].get("required", ()))
        in_path = re.findall(r"\{(\w+)\}", path)
        parameters = [{"name": name, "in": "path", "required": True, "schema": props.get(name, {"type": "string"}),
                       **({"description": props[name]["description"]} if props.get(name, {}).get("description") else {})}
                      for name in in_path]
        operation = {"operationId": tool, "summary": t["title"], "description": t["description"],
                     "tags": [t["group"]], "responses": {
                         "200": {"description": "Result", "content": {"application/json": {"schema": {"type": "object"}}}},
                         "400": {"$ref": "#/components/responses/Error"}, "401": {"$ref": "#/components/responses/Error"},
                         "403": {"$ref": "#/components/responses/Error"}, "404": {"$ref": "#/components/responses/Error"}}}
        others = {k: v for k, v in props.items() if k not in in_path}
        if method == "GET":
            parameters += [{"name": name, "in": "query", "required": name in required, "schema": spec,
                            **({"description": spec["description"]} if spec.get("description") else {}),
                            **({"style": "form", "explode": True} if spec.get("type") == "array" else {})}
                           for name, spec in others.items()]
        elif others:
            body = {"type": "object", "properties": others, "additionalProperties": False}
            needed = sorted(required - set(in_path))
            if needed:
                body["required"] = needed
            operation["requestBody"] = {"required": bool(needed), "content": {"application/json": {"schema": body}}}
        if parameters:
            operation["parameters"] = parameters
        paths.setdefault(PREFIX + path, {})[method.lower()] = operation
    return {"openapi": "3.1.0",
            "info": {"title": "loco API", "version": "v1 (loco " + config.VERSION + ")",
                     "description": "Same keys, rights and tools as the loco MCP server. Create a key in "
                                    "Administration > MCP and API and tick the tools it may use."},
            "servers": [{"url": config.portal_url()}],
            "security": [{"bearer": []}],
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
                           "responses": {"Error": {"description": "Error", "content": {"application/json": {"schema": {
                               "type": "object", "properties": {"error": {"type": "string"}}}}}}}},
            "paths": paths}
