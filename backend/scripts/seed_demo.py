"""
Tabularium — Script de seed com dados de demonstração.

Popula o banco com hosts, hardware, rede, discos, licenças e softwares
fictícios para testar o frontend sem precisar do collector real.

Uso:
    python seed_demo.py
    (ou via API: python seed_demo.py --api http://localhost:8091)
"""

import json
import random
import requests
import sys
from datetime import datetime, timedelta

API_URL = sys.argv[2] if "--api" in sys.argv and len(sys.argv) > 3 else "http://localhost:8091"

# ---- Dados fictícios ----

HOSTNAMES = [
    "FINANCE-01", "FINANCE-02", "FINANCE-03",
    "RH-01", "RH-02",
    "TI-HELPDESK-01", "TI-HELPDESK-02", "TI-HELPDESK-03",
    "TI-DEV-01", "TI-DEV-02",
    "DIR-EXEC-01", "DIR-EXEC-02",
    "COMPRAS-01", "COMPRAS-02",
    "LOGISTICA-01", "LOGISTICA-02", "LOGISTICA-03",
    "MARKETING-01", "MARKETING-02",
    "RECEPCAO-01",
]

MANUFACTURERS = ["Dell Inc.", "Lenovo", "HP", "Acer"]
MODELS = {
    "Dell Inc.": ["OptiPlex 7090", "OptiPlex 5090", "Latitude 5530"],
    "Lenovo": ["ThinkCentre M920", "ThinkPad T14", "ThinkCentre M75s"],
    "HP": ["ProDesk 400 G7", "EliteDesk 800 G6", "ProBook 450 G8"],
    "Acer": ["Veriton X2680G", "Aspire TC"],
}
CPUS = ["Intel Core i5-12500", "Intel Core i7-12700", "Intel Core i3-12100", "AMD Ryzen 5 5600G"]
RAM = [8.0, 16.0, 32.0]
DOMAIN = "empresa.local"

WINDOWS_EDITIONS = ["Professional", "Enterprise"]
OFFICE_VERSIONS = ["2016", "2019", "365"]

SOFTWARES = [
    ("Google Chrome", "125.0", "Google LLC"),
    ("Mozilla Firefox", "126.0", "Mozilla Foundation"),
    ("7-Zip", "23.01", "Igor Pavlov"),
    ("Microsoft Visual C++ Redistributable", "14.38", "Microsoft Corporation"),
    ("Adobe Acrobat Reader DC", "2024.001", "Adobe Inc."),
    ("Microsoft Office Professional Plus 2019", "16.0.104", "Microsoft Corporation"),
    ("VLC Media Player", "3.0.20", "VideoLAN"),
    ("WinRAR", "6.24", "Alexander L. Roshal"),
    ("TeamViewer", "15.51", "TeamViewer GmbH"),
    ("AnyDesk", "8.1", "AnyDesk Software GmbH"),
    ("Zoom Workplace", "6.0", "Zoom Video Communications"),
    ("Microsoft Teams", "24.10", "Microsoft Corporation"),
    ("Notepad++", "8.6.4", "Don Ho"),
    ("FileZilla", "3.67", "Tim Kosse"),
    ("PuTTY", "0.80", "Simon Tatham"),
    ("Java Runtime Environment", "8.0.401", "Oracle Corporation"),
    ("Python", "3.12.3", "Python Software Foundation"),
    ("Git", "2.45.0", "The Git Development Community"),
    ("Node.js", "20.13", "OpenJS Foundation"),
    ("Visual Studio Code", "1.89", "Microsoft Corporation"),
]


def random_serial():
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choices(chars, k=10))


def random_mac():
    return ":".join([f"{random.randint(0, 255):02X}" for _ in range(6)])


def generate_host(hostname, index, force_days_ago=None):
    """Gera payload de checkin para um host ficticio."""
    mfr = random.choice(MANUFACTURERS)
    model = random.choice(MODELS[mfr])
    is_online = random.random() > 0.15  # 85% online

    if force_days_ago:
        is_online = False
        last_seen = datetime.utcnow() - timedelta(days=force_days_ago)
    else:
        last_seen = datetime.utcnow() - timedelta(
            minutes=random.randint(1, 600) if is_online else random.randint(1440, 10080)
        )

    # Hardware
    hardware = {
        "manufacturer": mfr,
        "model": model,
        "serial": random_serial(),
        "cpu": random.choice(CPUS),
        "ram_gb": random.choice(RAM),
        "bios_version": f"{random.randint(1, 3)}.{random.randint(0, 9)}.{random.randint(0, 20)}",
        "last_boot": (datetime.utcnow() - timedelta(hours=random.randint(1, 720))).strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Discos
    disks = [
        {
            "drive": "C:",
            "total_gb": random.choice([128.0, 256.0, 512.0]),
            "free_gb": round(random.uniform(15.0, 200.0), 1),
            "filesystem": "NTFS",
        }
    ]

    # Rede
    subnet = random.choice(["192.168.1", "192.168.2", "10.0.1", "10.0.2"])
    ip = f"{subnet}.{random.randint(10, 250)}"
    network = [
        {
            "ip": ip,
            "mac": random_mac(),
            "gateway": f"{subnet}.1",
            "dns": "8.8.8.8, 8.8.4.4",
            "adapter_name": "Ethernet",
        }
    ]

    # Licenças
    win_status = random.choices(["Licensed", "Unlicensed", "Notification"], weights=[75, 15, 10])[0]
    licenses = [
        {
            "product": "windows",
            "edition": random.choice(WINDOWS_EDITIONS),
            "version": "22H2" if random.random() > 0.3 else "23H2",
            "build": str(random.choice([19045, 22631, 22621])),
            "channel": random.choice(["KMS", "MAK", "OEM", "Retail"]),
            "license_status": win_status,
            "partial_product_key": "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=5)),
            "oem_key_found": random.random() > 0.7,
        }
    ]

    # Office (nem toda máquina tem)
    if random.random() > 0.25:
        office_status = random.choices(["Licensed", "Unlicensed"], weights=[80, 20])[0]
        licenses.append(
            {
                "product": "office",
                "version": random.choice(OFFICE_VERSIONS),
                "edition": "Professional Plus",
                "channel": "Volume",
                "license_status": office_status,
                "partial_product_key": "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=5)),
                "detection_method": "WMI",
            }
        )

    # Software (8 a 15 softwares aleatórios)
    sw_list = random.sample(SOFTWARES, k=random.randint(8, min(15, len(SOFTWARES))))
    software = [
        {
            "name": s[0],
            "version": s[1],
            "publisher": s[2],
            "install_date": (datetime.utcnow() - timedelta(days=random.randint(1, 365))).strftime("%Y%m%d"),
            "install_location": f"C:\\Program Files\\{s[0].split()[0]}",
        }
        for s in sw_list
    ]

    return {
        "hostname": hostname,
        "domain": DOMAIN,
        "hardware": hardware,
        "disks": disks,
        "network": network,
        "licenses": licenses,
        "software": software,
    }


def main():
    print(f"Tabularium — Seed de demonstração")
    print(f"API: {API_URL}")
    print(f"Gerando {len(HOSTNAMES)} hosts...\n")

    ok = 0
    fail = 0

    for i, hostname in enumerate(HOSTNAMES, 1):
        payload = generate_host(hostname, i)
        try:
            resp = requests.post(
                f"{API_URL}/api/hosts/checkin",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                ok += 1
                print(f"  [{i:02d}/{len(HOSTNAMES)}] ✓ {hostname}")
            else:
                fail += 1
                print(f"  [{i:02d}/{len(HOSTNAMES)}] ✗ {hostname} — HTTP {resp.status_code}: {resp.text[:80]}")
        except Exception as e:
            fail += 1
            print(f"  [{i:02d}/{len(HOSTNAMES)}] ✗ {hostname} — {e}")

    # ---- Seed de hosts legados ----
    legacy_hostnames = [
        "OLD-FINANCE-04",
        "OLD-RH-03",
        "OLD-TI-HELPDESK-04",
        "OLD-LOGISTICA-04",
        "OLD-MARKETING-03",
    ]

    print(f"\nGerando {len(legacy_hostnames)} hosts legados...\n")

    for i, hostname in enumerate(legacy_hostnames, 1):
        days_ago = random.randint(91, 365)  # 91 a 365 dias sem contato
        payload = generate_host(hostname, i, force_days_ago=days_ago)
        try:
            resp = requests.post(
                f"{API_URL}/api/hosts/checkin",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                ok += 1
                print(f"  [{i:02d}/{len(legacy_hostnames)}] ✓ {hostname} (será marcado legado)")
            else:
                fail += 1
                print(f"  [{i:02d}/{len(legacy_hostnames)}] ✗ {hostname} — HTTP {resp.status_code}: {resp.text[:80]}")
        except Exception as e:
            fail += 1
            print(f"  [{i:02d}/{len(legacy_hostnames)}] ✗ {hostname} — {e}")

    print(f"\n{'='*40}")
    print(f"Resultado: {ok} OK | {fail} falhas | {len(HOSTNAMES) + len(legacy_hostnames)} total")
    print(f"\nNota: Hosts OLD-* foram criados com last_seen antigo.")
    print(f"      Eles serão automaticamente marcados como legados ao acessar /api/hosts/legacy.")
    print(f"\nAcesse: {API_URL.replace(':8091', ':8090')}/dashboard.html")


if __name__ == "__main__":
    main()
