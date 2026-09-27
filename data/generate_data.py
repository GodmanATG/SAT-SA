import os
import pathlib
import uuid
import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from faker import Faker

fake = Faker()
Faker.seed(42)
np.random.seed(42)
random.seed(42)

# Legacy generator (kept for reference). The maintained generator is
# app/synthetic/submissions.py, which writes per-CSE submission folders plus a
# ground-truth file. Output is derived from this file's location so the script
# is portable - the original absolute path only worked on one machine.
OUTPUT_DIR = str(pathlib.Path(__file__).resolve().parent / "sample")
os.makedirs(OUTPUT_DIR, exist_ok=True)

START_DATE = datetime(2026, 1, 1)
END_DATE = datetime(2026, 6, 30)

def random_dates(start, end, n, biz_hours_only=False, no_weekends=False, no_nights=False):
    dates = []
    while len(dates) < n:
        # Generate in batches
        timestamps = start.timestamp() + np.random.rand(n) * (end.timestamp() - start.timestamp())
        for ts in timestamps:
            if len(dates) >= n: break
            dt = datetime.fromtimestamp(ts)
            if no_weekends and dt.weekday() >= 5: continue
            if no_nights and (dt.hour < 6 or dt.hour >= 22): continue
            dates.append(dt)
    return dates

def get_base_notes():
    actions = ["Investigated source IP.", "Checked firewall logs.", "Contacted asset owner.", "Verified with SIEM correlation.", "Analyzed payload.", "Checked for lateral movement.", "Reviewed recent changes on host."]
    outcomes = ["False positive confirmed.", "True positive, contained.", "Escalated for further review.", "Added to watch list.", "No malicious activity found.", "Resolved by applying patch.", "User account disabled."]
    return f"{random.choice(actions)} {random.choice(outcomes)} {fake.sentence()}"

all_alerts = []
all_cases = []
all_assets = []
cse_profiles = []

# --- CSE-ALPHA (Power Grid) ---
print("Generating CSE-ALPHA...")
cse_id = str(uuid.uuid4())
cse_profiles.append({'cse_id': cse_id, 'name': 'CSE-ALPHA', 'sector': 'power', 'size': 'large', 'soc_model': 'in_house'})

analysts = [f"Analyst_A{i}" for i in range(1, 16)]
assets = []
asset_counts = {'server': 20, 'database': 15, 'workstation': 20, 'network_device': 15, 'web_app': 10}
for atype, count in asset_counts.items():
    for _ in range(count):
        a_id = str(uuid.uuid4())
        is_crit = True if atype in ['server', 'database'] and random.random() < 0.5 else False
        if atype == 'server' and len([a for a in assets if a['type'] == 'server' and a['is_critical']]) < 20: is_crit = True
        assets.append({'asset_id': a_id, 'cse_id': cse_id, 'hostname': fake.hostname(), 'ip_address': fake.ipv4(), 'type': atype, 'is_critical': is_crit})
all_assets.extend(assets)

n_alerts = 50000
severities = np.random.choice(['critical', 'high', 'medium', 'low', 'info'], n_alerts, p=[0.02, 0.08, 0.30, 0.40, 0.20])
dates = random_dates(START_DATE, END_DATE, n_alerts)

for i in range(n_alerts):
    sev = severities[i]
    dt = dates[i]
    ack_delay = random.uniform(10, 30) if sev == 'critical' else random.uniform(60, 240)
    ack_time = dt + timedelta(minutes=ack_delay)
    close_delay = random.uniform(2, 8) * 60 if sev == 'critical' else random.uniform(4, 24) * 60
    close_time = ack_time + timedelta(minutes=close_delay)
    escalated = True if sev == 'critical' and random.random() < 0.6 else (True if random.random() < 0.05 else False)
    
    alert_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    cat = random.choice(['malware', 'brute_force', 'policy_violation', 'data_exfiltration', 'phishing', 'unauthorized_access', 'ddos', 'sql_injection', 'privilege_escalation', 'anomalous_traffic'])
    
    all_alerts.append({'alert_id': alert_id, 'cse_id': cse_id, 'case_id': case_id, 'asset_id': random.choice(assets)['asset_id'], 'timestamp': dt, 'severity': sev, 'category': cat, 'source': random.choice(['firewall', 'ids', 'edr', 'siem'])})
    all_cases.append({'case_id': case_id, 'cse_id': cse_id, 'created_at': dt, 'acknowledged_at': ack_time, 'closed_at': close_time, 'analyst_id': random.choice(analysts), 'escalated': escalated, 'investigation_notes': get_base_notes()})

# --- CSE-BETA (Bank) ---
print("Generating CSE-BETA...")
cse_id = str(uuid.uuid4())
cse_profiles.append({'cse_id': cse_id, 'name': 'CSE-BETA', 'sector': 'banking', 'size': 'large', 'soc_model': 'hybrid'})

analysts = [f"Analyst_B{i}" for i in range(1, 13)]
assets = [{'asset_id': str(uuid.uuid4()), 'cse_id': cse_id, 'hostname': fake.hostname(), 'ip_address': fake.ipv4(), 'type': random.choice(['server', 'database', 'workstation', 'network_device']), 'is_critical': random.random() < 0.2} for _ in range(70)]
all_assets.extend(assets)

n_alerts = 45000
severities = np.random.choice(['critical', 'high', 'medium', 'low', 'info'], n_alerts, p=[0.05, 0.15, 0.30, 0.35, 0.15])
dates = random_dates(START_DATE, END_DATE, n_alerts)

templates = ["Alert reviewed. No action required. Closing as false positive.", "Investigated and resolved. Standard procedure followed.", "Alert triaged. No threat detected. Case closed."]

for i in range(n_alerts):
    sev = severities[i]
    dt = dates[i]
    analyst = random.choice(analysts)
    
    if analyst == 'Analyst_B3' and sev in ['critical', 'high'] and random.random() < 0.8:
        ack_time = dt + timedelta(minutes=random.uniform(0.5, 1))
        close_time = ack_time + timedelta(minutes=random.uniform(2, 4))
    else:
        ack_delay = random.uniform(10, 60)
        ack_time = dt + timedelta(minutes=ack_delay)
        close_delay = random.uniform(60, 480)
        close_time = ack_time + timedelta(minutes=close_delay)
    
    escalated = True if sev == 'critical' and random.random() < 0.15 else False
    notes = random.choice(templates) if random.random() < 0.75 else get_base_notes()
        
    alert_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    
    all_alerts.append({'alert_id': alert_id, 'cse_id': cse_id, 'case_id': case_id, 'asset_id': random.choice(assets)['asset_id'], 'timestamp': dt, 'severity': sev, 'category': random.choice(['malware', 'brute_force', 'data_exfiltration']), 'source': random.choice(['firewall', 'ids', 'edr'])})
    all_cases.append({'case_id': case_id, 'cse_id': cse_id, 'created_at': dt, 'acknowledged_at': ack_time, 'closed_at': close_time, 'analyst_id': analyst, 'escalated': escalated, 'investigation_notes': notes})


# --- CSE-GAMMA (Telecom) ---
print("Generating CSE-GAMMA...")
cse_id = str(uuid.uuid4())
cse_profiles.append({'cse_id': cse_id, 'name': 'CSE-GAMMA', 'sector': 'telecom', 'size': 'medium', 'soc_model': 'in_house'})
analysts = [f"Analyst_G{i}" for i in range(1, 9)]

silent_dbs = []
assets = []
for i in range(50):
    atype = 'database' if i < 10 else random.choice(['server', 'workstation', 'network_device'])
    is_crit = True if i < 10 else random.random() < 0.2
    a = {'asset_id': str(uuid.uuid4()), 'cse_id': cse_id, 'hostname': fake.hostname(), 'ip_address': fake.ipv4(), 'type': atype, 'is_critical': is_crit}
    assets.append(a)
    if i < 10: silent_dbs.append(a['asset_id'])
all_assets.extend(assets)

n_alerts = 18000
severities = np.random.choice(['critical', 'high', 'medium', 'low', 'info'], n_alerts)
dates = random_dates(START_DATE, END_DATE, n_alerts, no_nights=True, no_weekends=True)

for i in range(n_alerts):
    sev = severities[i]
    dt = dates[i]
    ack_time = dt + timedelta(minutes=random.uniform(10, 60))
    close_time = ack_time + timedelta(minutes=random.uniform(60, 300))
    
    asset = random.choice([a for a in assets if a['asset_id'] not in silent_dbs])
    cat = random.choice(['malware', 'brute_force', 'policy_violation', 'phishing', 'unauthorized_access', 'ddos', 'privilege_escalation', 'anomalous_traffic'])
    
    alert_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    all_alerts.append({'alert_id': alert_id, 'cse_id': cse_id, 'case_id': case_id, 'asset_id': asset['asset_id'], 'timestamp': dt, 'severity': sev, 'category': cat, 'source': 'ids'})
    all_cases.append({'case_id': case_id, 'cse_id': cse_id, 'created_at': dt, 'acknowledged_at': ack_time, 'closed_at': close_time, 'analyst_id': random.choice(analysts), 'escalated': False, 'investigation_notes': get_base_notes()})


# --- CSE-DELTA (Oil & Gas) ---
print("Generating CSE-DELTA...")
cse_id = str(uuid.uuid4())
cse_profiles.append({'cse_id': cse_id, 'name': 'CSE-DELTA', 'sector': 'energy', 'size': 'medium', 'soc_model': 'mssp'})
analysts = [f"Analyst_D{i}" for i in range(1, 7)]
assets = [{'asset_id': str(uuid.uuid4()), 'cse_id': cse_id, 'hostname': fake.hostname(), 'ip_address': fake.ipv4(), 'type': random.choice(['server', 'database', 'workstation', 'network_device']), 'is_critical': random.random() < 0.2} for _ in range(45)]
all_assets.extend(assets)

n_alerts = 22000
dates = sorted(random_dates(START_DATE, END_DATE, n_alerts))

for i in range(n_alerts):
    dt = dates[i]
    sev = random.choice(['critical', 'high', 'medium', 'low'])
    ack_time = dt + timedelta(minutes=random.uniform(1, 10))
    investigation_time = timedelta(minutes=random.uniform(2, 3))
    
    if dt.day >= 25 and random.random() < 0.1:
        close_time = dt.replace(hour=23, minute=random.randint(0,59))
        if close_time < ack_time: close_time = ack_time + investigation_time
    else:
        close_time = ack_time + investigation_time
        
    alert_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    all_alerts.append({'alert_id': alert_id, 'cse_id': cse_id, 'case_id': case_id, 'asset_id': random.choice(assets)['asset_id'], 'timestamp': dt, 'severity': sev, 'category': 'malware', 'source': 'siem'})
    all_cases.append({'case_id': case_id, 'cse_id': cse_id, 'created_at': dt, 'acknowledged_at': ack_time, 'closed_at': close_time, 'analyst_id': random.choice(analysts), 'escalated': False, 'investigation_notes': get_base_notes()})

# --- CSE-EPSILON (Airport) ---
print("Generating CSE-EPSILON...")
cse_id = str(uuid.uuid4())
cse_profiles.append({'cse_id': cse_id, 'name': 'CSE-EPSILON', 'sector': 'transport', 'size': 'small', 'soc_model': 'in_house'})
analysts = [f"Analyst_E{i}" for i in range(1, 5)]
assets = [{'asset_id': str(uuid.uuid4()), 'cse_id': cse_id, 'hostname': fake.hostname(), 'ip_address': fake.ipv4(), 'type': random.choice(['server', 'database', 'workstation']), 'is_critical': random.random() < 0.2} for _ in range(25)]
all_assets.extend(assets)
n_alerts = 6000
dates = random_dates(START_DATE, END_DATE, n_alerts)

for dt in dates:
    sev = np.random.choice(['critical', 'high', 'medium', 'low'], p=[0.05, 0.1, 0.3, 0.55])
    ack_time = dt + timedelta(minutes=random.uniform(5, 30))
    
    month = dt.month
    if month <= 2:
        close_delay = random.uniform(3, 5) * 60 if sev == 'critical' else random.uniform(4, 10) * 60
    elif month <= 4:
        close_delay = random.uniform(1.5, 2.5) * 60 if sev == 'critical' else random.uniform(2, 5) * 60
    else:
        close_delay = random.uniform(20, 40) if sev == 'critical' else random.uniform(30, 60)
        
    close_time = ack_time + timedelta(minutes=close_delay)
    
    alert_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    cat = random.choice(['malware', 'brute_force', 'policy_violation', 'data_exfiltration', 'phishing', 'unauthorized_access', 'ddos', 'sql_injection', 'anomalous_traffic'])
    all_alerts.append({'alert_id': alert_id, 'cse_id': cse_id, 'case_id': case_id, 'asset_id': random.choice(assets)['asset_id'], 'timestamp': dt, 'severity': sev, 'category': cat, 'source': 'firewall'})
    all_cases.append({'case_id': case_id, 'cse_id': cse_id, 'created_at': dt, 'acknowledged_at': ack_time, 'closed_at': close_time, 'analyst_id': random.choice(analysts), 'escalated': False, 'investigation_notes': get_base_notes()})

# --- CSE-ZETA (Hospital) ---
print("Generating CSE-ZETA...")
cse_id = str(uuid.uuid4())
cse_profiles.append({'cse_id': cse_id, 'name': 'CSE-ZETA', 'sector': 'healthcare', 'size': 'small', 'soc_model': 'mssp'})
analysts = [f"Analyst_Z{i}" for i in range(1, 4)]
assets = []
silent_crit = []
for i in range(30):
    is_crit = True if i < 8 else False
    atype = 'server' if is_crit else random.choice(['workstation', 'network_device', 'database'])
    a = {'asset_id': str(uuid.uuid4()), 'cse_id': cse_id, 'hostname': fake.hostname(), 'ip_address': fake.ipv4(), 'type': atype, 'is_critical': is_crit}
    assets.append(a)
    if is_crit: silent_crit.append(a['asset_id'])
all_assets.extend(assets)
n_alerts = 4000
dates = random_dates(START_DATE, END_DATE, n_alerts)

for dt in dates:
    sev = random.choice(['critical', 'high', 'medium', 'low'])
    ack_time = dt + timedelta(minutes=random.uniform(10, 60))
    close_time = ack_time + timedelta(minutes=random.uniform(60, 240))
    
    asset = random.choice([a for a in assets if a['asset_id'] not in silent_crit])
    notes = "" if random.random() < 0.4 else get_base_notes()
    
    alert_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    all_alerts.append({'alert_id': alert_id, 'cse_id': cse_id, 'case_id': case_id, 'asset_id': asset['asset_id'], 'timestamp': dt, 'severity': sev, 'category': 'malware', 'source': 'firewall'})
    all_cases.append({'case_id': case_id, 'cse_id': cse_id, 'created_at': dt, 'acknowledged_at': ack_time, 'closed_at': close_time, 'analyst_id': random.choice(analysts), 'escalated': False, 'investigation_notes': notes})

print("Exporting data to CSV...")
pd.DataFrame(cse_profiles).to_csv(os.path.join(OUTPUT_DIR, 'cse_profiles.csv'), index=False)
pd.DataFrame(all_assets).to_csv(os.path.join(OUTPUT_DIR, 'assets.csv'), index=False)
pd.DataFrame(all_alerts).to_csv(os.path.join(OUTPUT_DIR, 'alerts.csv'), index=False)
pd.DataFrame(all_cases).to_csv(os.path.join(OUTPUT_DIR, 'cases.csv'), index=False)

def insert_to_pg(connection_string):
    from sqlalchemy import create_engine
    engine = create_engine(connection_string)
    pd.DataFrame(cse_profiles).to_sql('cse_profiles', engine, if_exists='append', index=False)
    pd.DataFrame(all_assets).to_sql('assets', engine, if_exists='append', index=False)
    pd.DataFrame(all_alerts).to_sql('alerts', engine, if_exists='append', index=False)
    pd.DataFrame(all_cases).to_sql('cases', engine, if_exists='append', index=False)
    print("Inserted into PostgreSQL successfully!")

print("Data generation complete!")
print(f"Total Profiles: {len(cse_profiles)}")
print(f"Total Assets: {len(all_assets)}")
print(f"Total Alerts: {len(all_alerts)}")
print(f"Total Cases: {len(all_cases)}")
