#!/usr/bin/env python3
# Script4Unlock — fused:

import os, sys, json, time, base64, hashlib, random, importlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse, quote

# --- Ensure deps ---
for lib in ['requests','ntplib','imcplib']:
    try:
        importlib.import_module(lib)
    except ModuleNotFoundError:
        os.system(f'{sys.executable} -m pip install -q {lib}')
# icmplib is optional; we'll fall back if missing
try:
    import icmplib  # type: ignore
    HAVE_ICMP = True
except Exception:
    HAVE_ICMP = False

import requests
import ntplib

VERSION = "3.1 exp"
# Use latest seen app version (from v9)
VERSION_CODE = "500423"
VERSION_NAME = "5.4.23"

# API endpoints (same as v4/v9)
API_BASE = "https://sgp-api.buy.mi.com/bbs/api/global/"
U_STATE = API_BASE + "user/bl-switch/state"
U_APPLY = API_BASE + "apply/bl-auth"
U_INFO  = API_BASE + "user/data"

# Login constants (from v4/v9)
BASE_URL = "https://account.xiaomi.com"
SID = "18n_bbs_global"

# User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 12; Mi 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Mobile Safari/537.36",
    "okhttp/4.12.0", "okhttp/4.11.0", "okhttp/4.10.0",
    "Dalvik/2.1.0 (Linux; U; Android 13; MiuiBrowser/16.5.22)"
]

# Time & ping config (expanded NTP set from v9)
NTP_SERVERS = list(set([
    "time1.google.com", "time2.google.com", "time3.google.com", "time4.google.com",
    "time.google.com", "time.android.com", "time.cloudflare.com",
    "time.windows.com", "pool.ntp.org", "ntp.aliyun.com", "time.aws.com"
]))
MI_TARGETS = list(set(['sgp-api.buy.mi.com', '20.157.18.26']))

# ---------- Helpers ----------
def parse_json_response_text(text: str):
    # Xiaomi auth responses start with "&&&START&&&"
    return json.loads(text[11:])

def get_ntp_time():
    c = ntplib.NTPClient()
    for host in random.sample(NTP_SERVERS, len(NTP_SERVERS)):
        try:
            r = c.request(host, version=3, timeout=3)
            return datetime.fromtimestamp(r.tx_time, timezone.utc)
        except Exception:
            continue
    # fallback: system time
    return datetime.now(timezone.utc)

def bj_now():
    # UTC+8 (Beijing)
    return get_ntp_time().astimezone(timezone(timedelta(hours=8)))

def precise_sleep(target_dt: datetime, precision=0.01):
    """Tight sleep loop to hit target time with ~10ms granularity."""
    tz = target_dt.tzinfo
    while True:
        diff = (target_dt - datetime.now(tz)).total_seconds()
        if diff <= 0:
            return
        time.sleep(max(min(diff - precision/2, 1), precision))

def headers(micdata):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
        "Cookie": (
            f"new_bbs_serviceToken={micdata['new_bbs_serviceToken']};"
            f"versionCode={VERSION_CODE};versionName={VERSION_NAME};"
            f"deviceId={micdata['deviceId']};"
        ),
    }

def http_latency_ms(url=U_STATE, samples=3):
    vals=[]
    for _ in range(samples):
        try:
            s=time.perf_counter()
            requests.get(url, timeout=2)
            vals.append((time.perf_counter()-s)*1000.0)
        except Exception:
            pass
    return sum(vals)/len(vals) if vals else 10.0

def icmp_avg_ping_ms():
    if not HAVE_ICMP:
        return None
    vals=[]
    for host in random.sample(MI_TARGETS, len(MI_TARGETS)):
        try:
            # unprivileged pings (no root). Count 3, 200ms interval
            res = icmplib.ping(host, count=3, interval=0.2, timeout=1, privileged=False)
            if res.is_alive:
                vals.append(res.avg_rtt)
        except Exception:
            pass
    return round(sum(vals)/len(vals), 2) if vals else None

def calculate_script_time(ping_ms: float) -> float:
    """
    v9 formula:
    - if ping <= 5ms -> 59.975s
    - else -> 59.091 + (166 - ping) * 0.006
    clamp between 55 and 65 seconds
    """
    if ping_ms <= 0:
        ping_ms = 1.0
    result = 59.975 if ping_ms <= 5 else 59.091 + (166 - ping_ms) * 0.006
    return max(55.0, min(65.0, result))

# ---------- Login & Account (from v4) ----------
def login():
    print("\n[LOGIN] Xiaomi Account")
    user = input("Enter Email/Phone/ID: ").strip()
    pwd  = input("Enter Password: ").strip()
    hash_pwd = hashlib.md5(pwd.encode()).hexdigest().upper()
    cookies = {}

    # Step 1: initial login page
    r = requests.get(f"{BASE_URL}/pass/serviceLogin",
                     params={'sid': SID, '_json': True}, cookies=cookies)
    cookies.update(r.cookies.get_dict())
    deviceId = cookies.get("deviceId", "")

    data = {k: v[0] for k,v in parse_qs(urlparse(parse_json_response_text(r.text)['location']).query).items()}
    data.update({'user': user, 'hash': hash_pwd})

    # Step 2: auth
    r2 = requests.post(f"{BASE_URL}/pass/serviceLoginAuth2", data=data, cookies=cookies)
    res = parse_json_response_text(r2.text); cookies.update(r2.cookies.get_dict())
    if res.get("code") == 70016:
        sys.exit("Invalid credentials ❌")

    # Step 3: finalize token
    nonce, ssecurity = res['nonce'], res['ssecurity']
    clientSign = base64.b64encode(hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode()).digest())
    final_loc = res['location'] + "&clientSign=" + quote(clientSign)

    r3 = requests.get(final_loc, cookies=cookies)
    st = r3.cookies.get_dict().get("new_bbs_serviceToken")
    if not st:
        sys.exit("Failed to obtain service token ❌")

    # user region not strictly needed, but keep file format compatible with v4
    try:
        reg = requests.get(f"{BASE_URL}/pass/user/login/region", cookies=cookies).text
        region = parse_json_response_text(reg)["data"]["region"]
    except Exception:
        region = "N/A"

    micdata = {
        "userId": res.get("userId"),
        "new_bbs_serviceToken": st,
        "region": region,
        "deviceId": deviceId or generate_device_id()
    }
    with open("micdata.json","w",encoding="utf-8") as f:
        json.dump(micdata,f,indent=2,ensure_ascii=False)
    return micdata

def generate_device_id():
    raw = f"{random.random()}-{time.time()}".encode()
    return hashlib.sha1(raw).hexdigest().upper()

def load_account():
    try:
        with open('micdata.json','r',encoding='utf-8') as f:
            d=json.load(f)
        if all(d.get(k) for k in ("userId","new_bbs_serviceToken","region","deviceId")):
            print(f"\nAccount ID: {d['userId']}")
            _ = input("Press Enter to continue, Ctrl+C to re-login...")
            return d
    except Exception:
        pass
    if os.path.exists('micdata.json'):
        try: os.remove('micdata.json')
        except Exception: pass
    return login()

# ---------- API helpers ----------
def account_info(h):
    try:
        info=requests.get(U_INFO,headers=h,timeout=5).json().get('data',{})
        lvl=info.get('level_info',{})
        print("\n[INFO]")
        print(f"- Community days: {info.get('registered_day','?')}")
        print(f"- Level: LV{lvl.get('level','?')} {lvl.get('level_title','')}")
        print(f"- Points: {lvl.get('current_value',0)} (to next: {lvl.get('max_value',0)-lvl.get('current_value',0)})")
    except Exception as e:
        print(f"[WARN] Failed to fetch account info: {e}")

def check_state(h):
    s = requests.get(U_STATE,headers=h,timeout=5).json().get("data",{})
    print("\n[STATE]")
    if s.get("is_pass")==1:
        sys.exit(f"✅ Already approved until {s.get('deadline_format')} Beijing")
    if s.get("button_state") in [2,3]:
        sys.exit("❌ Not eligible now; try later")
    print("Eligible: Apply for unlocking")

def apply_request(h):
    try:
        r=requests.post(U_APPLY, json={"is_retry": True}, headers=h, timeout=5)
        resp=r.json()
        if resp.get("code")!=0:
            return False, resp
        d=resp.get("data",{})
        if d.get("apply_result")==1:
            return True, d
        if d.get("apply_result")==3:
            return False, {"msg":"Quota full", "deadline_format": d.get("deadline_format"), "desc": resp.get("desc","")}
        return False, d
    except Exception as e:
        return False, {"err": str(e)}

# ---------- v9-style Scheduling fused into v4 CLI ----------
def schedule_and_apply(micdata, once=True):
    h=headers(micdata)
    account_info(h)
    check_state(h)

    # Phase A: wait until ~23:59:48 CST to measure ping
    now = bj_now()
    ping_phase_start = now.replace(hour=23, minute=59, second=48, microsecond=0)
    if now > ping_phase_start:
        ping_phase_start += timedelta(days=1)

    print("\n[TIMING]")
    print(f"- Beijing now:     {now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    print(f"- Ping phase @:    {ping_phase_start.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (UTC+8)")

    precise_sleep(ping_phase_start)

    # Phase B: measure latency (ICMP preferred; fallback HTTP)
    icmp_ms = icmp_avg_ping_ms()
    if icmp_ms is not None:
        avg_ping = icmp_ms
        method = "ICMP"
    else:
        avg_ping = http_latency_ms(samples=4)
        method = "HTTP"

    script_time_s = calculate_script_time(avg_ping)

    now2 = bj_now()
    base = now2.replace(hour=23, minute=59, second=0, microsecond=0)
    target_apply = base + timedelta(seconds=script_time_s)
    if now2 > target_apply:
        target_apply += timedelta(days=1)

    print(f"- Ping method:     {method}")
    print(f"- Avg ping:        {avg_ping:.2f} ms")
    print(f"- Script offset:   {script_time_s:.3f} s  (v9 formula)")
    print(f"- Apply target:    {target_apply.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (UTC+8)")

    # Phase C: precise wait & apply
    precise_sleep(target_apply)
    sent_at = bj_now()
    ok, res = apply_request(h)
    recv_at = bj_now()

    print("\n[RESULT]")
    print(f"- Sent at:         {sent_at.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (UTC+8)")
    print(f"- Response at:     {recv_at.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (UTC+8)")

    if ok:
        print(f"✅ Application Successful. Valid till {res.get('deadline_format','?')}")
        return 1
    else:
        if res.get("msg")=="Quota full":
            print(f"⚠️ Quota full. Desc: {res.get('desc','')}. Try again on {res.get('deadline_format','tomorrow')}.")
        else:
            print(f"❌ Failed: {res}")
        return 0

    # daily loop (if requested)
    if not once:
        while True:
            time.sleep(3600)
            schedule_and_apply(micdata, once=True)

# ---------- Main ----------
if __name__ == "__main__":
    print(f"\n[~~~ Script4Unlock V{VERSION}]~~~")
    print(" - Bootloader Unlock Permission")
    print(" - Smarter scheduling")
    print(" - Enhanced by ChatGpt\n")

    micdata = load_account()
    daily_mode = "--daily" in sys.argv
    ret = schedule_and_apply(micdata, once=not daily_mode)
    if ret != 1:
        sys.exit(1)
