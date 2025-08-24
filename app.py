from flask import Flask, render_template, request, redirect, flash, url_for, abort
import os
import yaml
import requests
from datetime import datetime
import csv
from pathlib import Path
from dotenv import load_dotenv
from collections import defaultdict
import time

load_dotenv()

# Ensure Flask can find templates in the correct directory
app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET", "supersecretkey")

# Rate limiting storage (in-memory, basic)
rate_limits = defaultdict(list)
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 300  # 5 minutes

def resolve_env_variables(value):
    """Resolve environment variable references in YAML values"""
    if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
        env_var = value[2:-1]  # Remove ${ and }
        return os.getenv(env_var, value)  # Return original if env var not found
    return value

def load_flows():
    """Load flows from YAML, ENV, or legacy fallback"""
    flows = []
    
    # Try YAML first
    yaml_path = Path("flows.yml")
    if yaml_path.exists():
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                yaml_flows = data.get('flows', [])
                
                # Resolve environment variables in YAML flows
                for flow in yaml_flows:
                    flow['flow_url'] = resolve_env_variables(flow.get('flow_url', ''))
                    flow['launch_key'] = resolve_env_variables(flow.get('launch_key', ''))
                    
                return yaml_flows
        except Exception as e:
            print(f"Error loading YAML: {e}")
    
    # Try ENV groups (FLOW_1_*, FLOW_2_*, etc.)
    flow_groups = {}
    for key, value in os.environ.items():
        if key.startswith('FLOW_') and '_' in key[5:]:
            parts = key.split('_', 2)
            if len(parts) >= 3:
                flow_num = parts[1]
                field = parts[2].lower()
                if flow_num not in flow_groups:
                    flow_groups[flow_num] = {}
                flow_groups[flow_num][field] = value
    
    # Convert ENV groups to flow objects
    for flow_num, data in flow_groups.items():
        if all(k in data for k in ['id', 'url', 'key', 'title', 'desc']):
            flows.append({
                'id': data['id'],
                'title': data['title'],
                'description': data['desc'],
                'flow_url': data['url'],
                'launch_key': data['key']
            })
    
    if flows:
        return flows
    
    # Legacy fallback
    flow_url = os.getenv("FLOW_URL")
    launch_key = os.getenv("LAUNCH_KEY")
    if flow_url and launch_key:
        return [{
            'id': 'legacy_flow',
            'title': '🤖 Legacy RPA Flow',
            'description': 'Original single flow configuration',
            'flow_url': flow_url,
            'launch_key': launch_key
        }]
    
    return []

def get_client_ip():
    """Get client IP for rate limiting"""
    return request.headers.get('X-Forwarded-For', request.remote_addr)

def check_rate_limit(ip):
    """Simple in-memory rate limiting"""
    now = time.time()
    # Clean old entries
    rate_limits[ip] = [req_time for req_time in rate_limits[ip] 
                      if now - req_time < RATE_LIMIT_WINDOW]
    
    if len(rate_limits[ip]) >= RATE_LIMIT_REQUESTS:
        return False
    
    rate_limits[ip].append(now)
    return True

def log_trigger(flow_id, flow_title, name, status, http_status=None, ip=None, user_agent=None):
    """Log trigger attempt to CSV"""
    csv_path = Path("logs/trigger_log.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Truncate user agent to 200 chars
    if user_agent and len(user_agent) > 200:
        user_agent = user_agent[:200]
    
    row = [
        datetime.utcnow().isoformat() + "Z",
        flow_id,
        flow_title,
        name,
        status,
        http_status or "",
        ip or "",
        user_agent or ""
    ]
    
    file_exists = csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "time_utc", "flow_id", "flow_title", "name", 
                "status", "http_status", "ip", "ua"
            ])
        writer.writerow(row)

@app.route("/")
def dashboard():
    """Dashboard showing all available flows"""
    flows = load_flows()
    return render_template("dashboard.html", flows=flows)

@app.route("/flow/<flow_id>")
def flow_login(flow_id):
    """Per-flow login page"""
    flows = load_flows()
    flow = next((f for f in flows if f['id'] == flow_id), None)
    
    if not flow:
        flash("Flow not found", "danger")
        return redirect(url_for('dashboard'))
    
    return render_template("flow_login.html", flow=flow)

@app.route("/trigger/<flow_id>", methods=["POST"])
def trigger_flow(flow_id):
    """Trigger a specific flow"""
    flows = load_flows()
    flow = next((f for f in flows if f['id'] == flow_id), None)
    
    if not flow:
        flash("Flow not found", "danger")
        return redirect(url_for('dashboard'))
    
    # Get client info
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', '')
    
    # Rate limiting
    if not check_rate_limit(client_ip):
        flash("Too many requests. Please wait before trying again.", "warning")
        return redirect(url_for('flow_login', flow_id=flow_id))
    
    # Validate input
    name = request.form.get("name", "").strip()
    key = request.form.get("key", "").strip()
    
    if not name or not key:
        flash("Vennligst fyll ut begge felt", "warning")
        log_trigger(flow_id, flow['title'], name or "EMPTY", "VALIDATION_ERROR", 
                   ip=client_ip, user_agent=user_agent)
        return redirect(url_for('flow_login', flow_id=flow_id))
    
    # Validate access code
    if key != flow['launch_key']:
        flash("Feil kode. Prøv igjen.", "danger")
        log_trigger(flow_id, flow['title'], name, "ACCESS_DENIED", 
                   ip=client_ip, user_agent=user_agent)
        return redirect(url_for('flow_login', flow_id=flow_id))
    
    # Trigger flow
    try:
        # For GET request, send data as query parameters instead of JSON body
        params = {
            "triggered_by": name,
            "trigger_time": datetime.utcnow().isoformat() + "Z",
            "source": "flask",
            "flow_id": flow_id
        }
        
        response = requests.get(
            flow['flow_url'], 
            params=params,  # Query parameters instead of json payload
            timeout=20
        )
        
        if response.status_code in [200, 202]:
            flash(f"Flyten '{flow['title']}' ble trigget og logget!", "success")
            log_trigger(flow_id, flow['title'], name, "OK", 
                    response.status_code, client_ip, user_agent)
        else:
            flash(f"Feil ved kjøring. Statuskode: {response.status_code}", "danger")
            log_trigger(flow_id, flow['title'], name, "HTTP_ERROR", 
                    response.status_code, client_ip, user_agent)
            
    except Exception as e:
        flash(f"En feil oppstod: {str(e)}", "danger")
        log_trigger(flow_id, flow['title'], name, "EXCEPTION", 
                ip=client_ip, user_agent=user_agent)
    
    return redirect(url_for('flow_login', flow_id=flow_id))

if __name__ == "__main__":
    app.run(debug=True)
