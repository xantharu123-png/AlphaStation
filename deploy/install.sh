#!/bin/bash
# ============================================================
# Alpha Station / TradingBot — Server Setup (Produktion)
# Getestet auf: Ubuntu 22.04 / Debian 12
#
# Produkt-Architektur (siehe Audit LB-3):
#   tradingbot-api.service  -> uvicorn api:app   (127.0.0.1:8000)
#   tradingbot-bg.service   -> python3 bg_service.py
#   nginx                   -> TLS :443, statisches Frontend, /api/-Proxy
#   (KEIN tradingbot-frontend-Service — Frontend ist statisch.)
#
# Migration von Alt-Installationen:
#   tradingbot-frontend (:3000) und tradingbot (Streamlit :8501) werden erst
#   nach erfolgreichem TLS/nginx-Test deaktiviert. tradingbot-bg wird mit der
#   neuen, gehaerteten Unit-Datei ueberschrieben.
# ============================================================
set -e

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "FEHLER: install.sh muss vollstaendig als root laufen (sudo /bin/bash install.sh)." >&2
    exit 1
fi

INSTALL_PRIVATE_DIR=$(mktemp -d /run/alpha-station-install.XXXXXX)
chmod 0700 "$INSTALL_PRIVATE_DIR"
cleanup_install_private_dir() {
    case "${INSTALL_PRIVATE_DIR:-}" in
        /run/alpha-station-install.*) rm -rf -- "$INSTALL_PRIVATE_DIR" ;;
        "") ;;
        *) echo "FEHLER: Unerwarteter Installer-Arbeitsordner: $INSTALL_PRIVATE_DIR" >&2 ;;
    esac
}
trap cleanup_install_private_dir EXIT

SERVICE_HOME="/home/tradingbot"
APP_DIR="$SERVICE_HOME/app"
INSTALL_SYSTEMCTL_BIN="${INSTALL_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
INSTALL_PGREP_BIN="${INSTALL_PGREP_BIN:-/usr/bin/pgrep}"
INSTALL_ID_BIN="${INSTALL_ID_BIN:-/usr/bin/id}"
INSTALL_PROC_ROOT="${INSTALL_PROC_ROOT:-/proc}"

quiesce_install_source_user() {
    local unit service_uid
    if ! service_uid=$("$INSTALL_ID_BIN" -u tradingbot 2>/dev/null) \
        || [[ ! "$service_uid" =~ ^[0-9]+$ ]]; then
        echo "FEHLER: Dedizierter Service-User tradingbot fehlt." >&2
        return 1
    fi

    for unit in \
        tradingbot-api.service tradingbot-bg.service \
        tradingbot.service tradingbot-frontend.service; do
        query_install_unit_state "$unit" "vor Stop" || return 1
        if [ "$INSTALL_UNIT_LOAD_STATE" = "not-found" ]; then
            continue
        fi
        case "$INSTALL_UNIT_ACTIVE_STATE" in
            active|inactive|failed) ;;
            *)
                echo "FEHLER: Unit is not safely inactive/active before stop: $unit (ActiveState=${INSTALL_UNIT_ACTIVE_STATE:-missing})" >&2
                return 1
                ;;
        esac
        if ! "$INSTALL_SYSTEMCTL_BIN" stop "$unit" >/dev/null 2>&1; then
            echo "FEHLER: Service konnte nicht sicher gestoppt werden: $unit" >&2
            return 1
        fi
        query_install_unit_state "$unit" "nach Stop" || return 1
        if [ "$INSTALL_UNIT_LOAD_STATE" != "loaded" ]; then
            echo "FEHLER: Unexpected LoadState nach Stop: $unit (${INSTALL_UNIT_LOAD_STATE:-missing})" >&2
            return 1
        fi
        case "$INSTALL_UNIT_ACTIVE_STATE" in
            inactive|failed) ;;
            *)
                echo "FEHLER: Service ist nach Stop not safely inactive: $unit (ActiveState=${INSTALL_UNIT_ACTIVE_STATE:-missing})" >&2
                return 1
                ;;
        esac
    done
    assert_install_processes_quiesced "$service_uid" "nach Unit-Stop" || return 1
}

query_install_unit_state() {
    local unit="$1" context="$2" load_state="" active_state=""

    if ! load_state=$("$INSTALL_SYSTEMCTL_BIN" show \
        --property=LoadState --value "$unit" 2>/dev/null); then
        echo "FEHLER: Unit state query failed ($context, LoadState): $unit" >&2
        return 1
    fi
    load_state="${load_state%$'\r'}"
    case "$load_state" in
        loaded) ;;
        not-found)
            INSTALL_UNIT_LOAD_STATE="$load_state"
            INSTALL_UNIT_ACTIVE_STATE=""
            return 0
            ;;
        *)
            echo "FEHLER: Unexpected LoadState ($context): $unit (${load_state:-missing})" >&2
            return 1
            ;;
    esac

    if ! active_state=$("$INSTALL_SYSTEMCTL_BIN" show \
        --property=ActiveState --value "$unit" 2>/dev/null); then
        echo "FEHLER: Unit state query failed ($context, ActiveState): $unit" >&2
        return 1
    fi
    active_state="${active_state%$'\r'}"
    INSTALL_UNIT_LOAD_STATE="$load_state"
    INSTALL_UNIT_ACTIVE_STATE="$active_state"
}

install_pid_is_current_or_ancestor() {
    local candidate="$1" current="$$" parent="" key="" value="" rest=""
    local proc_root="${INSTALL_PROC_ROOT:-/proc}"

    while [[ "$current" =~ ^[0-9]+$ ]] && [ "$current" -gt 0 ]; do
        [ "$current" = "$candidate" ] && return 0
        parent=""
        [ -r "$proc_root/$current/status" ] || break
        while read -r key value rest; do
            if [ "$key" = "PPid:" ]; then
                parent="$value"
                break
            fi
        done < "$proc_root/$current/status"
        [ -n "$parent" ] || break
        current="$parent"
    done
    return 1
}

assert_no_install_service_processes() {
    local service_uid="$1" context="$2" matches="" rc=0

    if matches=$("$INSTALL_PGREP_BIN" -u "$service_uid" 2>/dev/null); then
        echo "FEHLER: Prozess des Service-Users tradingbot ist noch aktiv $context; Installation abgebrochen." >&2
        return 1
    else
        rc=$?
    fi
    if [ "$rc" -ne 1 ]; then
        echo "FEHLER: Service-user process query failed $context (rc=$rc)." >&2
        return 1
    fi
}

assert_no_install_legacy_root_processes() {
    local context="$1" matches="" rc=0 pid=""
    local pattern="/usr/local/sbin/alpha-station-auto-update|$APP_DIR/deploy/(auto_update|safe_deploy)\\.sh|/tmp/alpha-safe-deploy"

    if matches=$("$INSTALL_PGREP_BIN" -f -- "$pattern" 2>/dev/null); then
        rc=0
    else
        rc=$?
    fi
    case "$rc" in
        1) return 0 ;;
        0) ;;
        *)
            echo "FEHLER: Root process query failed $context (rc=$rc)." >&2
            return 1
            ;;
    esac
    while IFS= read -r pid; do
        [ -n "$pid" ] || continue
        if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
            echo "FEHLER: Root process query failed $context (ungueltige PID: $pid)." >&2
            return 1
        fi
        if ! install_pid_is_current_or_ancestor "$pid"; then
            echo "FEHLER: Legacy root process remains $context (PID=$pid)." >&2
            return 1
        fi
    done <<< "$matches"
}

assert_install_processes_quiesced() {
    local service_uid="$1" context="$2"
    assert_no_install_service_processes "$service_uid" "$context" || return 1
    assert_no_install_legacy_root_processes "$context" || return 1
}

validate_fresh_source_tree() {
    local source_root="$1" path_list="$INSTALL_PRIVATE_DIR/source-paths.nul"
    local metadata kind dev links owner mode base_dev path

    if ! metadata=$(LC_ALL=C stat -c '%F|%d|%h|%u|%a' -- "$source_root" 2>/dev/null); then
        echo "FEHLER: Frischer Quellbaum fehlt: $source_root" >&2
        return 1
    fi
    IFS='|' read -r kind base_dev links owner mode <<< "$metadata"
    if [ "$kind" != "directory" ]; then
        echo "FEHLER: Fresh source root is a symbolic link or special file: $source_root" >&2
        return 1
    fi
    if [ "$owner" != "0" ]; then
        echo "FEHLER: source path is not root-owned: $source_root" >&2
        return 1
    fi
    if [[ ! "$mode" =~ ^[0-7]+$ ]] || (( (8#$mode & 8#22) != 0 )); then
        echo "FEHLER: source path is group/world-writable: $source_root" >&2
        return 1
    fi
    if mountpoint -q -- "$source_root" 2>/dev/null; then
        echo "FEHLER: mount point is forbidden in fresh source: $source_root" >&2
        return 1
    fi

    # The source root is already root-owned and not service-writable. Lock it
    # before inspecting descendants, after all service-user processes stopped.
    chmod 0700 "$source_root"
    find "$source_root" -xdev -print0 > "$path_list" \
        || { echo "FEHLER: Frischer Quellbaum kann nicht vollstaendig gelesen werden." >&2; return 1; }

    while IFS= read -r -d '' path; do
        metadata=$(LC_ALL=C stat -c '%F|%d|%h|%u|%a' -- "$path" 2>/dev/null) \
            || { echo "FEHLER: source path disappeared during validation: $path" >&2; return 1; }
        IFS='|' read -r kind dev links owner mode <<< "$metadata"
        case "$kind" in
            directory) ;;
            "regular file")
                if [ "$links" != "1" ]; then
                    echo "FEHLER: hard link is forbidden in fresh source: $path" >&2
                    return 1
                fi
                ;;
            "symbolic link")
                echo "FEHLER: symbolic link is forbidden in fresh source: $path" >&2
                return 1
                ;;
            *)
                echo "FEHLER: special file is forbidden in fresh source ($kind): $path" >&2
                return 1
                ;;
        esac
        if [ "$dev" != "$base_dev" ]; then
            echo "FEHLER: source path crosses filesystems: $path" >&2
            return 1
        fi
        if [ "$kind" = "directory" ] && mountpoint -q -- "$path" 2>/dev/null; then
            echo "FEHLER: mount point is forbidden in fresh source: $path" >&2
            return 1
        fi
        if [ "$owner" != "0" ]; then
            echo "FEHLER: source path is not root-owned: $path" >&2
            return 1
        fi
        if [[ ! "$mode" =~ ^[0-7]+$ ]] || (( (8#$mode & 8#22) != 0 )); then
            echo "FEHLER: source path is group/world-writable: $path" >&2
            return 1
        fi
    done < "$path_list"

    # ACLs are normalized only after link/type/device validation succeeded.
    while IFS= read -r -d '' path; do
        if [ -d "$path" ]; then
            setfacl -b -k -- "$path"
        else
            setfacl -b -- "$path"
        fi
    done < "$path_list"
    chmod 0755 "$source_root"
}

echo "╔══════════════════════════════════════════╗"
echo "║  Alpha Station Server Setup (FastAPI)    ║"
echo "╚══════════════════════════════════════════╝"

# ── 1) System-Updates ──
echo "📦 System-Updates..."
sudo apt update && sudo apt upgrade -y

# ── 2) Pakete installieren (inkl. nginx + certbot) ──
echo "🐍 Python, nginx, certbot installieren..."
sudo apt install -y python3 python3-pip python3-venv git nginx cron acl certbot python3-certbot-nginx

# ── 3) User erstellen ──
echo "👤 User 'tradingbot' erstellen..."
if ! id "tradingbot" &>/dev/null; then
    sudo useradd -m -s /usr/sbin/nologin tradingbot
else
    sudo usermod -s /usr/sbin/nologin tradingbot
fi

# Stop/query barriers run before touching the service home or source tree.
quiesce_install_source_user

if [ -L "$SERVICE_HOME" ] || [ ! -d "$SERVICE_HOME" ]; then
    echo "FEHLER: Service-Home muss vor Ownership-Aenderungen ein echtes Verzeichnis sein: $SERVICE_HOME" >&2
    exit 1
fi
chown --no-dereference root:root "$SERVICE_HOME"
chmod 0755 "$SERVICE_HOME"

# ── 4) Projekt-Verzeichnis ──
echo "📁 Projekt aufsetzen..."
if [ -e "$APP_DIR" ] || [ -L "$APP_DIR" ]; then
    if [ -L "$APP_DIR" ] || [ ! -d "$APP_DIR" ]; then
        echo "FEHLER: Bestehender APP_DIR muss ein echtes Verzeichnis sein: $APP_DIR" >&2
        exit 1
    fi
else
    install -d -m 0755 -o root -g root "$APP_DIR"
fi

# ── 5) Code kopieren (manuell oder git clone) ──
echo ""
echo "⚠️  Jetzt das Projekt nach $APP_DIR kopieren!"
echo "    Nur als root aus einem verifizierten Fresh-Clone/Root-SCP-Staging;"
echo "    siehe deploy/DEPLOY_ANLEITUNG.md. Kein alter service-eigener Checkout."
echo ""
read -p "Ist der Code in $APP_DIR? (j/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Jj]$ ]]; then
    echo "❌ Bitte Code zuerst kopieren, dann Script nochmal starten."
    exit 1
fi

# Root-Cron und Root-Deploy duerfen niemals Code aus einem service-schreibbaren
# Checkout ausfuehren. Ein Altbaum wird nicht durch rekursives chown vertraut:
# nur ein bereits root-kontrollierter Fresh-Clone/Root-SCP-Baum wird akzeptiert.
quiesce_install_source_user
validate_fresh_source_tree "$APP_DIR"

# Persistente App-Daten unter data_cache werden link-/mount-sicher validiert.
# Gemeinsames /tmp und HOME erzeugt systemd separat als StateDirectory unter
# /var/lib, dessen root-kontrollierter Parent vom Service nicht tauschbar ist.
sudo env APP_DIR="$APP_DIR" SERVICE_USER=tradingbot \
    RUNTIME_WORK_DIR="$INSTALL_PRIVATE_DIR" \
    /bin/bash "$APP_DIR/deploy/runtime_state_guard.sh"

# ── 6) Virtual Environment + Dependencies ──
# Hinweis: Unit-Dateien und safe_deploy.sh erwarten das venv unter $APP_DIR/venv.
echo "📦 Python Dependencies installieren..."
sudo python3 -I -m venv "$APP_DIR/venv"
sudo "$APP_DIR/venv/bin/python" -m pip install --upgrade pip
sudo "$APP_DIR/venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
sudo chown -R root:root "$APP_DIR/venv"
sudo chmod -R go-w "$APP_DIR/venv"

echo "Pruefe versioniertes Frontend-Bundle..."
sudo "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/verify_frontend_bundle.py"

# ── 7) Produktions-.env anlegen (Pflicht — Units starten ohne .env NICHT) ──
# Die App bootet im Commercial-Mode fail-closed: mit Default-Secrets
# (z.B. Repo-Fallback fuer JWT_SECRET) verweigert sie den Start.
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ] || [ ! -s "$ENV_FILE" ]; then
    echo "🔑 Erzeuge $ENV_FILE aus .env.production.example ..."
    sudo install -m 0640 -o root -g tradingbot "$APP_DIR/.env.production.example" "$ENV_FILE"
    echo ""
    echo "⚠️  PFLICHT: $ENV_FILE jetzt editieren und ALLE Platzhalter ersetzen"
    echo "    (JWT_SECRET, COMMERCE_ENFORCE_AUTH=1, ALLOW_LEGACY_ADMIN_MASTER_KEY=0,"
    echo "     CORS_ORIGINS, Stripe-Live-Keys, Provider-Keys, GMAIL_*)."
    read -p "Weiter mit Enter, wenn .env fertig editiert ist..." -r
else
    echo "✅ $ENV_FILE existiert bereits (wird nicht ueberschrieben)"
fi
sudo chown root:tradingbot "$ENV_FILE"
sudo chmod 0640 "$ENV_FILE"

# ── 8) Systemd Services installieren ──
echo "⚙️ Systemd Services einrichten..."
sudo cp "$APP_DIR/deploy/tradingbot-api.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/tradingbot-bg.service"  /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable tradingbot-api tradingbot-bg
sudo systemctl restart tradingbot-bg
sudo systemctl restart tradingbot-api

disable_legacy_frontends() {
    for legacy_unit in tradingbot-frontend.service tradingbot.service; do
        if systemctl list-unit-files "$legacy_unit" --no-legend 2>/dev/null | grep -q "^$legacy_unit"; then
            echo "Migration: deaktiviere Legacy-Unit $legacy_unit ..."
            sudo systemctl disable --now "$legacy_unit" || true
            sudo rm -f "/etc/systemd/system/$legacy_unit"
        fi
    done
    sudo systemctl daemon-reload
}

echo "✅ Services gestartet (tradingbot-api, tradingbot-bg)"

# ── 10) Nginx + TLS (Let's Encrypt) ──
echo "🌐 Nginx + TLS konfigurieren..."
echo "   (Die Domain muss bereits per DNS-A-Record auf diesen Server zeigen.)"
read -p "Domain fuer die App (z.B. app.deine-domain.de, leer = Schritt ueberspringen): " DOMAIN

if [ -n "$DOMAIN" ]; then
    # nginx (www-data) muss das Frontend unter /home/tradingbot/app/frontend lesen koennen.
    sudo chmod 755 /home/tradingbot
    sudo chmod -R a+rX "$APP_DIR/frontend"

    # Webroot fuer ACME-Renewals (Port-80-Block der nginx-Config).
    sudo mkdir -p /var/www/certbot

    # Zertifikat ZUERST holen — der 443-vHost referenziert die Cert-Dateien,
    # ohne sie schlaegt `nginx -t` fehl. certbot nutzt dafuer den gerade
    # installierten nginx (Default-Site reicht aus).
    if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
        echo "🔐 Hole Let's-Encrypt-Zertifikat fuer $DOMAIN ..."
        sudo certbot certonly --nginx -d "$DOMAIN"
    else
        echo "✅ Zertifikat fuer $DOMAIN existiert bereits"
    fi

    # vHost installieren: ${DOMAIN}-Platzhalter ersetzen, dann aktivieren.
    sudo sed "s/\${DOMAIN}/$DOMAIN/g" "$APP_DIR/deploy/nginx-tradingbot.conf" \
        | sudo tee /etc/nginx/sites-available/tradingbot > /dev/null
    sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t && sudo systemctl reload nginx

    # Verify the replacement first while the old frontend is still available.
    sudo env APP_DIR="$APP_DIR" ENV_FILE="$ENV_FILE" ALLOW_LEGACY_FRONTENDS=1 \
        bash "$APP_DIR/deploy/verify_commercial_edge.sh"

    # Retire direct legacy frontends only after their TLS replacement is live.
    disable_legacy_frontends
    sudo env APP_DIR="$APP_DIR" ENV_FILE="$ENV_FILE" \
        bash "$APP_DIR/deploy/verify_commercial_edge.sh"

    echo "✅ Nginx aktiv: https://$DOMAIN  (Renewal laeuft automatisch via certbot.timer)"
else
    echo "⏭️  Nginx/TLS uebersprungen. Spaeter manuell:"
    echo "    sudo certbot certonly --nginx -d DEINE_DOMAIN"
    echo "    sudo sed 's/\${DOMAIN}/DEINE_DOMAIN/g' $APP_DIR/deploy/nginx-tradingbot.conf | sudo tee /etc/nginx/sites-available/tradingbot"
    echo "    sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx"
    echo "    Commercial launch remains BLOCKED; legacy services stay untouched until the TLS replacement is verified."
fi

# ── 11) Auto-Update (nur fuer Git-Checkouts) ──
if [ -d "$APP_DIR/.git" ]; then
    echo "🔄 Sicheren Auto-Update-Cron einrichten..."
    sudo env APP_DIR="$APP_DIR" /bin/bash "$APP_DIR/deploy/install_auto_update.sh"
else
    echo "⚠️  Auto-Update nicht eingerichtet: $APP_DIR ist kein Git-Checkout."
    echo "    Fuer automatische Pulls das Projekt per Git klonen und danach ausfuehren:"
    echo "    sudo APP_DIR=$APP_DIR /bin/bash $APP_DIR/deploy/install_auto_update.sh"
fi

# ── 12) Abschluss ──
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ SETUP FERTIG!                                    ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  App:        https://\$DOMAIN  (nginx, TLS)           ║"
echo "║  API lokal:  http://127.0.0.1:8000/api/health        ║"
echo "║  Logs API:   journalctl -u tradingbot-api -f          ║"
echo "║  Logs BG:    journalctl -u tradingbot-bg -f           ║"
echo "║  Deploy:     bash deploy/safe_deploy.sh               ║"
echo "║  Auto-Update: bash deploy/health_check.sh --auto-update-only ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Naechste Schritte (siehe COMMERCIAL_LAUNCH_CHECKLIST.md):"
echo "  curl -s http://127.0.0.1:8000/api/commercial-readiness | python3 -m json.tool"
