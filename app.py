#!/usr/bin/env python3
import html
import glob
import json
import os
import pwd
import subprocess
import yaml

from site_config import load as load_site_config

MAX_REQUEST = 65536

PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="Open OnDemand">
  <title>__APP_NAME__</title>
  <link rel="icon" type="image/x-icon" href="/public/favicon.ico">
  <script src="__OOD_JS__"></script>
  <link rel="preload stylesheet" href="__OOD_CSS__" media="all" as="style" type="text/css">
  <link rel="stylesheet" media="all" href="/public/custom.css">
  <style>
    .navbar-dark, .navbar-light { background-color:#162853; }
    .restore-toolbar { min-height:4rem; }
    .restore-sidebar .nav-link.active { background-color:#162853; }
    .crumbs { min-height:2.5rem; }
    .crumbs button { border:0; background:transparent; color:var(--bs-link-color); padding:.25rem; }
    .table-wrap { overflow:auto; }
    table { min-width:690px; }
    td.actions { white-space:nowrap; text-align:right; }
    .kind { width:2rem; color:var(--bs-primary); }
    .empty { text-align:center; color:var(--bs-secondary-color); padding:2rem !important; }
    .restore-notification {
      position:fixed;
      top:4.75rem;
      right:1rem;
      z-index:1080;
      width:min(48rem,calc(100vw - 2rem));
      box-shadow:0 .5rem 1rem rgba(0,0,0,.2);
      overflow-wrap:anywhere;
    }
    footer { margin-top:3rem; }
  </style>
</head>
<body>
  <header>
    <span class="row"><a href="#main_container" class="skip-link">Skip Navigation</a></span>
    <nav class="navbar navbar-expand-md shadow-sm navbar-color navbar-dark" aria-label="Main Menu">
      <ul class="navbar-nav w-100 align-items-center" role="menubar">
        <li role="none">
          <a class="navbar-brand" href="/pun/sys/dashboard/" role="menuitem">Open OnDemand</a>
        </li>
        <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbar" aria-controls="navbar" aria-expanded="false" aria-label="Toggle navigation">
          <span class="navbar-toggler-icon"></span>
        </button>
        <div class="collapse navbar-collapse" id="navbar">
          <li class="nav-item dropdown" role="none">
            <a href="#" class="nav-link dropdown-toggle active" data-bs-toggle="dropdown" aria-haspopup="true" aria-expanded="false" role="menuitem" title="Files">Files</a>
            <ul class="dropdown-menu" title="Files" role="menu">
              <li><a class="dropdown-item active" aria-current="page" href="/pun/sys/pbs-file-restore"><i class="fas fa-history fa-fw app-icon me-1" aria-hidden="true"></i> File Restore</a></li>
              <li><a class="dropdown-item" href="/pun/sys/dashboard/files/fs/home/__USERNAME__"><i class="fas fa-home fa-fw app-icon me-1" aria-hidden="true"></i> Home Directory</a></li>
            </ul>
          </li>
          <li class="nav-item dropdown" role="none">
            <a href="#" class="nav-link dropdown-toggle" data-bs-toggle="dropdown" aria-haspopup="true" aria-expanded="false" role="menuitem" title="Clusters">Clusters</a>
            <ul class="dropdown-menu" title="Clusters" role="menu">
              <li><a class="dropdown-item" href="/pun/sys/shell" target="_blank" rel="noopener" role="menuitem"><i class="fas fa-terminal fa-fw app-icon me-1" aria-hidden="true"></i> Login Shell</a></li>
              <li><a class="dropdown-item" href="/pun/sys/dashboard/module-browser" role="menuitem"><i class="fas fa-box fa-fw app-icon me-1" aria-hidden="true"></i> Module Browser</a></li>
              <li><a class="dropdown-item" href="/pun/sys/dashboard/system-status" role="menuitem"><i class="fas fa-tachometer-alt fa-fw app-icon me-1" aria-hidden="true"></i> System Status</a></li>
            </ul>
          </li>
          <li class="nav-item dropdown" role="none">
            <a href="#" class="nav-link dropdown-toggle" data-bs-toggle="dropdown" aria-haspopup="true" aria-expanded="false" role="menuitem" title="Interactive Apps">Interactive Apps</a>
            <ul class="dropdown-menu" title="Interactive Apps" role="menu">
              __INTERACTIVE_APPS_MENU__
            </ul>
          </li>
          <li class="nav-item" role="none"><a class="nav-link" href="/pun/sys/dashboard/batch_connect/sessions" role="menuitem"><i class="fas fa-window-restore" aria-hidden="true"></i> My Interactive Sessions</a></li>
          <div class="ms-auto"></div>
          <li class="nav-item" role="none"><a class="nav-link disabled" href="#" title="Logged in as __USERNAME__"><i class="fas fa-user" aria-hidden="true"></i> Logged in as __USERNAME__</a></li>
          <li class="nav-item" role="none"><a class="nav-link" href="/logout" title="Log Out"><i class="fas fa-sign-out-alt" aria-hidden="true"></i> Log Out</a></li>
        </div>
      </ul>
    </nav>
  </header>
  <div id="status" class="d-none restore-notification" role="status" aria-live="polite" aria-atomic="true"></div>
  <div id="main_container" class="container-fluid content mt-4" role="main">
    <div class="text-end sticky-top bg-white p-2 mb-4 d-flex flex-wrap gap-2 justify-content-end z-index-999 restore-toolbar" role="region" aria-label="Backup controls">
      <div class="text-start me-auto align-self-center">
        <strong>__RETENTION_DAYS__-day backup window</strong>
        <div id="retentionRange" class="small text-muted">Completed home-directory backups are shown for up to __RETENTION_DAYS__ days.</div>
      </div>
      <label class="text-start">Backup date
        <input id="backupDate" class="form-control form-control-sm" type="date" disabled>
      </label>
      <button id="refresh" class="btn btn-primary btn-sm align-self-end" disabled><i class="fas fa-rotate-right" aria-hidden="true"></i> Load backup</button>
    </div>
    <hr>
    <div class="row gap-3 gap-md-0">
      <div class="col-md-3 restore-sidebar">
        <ul class="nav nav-pills flex-column">
          <li class="nav-item"><a class="nav-link" href="/pun/sys/dashboard/files/fs/home/__USERNAME__"><i class="fas fa-home fa-fw" aria-hidden="true"></i> Home Directory</a></li>
          <li class="nav-item"><a class="nav-link active" aria-current="page" href="/pun/sys/pbs-file-restore"><i class="fas fa-history fa-fw" aria-hidden="true"></i> File Restore</a></li>
        </ul>
        <div class="card mt-4">
          <div class="card-body">
            <h2 class="h6 card-title">Safe restore location</h2>
            <p class="card-text small mb-0">Restores never overwrite current files. Recovered content is placed under <strong>~/__RESTORE_DIRECTORY__</strong> by backup date.</p>
          </div>
        </div>
      </div>
      <div class="col-md-9">
        <div class="d-flex flex-wrap align-items-center justify-content-between mb-2">
          <div>
            <h1 class="h3 mb-1">__APP_NAME__</h1>
            <p class="text-muted mb-0">__APP_DESCRIPTION__</p>
          </div>
        </div>
        <nav id="crumbs" class="breadcrumb breadcrumb-no-delimiter rounded crumbs mt-3" aria-label="Backup path"></nav>
        <div class="small text-muted d-flex flex-wrap gap-3 mb-2" aria-label="File type legend">
          <strong class="text-body">Type:</strong>
          <span><i class="fas fa-folder text-primary" aria-hidden="true"></i> Folder</span>
          <span><i class="fas fa-file text-secondary" aria-hidden="true"></i> File</span>
          <span><i class="fas fa-link text-info" aria-hidden="true"></i> Symbolic link</span>
          <span><i class="fas fa-link text-warning" aria-hidden="true"></i> Hard link</span>
        </div>
        <div class="table-wrap" role="region" aria-label="Backup contents">
          <table class="table table-striped table-condensed w-100 align-middle">
            <caption class="visually-hidden">Contents of the selected home-directory backup</caption>
            <thead><tr><th scope="col">Type</th><th scope="col">Name</th><th scope="col">Size</th><th scope="col">Modified at</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead>
            <tbody id="files"><tr><td class="empty" colspan="5">Loading available backups…</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
  <footer>
    <div class="footer-info">© 2026 Sqoia Labs LLC | AGPL-3.0-or-later | <a href="__SUPPORT_URL__" target="_blank" rel="noopener">Support and source</a> | Built on <a href="https://openondemand.org/" target="_blank" rel="noopener">Open OnDemand</a></div>
  </footer>
<script>
const dateInput = document.getElementById("backupDate");
const refresh = document.getElementById("refresh");
const files = document.getElementById("files");
const crumbs = document.getElementById("crumbs");
const statusBox = document.getElementById("status");
const retentionRange = document.getElementById("retentionRange");
const appBase = window.location.pathname.endsWith("/")
  ? window.location.pathname
  : `${window.location.pathname}/`;
const apiEndpoint = `${appBase}api`;
let snapshotsByDate = new Map();
let pathStack = [{name:"Home", token:""}];

function status(message, kind="info") {
  statusBox.replaceChildren();
  statusBox.setAttribute("role", kind === "error" ? "alert" : "status");
  if (message) {
    const content=document.createElement("div");
    content.className="pe-4";
    content.textContent=message;
    const close=document.createElement("button");
    close.type="button";
    close.className="btn-close";
    close.setAttribute("aria-label","Dismiss notification");
    close.onclick=()=>status("");
    statusBox.append(content,close);
  }
  const bootstrapKind = kind === "error" ? "danger" : kind;
  statusBox.className = message
    ? `alert alert-${bootstrapKind} alert-dismissible restore-notification`
    : "d-none restore-notification";
}
async function api(body) {
  const response = await fetch(apiEndpoint, {
    method:"POST", credentials:"same-origin",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });
  const payload = await response.json().catch(() => ({ok:false,error:"Invalid server response"}));
  if (!response.ok || !payload.ok) throw new Error(payload.error || "Request failed");
  return payload.data;
}
function selectedSnapshot() {
  const item = snapshotsByDate.get(dateInput.value);
  return item ? item.epoch : null;
}
function size(value) {
  if (value === null || value === undefined) return "—";
  const units=["B","KB","MB","GB","TB"]; let n=Number(value), i=0;
  while(n>=1000 && i<units.length-1){n/=1000;i++;}
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
function modified(epoch) {
  if (!epoch) return "—";
  return new Date(epoch*1000).toLocaleString();
}
function renderCrumbs() {
  crumbs.replaceChildren();
  pathStack.forEach((part,index) => {
    if(index){const sep=document.createElement("span");sep.textContent="/";crumbs.append(sep);}
    const button=document.createElement("button");
    button.textContent=part.name;
    button.onclick=()=>{pathStack=pathStack.slice(0,index+1);loadDirectory();};
    crumbs.append(button);
  });
}
function actionButton(label, className, handler) {
  const button=document.createElement("button");
  button.textContent=label; button.className=className || "btn btn-outline-dark btn-sm";
  button.onclick=handler; return button;
}
function renderEntries(entries) {
  files.replaceChildren();
  if (!entries.length) {
    const row=document.createElement("tr"), cell=document.createElement("td");
    cell.colSpan=5; cell.className="empty"; cell.textContent="This directory is empty.";
    row.append(cell); files.append(row); return;
  }
  entries.forEach(entry => {
    const row=document.createElement("tr");
    const typeInfo = {
      d: {label:"Folder", icon:"fas fa-folder text-primary"},
      f: {label:"File", icon:"fas fa-file text-secondary"},
      l: {label:"Symbolic link", icon:"fas fa-link text-info"},
      h: {label:"Hard link", icon:"fas fa-link text-warning"}
    };
    const type = typeInfo[entry.type] || {label:"Other", icon:"fas fa-question-circle text-muted"};
    const kind=document.createElement("td"); kind.className="kind"; kind.title=type.label;
    const kindIcon=document.createElement("i"); kindIcon.className=type.icon; kindIcon.setAttribute("aria-hidden","true");
    const kindLabel=document.createElement("span"); kindLabel.className="visually-hidden"; kindLabel.textContent=type.label;
    kind.append(kindIcon,kindLabel);
    const name=document.createElement("td"); name.textContent=entry.name;
    const bytes=document.createElement("td"); bytes.textContent=size(entry.size);
    const mtime=document.createElement("td"); mtime.textContent=modified(entry.mtime);
    const actions=document.createElement("td"); actions.className="actions";
    if(!entry.leaf) actions.append(actionButton("Open","btn btn-outline-dark btn-sm",()=>{pathStack.push({name:entry.name,token:entry.token});loadDirectory();}));
    actions.append(document.createTextNode(" "));
    actions.append(actionButton("Restore","btn btn-primary btn-sm",()=>restoreEntry(entry)));
    row.append(kind,name,bytes,mtime,actions);
    if(!entry.leaf) row.ondblclick=()=>{pathStack.push({name:entry.name,token:entry.token});loadDirectory();};
    files.append(row);
  });
}
async function loadDirectory() {
  const snapshot=selectedSnapshot();
  if(!snapshot) return;
  renderCrumbs(); status("Loading directory…");
  files.innerHTML='<tr><td class="empty" colspan="5">Loading…</td></tr>';
  try {
    const data=await api({action:"list",snapshot,path:pathStack.at(-1).token});
    renderEntries(data.entries); status("");
  } catch(error) { status(error.message,"error"); files.innerHTML='<tr><td class="empty" colspan="5">Unable to load this directory.</td></tr>'; }
}
async function restoreEntry(entry) {
  const message=`Restore “${entry.name}” from ${dateInput.value}? It will be placed under ~/__RESTORE_DIRECTORY__ and will not overwrite current data.`;
  if(!window.confirm(message)) return;
  status(`Restoring ${entry.name}… Keep this page open; large directories can take time.`);
  document.querySelectorAll("button").forEach(button=>button.disabled=true);
  try {
    const data=await api({action:"restore",snapshot:selectedSnapshot(),path:entry.token});
    status(`Restore completed: ${data.restored_to}`,"success");
  } catch(error) { status(error.message,"error"); }
  finally {
    document.querySelectorAll("button").forEach(button=>button.disabled=false);
    dateInput.disabled=false;
  }
}
async function initialize() {
  try {
    const data=await api({action:"snapshots"});
    data.snapshots.forEach(item=>{if(!snapshotsByDate.has(item.date)) snapshotsByDate.set(item.date,item);});
    const dates=[...snapshotsByDate.keys()].sort();
    if(!dates.length) throw new Error("No backup snapshots are currently available.");
    dateInput.min=dates[0]; dateInput.max=dates.at(-1); dateInput.value=dates.at(-1);
    const firstAvailable=new Date(`${dates[0]}T00:00:00`).toLocaleDateString();
    const lastAvailable=new Date(`${dates.at(-1)}T00:00:00`).toLocaleDateString();
    retentionRange.textContent=`Completed home-directory backups in the configured __RETENTION_DAYS__-day window are currently available from ${firstAvailable} through ${lastAvailable}.`;
    dateInput.disabled=false; refresh.disabled=false;
    await loadDirectory();
  } catch(error) { status(error.message,"error"); files.innerHTML='<tr><td class="empty" colspan="5">No backups available.</td></tr>'; }
}
dateInput.addEventListener("change",()=>{
  if(!snapshotsByDate.has(dateInput.value)){status("There is no completed backup for that date.","error");refresh.disabled=true;}
  else {status("");refresh.disabled=false;}
});
refresh.onclick=()=>{pathStack=[{name:"Home",token:""}];loadDirectory();};
initialize();
</script>
</body>
</html>
"""


def interactive_apps_menu():
    apps = []
    for manifest_path in glob.glob("/var/www/ood/apps/sys/*/manifest.yml"):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle) or {}
        if manifest.get("category") != "Interactive Apps":
            continue

        slug = os.path.basename(os.path.dirname(manifest_path))
        name = str(manifest.get("name") or slug)
        subcategory = str(manifest.get("subcategory") or "Other")
        target = ""
        rel = ""
        if manifest.get("role") == "batch_connect":
            url = f"/pun/sys/dashboard/batch_connect/sys/{slug}/session_contexts/new"
        else:
            configured_url = str(manifest.get("url") or "")
            if configured_url.startswith(("https://", "http://")):
                url = configured_url
                target = ' target="_blank"'
                rel = ' rel="noopener"'
            else:
                url = f"/pun/sys/{slug}/{configured_url.lstrip('/')}".rstrip("/")
        apps.append((subcategory, name.casefold(), name, url, target, rel))

    if not apps:
        return (
            '<li><span class="dropdown-item-text text-muted">'
            "No interactive applications are available.</span></li>"
        )

    items = []
    current_subcategory = None
    for subcategory, _, name, url, target, rel in sorted(apps):
        if subcategory != current_subcategory:
            if current_subcategory is not None:
                items.append('<li><hr class="dropdown-divider"></li>')
            items.append(
                f'<li><h6 class="dropdown-header">{html.escape(subcategory)}</h6></li>'
            )
            current_subcategory = subcategory
        items.append(
            '<li><a class="dropdown-item" role="menuitem"'
            f' href="{html.escape(url, quote=True)}"{target}{rel}>'
            '<i class="fas fa-window-maximize fa-fw app-icon me-1" aria-hidden="true"></i> '
            f"{html.escape(name)}</a></li>"
        )
    return "\n".join(items)


def render_page(username):
    config = load_site_config()
    app_config = config["app"]
    manifests = glob.glob(
        "/var/www/ood/apps/sys/dashboard/public/assets/.sprockets-manifest-*.json"
    )
    if not manifests:
        raise RuntimeError("Open OnDemand asset manifest was not found")
    manifest_path = max(manifests, key=os.path.getmtime)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        assets = json.load(handle).get("assets", {})
    css = assets.get("application.css")
    javascript = assets.get("application.js")
    if not css or not javascript:
        raise RuntimeError("Open OnDemand application assets were not found")
    escaped_user = html.escape(username, quote=True)
    return (
        PAGE.replace("__OOD_CSS__", "/pun/sys/dashboard/assets/" + css)
        .replace("__OOD_JS__", "/pun/sys/dashboard/assets/" + javascript)
        .replace("__INTERACTIVE_APPS_MENU__", interactive_apps_menu())
        .replace("__USERNAME__", escaped_user)
        .replace("__APP_NAME__", html.escape(app_config["name"], quote=True))
        .replace("__APP_DESCRIPTION__", html.escape(app_config["description"], quote=True))
        .replace("__SUPPORT_URL__", html.escape(app_config["support_url"], quote=True))
        .replace("__RESTORE_DIRECTORY__", html.escape(app_config["restore_directory_name"], quote=True))
        .replace("__RETENTION_DAYS__", str(config["broker"]["snapshot_max_age_days"]))
    )


def response(start_response, status, body, content_type):
    encoded = body.encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", content_type),
            ("Content-Length", str(len(encoded))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
            (
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'self'",
            ),
        ],
    )
    return [encoded]


def application(environ, start_response):
    username = pwd.getpwuid(os.getuid()).pw_name
    path = environ.get("PATH_INFO", "/").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if path == "/" and method == "GET":
        return response(
            start_response,
            "200 OK",
            render_page(username),
            "text/html; charset=utf-8",
        )
    if path != "/api" or method != "POST":
        return response(
            start_response,
            "404 Not Found",
            json.dumps({"ok": False, "error": "Not found"}),
            "application/json",
        )
    if not environ.get("CONTENT_TYPE", "").lower().startswith("application/json"):
        return response(
            start_response,
            "415 Unsupported Media Type",
            json.dumps({"ok": False, "error": "JSON is required"}),
            "application/json",
        )
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        length = 0
    if length < 2 or length > MAX_REQUEST:
        return response(
            start_response,
            "400 Bad Request",
            json.dumps({"ok": False, "error": "Invalid request size"}),
            "application/json",
        )
    raw = environ["wsgi.input"].read(length)
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        request = None
    if not isinstance(request, dict) or "user" in request:
        return response(
            start_response,
            "400 Bad Request",
            json.dumps({"ok": False, "error": "Invalid request"}),
            "application/json",
        )
    try:
        completed = subprocess.run(
            ["/usr/bin/sudo", "-n", load_site_config()["portal"]["client_path"]],
            input=(json.dumps(request) + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=86500,
        )
        payload = json.loads(completed.stdout.decode("utf-8"))
        status = "200 OK" if payload.get("ok") else "400 Bad Request"
    except (subprocess.TimeoutExpired, UnicodeError, ValueError):
        payload = {"ok": False, "error": "The restore service is unavailable"}
        status = "503 Service Unavailable"
    return response(
        start_response, status, json.dumps(payload), "application/json; charset=utf-8"
    )
