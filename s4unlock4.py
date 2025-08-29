#!/usr/bin/python
# Script4Unlock v4.2.1 Stealth

import os, importlib, requests, json, hashlib, urllib.parse, time, sys, base64, ntplib, random
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse, quote

# --- Ensure dependencies ---
for lib in ['requests','ntplib']:
    try:
        importlib.import_module(lib)
    except ModuleNotFoundError:
        os.system(f'pip install {lib}')

# ---------------- CONFIG ----------------
VERSION = "4.2.1"
UserAgents = [
    "okhttp/4.12.0", "okhttp/4.11.0", "okhttp/4.10.0",
    "Dalvik/2.1.0 (Linux; U; Android 13; MiuiBrowser/16.5.22)"
]
versionCode = '500418'
versionName = '5.4.18'
api = "https://sgp-api.buy.mi.com/bbs/api/global/"
U_state = api + "user/bl-switch/state"
U_apply = api + "apply/bl-auth"
U_info  = api + "user/data"

# Telegram Bot (optional)
TELEGRAM_ENABLED = False
TELEGRAM_TOKEN = "your-bot-token"
TELEGRAM_CHATID = "your-chat-id"

# ---------------- HELPERS ----------------
def notify(msg):
    print(msg)
    if TELEGRAM_ENABLED:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHATID, "text": msg}
            )
        except: pass

def parse_json_response(res): 
    return json.loads(res.text[11:])

def get_ntp_time(servers=["pool.ntp.org","time.google.com","time.windows.com"]):
    c = ntplib.NTPClient()
    for s in servers:
        try:
            return datetime.fromtimestamp(c.request(s, version=3, timeout=5).tx_time, timezone.utc)
        except: continue
    return datetime.now(timezone.utc)

def get_beijing_time(): 
    return get_ntp_time().astimezone(timezone(timedelta(hours=8)))

def precise_sleep(target_time, precision=0.01):
    while True:
        diff = (target_time - datetime.now(target_time.tzinfo)).total_seconds()
        if diff <= 0: return
        time.sleep(max(min(diff - precision/2, 1), precision))

def measure_latency(url=U_state, samples=3):
    """Measure latency against Xiaomi unlock API endpoint"""
    vals=[]
    for _ in range(samples):
        try:
            s=time.perf_counter()
            requests.get(url,timeout=2)
            vals.append((time.perf_counter()-s)*1000)
        except: continue
    return sum(vals)/len(vals) if vals else 200

# ---------------- LOGIN ----------------
def login():
    base_url = "https://account.xiaomi.com"
    sid = "18n_bbs_global"
    user = input('\nEnter Mobile/ID: ')
    pwd  = input('Enter Password: ')
    hash_pwd = hashlib.md5(pwd.encode()).hexdigest().upper()
    cookies = {}

    # Initial login request
    r = requests.get(f"{base_url}/pass/serviceLogin", params={'sid': sid, '_json': True}, cookies=cookies)
    cookies.update(r.cookies.get_dict()); deviceId=cookies["deviceId"]

    data = {k: v[0] for k,v in parse_qs(urlparse(parse_json_response(r)['location']).query).items()}
    data.update({'user': user,'hash': hash_pwd})

    r = requests.post(f"{base_url}/pass/serviceLoginAuth2", data=data, cookies=cookies)
    res=parse_json_response(r);cookies.update(r.cookies.get_dict())
    if res["code"]==70016: exit("Invalid user/pass ❌")

    region = json.loads(requests.get(f"{base_url}/pass/user/login/region",cookies=cookies).text[11:])["data"]["region"]
    nonce,ssecurity=res['nonce'],res['ssecurity']
    res['location']+=f"&clientSign={quote(base64.b64encode(hashlib.sha1(f'nonce={nonce}&{ssecurity}'.encode()).digest()))}"
    serviceToken=requests.get(res['location'],cookies=cookies).cookies.get_dict()

    micdata={"userId":res['userId'],"new_bbs_serviceToken":serviceToken["new_bbs_serviceToken"],"region":region,"deviceId":deviceId}
    with open("micdata.json","w") as f: json.dump(micdata,f)
    return micdata

def load_account():
    try:
        with open('micdata.json') as f: d=json.load(f)
        if all(d.get(k) for k in ("userId","new_bbs_serviceToken","region","deviceId")): 
            print(f"\nAccount ID: {d['userId']}")
            input("Press Enter to continue, Ctrl+D to logout...")
            return d
    except: pass
    if os.path.exists('micdata.json'): os.remove('micdata.json')
    return login()

# ---------------- MAIN API ----------------
def get_headers(micdata):
    ua = random.choice(UserAgents)
    return {
        'User-Agent': ua,
        'Accept-Encoding': "gzip",
        'Content-Type': "application/json",
        'Cookie': f"new_bbs_serviceToken={micdata['new_bbs_serviceToken']};versionCode={versionCode};versionName={versionName};deviceId={micdata['deviceId']};"
    }

def account_info(h):
    print("\n[INFO]:")
    info=requests.get(U_info,headers=h).json().get('data',{})
    lvl=info.get('level_info',{})
    print(f"{info.get('registered_day','?')} days in Community")
    print(f"LV{lvl.get('level','?')} {lvl.get('level_title','')}")
    print(f"{lvl.get('max_value',0) - lvl.get('current_value',0)} more points to next level")
    print(f"Points: {lvl.get('current_value',0)}")

def state_request(h):
    print("\n[STATE]:")
    s=requests.get(U_state,headers=h).json().get("data",{})
    if s.get("is_pass")==1: exit(f"✅ Already approved until {s.get('deadline_format')} Beijing\n")
    if s.get("button_state") in [2,3]: exit("❌ Not eligible, try later\n")
    print("Eligible: Apply for unlocking\n")

def apply_request(h):
    print("\n[APPLY]:")
    try:
        r=requests.post(U_apply,data=json.dumps({"is_retry":True}),headers=h)
        resp=r.json()
        if resp.get("code")!=0: return False,resp
        d=resp.get("data",{})
        if d.get("apply_result")==1: 
            print("✅ Application Successful")
            return True,d
        if d.get("apply_result")==3: 
            print("⚠️ Quota limit reached, retry tomorrow")
            return False,{"msg":"Quota full"}
        return False,d
    except Exception as e: return False,{"err":str(e)}

# ---------------- SCHEDULER ----------------
def schedule_daily_task(micdata, once=True):
    h=get_headers(micdata)
    account_info(h); state_request(h)

    now=get_beijing_time()
    target=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if now>=target: target+=timedelta(days=1)

    latency=measure_latency(); jitter=random.randint(-8,8)
    exec_time=target-timedelta(milliseconds=(latency-5+jitter))
    print(f"\nQuota reset target: {target.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")
    print(f"Measured latency: {latency:.2f} ms, Jitter: {jitter} ms")
    print(f"Execution time: {exec_time.strftime('%Y-%m-%d %H:%M:%S.%f')} (UTC+8)")

    precise_sleep(exec_time)
    ok,res=apply_request(h)
    if ok: 
        notify(f"✅ Application successful, valid till {res.get('deadline_format','?')}")
        return 1
    else:
        if res.get("msg")=="Quota full": 
            notify("⚠️ Quota full, retry tomorrow.")
        else: 
            notify(f"❌ Failed: {res}")
        return 0

    if not once:
        print("Looping daily...")
        while True:
            time.sleep(3600); schedule_daily_task(micdata,once=True)

# ---------------- RUN ----------------
if __name__=="__main__":
    print(f"\n[~~~V{VERSION}] Stealth\n - Bootloader Unlock Apply for Permission\n - Enhanced by ChatGpt\n")
    micdata=load_account()
    mode="--daily" in sys.argv
    result = schedule_daily_task(micdata, once=not mode)
    if result!=1: sys.exit(1)
